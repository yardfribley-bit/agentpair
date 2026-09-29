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
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from .methods import method
from .pair_worker import run
from .jev import apply_jev


class NodeBackend:
    def __init__(self, token, driver, key, known_hosts):
        self.token=token; self.driver=driver; self.key=key; self.known_hosts=known_hosts
        self.jev=None

    def estimate(self, envelope):
        # Reserve conservatively BEFORE collection/model invocation. Failed calls
        # remain reserved; history-derived rates are not provider monetary caps.
        return 0.10

    def call(self, role, envelope, timeout):
        if role=='navigator': return apply_jev(self.jev,envelope,run(envelope,self.token))
        if role!='driver': raise ValueError('Invalid role')
        private=dict(envelope,relayToken=self.token)
        response=subprocess.run(['ssh','-i',self.key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+self.known_hosts,
            '-o','ConnectTimeout=8','pair@'+self.driver,
            'cd /home/pair/AgentPair && python3 -m agentpair.pair_worker'],
            input=json.dumps(private).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            timeout=min(timeout,600),check=False)
        if response.returncode:
            try: reason=json.loads(response.stderr).get('errorType','WorkerError')
            except (ValueError,TypeError): reason='SSH or worker error'
            raise RuntimeError('Driver: '+str(reason)[:80])
        return json.loads(response.stdout)


class CloudDriverBackend(NodeBackend):
    """Allocate one bounded UCloud Driver lazily, then use the normal SSH worker."""
    def __init__(self, token, manager, key, known_hosts, public_key, firewall_id,
                 worker_root, zone='cn-bj2-04'):
        super().__init__(token,None,key,known_hosts)
        self.manager=manager
        self.public_key=public_key
        self.firewall_id=firewall_id
        self.worker_root=Path(worker_root)
        self.zone=zone
        self.lease_id=None

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
        self.manager.release_expired()
        leases=[x for x in self.manager.records() if x['state']=='active' and x.get('hostId') and x['id'] not in excluded
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
            selected=envelope['task'].get('engineeringMethod','local')
            count=method(selected)['drivers']
            if not count:
                result=run(envelope,self.token)
                result['executionNode']='navigator_local'
                result['resourceDecision']='No paid Driver instance needed for this task'
                return result
            if count==2:return self._parallel(envelope,timeout)
            ip,fresh=self._provision()
            self.driver=ip
            if fresh:self._deploy_worker(ip)
            if envelope['task'].get('executionProfile','none')!='none':
                from .executor import PROFILES
                image=PROFILES[envelope['task']['executionProfile']][0]
                prepared=subprocess.run(['ssh','-i',self.key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                    '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+self.known_hosts,
                    'pair@'+ip,'test -f /etc/agentpair-driver && docker pull '+image],
                    capture_output=True,timeout=180)
                if prepared.returncode: raise RuntimeError('Isolated Driver runtime not ready; no code executed')
            result=super().call(role,envelope,timeout)
            result['executionNode']='ucloud_driver'
            result['resourceDecision']='Cloud Driver provisioned under a one-hour lease'
            result['leaseId']=self.lease_id
            return result
        return super().call(role,envelope,timeout)

    def _parallel(self,envelope,timeout):
        started=time.monotonic();nodes=[];excluded=[]
        def event(branch,text,kind='branch_progress'):
            callback=getattr(self,'event_callback',None)
            if callback:callback(envelope['task']['id'],{'kind':kind,'role':branch,'stage':'driver','text':text})
        routes=envelope.get('outputs',{}).get('plan',{}).get('answer',{}).get('approaches')
        if not isinstance(routes,list) or len(routes)!=2 or not all(isinstance(x,str) and x.strip() for x in routes) or routes[0].strip()==routes[1].strip():
            raise ValueError('并行探索需要 Navigator 给出两个不同的方案，再启动机器')
        for index in range(2):
            branch='Driver '+('A' if index==0 else 'B')
            event(branch,'准备独立节点，优先复用本计费周期的租约')
            ip,fresh=self._provision(excluded);excluded.append(self.lease_id)
            if fresh:self._deploy_worker(ip)
            nodes.append((branch,ip,self.lease_id,routes[index]))
        def execute(node):
            branch,ip,lease,route=node
            task=copy.deepcopy(envelope);task['task']['approach']=route;task['task']['branch']=branch
            event(branch,'开始执行：'+route)
            try:
                result=NodeBackend(self.token,ip,self.key,self.known_hosts).call('driver',task,max(1,timeout-(time.monotonic()-started)))
                event(branch,result['answer'].get('summary','已返回结果'),'branch_completed')
                return branch,{'status':'completed','result':result,'leaseId':lease,'approach':route}
            except Exception as error:
                event(branch,'分支失败：'+type(error).__name__,'branch_failed')
                return branch,{'status':'failed','errorType':type(error).__name__,'leaseId':lease,'approach':route}
        results={}
        with ThreadPoolExecutor(max_workers=2) as pool:
            for future in as_completed([pool.submit(execute,n) for n in nodes]):
                branch,result=future.result();results[branch]=result
        usage={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0};cost=0
        for branch in results.values():
            output=branch.get('result',{});cost+=output.get('estimatedUpperCostCNY',0)
            for key in usage:usage[key]+=output.get('usage',{}).get(key,0)
        evidence=next((results[b]['result'].get('evidence') for b in sorted(results) if results[b].get('result',{}).get('evidence')),None)
        return {'answer':{'summary':'两条独立路线已返回，由 Navigator 比较、选择或综合。','branches':results},'evidence':evidence,
                'usage':usage,'model':'deepseek-v3.2','estimatedUpperCostCNY':cost,
                'budgetMode':'historical_estimate_not_hard_cap','executionNode':'ucloud_parallel',
                'resourceDecision':'2 台独立 Driver；任务结束保留租约供复用，到期回收'}
