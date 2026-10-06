"""Task-linked cloud tools. A model can propose; only an admin can confirm spending."""
import copy
import datetime
import hashlib
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path

from .software_install import SoftwareCatalog, verify_receipt
from .tasks import Conflict, now


class CloudWorkflow:
    def __init__(self, engine, console, *, sleep=time.sleep, start_threads=True):
        self.engine = engine
        self.console = console
        self.sleep = sleep
        self.start_threads = start_threads
        self.active = set()
        self.lock = threading.RLock()
        self.catalog = SoftwareCatalog(Path(engine.db).parent / 'software-catalog.json')
        for item in engine.list():
            task = engine.get(item['id'])
            action = task.get('cloudAction', {})
            if (task['status'] == 'interrupted' and action.get('round') == task['round']
                    and action.get('state') in ('creating', 'checking_login', 'installing')):
                self._save(task['id'], 'interrupted', '服务重启，云操作回执未完整收到；已保留机器与安装编号，不自动重复执行。')

    def capabilities(self):
        return {'configured': self.console is not None, 'actions': ['create'],
                'systems': ['Windows', 'Linux'], 'requiresPriceConfirmation': True,
                'software': [{'id': r['id'], 'name': r['name'], 'platform': r['platform'],
                              'version': r.get('version')} for r in self.catalog.items().values()]}

    @staticmethod
    def _fingerprint(recipe):
        return hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()

    def _save(self, tid, state, summary, **changes):
        with self.engine.lock:
            task = self.engine._load(tid)
            action = task['cloudAction']
            if task['round'] != action['round']:
                raise Conflict('任务已进入新一轮，旧云操作不能继续')
            if task['status'] in ('cancelled', 'cancelling'):
                raise InterruptedError('Cancelled')
            action.update(changes, state=state, summary=summary, updatedAt=now())
            task['status'] = {'creating': 'running', 'checking_login': 'running',
                              'installing': 'running', 'waiting_machine': 'waiting_for_machine',
                              'awaiting_confirmation': 'awaiting_confirmation',
                              'unavailable': 'unsupported_capability'}.get(state, state)
            action['finalAnswer'] = summary
            task['events'].append({'at': now(), 'round': task['round'], 'kind': 'cloud_tool_result',
                                   'role': 'navigator', 'stage': 'cloud_management', 'text': summary,
                                   'cloudState': state, 'requestId': action['requestId']})
            self.engine._save(task)
            return copy.deepcopy(action)

    def prepare(self, tid, plan, outputs):
        tool = plan.get('tool', {})
        if not isinstance(tool, dict) or tool.get('name') != 'cloud_management':
            return False
        with self.engine.lock:
            task = self.engine._load(tid)
            action = {'round': task['round'], 'requestId': uuid.uuid4().hex,
                      'system': tool.get('system'), 'softwareIds': [], 'software': [],
                      'operations': [], 'leaseId': None, 'state': 'preparing', 'createdAt': now()}
            task['cloudAction'] = action
            # Preserve the plan without inventing Driver/model invocations.
            task['results'].append({'round': task['round'], 'outputs': dict(outputs, driver={})})
            self.engine._save(task)
        if task.get('owner') not in (None, 'admin') or task.get('securityEvidence'):
            self._save(tid, 'unavailable', '云机器操作需要管理员确认；本次未创建机器。')
            return True
        if self.console is None:
            self._save(tid, 'unavailable', '平台尚未配置云机器工具；本次未创建机器。')
            return True
        if tool.get('action') != 'create' or action['system'] not in ('Windows', 'Linux'):
            self._save(tid, 'unavailable', '当前任务工具支持创建 Windows 或 Linux 机器；未执行其他云操作。')
            return True
        ids = tool.get('softwareIds', [])
        if (not isinstance(ids, list) or len(ids) > 5
                or any(not isinstance(x, str) or not re.fullmatch(r'[a-z][a-z0-9-]{2,60}', x) for x in ids)):
            self._save(tid, 'unavailable', '安装软件清单无效；本次未创建机器。')
            return True
        ids = list(dict.fromkeys(ids))
        recipes = self.catalog.items()
        missing = [sid for sid in ids if sid not in recipes or recipes[sid]['platform'] != action['system']]
        if missing:
            self._save(tid, 'unavailable', '这些软件尚未登记可验证的安装包：' + '、'.join(missing)
                       + '。请先在软件包页面登记，本次未创建机器。', missingSoftware=missing, softwareIds=ids)
            return True
        software = [{'id': sid, 'name': recipes[sid]['name'], 'version': recipes[sid].get('version'),
                     'recipeFingerprint': self._fingerprint(recipes[sid])} for sid in ids]
        try:
            quote = self.console.quote(action['system'], tool.get('sizing'))
        except (ValueError, RuntimeError) as error:
            self._save(tid, 'unavailable', '报价未完成，未创建机器（' + type(error).__name__ + '）。请检查云配置及报价参数。')
            return True
        self._save(tid, 'awaiting_confirmation',
                   f"已取得 {quote['system']} 机器报价 ¥{quote['hourlyCNY']:.2f}/小时，租约 60 分钟。请确认后开机并安装；目前尚未产生新机器。",
                   quote=quote, softwareIds=ids, software=software,
                   quoteExpiresAt=(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)).isoformat())
        return True

    def confirm(self, tid, data):
        if set(data) - {'requestId', 'confirmed', 'maxHourlyCNY'}:
            raise ValueError('确认只接受报价编号及金额，不能改动机器或软件清单')
        with self.lock, self.engine.lock:
            task = self.engine._load(tid)
            action = task.get('cloudAction', {})
            if task['round'] != action.get('round') or data.get('requestId') != action.get('requestId'):
                raise Conflict('报价已改变，请刷新任务并重新确认')
            if action.get('state') != 'awaiting_confirmation':
                # Identical repeated confirms acknowledge the existing operation; never allocate again.
                if action.get('confirmedAt'):
                    return copy.deepcopy(action)
                raise Conflict('本任务没有可确认的云机器报价')
            if data.get('confirmed') is not True:
                raise ValueError('请明确确认报价及 60 分钟租约')
            cap = data.get('maxHourlyCNY')
            quote = action['quote']
            if type(cap) not in (float, int) or not math.isfinite(cap) or cap <= 0 or cap != quote['hourlyCNY']:
                raise ValueError('确认金额必须与本任务报价一致')
            if datetime.datetime.fromisoformat(action['quoteExpiresAt']) <= datetime.datetime.now(datetime.timezone.utc):
                raise Conflict('报价已过期，请重新提出开机请求获取报价')
            recipes = self.catalog.items()
            for item in action['software']:
                if item['id'] not in recipes or self._fingerprint(recipes[item['id']]) != item['recipeFingerprint']:
                    raise Conflict('安装包登记已改变，请重新获取报价和安装计划')
            self._save(tid, 'creating', '已确认报价，正在调用云厂商创建机器。', confirmedAt=now())
            self._launch(tid, create=True)
            return copy.deepcopy(self.engine._load(tid)['cloudAction'])

    def resume(self, tid, data):
        if set(data) - {'requestId'}:
            raise ValueError('继续操作不能改动原机器或软件清单')
        with self.lock, self.engine.lock:
            action = self.engine._load(tid).get('cloudAction', {})
            if data.get('requestId') != action.get('requestId'):
                raise Conflict('云操作已改变，请刷新任务')
            if action.get('state') != 'waiting_machine' or not action.get('leaseId'):
                raise Conflict('仅能继续检查已创建且等待登录的机器；不会重新开机')
            if tid in self.active:
                return copy.deepcopy(action)
            self._save(tid, 'checking_login', '继续检查同一台机器的登录状态。')
            self._launch(tid, create=False)
            return copy.deepcopy(self.engine._load(tid)['cloudAction'])

    def _launch(self, tid, create):
        if tid in self.active:
            return
        self.active.add(tid)
        if self.start_threads:
            threading.Thread(target=self._run, args=(tid, create), daemon=True).start()

    def _record_reference(self, tid, **changes):
        # An in-flight request may finish after cancellation. Keep its identity
        # for reconciliation without claiming the operation was cancelled remotely.
        with self.engine.lock:
            task = self.engine._load(tid)
            task['cloudAction'].update(changes)
            self.engine._save(task)

    def _run(self, tid, create):
        try:
            action = self.engine.get(tid)['cloudAction']
            recipes = self.catalog.items()
            for item in action['software']:
                if item['id'] not in recipes or self._fingerprint(recipes[item['id']]) != item['recipeFingerprint']:
                    raise Conflict('Confirmed software recipe changed')
            if create:
                q = action['quote']
                lease = self.console.create(action['system'], q['hourlyCNY'], action['requestId'], q.get('sizing'))
                self._record_reference(tid, leaseId=lease['id'], expiresAt=lease['expiresAt'])
                self._save(tid, 'checking_login', '机器创建已返回，正在验证云端状态和实际 SSH 登录。',
                           leaseId=lease['id'], expiresAt=lease['expiresAt'])
            else:
                lease = {'id': action['leaseId']}
            access = None
            for _ in range(20):
                self._save(tid, 'checking_login', '正在检查机器登录；云端开机状态不能代替登录成功。')
                access = self.console.login(lease['id'])
                if access.get('loginState') == 'ssh_authenticated':
                    break
                self.sleep(3)
            else:
                self._save(tid, 'waiting_machine', '机器已创建，SSH 登录尚未就绪，软件还未安装。可继续检查同一台机器。',
                           cloudState=access.get('cloudState'), address=access.get('address'))
                return
            self._save(tid, 'installing', '实际登录验证通过，准备执行已确认的软件安装。',
                       address=access.get('address'), loginState=access['loginState'])
            for sid in action['softwareIds']:
                current_recipe = self.catalog.items().get(sid)
                confirmed = next(item for item in action['software'] if item['id'] == sid)
                if not current_recipe or self._fingerprint(current_recipe) != confirmed['recipeFingerprint']:
                    raise Conflict('Confirmed software recipe changed')
                # Persist an install claim before the call. If a connection is
                # lost before operationId returns, restart cannot replay it.
                self._save(tid, 'installing', '正在提交安装 ' + sid, installingSoftwareId=sid)
                operation = self.console.install(lease['id'], sid)
                current = self.engine.get(tid)['cloudAction']
                operations = current['operations'] + [{'id': operation['id'], 'softwareId': sid, 'state': operation['state']}]
                self._record_reference(tid, operations=operations)
                self._save(tid, 'installing', '正在安装 ' + sid, operations=operations)
                deadline = time.monotonic() + 920
                while time.monotonic() < deadline:
                    operation = self.console.operation(operation['id'])
                    if operation['state'] in ('completed', 'failed', 'interrupted'):
                        break
                    self.sleep(3)
                else:
                    raise TimeoutError('安装等待超时，远程安装可能仍在执行，不能自动重复安装')
                # Only allow vetted evidence fields into public task records.
                evidence = {k: operation.get('evidence', {}).get(k) for k in
                            ('softwareId', 'verified', 'installedVersion', 'sha256', 'selfTestExitCode', 'entryPoint',
                             'versionSource', 'binaryVersionVerified', 'launchVerified', 'recipeFingerprint', 'checkedBinarySha256')
                            if k in operation.get('evidence', {})}
                recipe = self.catalog.get(sid)
                verified = operation['state'] == 'completed' and verify_receipt(recipe, operation)
                if operation['state'] == 'completed' and not verified:
                    operation['state'] = 'failed'
                    evidence['verified'] = False
                operations[-1].update(state=operation['state'], evidence=evidence)
                self._save(tid, 'installing', sid + ' 安装回执：' + operation['state'], operations=operations)
                if not verified:
                    self._save(tid, operation['state'] if operation['state'] in ('failed', 'interrupted') else 'failed',
                               sid + ' 未取得有效的安装验证回执。机器创建不代表安装成功。')
                    return
            names = '、'.join(x['name'] for x in action['software'])
            self._save(tid, 'completed', '机器已创建并通过登录验证。' + (names + ' 安装与验证完成。' if names else '')
                       + '尚未验证用户登录及实际任务执行。')
        except InterruptedError:
            # Cancellation never destroys a lease or claims an in-flight installation stopped.
            with self.engine.lock:
                task = self.engine._load(tid)
                task['status'] = 'cancelled'
                task['cloudAction'].update(state='cancelled', finalAnswer='已停止后续调度；已创建的租约仍按到期时间清理，在途安装需核验。')
                self.engine._save(task)
        except Exception as error:
            # Do not expose cloud/provider/SSH exception bodies or secrets in public tasks.
            with self.engine.lock:
                task = self.engine._load(tid)
                if task['round'] == task['cloudAction']['round'] and task['status'] not in ('cancelled', 'cancelling'):
                    self._save(tid, 'interrupted', '云操作未取得完整回执（' + type(error).__name__
                               + '）。请先核对已有机器与安装状态，不能重复开机或宣称安装成功。')
        finally:
            with self.lock:
                self.active.discard(tid)
