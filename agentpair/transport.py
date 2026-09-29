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
            timeout=min(timeout,150),check=False)
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

    def _provision(self):
        self.manager.release_expired()
        leases=[x for x in self.manager.records() if x['state']=='active' and x.get('hostId')]
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
            mode=envelope.get('outputs',{}).get('plan',{}).get('answer',{}).get('executionMode','local')
            if mode!='cloud_driver':
                result=run(envelope,self.token)
                result['executionNode']='navigator_local'
                result['resourceDecision']='No paid Driver instance needed for this task'
                return result
            ip,fresh=self._provision()
            self.driver=ip
            if fresh:self._deploy_worker(ip)
            result=super().call(role,envelope,timeout)
            result['executionNode']='ucloud_driver'
            result['resourceDecision']='Cloud Driver provisioned under a one-hour lease'
            result['leaseId']=self.lease_id
            return result
        return super().call(role,envelope,timeout)
