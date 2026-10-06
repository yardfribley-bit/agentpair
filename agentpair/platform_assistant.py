"""Platform tools use local stores, with identity enforced outside model output."""
import copy
import threading
from urllib.parse import urlencode
from .tasks import now


class PlatformAssistant:
    ACTIONS = ('machines', 'devices', 'model_data', 'security', 'sessions', 'session_detail', 'release_machine', 'device_detail', 'model_calls', 'security_detail', 'session_upload_status', 'access_audit')

    def __init__(self, engine, devices, sessions, cloud):
        self.engine, self.devices, self.sessions, self.cloud = engine, devices, sessions, cloud

    def capabilities(self):
        return {'actions': list(self.ACTIONS), 'cloudAvailable': self.cloud is not None,
                'description': '列表之后可按真实编号查询设备详情、模型调用、发现详情、会话详情。创建云机使用 cloud_management，其他写操作不支持，不能假装执行。'}

    def prepare(self, tid, plan, outputs):
        tool = plan.get('tool', {})
        if not isinstance(tool, dict) or tool.get('name') != 'platform_management':
            return False
        task = self.engine.get(tid)
        owner = task.get('owner') or 'admin'
        action = tool.get('action')
        result = {'action': action, 'checkedAt': now(), 'items': [], 'links': [], 'nextSteps': []}
        status = 'completed'
        try:
            if task.get('securityEvidence'):
                raise PermissionError('采集证据不能发起平台管理操作')
            if action not in self.ACTIONS:
                raise ValueError('尚未接入这个平台操作，未执行修改')
            if action == 'release_machine':
                if owner != 'admin': raise PermissionError('释放机器需要管理员账号')
                if self.cloud is None: raise ValueError('云机器工具未配置')
                lease_id = tool.get('leaseId')
                matches = [r for r in self.cloud.list() if r['id'] == lease_id]
                if len(matches) != 1: raise ValueError('请先查询云机器并指定真实机器编号，不会自动选择机器释放')
                row = matches[0]
                result['items'] = [{k:row.get(k) for k in ('id','name','platform','state','expiresAt')}]
                result['links'] = [{'label':'查看目标机器','url':'/cloud-machines'}]
                if row['state'] == 'released':
                    text = '这台机器已经释放，没有再次执行。'
                else:
                    status = 'awaiting_confirmation'
                    result['pendingRelease'] = lease_id
                    text = '将释放机器 '+row['name']+'（'+lease_id+'），机内数据会随实例删除。请回复“确认释放”后执行，当前尚未释放。'
            elif action == 'machines':
                if owner != 'admin': raise PermissionError('云机器管理需要管理员账号')
                if self.cloud is None: raise ValueError('云机器工具未配置')
                rows = self.cloud.list()
                result['items'] = [{k: r.get(k) for k in ('id', 'name', 'platform', 'state', 'expiresAt')} for r in rows]
                active = sum(r.get('state') == 'active' for r in rows)
                text = f'目前有 {active} 台有效租约机器，另有 {len(rows)-active} 条历史机器记录。有效租约不等于已验证登录。'
                previous = task.get('cloudAction', {})
                latest = next((m.get('text', '') for m in reversed(task['messages']) if m['role']=='user'), '')
                if tool.get('leaseId') or (previous and any(word in latest for word in ('创建了吗','开好了吗','创建成功','那台','这台','开机了吗'))):
                    target = next((r for r in rows if r['id']==(tool.get('leaseId') or previous.get('leaseId'))), None)
                    if target and target.get('state')=='active':
                        info=self.cloud.login(target['id'])
                        text=('这次机器已创建，实际登录验证通过。' if info.get('loginState')=='ssh_authenticated' else '这次机器已创建，仍在初始化，暂未验证登录。')
                        result['items']=[r for r in result['items'] if r['id']==target['id']]
                    elif target and target.get('state')=='failed':
                        text='这次创建失败，没有取得有效的机器回执。'+previous.get('finalAnswer','')
                        result['items']=[r for r in result['items'] if r['id']==target['id']]
                    elif target and target.get('state')=='reconcile_required':
                        receipt=self.cloud.creation_status(previous['requestId']) if previous.get('requestId') else {}
                        text=('云端核对已找到这次机器，需要继续验证登录。' if receipt.get('state')=='created' else '这次没有创建成功回执；最新云端核对未找到该机器，系统没有重复创建。')
                        result['items']=[r for r in result['items'] if r['id']==target['id']]
                    elif target:
                        text = ('这次的机器已经释放。' if target['state']=='released' else
                                '这次的机器已取得创建回执，编号 '+target['id']+'。当前操作状态：'+previous.get('finalAnswer', previous.get('state','待核对')))
                        result['items'] = [r for r in result['items'] if r['id']==target['id']]
                    else:
                        text = '这次还没有取得创建成功回执，不能确认机器已经创建。'+{
                            'awaiting_confirmation':'正在等待你确认报价。',
                            'interrupted':'创建请求已经中断，需要核对云端结果；目前没有重新创建，避免重复开机。',
                            'creating':'请求正在处理，请等待结果，不需要重复确认。',
                        }.get(previous.get('state'), '需要进一步核对当前操作记录。')
                        result['items'] = []
                result['links'] = [{'label': '查看云机器', 'url': '/cloud-machines'}]
            elif action=='access_audit':
                if owner!='admin':raise PermissionError('访问审计需要管理员账号')
                from .access_audit import recent_access
                data=recent_access(minutes=tool.get('minutes',10))
                result['items']=data['items'];result['coverage']=data['basis'];result['windowMinutes']=data['windowMinutes']
                text='最近 '+str(data['windowMinutes'])+' 分钟观察到 '+str(len(data['items']))+' 个HTTP访问来源。IP尚未关联账号，不能据此确认具体是谁或仍然在线。'
                result['links']=[]
            elif action=='session_upload_status':
                selected=tool.get('deviceId')
                if selected and owner!='admin' and selected not in {r['id'] for r in self.devices.list(owner)}:
                    raise PermissionError('设备不属于当前账号')
                data=self.sessions.upload_status(owner,selected)
                import datetime
                def stamp(value):
                    return datetime.datetime.fromtimestamp(value,datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S（北京时间）')
                last=data['lastAttempt'];success=data['lastSuccess']
                if last:
                    item={'name':'SessionLens 上报状态','lastAttempt':stamp(last['received']),
                          'lastSuccess':stamp(success['received']) if success else '尚无成功接收记录',
                          'device':last.get('device') or '认证失败，设备无法确认',
                          'uploadStatus':'成功接收' if last['status']==200 else '上报失败（HTTP '+str(last['status'])+'）',
                          'failureReason':{'device_not_authorized':'设备认证失败','invalid_batch':'数据格式不符合要求','payload_too_large':'上报批次过大','accepted':'无'}.get(last['reason'],'需要核对'),
                          'receiptSource':'服务器接口回执' if last['source']=='endpoint' else '历史访问日志'}
                    result['items']=[item]
                    text='最近一次上报尝试：'+item['lastAttempt']+'，'+item['uploadStatus']+'。最近成功接收：'+item['lastSuccess']+'。'
                else:text='尚无可查询的 SessionLens 上报回执，不能确定最近上报时间。'
                result['coverage']=data['historicalCoverage']
                result['links']=[{'label':'查看会话洞察','url':'/session-insights/'}]
            elif action in ('devices','device_detail'):
                rows = self.devices.list(owner)
                if action=='device_detail':
                    selected=tool.get('deviceId')
                    rows=[r for r in rows if r['id']==selected]
                    if not rows:raise ValueError('请选择查询结果中的设备，我会继续查看它的状态。')
                result['items'] = [{k: r.get(k) for k in ('id', 'name', 'online', 'lastSeen')} for r in rows]
                text = f'你的账号有 {len(rows)} 台设备，{sum(bool(r.get("online")) for r in rows)} 台最近在线。在线状态来自设备心跳，不能代替采集完整性。'
                result['links'] = [{'label': '查看我的设备', 'url': '/devices'}]
            elif action in ('model_data','model_calls'):
                rows = self.devices.list(owner)
                selected = tool.get('deviceId')
                if selected and selected not in {r['id'] for r in rows}: raise PermissionError('设备不属于当前账号')
                for row in rows:
                    if selected and row['id'] != selected: continue
                    data = self.devices.llm_data(owner, row['id'], summary=True)
                    if action=='model_calls':
                        result['items'].extend({k:c.get(k) for k in ('id','model','source','sessionId','sessionName','timestamp','bodyBytes')} for c in data.get('calls',[])[:20])
                    else:result['items'].append({'id': row['id'], 'name': row['name'], 'recentCalls': len(data.get('calls', []))})
                text = '已检查你设备的模型数据。下列数量是最近查询窗口中的记录，不是全部历史；原文和请求详情请打开对应设备。'
                result['links'] = [{'label': r['name']+' · 模型数据', 'url': '/model-data?'+urlencode({'device': r['id']})} for r in rows if not selected or r['id']==selected]
            elif action in ('security','security_detail'):
                # This is the same globally visible audit scope as /api/audit/credential-threats.
                data = self.devices.credential_threats.inventory(tool.get('deviceId'))
                rows=data['items']
                if action=='security_detail':
                    rows=[r for r in rows if r['id']==tool.get('findingId')]
                    if not rows:raise ValueError('请选择已有安全事件，我会继续查看对应证据和处理状态。')
                result['items'] = [{k: r.get(k) for k in ('id','deviceId','deviceName','kind','label','networkRecords','contextRecords','firstSeen','lastSeen')} for r in rows]
                if action=='security_detail':
                    for item,row in zip(result['items'],rows):
                        item['evidenceStrength']='请求正文中已发现' if row.get('networkRecords') else '上下文记录中已发现，尚无发送证据'
                        item['verificationState']=row.get('verification',{}).get('state')
                        item['handlingState']=row.get('workflow',{}).get('state')
                        item['requestIds']=list(dict.fromkeys(e.get('requestId') for e in row.get('evidence',[]) if e.get('requestId')))
                    text='已定位这项安全事件。下面列出涉及的设备、请求编号、证据强度和处理状态。'
                else:
                    text = f'当前安全审计中有 {len(result["items"])} 项已有发现。这是现有检测结果查询，没有启动新的模型分析；没有发现也不代表没有风险。'
                result['links'] = [{'label':'查看威胁、证据和处置','url':'/model-security'+('?' + urlencode({'finding':rows[0]['id']}) if action=='security_detail' else '')}]
            else:
                rows = self.sessions.sessions(owner)
                if action == 'sessions':
                    result['items'] = rows[:50]
                    text = f'找到 {len(rows)} 个会话，下面展示前 {min(50,len(rows))} 个。选择一个会话，我会继续查看它的执行过程。'
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
        except FileNotFoundError:
            status,text='unsupported_capability','访问日志暂不可读，不能确认当前访问者。'
        except Exception:
            status, text = 'failed', '平台数据暂时读取失败，本次未执行任何修改。请稍后重试。'
        if status=='completed':
            prompts={
                'machines':['检查机器是否可以使用','查看机器到期时间'],
                'devices':['查看这台设备的模型数据','查看这台设备的安全事件'],
                'device_detail':['查看这台设备的模型数据','查看这台设备的安全事件'],
                'model_data':['展开这台设备最近的模型调用'],
                'model_calls':['查看相关安全事件'],
                'security':['查看这项事件的证据和处理状态'],
                'security_detail':['这项事件有什么危害，应该如何处理？'],
                'sessions':['查看这个会话的执行过程'],
                'session_detail':['查看已有安全事件'],
            }
            result['nextSteps']=prompts.get(action,[]) if len(result['items'])==1 else []
        with self.engine.lock:
            saved = self.engine._load(tid)
            if saved['status'] in ('cancelling', 'cancelled'): return True
            result['summary'] = text
            result['basis']=result.get('coverage') or {'security':'已有审计发现，未启动新分析','security_detail':'已有发现与请求记录，未验证凭据是否仍有效','sessions':'已接收的会话记录','session_detail':'已接收的会话和工具配对记录','devices':'设备注册记录与心跳','device_detail':'设备注册记录与心跳','model_data':'最近模型数据查询窗口','model_calls':'最近模型调用查询窗口','machines':'机器租约记录，登录状态单独核验'}.get(action,'平台真实查询结果')
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

    def handle_message(self, tid, message, *, administrator=False):
        message = self.engine._message(message)
        with self.engine.lock:
            task = self.engine._load(tid)
            result = task.get('platformResult', {})
            if (result.get('round') != task['round'] or not result.get('pendingRelease')
                    or task['status'] != 'awaiting_confirmation'):
                return False
            if message.strip().rstrip('。.!！') not in ('确认释放','确认','同意释放','confirm release'):
                return False
            if not administrator: raise PermissionError('释放机器需要管理员确认')
            lease_id = result['pendingRelease']
            task['messages'].append({'role':'user','text':message,'round':task['round'],'at':now()})
            task['status'] = 'running'
            result.pop('pendingRelease')
            result['summary'] = '正在释放已确认的机器 '+lease_id
            self.engine._save(task)
            round_number = task['round']
        threading.Thread(target=self._release, args=(tid,round_number,lease_id),daemon=True).start()
        return True

    def _release(self, tid, round_number, lease_id):
        try:
            released = self.cloud.manager.release(lease_id)
            success = released['state'] == 'released'
            text = '机器已释放，编号 '+lease_id if success else '释放尚未完成，请核对云机器状态。'
        except Exception:
            success, text = False, '未取得完整释放回执，请先核对云端机器状态，不能直接认定释放成功。'
        with self.engine.lock:
            task = self.engine._load(tid)
            if task['round'] != round_number: return
            task['status'] = 'completed' if success else 'interrupted'
            task['platformResult']['summary'] = text
            task['platformResult']['items'] = [{'id':lease_id,'state':'released' if success else '待核对'}]
            task['messages'].append({'role':'navigator','stage':'review','round':round_number,'at':now(),
                                    'answer':{'summary':text,'finalAnswer':text}})
            task['events'].append({'kind':'platform_tool_result','round':round_number,'at':now(),'text':text})
            self.engine._save(task)
