"""Navigator local role, Driver over key-authenticated SSH. No arbitrary commands."""
import json
import base64
import io
import ipaddress
from pathlib import Path
import secrets
import subprocess
import tarfile
import time
import copy
import threading
import re
from urllib.parse import urlsplit
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from .methods import method
from .pair_worker import run
from .jev import apply_jev
from .collaboration import message as collaboration_message, validate as validate_collaboration


class NodeBackend:
    def __init__(self, token, driver, key, known_hosts):
        self.token=token; self.driver=driver; self.key=key; self.known_hosts=known_hosts
        self.jev=None

    def estimate(self, envelope):
        # Reserve conservatively BEFORE collection/model invocation. Failed calls
        # remain reserved; history-derived rates are not provider monetary caps.
        if envelope.get('mode')=='driver' and envelope.get('task',{}).get('engineeringMethod')=='parallel':
            return 0.30
        return 0.10

    def observe(self, envelope, role, item):
        callback=getattr(self,'event_callback',None)
        if callback and item.get('kind') in {
                'worker_received','worker_completed','worker_failed','model_started',
                'model_completed','tool_started','tool_result'}:
            callback(envelope['task']['id'],dict(item,
                role=envelope['task'].get('branch',role),stage=envelope['mode'],
                jobId=envelope['task'].get('jobId'),
                phase=envelope['task'].get('collaborationPhase'),
                invocationId=(envelope.get('handoff') or {}).get('id'),
                messageId=(envelope['task'].get('collaborationMessage') or envelope.get('handoff') or {}).get('id')))

    def call(self, role, envelope, timeout):
        if role=='navigator': return apply_jev(self.jev,envelope,run(envelope,self.token,
            emit=lambda item:self.observe(envelope,role,item)))
        if role!='driver': raise ValueError('Invalid role')
        private=dict(envelope,relayToken=self.token)
        response=subprocess.Popen(['ssh','-i',self.key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+self.known_hosts,
            '-o','ConnectTimeout=8','pair@'+self.driver,
            'cd /home/pair/AgentPair && python3 -m agentpair.pair_worker'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        output=[];errors=[]
        def drain_output():output.append(response.stdout.read(4000000))
        def drain_events():
            for line in response.stderr:
                try:
                    item=json.loads(line)
                    if item.get('event')=='tool_result':
                        receipt=item['receipt']
                        self.observe(envelope,role,{'kind':'tool_result',
                            'text':str(receipt.get('tool'))+' · '+('成功' if receipt.get('ok') else '失败'),
                            'receipt':receipt})
                    elif item.get('event')=='worker_event':
                        self.observe(envelope,role,item['data'])
                    else:errors.append(item)
                except (ValueError,KeyError):pass
        readers=[threading.Thread(target=drain_output,daemon=True),threading.Thread(target=drain_events,daemon=True)]
        for reader in readers:reader.start()
        try:
            response.stdin.write(json.dumps(private).encode());response.stdin.close()
            response.wait(timeout=min(timeout,600))
        except BaseException:
            response.kill();response.wait();raise
        finally:
            for reader in readers:reader.join(timeout=3)
        if response.returncode:
            try: reason=errors[-1].get('errorType','WorkerError')
            except (ValueError,TypeError,IndexError): reason='SSH or worker error'
            raise RuntimeError('Driver: '+str(reason)[:80])
        return json.loads(b''.join(output))


class CloudDriverBackend(NodeBackend):
    """Allocate one bounded UCloud Driver lazily, then use the normal SSH worker."""
    def __init__(self, token, manager, key, known_hosts, public_key, firewall_id,
                 worker_root, zone='cn-bj2-04', persistent_driver=None):
        super().__init__(token,None,key,known_hosts)
        self.manager=manager
        self.public_key=public_key
        self.firewall_id=firewall_id
        self.worker_root=Path(worker_root)
        self.zone=zone
        self.lease_id=None
        self.persistent_driver=str(persistent_driver or '').strip()
        if self.persistent_driver:
            address=ipaddress.ip_address(self.persistent_driver)
            if address.version!=4 or not address.is_global:
                raise ValueError('Persistent Driver must have a public IPv4 address')
        self._persistent_status=None
        self._persistent_checked=0.0
        self._persistent_lock=threading.Lock()
        self._persistent_work={}

    def persistent_status(self):
        if not self.persistent_driver:return None
        with self._persistent_lock:
            if time.monotonic()-self._persistent_checked>10 or self._persistent_status is None:
                command=['ssh','-i',self.key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                    '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+self.known_hosts,
                    '-o','ConnectTimeout=4','pair@'+self.persistent_driver,
                    'if test -f /home/pair/AgentPair/agentpair/pair_worker.py; then echo READY; else echo UNPREPARED; fi']
                try:
                    probe=subprocess.run(command,capture_output=True,timeout=7)
                    self._persistent_status={'reachable':probe.returncode==0,
                        'workerReady':probe.returncode==0 and probe.stdout.strip()==b'READY',
                        'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat()}
                except (OSError,subprocess.TimeoutExpired):
                    self._persistent_status={'reachable':False,
                        'workerReady':False,
                        'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat()}
                self._persistent_checked=time.monotonic()
            return dict(self._persistent_status,ip=self.persistent_driver,
                currentTasks=list(self._persistent_work.values()))

    def _driver_ip(self, lease):
        hosts=self.manager.api.call('DescribeUHostInstance',**{'UHostIds.0':lease['hostId']}).get('UHostSet',[])
        if len(hosts)!=1 or hosts[0].get('Name')!=lease['name']:
            raise RuntimeError('Leased Driver identity mismatch')
        for item in hosts[0].get('IPSet',[]):
            ip=item.get('IP')
            if ip and ipaddress.ip_address(ip).is_global and hosts[0].get('State')=='Running':
                return ip
        return None

    def _provision(self, excluded=()):
        persistent_id='persistent:'+self.persistent_driver
        if self.persistent_driver and persistent_id not in excluded:
            self.lease_id=persistent_id
            # The pre-registered machine is paid for separately; never allocate a
            # replacement on connection failure without an explicit new decision.
            return self.persistent_driver,True
        self.manager.release_expired()
        leases=[x for x in self.manager.records() if x['state']=='active' and x.get('hostId') and x['id'] not in excluded
                and x.get('platform','linux')=='linux' and x.get('purpose')!='Windows installer/inventory acceptance'
                and (datetime.datetime.fromisoformat(x['expiresAt'])-self.manager.clock()).total_seconds()>600]
        if self.lease_id:leases.sort(key=lambda x:x['id']!=self.lease_id)
        for lease in leases:
            ip=self._driver_ip(lease)
            if ip:
                self.lease_id=lease['id']
                return ip,True
        images=self.manager.api.call('DescribeImage',Zone=self.zone,ImageType='Base',OsType='Linux',Limit=100).get('ImageSet',[])
        image=next((x for x in images if x.get('ImageName')=='Ubuntu 22.04 64位'),None)
        if not image:raise RuntimeError('Approved Ubuntu image unavailable')
        config={'Zone':self.zone,'ImageId':image['ImageId'],'MachineType':'N','CPU':1,'Memory':1024,
                'ChargeType':'Dynamic','Disks.0.Size':20,'Disks.0.IsBoot':'True',
                'Disks.0.Type':'CLOUD_SSD','SecurityGroupId':self.firewall_id,
                'LoginMode':'Password','Password':base64.b64encode(('Aa9!'+secrets.token_hex(12)).encode()).decode()}
        eip={'Bandwidth':1,'ChargeType':'Dynamic','PayMode':'Bandwidth','OperatorName':'Bgp'}
        lease=self.manager.create_driver(config,eip,'agentpair-driver-'+secrets.token_hex(6),
                                         self.public_key,seconds=3600)
        self.lease_id=lease['id']
        for _ in range(30):
            ip=self._driver_ip(lease)
            if ip:return ip,True
            time.sleep(5)
        raise RuntimeError('Driver did not become reachable')

    def _deploy_worker(self, ip):
        bundle=io.BytesIO()
        with tarfile.open(fileobj=bundle,mode='w') as archive:
            for path in (self.worker_root/'agentpair').rglob('*.py'):
                if '__pycache__' not in path.parts:
                    archive.add(path,arcname=str(path.relative_to(self.worker_root)))
        command='mkdir -p /home/pair/AgentPair && tar -xf - -C /home/pair/AgentPair'
        args=['ssh','-i',self.key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
              '-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+self.known_hosts,
              '-o','ConnectTimeout=8','pair@'+ip,command]
        for _ in range(18):
            result=subprocess.run(args,input=bundle.getvalue(),capture_output=True,timeout=25)
            if result.returncode==0:return
            time.sleep(5)
        raise RuntimeError('Leased Driver SSH bootstrap failed')

    def call(self,role,envelope,timeout):
        if role=='driver':
            if envelope['task'].get('executionProfile') in ('python','node'):
                raise ValueError('旧 Docker 执行环境已停用，请选择原生开发环境；未创建资源')
            browser=envelope['task'].get('executionProfile') in ('native','browser')
            if browser:
                assets=self.worker_root/'browser-assets'
                if not all((assets/n).is_file() for n in ('browserkit','lightpanda')):
                    raise RuntimeError('Verified browser binaries unavailable; no machine allocated')
                text=json.dumps([m.get('text','') for m in envelope.get('history',[]) if m.get('role')=='user'])
                domains=sorted({urlsplit(u).hostname for u in re.findall(r'https?://[^\s"<>\\]+',text) if urlsplit(u).hostname})
                if not domains:raise ValueError('浏览器任务请写明完整 https:// 网站地址；未创建资源')
            selected=envelope['task'].get('engineeringMethod','local')
            count=method(selected)['drivers']
            if not count:
                result=run(envelope,self.token,emit=lambda item:self.observe(envelope,role,item))
                result['executionNode']='navigator_local'
                result['resourceDecision']='No paid Driver instance needed for this task'
                return result
            if count==2:return self._parallel(envelope,timeout)
            ip,fresh=self._provision()
            self.driver=ip
            callback=getattr(self,'event_callback',None)
            if callback:callback(envelope['task']['id'],{'kind':'node_assigned','role':'driver','stage':'driver',
                'address':ip,'nodeKind':'persistent_linux' if self.lease_id=='persistent:'+self.persistent_driver else 'leased_linux',
                'text':'已分配 Linux Driver '+ip})
            if fresh:self._deploy_worker(ip)
            if browser:self._deploy_browser(ip,domains)
            elif envelope['task'].get('executionProfile','none')!='none':
                from .executor import PROFILES
                image=PROFILES[envelope['task']['executionProfile']][0]
                prepared=subprocess.run(['ssh','-i',self.key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                    '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+self.known_hosts,
                    'pair@'+ip,'test -f /etc/agentpair-driver && docker pull '+image],
                    capture_output=True,timeout=180)
                if prepared.returncode: raise RuntimeError('Isolated Driver runtime not ready; no code executed')
            persistent=self.lease_id=='persistent:'+self.persistent_driver
            task_id=envelope['task']['id']
            if persistent:
                with self._persistent_lock:self._persistent_work[task_id]={'id':task_id,'title':envelope['task']['title']}
            try:result=super().call(role,envelope,timeout)
            finally:
                if persistent:
                    with self._persistent_lock:self._persistent_work.pop(task_id,None)
            result['executionNode']='persistent_linux_driver' if persistent else 'ucloud_driver'
            result['resourceDecision']='Reused registered Linux Driver' if persistent else 'Cloud Driver provisioned under a one-hour lease'
            if not persistent:result['leaseId']=self.lease_id
            return result
        return super().call(role,envelope,timeout)

    def _deploy_browser(self,ip,domains):
        args=['ssh','-i',self.key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
              '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+self.known_hosts,'pair@'+ip]
        bundle=io.BytesIO()
        with tarfile.open(fileobj=bundle,mode='w') as archive:
            for name in ('browserkit','lightpanda'):
                archive.add(self.worker_root/'browser-assets'/name,arcname=name)
        subprocess.run(args+['mkdir -p /home/pair/AgentPair/browser-assets && tar -xf - -C /home/pair/AgentPair/browser-assets'],input=bundle.getvalue(),capture_output=True,check=True,timeout=150)
        subprocess.run(args+['cd /home/pair/AgentPair && python3 -m agentpair.browser_setup'],input=json.dumps(domains).encode(),capture_output=True,check=True,timeout=90)

    def _parallel(self,envelope,timeout):
        started=time.monotonic();nodes=[];excluded=[]
        def event(branch,text,kind='branch_progress',recipient=None,phase=None,intelligence=None,packet=None,job_id=None):
            callback=getattr(self,'event_callback',None)
            if callback:callback(envelope['task']['id'],{'kind':kind,'role':branch,'stage':'driver',
                'to':recipient,'phase':phase,'text':str(text)[:1200],'jobId':job_id,
                **({'intelligence':intelligence} if intelligence is not None else {}),
                **({'message':packet} if packet is not None else {})})
        routes=envelope.get('outputs',{}).get('plan',{}).get('answer',{}).get('approaches')
        if not isinstance(routes,list) or len(routes)!=2 or not all(isinstance(x,str) and x.strip() for x in routes) or routes[0].strip()==routes[1].strip():
            raise ValueError('并行探索需要 Navigator 给出两个不同的方案，再启动机器')
        for index in range(2):
            branch='Driver '+('A' if index==0 else 'B')
            event(branch,'准备独立节点，优先复用本计费周期的租约')
            ip,fresh=self._provision(excluded);excluded.append(self.lease_id)
            event(branch,'已分配 Linux Driver '+ip,'node_assigned',phase='explore',
                  intelligence={'address':ip,'nodeKind':'persistent_linux' if self.lease_id=='persistent:'+self.persistent_driver else 'leased_linux'})
            if fresh:self._deploy_worker(ip)
            nodes.append((branch,ip,self.lease_id,routes[index]))
        def execute(node,phase,peer=None,feedback=None,own=None):
            branch,ip,lease,route=node
            task=copy.deepcopy(envelope);task['task']['approach']=route;task['task']['branch']=branch
            task['task']['jobId']=secrets.token_hex(16)
            task['task']['collaborationPhase']=phase
            packet=None
            if peer is not None:
                if isinstance(peer,tuple):peer,packet=peer
                task['task']['peerResult']=peer
            if feedback is not None:task['task']['peerFeedback']=feedback
            if own is not None:task['task']['ownResult']=own
            if packet is not None:
                task['task']['collaborationMessage']=validate_collaboration(packet,
                    task_id=envelope['task']['id'],recipient=branch)
            event(branch,{'explore':'开始独立探索：','review_peer':'开始复核另一方案：','revise':'根据交叉意见修订：'}[phase]+route,'job_started',phase=phase,job_id=task['task']['jobId'])
            try:
                worker=NodeBackend(self.token,ip,self.key,self.known_hosts)
                worker.event_callback=getattr(self,'event_callback',None)
                result=worker.call('driver',task,max(1,timeout-(time.monotonic()-started)))
                if packet is not None:
                    event(branch,'已处理交接消息并返回结果','branch_handoff_processed',phase=phase,
                          recipient=packet['from'],packet=packet)
                event(branch,result['answer'].get('summary','已返回结果'),'job_completed',phase=phase,job_id=task['task']['jobId'])
                return branch,{'status':'completed','result':result,'leaseId':lease,'approach':route}
            except Exception as error:
                event(branch,'分支失败：'+type(error).__name__,'job_failed',phase=phase,job_id=task['task']['jobId'])
                return branch,{'status':'failed','errorType':type(error).__name__,'leaseId':lease,'approach':route}
        def phase_run(phase,inputs):
            completed={}
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(execute,node,phase,*inputs.get(node[0],(None,None,None))) for node in nodes]
                for future in as_completed(futures):
                    branch,result=future.result();completed[branch]=result
            return completed
        initial=phase_run('explore',{})
        names=('Driver A','Driver B')
        if all(initial[name]['status']=='completed' for name in names):
            reviews={}
            for branch,other in ((names[0],names[1]),(names[1],names[0])):
                peer=initial[other]['result']['answer']
                intelligence={'from':other,'to':branch,'summary':str(peer.get('summary',''))[:500],
                              'findings':peer.get('findings',[])[:4] if isinstance(peer.get('findings'),list) else []}
                packet=collaboration_message(other,branch,'finding',intelligence['summary'],
                    task_id=envelope['task']['id'],round_number=envelope['task'].get('round',1),
                    phase='review_peer',evidence_refs=[ref for finding in intelligence['findings']
                    if isinstance(finding,dict) for ref in finding.get('evidenceRefs',[])],content=peer)
                event(other,'已共享第一轮情报，交给 '+branch+' 交叉复核','branch_handoff',recipient=branch,
                      phase='review_peer',intelligence=intelligence,packet=packet)
                reviews[branch]=((peer,packet),None,initial[branch]['result']['answer'])
            cross=phase_run('review_peer',reviews)
        else:cross={}
        if cross and all(cross[name]['status']=='completed' for name in names):
            revisions={}
            for branch,other in ((names[0],names[1]),(names[1],names[0])):
                feedback=cross[other]['result']['answer']
                peer=initial[other]['result']['answer']
                intelligence={'from':other,'to':branch,'summary':str(feedback.get('summary',''))[:500],
                    'questions':feedback.get('questions',[])[:4] if isinstance(feedback.get('questions'),list) else []}
                packet=collaboration_message(other,branch,'peer_review',intelligence['summary'],
                    task_id=envelope['task']['id'],round_number=envelope['task'].get('round',1),phase='revise',content=feedback)
                event(other,'已共享交叉复核意见，交给 '+branch+' 修订','branch_handoff',recipient=branch,
                      phase='revise',intelligence=intelligence,packet=packet)
                revisions[branch]=((peer,packet),feedback,initial[branch]['result']['answer'])
            revised=phase_run('revise',revisions)
        else:revised={}
        results={}
        for name in names:
            other=names[1] if name==names[0] else names[0]
            first=initial[name];review=cross.get(other);review_of_peer=cross.get(name);revision=revised.get(name)
            final=revision if revision and revision['status']=='completed' else first
            results[name]=dict(final,initial=first,peerReview=review,reviewOfPeer=review_of_peer,revision=revision,
                               status='completed' if review and review['status']=='completed'
                               and review_of_peer and review_of_peer['status']=='completed'
                               and revision and revision['status']=='completed' else 'failed')
        usage={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0};cost=0
        for branch in results.values():
            for phase in ('initial','peerReview','revision'):
                output=(branch.get(phase) or {}).get('result',{})
                cost+=output.get('estimatedUpperCostCNY',0)
                for key in usage:usage[key]+=output.get('usage',{}).get(key,0)
        evidence=next((results[b]['result'].get('evidence') for b in sorted(results) if results[b].get('result',{}).get('evidence')),None)
        return {'answer':{'summary':'两条路线已探索、交叉复核并修订；交由 Navigator 比较验收。' if revised else '并行探索未完成交叉复核或修订，交由 Navigator 标注缺口。','branches':results},'evidence':evidence,
                'usage':usage,'model':'deepseek-v3.2','estimatedUpperCostCNY':cost,
                'budgetMode':'historical_estimate_not_hard_cap','executionNode':'ucloud_parallel',
                'resourceDecision':'2 台独立 Driver；任务结束保留租约供复用，到期回收'}
