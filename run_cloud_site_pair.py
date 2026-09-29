"""Two-node bounded experiment; credentials in memory, cleanup in finally."""
import base64
import datetime
import getpass
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parent
KEY=Path('/private/tmp/agentpair-run.vQ4wLt/id_ed25519')
KNOWN=KEY.parent/'known_hosts'
resume_id=sys.argv[1] if len(sys.argv)>1 else None
runid=resume_id or datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6]
out=ROOT/'runs'/runid
out.mkdir(parents=True,mode=0o700,exist_ok=bool(resume_id))
os.umask(0o077)
pub=getpass.getpass('UCloud API ID (hidden): ')
priv=getpass.getpass('UCloud API secret (hidden): ')
relay=getpass.getpass('Relay token (hidden): ')
hosts=[]; names=[]; done=threading.Event(); lock=threading.RLock()
manifest={'runID':runid,'region':'cn-bj2','projectID':'org-2qgt5t','hosts':hosts,'status':'preflight','cleanup':[],
          'target':'http://102.68.79.149/','deadlineSeconds':900,'modelBudgetMode':'historical_estimate_not_hard_cap'}
retain=bool(resume_id)
expiry=datetime.datetime.strptime(runid[:16],'%Y%m%dT%H%M%SZ').replace(tzinfo=datetime.timezone.utc).timestamp()+3600
if resume_id:
    manifest=json.loads((out/'manifest.json').read_text())
    hosts=manifest['hosts']
    names=['agentpair-'+h['role']+'-'+runid for h in hosts]
    manifest['retainUntilUTC']=datetime.datetime.fromtimestamp(expiry,datetime.timezone.utc).isoformat()


def save(name,value):
    path=out/name
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def api(action,extra=None,check=True):
    p={'Action':action,'PublicKey':pub,'ProjectId':'org-2qgt5t','Region':'cn-bj2'}
    p.update(extra or {})
    def value(v): return str(v).lower() if isinstance(v,bool) else str(v)
    p['Signature']=hashlib.sha1((''.join(k+value(p[k]) for k in sorted(p))+priv).encode()).hexdigest()
    request=urllib.request.Request('https://api.ucloud.cn/',data=json.dumps(p).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=25) as response: d=json.load(response)
    if check and d.get('RetCode')!=0:
        print(action, json.dumps({'RetCode':d.get('RetCode'),'Message':d.get('Message')},ensure_ascii=False),flush=True)
        raise RuntimeError('Cloud API rejected '+action)
    return d


def cleanup():
    with lock:
        try:
            # Reconcile a timed-out create only by this run's unique, exact names.
            actual=api('DescribeUHostInstance',{'Limit':100}).get('UHostSet',[])
            for h in actual:
                if h.get('Name') in names and h.get('UHostId') not in {x['id'] for x in hosts}:
                    hosts.append({'id':h['UHostId'],'role':'reconciled'})
            for h in hosts:
                if h.get('deleted'): continue
                hid=h['id']; print('CLEANUP',hid,flush=True)
                state=api('DescribeUHostInstance',{'UHostIds.0':hid}).get('UHostSet',[])
                if not state: h['deleted']=True; continue
                if state[0].get('State')=='Running':
                    api('StopUHostInstance',{'UHostId':hid,'Zone':'cn-bj2-04'},check=False)
                success=False
                for attempt in range(20):
                    d=api('TerminateUHostInstance',{'UHostId':hid,'Zone':'cn-bj2-04','ReleaseEIP':True,'ReleaseUDisk':True},check=False)
                    if d.get('RetCode')==0:
                        manifest['cleanup'].append({'id':hid,'deleteResult':d}); success=True; break
                    if d.get('RetCode')==299:
                        manifest['cleanup'].append({'id':hid,'permissionDenied':True}); break
                    time.sleep(5)
                remaining=api('DescribeUHostInstance',{'UHostIds.0':hid}).get('UHostSet',[])
                h['deleted']=not remaining
                print('DELETE_VERIFIED',hid,h['deleted'],flush=True)
            # Compare EIPs against the pre-run list, do not delete unrelated resources.
            eips=api('DescribeEIP',{'Limit':100}).get('EIPSet',[])
            new=[e for e in eips if e.get('EIPId') not in baseline_eips]
            manifest['newEIPsAfterCleanup']=[{'id':e.get('EIPId')} for e in new]
        except Exception as error:
            manifest['cleanupErrorType']=type(error).__name__
            print('CLEANUP_ERROR',type(error).__name__,flush=True)
        save('manifest.json',manifest)


def watchdog():
    if not done.wait(max(0,expiry-time.time()) if retain else 900):
        print('DEADLINE_CLEANUP',flush=True); cleanup()


def ssh(ip,command,data=b'',timeout=90):
    args=['ssh','-i',str(KEY),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new',
          '-o','UserKnownHostsFile='+str(KNOWN),'-o','ConnectTimeout=8','pair@'+ip,command]
    result=subprocess.run(args,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    if result.returncode: raise RuntimeError('SSH command failed')
    return result.stdout


def call_worker(ip,envelope,name):
    envelope.update({'relayToken':relay,'target':'http://102.68.79.149/'})
    result=json.loads(ssh(ip,'cd /home/pair/AgentPair && python3 -m agentpair.site_worker',json.dumps(envelope).encode(),timeout=180))
    save(name,result)
    print('COMPLETED',name, 'usage',result.get('usage',result.get('analysis',{}).get('usage')),flush=True)
    return result


baseline_eips=set()
timer=None
try:
    initial=api('DescribeUHostInstance',{'Limit':100}).get('UHostSet',[])
    baseline_eips={e.get('EIPId') for e in api('DescribeEIP',{'Limit':100}).get('EIPSet',[])}
    images=api('DescribeImage',{'Zone':'cn-bj2-04','ImageType':'Base','OsType':'Linux','Limit':100}).get('ImageSet',[])
    image=next(i for i in images if i.get('ImageName')=='Ubuntu 22.04 64位')
    config={'Zone':'cn-bj2-04','ImageId':image['ImageId'],'MachineType':'N','CPU':1,'Memory':1024,
            'ChargeType':'Dynamic','Disks.0.Size':20,'Disks.0.IsBoot':'True','Disks.0.Type':'CLOUD_SSD'}
    price=next(float(p['Price']) for p in api('GetUHostInstancePrice',dict(config,Count=1))['PriceSet'] if p['ChargeType']=='Dynamic')
    eprice=next(float(p['Price']) for p in api('GetEIPPrice',{'OperatorName':'Bgp','Bandwidth':1,'ChargeType':'Dynamic','PayMode':'Bandwidth'})['PriceSet'] if p['ChargeType']=='Dynamic')
    total=2*(price+eprice)
    if total*2>0.99: raise RuntimeError('Budget guard: two-hour margin exceeds server budget')
    manifest['hourlyQuoteCNY']=total
    print('TWO_NODE_HOURLY_QUOTE',total,flush=True)
    key=KEY.with_suffix('.pub').read_text().strip()
    userdata='#cloud-config\nusers:\n  - default\n  - name: pair\n    lock_passwd: true\n    shell: /bin/bash\n    ssh_authorized_keys:\n      - '+key+'\nssh_pwauth: false\n'
    config.update({'LoginMode':'Password','Password':base64.b64encode(('Aa9!'+secrets.token_hex(12)).encode()).decode(),
                   'UserData':base64.b64encode(userdata.encode()).decode(),'MaxCount':1,'MinCount':1,
                   'SecurityGroupId':'firewall-4irrosdv','NetworkInterface.0.EIP.Bandwidth':1,
                   'NetworkInterface.0.EIP.PayMode':'Bandwidth','NetworkInterface.0.EIP.OperatorName':'Bgp'})
    timer=threading.Thread(target=watchdog,daemon=True); timer.start()
    for role in (() if resume_id else ('navigator','driver')):
        name='agentpair-'+role+'-'+runid
        names.append(name)
        d=api('CreateUHostInstance',dict(config,Name=name))
        ids=d.get('UHostIds',[])
        for hid in ids: hosts.append({'id':hid,'role':role})
        save('manifest.json',manifest)
        if len(ids)!=1: raise RuntimeError('Unexpected created resource count')
        print('CREATED',role,ids[0],flush=True)
    for h in hosts:
        for attempt in range(30):
            item=api('DescribeUHostInstance',{'UHostIds.0':h['id']})['UHostSet'][0]
            if resume_id and item.get('Name')!='agentpair-'+h['role']+'-'+runid:
                raise RuntimeError('Resume resource identity mismatch')
            public=[i['IP'] for i in item.get('IPSet',[]) if i.get('IP') and ipaddress.ip_address(i['IP']).is_global]
            if item.get('State')=='Running' and public:
                h['ip']=public[0]; break
            time.sleep(5)
        if not h.get('ip'): raise RuntimeError('Host failed to start with public IP')
        print('RUNNING',h['role'],h['ip'],flush=True)
    bundle=io.BytesIO()
    with tarfile.open(fileobj=bundle,mode='w') as archive:
        for filename in ('__init__.py','site_probe.py','site_worker.py'):
            archive.add(ROOT/'agentpair'/filename,arcname='agentpair/'+filename)
    for h in hosts:
        ready=False
        for attempt in range(18):
            try:
                ssh(h['ip'],'python3 --version',timeout=12); ready=True; break
            except Exception: time.sleep(5)
        if not ready: raise RuntimeError('SSH provisioning unavailable')
        ssh(h['ip'],'mkdir -p /home/pair/AgentPair && tar -xf - -C /home/pair/AgentPair',bundle.getvalue())
        print('DEPLOYED',h['role'],flush=True)
    manifest['status']='deployed'; save('manifest.json',manifest)
    nav=next(h['ip'] for h in hosts if h['role']=='navigator')
    driver=next(h['ip'] for h in hosts if h['role']=='driver')
    plan=call_worker(nav,{'mode':'plan'},'plan.json')
    analysis=call_worker(driver,{'mode':'driver','plan':plan['answer']},'driver.json')
    review=call_worker(nav,{'mode':'review','evidence':analysis['evidence'],'driverReport':analysis['analysis']['answer']},'review.json')
    manifest['status']='analysis_completed'
    manifest['estimatedModelUpperCostCNY']=sum([plan['estimatedUpperCostCNY'],analysis['analysis']['estimatedUpperCostCNY'],review['estimatedUpperCostCNY']])
    print('RESULT_DIRECTORY',str(out),flush=True)
except BaseException as error:
    manifest['status']='failed'; manifest['errorType']=type(error).__name__
    print('RUN_FAILED',type(error).__name__,flush=True)
finally:
    if retain:
        save('manifest.json',manifest)
        print('RETAINED_UNTIL',manifest['retainUntilUTC'],flush=True)
        done.wait(max(0,expiry-time.time()))
    cleanup(); done.set()
    save('manifest.json',manifest)
    print('FINISHED',manifest['status'],str(out),flush=True)

sys.exit(0 if manifest['status']=='analysis_completed' and all(h.get('deleted') for h in hosts) and not manifest.get('newEIPsAfterCleanup') and not manifest.get('cleanupErrorType') else 1)
