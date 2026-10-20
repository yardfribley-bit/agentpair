"""Build-gated, administrator-owned acceptance runs. No model-supplied shell."""
import copy
import datetime as dt
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.request
from pathlib import Path

from .software_install import SoftwareCatalog, verify_receipt


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class GitHubBuildProof:
    """Verify public CI and release asset identity with GitHub, not client booleans."""
    def __init__(self, get=None):
        self.get = get or self._get

    @staticmethod
    def _get(path):
        request = urllib.request.Request('https://api.github.com/' + path,
            headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'AgentPair-readiness'})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)

    def verify(self, proof, recipe):
        if not isinstance(proof, dict) or set(proof) != {'repository', 'runId', 'commit', 'tag'}:
            raise ValueError('需要仓库、CI 编号、源码提交和发布标签')
        repo, run, commit, tag = (proof[k] for k in ('repository', 'runId', 'commit', 'tag'))
        if (not isinstance(repo, str) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo)
                or type(run) is not int or run <= 0
                or not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}', commit)
                or not isinstance(tag, str) or not re.fullmatch(r'[\w.-]{1,100}', tag)):
            raise ValueError('构建标识无效')
        ci = self.get(f'repos/{repo}/actions/runs/{run}')
        if (ci.get('status') != 'completed' or ci.get('conclusion') != 'success'
                or ci.get('head_sha') != commit or ci.get('repository', {}).get('full_name') != repo):
            raise ValueError('CI 未成功或不是所选源码版本；禁止开机')
        ref = self.get(f'repos/{repo}/git/ref/tags/{tag}')['object']
        for _ in range(4):
            if ref['type'] == 'commit':
                break
            if ref['type'] != 'tag':
                raise ValueError('发布标签类型无效')
            ref = self.get(f'repos/{repo}/git/tags/{ref["sha"]}')['object']
        if ref.get('type') != 'commit' or ref.get('sha') != commit:
            raise ValueError('发布标签与 CI 源码不一致')
        release = self.get(f'repos/{repo}/releases/tags/{tag}')
        if release.get('draft'):
            raise ValueError('安装包尚未发布')
        asset = next((a for a in release.get('assets', [])
                      if a.get('browser_download_url') == recipe.get('url')), None)
        if (not asset or asset.get('state') != 'uploaded' or asset.get('size', 0) <= 0
                or asset.get('digest') != 'sha256:' + recipe.get('sha256', '')):
            raise ValueError('发布安装包不存在或 SHA256 不一致；禁止开机')
        return {'repository': repo, 'runId': run, 'commit': commit, 'tag': tag,
                'ciURL': f'https://github.com/{repo}/actions/runs/{run}',
                'assetId': asset['id'], 'sha256': recipe['sha256'], 'checkedAt': stamp()}


class TestRuns:
    """Persist every dispatch identity, expose evidence, clean up on every terminal path."""
    TERMINAL = {'passed', 'failed', 'cancelled', 'interrupted'}
    CASES = ('realtime_events', 'security_observation', 'cpu_idle_active_hidden')

    def __init__(self, directory, console, *, verifier=None, start_threads=True, sleep=time.sleep, clock=None):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.console = console
        self.catalog = SoftwareCatalog(self.directory.parent / 'software-catalog.json')
        self.verifier = verifier or GitHubBuildProof()
        self.start_threads, self.sleep = start_threads, sleep
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.lock = threading.RLock()
        self.active = set()
        if start_threads:
            for row in self.list():
                if row.get('confirmedAt') and row.get('cleanupState') != 'released':
                    self._launch(row['id'], recovery=True)

    def _path(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{24}', ident):
            raise ValueError('测试编号无效')
        return self.directory / (ident + '.json')

    def get(self, ident):
        with self.lock:
            try:
                return json.loads(self._path(ident).read_text())
            except FileNotFoundError:
                raise KeyError('Test run not found') from None

    def list(self):
        with self.lock:
            return sorted([json.loads(p.read_text()) for p in self.directory.glob('*.json')],
                          key=lambda r: r['createdAt'], reverse=True)

    def _write(self, row):
        path = self._path(row['id'])
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(row, ensure_ascii=False, indent=2))
        tmp.chmod(0o600)
        tmp.replace(path)

    def event(self, ident, stage, message, **evidence):
        with self.lock:
            row = self.get(ident)
            row['events'].append({'sequence': len(row['events']) + 1, 'at': stamp(),
                                  'stage': stage, 'message': message, 'evidence': evidence})
            row['updatedAt'] = stamp()
            self._write(row)

    @staticmethod
    def fingerprint(recipe):
        return hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()

    def prepare(self, data):
        if not self.console:
            raise ValueError('云服务未配置')
        if set(data) != {'softwareIds', 'subjectSoftwareId', 'build', 'plan'}:
            raise ValueError('请提供软件清单、被测软件、构建证明及验收计划')
        ids = data['softwareIds']
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 5
                or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids)
                or data['subjectSoftwareId'] not in ids):
            raise ValueError('软件清单无效')
        plan = data['plan']
        if (not isinstance(plan, dict) or set(plan) != set(self.CASES)
                or any(not isinstance(v, str) or not 10 <= len(v.strip()) <= 2000 for v in plan.values())):
            raise ValueError('必须说明实时事件、安全观测和三种状态 CPU 的验收步骤与标准')
        recipes = [self.catalog.items().get(sid) for sid in ids]
        if any(r is None for r in recipes):
            raise ValueError('请先登记所有安装包')
        if any(r['platform'] != 'Windows' for r in recipes):
            raise ValueError('当前验收模板仅支持 Windows')
        subject = next(r for r in recipes if r['id'] == data['subjectSoftwareId'])
        try:
            proof = self.verifier.verify(data['build'], subject)
        except (OSError, KeyError, TypeError):
            raise ValueError('未能核验 GitHub 构建或发布资产，未创建机器') from None
        # Quote only after proof succeeds. No create call exists in preparation.
        quote = self.console.quote('Windows')
        row = {'id': secrets.token_hex(12), 'createdAt': stamp(), 'updatedAt': stamp(),
               'state': 'ready', 'softwareIds': ids, 'subjectSoftwareId': subject['id'],
               'build': copy.deepcopy(data['build']), 'proof': proof, 'plan': plan,
               'fingerprints': {r['id']: self.fingerprint(r) for r in recipes},
               'quote': quote, 'quoteExpiresAt': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)).isoformat(),
               'requestId': secrets.token_hex(16), 'leaseId': None, 'events': [], 'operations': [],
               'results': {}, 'cleanupState': 'not_required', 'cancelRequested': False}
        with self.lock:
            self._write(row)
        self.event(row['id'], 'readiness', 'CI、源码、发布安装包及验收计划已核验；尚未开机。', proof=proof)
        return self.get(row['id'])

    def confirm(self, ident, data):
        with self.lock:
            row = self.get(ident)
            if set(data) != {'requestId', 'confirmed', 'maxHourlyCNY'} or data['requestId'] != row['requestId']:
                raise ValueError('请确认当前测试报价')
            if row['state'] != 'ready':
                if row.get('confirmedAt'):
                    return row
                raise ValueError('当前测试不可开机')
            if (data['confirmed'] is not True or type(data['maxHourlyCNY']) not in (int, float)
                    or data['maxHourlyCNY'] != row['quote']['hourlyCNY']):
                raise ValueError('请明确确认当前金额与 60 分钟租约')
            if dt.datetime.fromisoformat(row['quoteExpiresAt']) <= dt.datetime.now(dt.timezone.utc):
                raise ValueError('报价已过期，请重新准备测试')
            self._check_recipes(row)
            subject = self.catalog.items()[row['subjectSoftwareId']]
            try:
                self.verifier.verify(row['build'], subject)
            except (OSError, KeyError, TypeError):
                raise ValueError('构建复核失败，未创建机器') from None
            row.update(state='creating', confirmedAt=stamp(), cleanupState='pending')
            self._write(row)
            self.event(ident, 'creating', '管理员已确认报价；开始创建。异常、完成或停止测试保留原机器，第58分钟询问回收。')
            self._launch(ident)
            return self.get(ident)

    def _check_recipes(self, row):
        for sid, fingerprint in row['fingerprints'].items():
            if self.fingerprint(self.catalog.items().get(sid)) != fingerprint:
                raise ValueError('安装方案已改变，必须重新准备测试')

    def _launch(self, ident, recovery=False):
        with self.lock:
            if ident in self.active:
                return
            self.active.add(ident)
        if self.start_threads:
            threading.Thread(target=self.run, args=(ident, recovery), daemon=True).start()

    def _state(self, ident, state, **changes):
        with self.lock:
            row = self.get(ident)
            row.update(state=state, updatedAt=stamp(), **changes)
            self._write(row)

    def _check(self, ident):
        row = self.get(ident)
        if row['cancelRequested']:
            raise InterruptedError('Cancelled')
        if row.get('expiresAt') and dt.datetime.fromisoformat(row['expiresAt']) <= self.clock() + dt.timedelta(seconds=120):
            raise TimeoutError('Lease ending')
        return row

    def run(self, ident, recovery=False):
        try:
            if recovery:
                previous = self.get(ident)
                if previous.get('leaseId') and previous.get('expiresAt') and not previous['cancelRequested'] and previous['state'] not in self.TERMINAL:
                    self._hold_for_reconciliation(ident, '服务重启；保留原机器现场，不重复创建或安装。')
                    return
                if previous['state'] not in self.TERMINAL:
                    self._state(ident, 'cancelled' if previous['cancelRequested'] else 'interrupted')
                self.event(ident, 'recovery', '服务重启；不重复创建或安装，核对原请求，按原租约回收规则处理。')
                return
            row = self._check(ident)
            self._check_recipes(row)
            lease = self.console.create('Windows', row['quote']['hourlyCNY'], row['requestId'], row['quote'].get('sizing'))
            self._state(ident, 'checking_login', leaseId=lease['id'], expiresAt=lease['expiresAt'])
            self.event(ident, 'checking_login', '机器返回；开始验证 SSH。',
                       lease={k: lease.get(k) for k in ('id', 'name', 'hostId', 'expiresAt', 'state')})
            for _ in range(120):
                self._check(ident)
                access = self.console.login(lease['id'])
                if access.get('loginState') == 'ssh_authenticated':
                    break
                self.sleep(3)
            else:
                raise TimeoutError('SSH readiness timeout')
            self.event(ident, 'checking_login', 'SSH 认证通过；不是产品功能通过。', loginState=access['loginState'])
            for sid in row['softwareIds']:
                self._check(ident)
                self._check_recipes(row)
                self._state(ident, 'installing')
                self.event(ident, 'installing', '提交安装 ' + sid)
                op = self.console.install(lease['id'], sid)
                with self.lock:
                    current = self.get(ident)
                    current['operations'].append({'id': op['id'], 'softwareId': sid})
                    self._write(current)
                last = None
                for _ in range(310):
                    self._check(ident)
                    op = self.console.operation(op['id'])
                    public = {k: op.get(k) for k in ('id', 'softwareId', 'state', 'stage', 'summary')}
                    public['evidence'] = {k: op.get('evidence', {}).get(k) for k in
                        ('softwareId', 'verified', 'installedVersion', 'sha256', 'launchVerified', 'selfTestExitCode')
                        if k in op.get('evidence', {})}
                    if public != last:
                        self.event(ident, 'installing', sid + ' 安装进度', operation=public)
                        last = public
                    if op['state'] not in ('queued', 'running'):
                        break
                    self.sleep(3)
                if op['state'] != 'completed' or not verify_receipt(self.catalog.get(sid), op):
                    raise RuntimeError('Installation not verified')
            self._check(ident)
            self._state(ident, 'awaiting_login')
            self.event(ident, 'login', '安装核验完成，请在远程桌面登录 WorkBuddy。管理员确认登录后再进入测试；无需提交验证码。')
            while self._check(ident)['state'] == 'awaiting_login':
                self.sleep(2)
            self.event(ident, 'testing', '管理员已确认账号登录，开始等待三项实测证据；登录确认不是功能通过。')
            # Results arrive via authenticated API; bounded by original lease.
            while True:
                current = self._check(ident)
                if any(not r['passed'] for r in current['results'].values()):
                    self._state(ident, 'failed')
                    return
                if set(current['results']) == set(self.CASES):
                    self._state(ident, 'passed' if all(r['passed'] for r in current['results'].values()) else 'failed')
                    return
                self.sleep(2)
        except InterruptedError:
            self._state(ident, 'cancelled')
            self.event(ident, 'cancelled', '停止后续调度，保留原机器到回收确认时间；在途安装是否停止尚未核验。')
        except Exception as error:
            previous = self.get(ident)
            if previous.get('leaseId') and previous.get('expiresAt') and previous['state'] in ('installing', 'checking_login'):
                self._hold_for_reconciliation(ident, '安装或连接未取得有效回执：' + type(error).__name__ + '。暂停后续步骤，保留当前机器供核查；不重复安装、不延长租约。')
            else:
                self._state(ident, 'failed')
                self.event(ident, 'failed', '测试未通过：' + type(error).__name__ + '；详细异常不公开以避免凭据泄露。')
        finally:
            try:
                self.cleanup(ident)
            finally:
                with self.lock:
                    self.active.discard(ident)

    def _hold_for_reconciliation(self, ident, reason):
        self._state(ident, 'needs_reconciliation')
        self.event(ident, 'needs_reconciliation', reason + ' 停止测试也保留机器；第58分钟询问回收，第59分钟无回复则回收。')

    def release_decision(self, ident, data):
        if set(data) != {'choice'} or data['choice'] not in ('release', 'keep'):
            raise ValueError('请选择立即回收或保留到原租约到期')
        with self.lock:
            row = self.get(ident)
            if not row.get('expiresAt'):
                raise ValueError('尚无有效租约')
            expiry = dt.datetime.fromisoformat(row['expiresAt'])
            now = self.clock()
            if row['cleanupState'] == 'released' or not expiry-dt.timedelta(minutes=2) <= now < expiry-dt.timedelta(minutes=1):
                raise ValueError('回收确认仅在第58至59分钟之间有效')
            if row.get('releaseDecision', {}).get('choice'):
                raise ValueError('本次回收选择已经固定')
            row['releaseDecision'] = dict(row.get('releaseDecision', {}), choice=data['choice'], repliedAt=now.isoformat())
            self._write(row)
            self.event(ident, 'release_decision', '管理员选择' + ('立即回收。' if data['choice']=='release' else '保留到原租约第60分钟；不续费。'))
            return self.get(ident)

    def confirm_login(self, ident, data):
        if set(data) != {'confirmed'} or data['confirmed'] is not True:
            raise ValueError('请明确确认已在远程桌面完成账号登录')
        with self.lock:
            row = self._check(ident)
            if row['state'] != 'awaiting_login':
                raise ValueError('尚未进入账号登录步骤')
            row.update(state='awaiting_results', accountLogin={'state':'operator_confirmed', 'at':stamp(), 'source':'administrator'})
            self._write(row)
            self.event(ident, 'login', '管理员确认已完成账号登录；此记录为人工确认，未由系统验证账号身份。')
            return self.get(ident)

    def _wait_for_release_window(self, ident):
        row = self.get(ident)
        if not row.get('expiresAt'):
            raise RuntimeError('未取得原租约截止时间，禁止提前回收')
        expiry = dt.datetime.fromisoformat(row['expiresAt'])
        while True:
            now = self.clock()
            row = self.get(ident)
            decision = row.get('releaseDecision', {})
            if row['cancelRequested'] and row['state'] == 'needs_reconciliation':
                self._state(ident, 'cancelled')
            if now >= expiry or decision.get('choice') == 'release':
                if self.get(ident)['state'] == 'needs_reconciliation':
                    self._state(ident, 'failed')
                return
            if now >= expiry-dt.timedelta(minutes=2) and not decision.get('requestedAt'):
                with self.lock:
                    row = self.get(ident)
                    row['releaseDecision'] = dict(row.get('releaseDecision', {}), requestedAt=now.isoformat(),
                        replyDeadline=(expiry-dt.timedelta(minutes=1)).isoformat())
                    self._write(row)
                self.event(ident, 'release_question', '租约已到第58分钟，是否回收？第59分钟前未回复将自动回收；选择保留则到第60分钟回收，不续费。')
            if now >= expiry-dt.timedelta(minutes=1) and not decision.get('choice'):
                if self.get(ident)['state'] == 'needs_reconciliation':
                    self._state(ident, 'failed')
                self.event(ident, 'release_timeout', '第58分钟询问后，一分钟内未收到回复，按约定回收。')
                return
            self.sleep(2)

    def result(self, ident, data):
        if (set(data) != {'case', 'passed', 'evidence'} or data['case'] not in self.CASES
                or type(data['passed']) is not bool or not isinstance(data['evidence'], str)
                or not 20 <= len(data['evidence'].strip()) <= 12000):
            raise ValueError('请提供验收项、结论和具体实测证据；不要粘贴凭据')
        with self.lock:
            row = self._check(ident)
            if row['state'] != 'awaiting_results':
                raise ValueError('尚未进入功能验收阶段')
            if data['case'] in row['results']:
                raise ValueError('验收证据已固定，不能覆盖')
            row['results'][data['case']] = dict(data, at=stamp(), source='administrator')
            self._write(row)
            self.event(ident, 'testing', '管理员提交实测证据：' + data['case'], result=row['results'][data['case']])
            return self.get(ident)

    def cancel(self, ident):
        with self.lock:
            row = self.get(ident)
            if row['state'] == 'ready':
                row.update(state='cancelled', cancelRequested=True)
            else:
                row['cancelRequested'] = True
            self._write(row)
            self.event(ident, 'cancel_requested', '请求停止后续调度，保留原机器；不提前回收。')
            if ident not in self.active and row['cleanupState'] != 'released' and row.get('confirmedAt'):
                self._launch(ident, recovery=True)
            return self.get(ident)

    def probe(self, ident, data):
        if set(data) != {'mode'} or data['mode'] not in ('idle', 'active', 'hidden'):
            raise ValueError('请选择空闲、活跃或隐藏窗口采样')
        with self.lock:
            row = self._check(ident)
            if row['state'] != 'awaiting_results' or row.get('probeActive'):
                raise ValueError('当前不可采样或已有采样正在执行')
            row['probeActive'] = True
            self._write(row)
        self.event(ident, 'cpu_probe', '开始只读 CPU 采样；场景标签由执行者指定，不自动证明窗口或任务状态。', mode=data['mode'])
        try:
            result = self.console.test_process_cpu(row['leaseId'])
            self.event(ident, 'cpu_probe', '采样返回；尚未判定功能或性能通过。', mode=data['mode'], **result)
            return self.get(ident)
        except Exception as error:
            self.event(ident, 'cpu_probe_failed', '采样失败，未标记通过。', errorType=type(error).__name__)
            raise ValueError('采样未完成，请查看时间线') from None
        finally:
            with self.lock:
                current = self.get(ident)
                current['probeActive'] = False
                self._write(current)

    def cleanup(self, ident):
        row = self.get(ident)
        if not row.get('confirmedAt') or row['cleanupState'] == 'released':
            return
        try:
            if not row['leaseId']:
                status = self.console.creation_status(row['requestId'])
                if not status.get('leaseId') or status.get('state') not in ('created', 'failed'):
                    # Unknown dispatch cannot be treated as zero billable resources.
                    raise RuntimeError('Creation needs reconciliation')
                self._state(ident, row['state'], leaseId=status['leaseId'])
                row = self.get(ident)
            if not row.get('expiresAt'):
                # Recover the original deadline; never start another lease.
                lease = self.console.inspect(row['leaseId'])
                self._state(ident, row['state'], expiresAt=lease['expiresAt'])
            self._wait_for_release_window(ident)
            row = self.get(ident)
            self.event(ident, 'releasing', '开始释放同一租约及绑定资源。', leaseId=row['leaseId'])
            receipt = self.console.release(row['leaseId'])
            if receipt.get('state') != 'released' or receipt.get('remainingHostCount') != 0:
                raise RuntimeError('Release not verified')
            self._state(ident, row['state'], cleanupState='released', releaseReceipt=receipt)
            self.event(ident, 'released', '主机已释放；绑定资源释放请求及回执如下。', receipt=receipt)
        except Exception as error:
            self._state(ident, self.get(ident)['state'], cleanupState='failed')
            self.event(ident, 'cleanup_failed', '回收未取得有效回执，需要重试；到期回收定时器仍负责兜底。', errorType=type(error).__name__)
