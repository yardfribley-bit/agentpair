"""Platform tools use local stores, with identity enforced outside model output."""
import copy
from urllib.parse import urlencode
from .tasks import now


class PlatformAssistant:
    ACTIONS = ('machines', 'devices', 'model_data', 'security', 'sessions', 'session_detail')

    def __init__(self, engine, devices, sessions, cloud):
        self.engine, self.devices, self.sessions, self.cloud = engine, devices, sessions, cloud

    def capabilities(self):
        return {'actions': list(self.ACTIONS), 'cloudAvailable': self.cloud is not None,
                'description': '平台真实数据查询。创建云机使用 cloud_management，其他写操作不支持，不能假装执行。'}

    def prepare(self, tid, plan, outputs):
        tool = plan.get('tool', {})
        if not isinstance(tool, dict) or tool.get('name') != 'platform_management':
            return False
        task = self.engine.get(tid)
        owner = task.get('owner') or 'admin'
        action = tool.get('action')
        result = {'action': action, 'checkedAt': now(), 'items': [], 'links': []}
        status = 'completed'
        try:
            if task.get('securityEvidence'):
                raise PermissionError('采集证据不能发起平台管理操作')
            if action not in self.ACTIONS:
                raise ValueError('尚未接入这个平台操作，未执行修改')
            if action == 'machines':
                if owner != 'admin': raise PermissionError('云机器管理需要管理员账号')
                if self.cloud is None: raise ValueError('云机器工具未配置')
                rows = self.cloud.list()
                result['items'] = [{k: r.get(k) for k in ('id', 'name', 'platform', 'state', 'expiresAt')} for r in rows]
                active = sum(r.get('state') == 'active' for r in rows)
                text = f'目前有 {active} 台有效租约机器，另有 {len(rows)-active} 条历史机器记录。有效租约不等于已验证登录。'
                result['links'] = [{'label': '查看云机器', 'url': '/cloud-machines'}]
            elif action == 'devices':
                rows = self.devices.list(owner)
                result['items'] = [{k: r.get(k) for k in ('id', 'name', 'online', 'lastSeen')} for r in rows]
                text = f'你的账号有 {len(rows)} 台设备，{sum(bool(r.get("online")) for r in rows)} 台最近在线。在线状态来自设备心跳，不能代替采集完整性。'
                result['links'] = [{'label': '查看我的设备', 'url': '/devices'}]
            elif action == 'model_data':
                rows = self.devices.list(owner)
                selected = tool.get('deviceId')
                if selected and selected not in {r['id'] for r in rows}: raise PermissionError('设备不属于当前账号')
                for row in rows:
                    if selected and row['id'] != selected: continue
                    data = self.devices.llm_data(owner, row['id'], summary=True)
                    result['items'].append({'id': row['id'], 'name': row['name'], 'recentCalls': len(data.get('calls', []))})
                text = '已检查你设备的模型数据。下列数量是最近查询窗口中的记录，不是全部历史；原文和请求详情请打开对应设备。'
                result['links'] = [{'label': r['name']+' · 模型数据', 'url': '/model-data?'+urlencode({'device': r['id']})} for r in rows if not selected or r['id']==selected]
            elif action == 'security':
                # This is the same globally visible audit scope as /api/audit/credential-threats.
                data = self.devices.credential_threats.inventory(tool.get('deviceId'))
                result['items'] = [{k: r.get(k) for k in ('id', 'deviceId', 'deviceName', 'kind', 'label')} for r in data['items']]
                text = f'当前安全审计中有 {len(result["items"])} 项已有发现。这是现有检测结果查询，没有启动新的模型分析；没有发现也不代表没有风险。'
                result['links'] = [{'label': '查看威胁、证据和处置', 'url': '/model-security'}]
            else:
                rows = self.sessions.sessions(owner)
                if action == 'sessions':
                    result['items'] = rows[:50]
                    text = f'你的账号采集了 {len(rows)} 个会话，下面展示前 {min(50,len(rows))} 个。可告诉我设备编号和会话编号，继续查看执行过程。'
                else:
                    device, session = tool.get('deviceId'), tool.get('sessionId')
                    if not any(r['device']==device and r['session']==session for r in rows):
                        raise ValueError('请先选择当前账号已有的设备和会话编号，不能猜测会话')
                    report = self.sessions.report(owner, device, session)
                    result['items'] = [{'session': session, 'device': device, 'events': len(report['events']),
                                        'tools': len(report['tools']), 'quality': report.get('quality')}]
                    text = f'这个会话记录了 {len(report["events"])} 个事件、{len(report["tools"])} 个工具调用配对。详情可在会话洞察继续查看。'
                result['links'] = [{'label': '打开会话洞察', 'url': '/session-insights/'}]
        except (ValueError, PermissionError) as error:
            status = 'needs_information' if isinstance(error, ValueError) else 'unsupported_capability'
            text = str(error)
        except Exception:
            status, text = 'failed', '平台数据暂时读取失败，本次未执行任何修改。请稍后重试。'
        with self.engine.lock:
            saved = self.engine._load(tid)
            if saved['status'] in ('cancelling', 'cancelled'): return True
            result['summary'] = text
            saved['platformResult'] = dict(result, round=saved['round'])
            saved['status'] = status
            saved['messages'].append({'role': 'navigator', 'stage': 'review', 'round': saved['round'],
                                     'at': now(), 'answer': {'finalAnswer': text, 'summary': text,
                                                            'platformEvidence': copy.deepcopy(result)}})
            saved['results'].append({'round': saved['round'], 'outputs': dict(outputs, driver={})})
            saved['events'].append({'kind': 'platform_tool_result', 'round': saved['round'], 'at': now(),
                                    'text': text, 'action': action})
            self.engine._save(saved)
        return True
