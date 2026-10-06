"""Navigator-owned, auditable leases for disposable Driver machines."""
import datetime
import json
import os
from pathlib import Path
import secrets
import time


def utcnow(): return datetime.datetime.now(datetime.timezone.utc)


class ResourceManager:
    def __init__(self, client, directory, *, max_hosts=4, max_seconds=3600,
                 max_hourly_cny=1.0, clock=utcnow):
        self.api=client
        self.directory=Path(directory)
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        os.chmod(self.directory,0o700)
        self.max_hosts=max_hosts
        self.max_seconds=max_seconds
        self.max_hourly_cny=max_hourly_cny
        self.clock=clock

    def records(self):
        return [json.loads(p.read_text()) for p in self.directory.glob('*.json')]

    def _save(self, lease):
        p=self.directory/(lease['id']+'.json')
        tmp=p.with_suffix('.tmp')
        tmp.write_text(json.dumps(lease,ensure_ascii=False,indent=2))
        os.chmod(tmp,0o600)
        # POSIX root preserves the lease directory's owner for the reaper.
        # Windows has no Unix effective uid; it keeps the file's inherited ACL.
        geteuid=getattr(os,'geteuid',None)
        if geteuid is not None and geteuid()==0:
            owner=self.directory.stat()
            os.chown(tmp,owner.st_uid,owner.st_gid)
        tmp.replace(p)

    def quote(self, config, eip):
        host=self.api.call('GetUHostInstancePrice',**dict(config,Count=1))
        net=self.api.call('GetEIPPrice',**eip)
        def dynamic(data):
            for item in data['PriceSet']:
                if item['ChargeType']=='Dynamic': return float(item['Price'])
            raise RuntimeError('No dynamic price returned')
        hourly=dynamic(host)+dynamic(net)
        if hourly<=0 or hourly>self.max_hourly_cny:
            raise ValueError('Hourly quote exceeds configured ceiling')
        return {'hourlyCNY':hourly,'hostCNY':dynamic(host),'eipCNY':dynamic(net),
                'quotedAt':self.clock().isoformat()}

    def create_driver(self, config, eip, name, ssh_public_key, *, seconds=3600, platform=None, purpose=None, request_id=None):
        if not 60<=seconds<=self.max_seconds: raise ValueError('Invalid lease duration')
        if not name.startswith('agentpair-driver-') or len(name)>80:
            raise ValueError('Driver name must be scoped to AgentPair')
        active=[r for r in self.records() if r['state'] not in ('released','failed')]
        if len(active)>=self.max_hosts: raise ValueError('Driver host quota reached')
        # Pricing API accepts machine specifications, not creation-only credentials.
        price_config={k:v for k,v in config.items()
                      if k not in ('LoginMode','Password','SecurityGroupId')}
        price=self.quote(price_config,eip)
        lease={'id':secrets.token_hex(12),'name':name,'state':'creating',
               'createdAt':self.clock().isoformat(),
               'expiresAt':(self.clock()+datetime.timedelta(seconds=seconds)).isoformat(),
               'price':price,'hostId':None,'region':self.api.region,
               'projectId':self.api.project_id,'zone':config['Zone'],
               'platform':platform,'purpose':purpose,'requestId':request_id}
        self._save(lease)
        # Narrow config is assembled by the caller; only authorized key is put in cloud-init.
        import base64
        userdata='#cloud-config\nusers:\n  - default\n  - name: pair\n    lock_passwd: true\n    groups: sudo\n    sudo: ALL=(ALL) NOPASSWD:ALL\n    ssh_authorized_keys:\n      - '+ssh_public_key.strip()+'\nssh_pwauth: false\npackages: [git, python3, ca-certificates]\nwrite_files:\n  - path: /etc/agentpair-driver\n    permissions: "0444"\n    content: isolated-driver\n'
        request={**config,'Name':name,'MaxCount':1,'MinCount':1,
                 'LoginMode':'Password',
                 'Password':base64.b64encode(('Ap9!'+secrets.token_hex(20)).encode()).decode(),
                 'SecurityGroupId':config['SecurityGroupId'],
                 'UserData':base64.b64encode(userdata.encode()).decode(),
                 'NetworkInterface.0.EIP.Bandwidth':eip['Bandwidth'],
                 'NetworkInterface.0.EIP.PayMode':eip['PayMode'],
                 'NetworkInterface.0.EIP.OperatorName':eip['OperatorName']}
        try:
            response=self.api.call('CreateUHostInstance',**request)
            ids=response.get('UHostIds',[])
            if len(ids)!=1: raise RuntimeError('Unexpected host count; reconcile by name')
            lease['hostId']=ids[0]
            lease['state']='active'
            self._save(lease)
            return lease
        except Exception as error:
            from .ucloud import CloudRejected
            lease['state']='failed' if isinstance(error,CloudRejected) else 'reconcile_required'
            lease['errorCode']=getattr(error,'code',None)
            self._save(lease)
            raise

    def release(self, lease_id):
        p=self.directory/(lease_id+'.json')
        lease=json.loads(p.read_text())
        if lease['state']=='released': return lease
        hid=lease.get('hostId')
        if not hid:
            matches=[x for x in self.api.call('DescribeUHostInstance',Limit=100).get('UHostSet',[])
                     if x.get('Name')==lease['name']]
            if len(matches)>1: raise RuntimeError('Ambiguous lease name')
            if matches: hid=matches[0]['UHostId']; lease['hostId']=hid; self._save(lease)
        if hid:
            hosts=self.api.call('DescribeUHostInstance',**{'UHostIds.0':hid}).get('UHostSet',[])
            if hosts:
                if len(hosts)!=1 or hosts[0].get('Name')!=lease['name']:
                    raise RuntimeError('Host identity mismatch')
                if hosts[0].get('State')=='Running':
                    self.api.call('StopUHostInstance',UHostId=hid,Zone=lease['zone'])
                for _ in range(20):
                    if not self.api.call('DescribeUHostInstance',**{'UHostIds.0':hid}).get('UHostSet',[]):
                        break
                    try:
                        self.api.call('TerminateUHostInstance',UHostId=hid,Zone=lease['zone'],
                                      ReleaseEIP=True,ReleaseUDisk=True)
                    except RuntimeError:
                        pass
                    time.sleep(3)
                else: raise RuntimeError('Host still exists after termination')
        firewall=lease.get('dedicatedFirewallId')
        if firewall:
            rows=self.api.call('DescribeFirewall').get('DataSet',[])
            match=next((r for r in rows if r.get('FWId')==firewall),None)
            if match and match.get('ResourceCount')==0:
                self.api.call('DeleteFirewall',FWId=firewall)
            elif match:
                raise RuntimeError('Dedicated firewall still bound; refusing deletion')
        lease['state']='released';lease['releasedAt']=self.clock().isoformat()
        self._save(lease)
        return lease

    def release_expired(self):
        return [self.release(r['id']) for r in self.records()
                if r['state']!='released' and datetime.datetime.fromisoformat(r['expiresAt'])<=self.clock()]
