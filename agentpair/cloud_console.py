"""Admin cloud-machine controls; provider credentials remain on Navigator."""
import base64
import datetime
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import secrets
import shlex
import subprocess
import threading

from .windows_access import bootstrap_script


class CloudConsole:
    def __init__(self, manager, cloud, key, known_hosts, reaper_ready=None):
        self.manager=manager
        self.cloud=cloud
        self.key=key
        self.known_hosts=known_hosts
        self.operation_dir=manager.directory.parent/'cloud-operations'
        self.operation_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
        self._running_operations=set()
        self._operation_lock=threading.Lock()
        self._creation_lock=threading.Lock()
        self.reaper_ready=reaper_ready or self._reaper_ready

    @staticmethod
    def _reaper_ready():
        try:return subprocess.run(['systemctl','is-active','--quiet','agentpair-reaper.timer'],timeout=5).returncode==0
        except (OSError,subprocess.TimeoutExpired):return False

    def _plan(self, system, sizing=None):
        zone=self.cloud.get('zone','cn-bj2-04')
        if system=='Windows':
            # This official Windows Server 2022 image and O/2C/4G profile
            # were used in the prior acceptance run.
            config={'Zone':'cn-bj2-03','ImageId':'uimage-1is1syh4tgxx','MachineType':'O',
                    'CPU':2,'Memory':4096,'ChargeType':'Dynamic',
                    'Disks.0.Size':40,'Disks.0.IsBoot':True,'Disks.0.Type':'CLOUD_RSSD',
                    'Disks.1.Size':20,'Disks.1.IsBoot':False,'Disks.1.Type':'CLOUD_RSSD'}
        elif system=='Linux':
            images=self.manager.api.call('DescribeImage',Zone=zone,ImageType='Base',OsType='Linux',Limit=100).get('ImageSet',[])
            image=next((x for x in images if x.get('ImageName')=='Ubuntu 22.04 64位'),None)
            if not image:raise RuntimeError('Approved Ubuntu 22.04 image unavailable')
            config={'Zone':zone,'ImageId':image['ImageId'],'MachineType':'N',
                    'CPU':1,'Memory':1024,'ChargeType':'Dynamic',
                    'Disks.0.Size':20,'Disks.0.IsBoot':True,'Disks.0.Type':'CLOUD_SSD',
                    'SecurityGroupId':self.cloud['firewallId']}
        else:raise ValueError('Select Windows or Linux')
        if sizing is not None:
            if not isinstance(sizing,dict) or set(sizing)-{'cpu','memoryGB','systemDiskGB','dataDiskGB'}:
                raise ValueError('Invalid machine sizing')
            limits={'cpu':(1,2,4,8,16),'memoryGB':(1,2,4,8,16,32,64)}
            for field,key in [('cpu','CPU'),('memoryGB','Memory')]:
                value=sizing.get(field,config[key] if field=='cpu' else config[key]//1024)
                if type(value) is not int or value not in limits[field]:raise ValueError('Invalid '+field)
                config[key]=value if field=='cpu' else value*1024
            for field,index,minimum in [('systemDiskGB',0,40 if system=='Windows' else 20),('dataDiskGB',1,0)]:
                value=sizing.get(field,config.get('Disks.%s.Size'%index,0))
                if type(value) is not int or value<minimum or value>1000:raise ValueError('Invalid '+field)
                prefix='Disks.%s.'%index
                if value:
                    config.update({prefix+'Size':value,prefix+'IsBoot':index==0,prefix+'Type':config['Disks.0.Type']})
                else:
                    for key in list(config):
                        if key.startswith(prefix):del config[key]
        eip={'Bandwidth':1,'ChargeType':'Dynamic','PayMode':'Bandwidth','OperatorName':'Bgp'}
        return config,eip

    def quote(self, system, sizing=None):
        config,eip=self._plan(system,sizing)
        price=self.manager.quote({k:v for k,v in config.items() if k!='SecurityGroupId'},eip)
        return {'system':system,'cpu':config['CPU'],'memoryMB':config['Memory'],
                'sizing':{'cpu':config['CPU'],'memoryGB':config['Memory']//1024,'systemDiskGB':config['Disks.0.Size'],'dataDiskGB':config.get('Disks.1.Size',0)},
                'zone':config['Zone'],'hourlyCNY':price['hourlyCNY'],
                'hostCNY':price['hostCNY'],'eipCNY':price['eipCNY'],
                'quotedAt':price['quotedAt'],'durationMinutes':60}

    def create(self, system, max_hourly_cny, request_id, sizing=None):
        import re
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}',request_id):
            raise ValueError('Creation request ID required')
        requests=self.manager.directory.parent/'cloud-create-requests'
        requests.mkdir(mode=0o700,parents=True,exist_ok=True)
        path=requests/(request_id+'.json')
        with self._creation_lock:
            if path.exists():
                existing=json.loads(path.read_text())
                if existing.get('system')!=system or existing.get('sizing')!=sizing:raise ValueError('Creation request configuration changed')
                if existing.get('leaseId'):return self._public_lease(self._lease(existing['leaseId']))
                raise RuntimeError('Creation already attempted; reconcile its result before retrying')
            if not self.reaper_ready():raise RuntimeError('Expiry cleanup service is not running')
            if self.manager.max_seconds<3600:raise ValueError('Configured lease duration is below one hour')
            record={'system':system,'sizing':sizing,'state':'pending'}
            path.write_text(json.dumps(record));path.chmod(0o600)
            try:
                result=self._create(system,max_hourly_cny,sizing)
                record.update(state='created',leaseId=result['id'])
                path.write_text(json.dumps(record))
                return result
            except Exception:
                record['state']='reconcile_required';path.write_text(json.dumps(record))
                raise

    def _create(self, system, max_hourly_cny, sizing=None):
        if type(max_hourly_cny) not in (float,int) or not math.isfinite(max_hourly_cny) or max_hourly_cny<=0:
            raise ValueError('Confirm a positive hourly price limit')
        config,eip=self._plan(system,sizing)
        price=self.manager.quote({k:v for k,v in config.items() if k!='SecurityGroupId'},eip)
        if price['hourlyCNY']>max_hourly_cny:
            raise ValueError('Current quote exceeds the confirmed price')
        if system=='Linux':
            lease=self.manager.create_driver(config,eip,'agentpair-driver-'+secrets.token_hex(6),
                                             self.cloud['sshPublicKey'],seconds=3600)
            lease.update(platform='linux',purpose='Admin machine console')
            self.manager._save(lease)
            return self._public_lease(lease)
        active=[r for r in self.manager.records() if r['state'] not in ('released','failed')]
        if len(active)>=self.manager.max_hosts:raise ValueError('Cloud host quota reached')
        now=self.manager.clock();lease={'id':secrets.token_hex(12),'name':'agentpair-driver-windows-'+secrets.token_hex(4),
            'state':'creating','createdAt':now.isoformat(),'expiresAt':(now+datetime.timedelta(hours=1)).isoformat(),
            'price':price,'hostId':None,'region':self.manager.api.region,'projectId':self.manager.api.project_id,
            'zone':config['Zone'],'platform':'windows','purpose':'Admin machine console'}
        self.manager._save(lease)
        firewall=self.manager.api.call('CreateFirewall',Name='agentpair-console-'+lease['id'][:8],
            **{'Rule.0':'TCP|3389|0.0.0.0/0|ACCEPT|HIGH|PublicRDP',
               'Rule.1':'TCP|22|'+self.cloud.get('navigatorSource','50.118.187.180/32')+'|ACCEPT|HIGH|NavigatorSSH'})['FWId']
        lease['dedicatedFirewallId']=firewall;self.manager._save(lease)
        password='W9!'+secrets.token_hex(10)
        secret_path=self.manager.directory.parent/('windows-trial-'+lease['id']+'.private.json')
        secret_path.write_text(json.dumps({'username':'Administrator','password':password}))
        secret_path.chmod(0o600)
        request={**config,'Name':lease['name'],'MinCount':1,'MaxCount':1,
            'SecurityGroupId':firewall,'LoginMode':'Password',
            'Password':base64.b64encode(password.encode()).decode(),
            'UserData':base64.b64encode(('#ps1_sysnative\n'+bootstrap_script(self.cloud['sshPublicKey'])).encode()).decode(),
            'NetworkInterface.0.EIP.Bandwidth':1,
            'NetworkInterface.0.EIP.PayMode':'Bandwidth',
            'NetworkInterface.0.EIP.OperatorName':'Bgp'}
        try:
            result=self.manager.api.call('CreateUHostInstance',**request)
            ids=result.get('UHostIds',[])
            if len(ids)!=1:raise RuntimeError('Creation result ambiguous; reconcile exact lease')
            lease.update(hostId=ids[0],state='active')
            self.manager._save(lease)
        except Exception:
            lease['state']='reconcile_required';self.manager._save(lease)
            raise
        return self._public_lease(lease)

    @staticmethod
    def _public_lease(lease):
        return {key:lease.get(key) for key in ('id','name','hostId','platform','state','createdAt','expiresAt','purpose','price')}

    def list(self):
        return [self._public_lease(r) for r in self.manager.records()
                if r.get('purpose')=='Admin machine console']

    def inspect(self, lease_id):
        lease=self._lease(lease_id)
        if not lease['hostId']:return dict(self._public_lease(lease),cloudState='Unknown',address=None)
        rows=self.manager.api.call('DescribeUHostInstance',**{'UHostIds.0':lease['hostId']}).get('UHostSet',[])
        if len(rows)!=1 or rows[0].get('Name')!=lease['name']:
            return dict(self._public_lease(lease),cloudState='NotFound',address=None)
        ips=[x['IP'] for x in rows[0].get('IPSet',[]) if x.get('Type') in ('BGP','Bgp')]
        address=ips[0] if len(ips)==1 and ipaddress.ip_address(ips[0]).is_global else None
        return dict(self._public_lease(lease),cloudState=rows[0].get('State'),address=address)

    def login(self, lease_id):
        info=self.inspect(lease_id)
        if info['cloudState']!='Running' or not info['address']:
            return dict(info,loginState='machine_not_running')
        lease=self._lease(lease_id)
        user='Administrator' if lease['platform']=='windows' else 'pair'
        try:
            probe=subprocess.run(self._ssh_args(user,info['address'])+['whoami'],capture_output=True,timeout=12)
        except (OSError,subprocess.TimeoutExpired):
            return dict(info,loginState='ssh_pending',loginUser=user)
        identity=probe.stdout.decode(errors='replace').strip().lower()
        verified=identity.endswith('\\administrator') if user=='Administrator' else identity=='pair'
        state='ssh_authenticated' if probe.returncode==0 and verified else 'ssh_pending'
        return dict(info,loginState=state,loginUser=user)

    def connection_info(self,lease_id):
        info=self.inspect(lease_id)
        if info['platform']=='windows':
            path=self.manager.directory.parent/('windows-trial-'+lease_id+'.private.json')
            if not path.is_file():raise RuntimeError('Windows login credential unavailable')
            credential=json.loads(path.read_text())
            return {'address':info['address'],'username':credential['username'],
                    'password':credential['password'],'cloudState':info['cloudState']}
        return {'address':info['address'],'username':'pair','authentication':'SSH key',
                'cloudState':info['cloudState']}

    def start(self,lease_id):
        lease=self._lease(lease_id)
        if lease['state']!='active' or datetime.datetime.fromisoformat(lease['expiresAt'])<=self.manager.clock():
            raise ValueError('Machine lease expired; renew before starting')
        status=self.inspect(lease_id)
        if status['cloudState']=='Running':return dict(status,startState='already_running')
        if status['cloudState']!='Stopped':raise ValueError('Machine is not in a startable state')
        self.manager.api.call('StartUHostInstance',UHostId=lease['hostId'],Zone=lease['zone'])
        return dict(status,startState='start_requested')

    def _ssh_args(self,user,address):
        return ['ssh','-i',self.key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+self.known_hosts,
            '-o','ConnectTimeout=8',user+'@'+address]

    def _operation_path(self,operation_id):
        if not isinstance(operation_id,str) or len(operation_id)!=24 or any(c not in '0123456789abcdef' for c in operation_id):
            raise ValueError('Invalid operation ID')
        return self.operation_dir/(operation_id+'.json')

    def _save_operation(self,operation):
        path=self._operation_path(operation['id']);temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(operation,ensure_ascii=False))
        temporary.chmod(0o600);temporary.replace(path)

    def operation(self,operation_id):
        try:operation=json.loads(self._operation_path(operation_id).read_text())
        except FileNotFoundError:raise KeyError('Operation not found') from None
        with self._operation_lock:active=operation_id in self._running_operations
        if operation['state'] in ('queued','running') and not active:
            operation.update(state='interrupted',summary='服务重启，安装回执未收到；请先核验目标机再重试')
            self._save_operation(operation)
        return operation

    def install(self,lease_id,recipe):
        from .software_install import SoftwareCatalog
        lease=self._lease(lease_id)
        if lease['state']!='active':raise ValueError('Machine lease is not active')
        if datetime.datetime.fromisoformat(lease['expiresAt'])<=self.manager.clock():
            raise ValueError('Machine lease has expired; verify billing before installation')
        recipe=SoftwareCatalog(self.manager.directory.parent/'software-catalog.json').get(recipe)
        if recipe['platform'].lower()!=lease['platform']:raise ValueError('Software platform mismatch')
        access=self.login(lease_id)
        if access.get('loginState')!='ssh_authenticated':raise ValueError('SSH login not ready')
        operation={'id':secrets.token_hex(12),'leaseId':lease_id,'softwareId':recipe['id'],
                   'state':'queued','stage':'waiting','summary':'等待执行安装',
                   'createdAt':self.manager.clock().isoformat()}
        self._save_operation(operation)
        with self._operation_lock:self._running_operations.add(operation['id'])
        worker=threading.Thread(target=self._install_worker,args=(operation,access,recipe),daemon=True)
        worker.start()
        return operation

    def _install_worker(self,operation,access,recipe):
        def stage(name,summary):
            operation.update(state='running',stage=name,summary=summary)
            self._save_operation(operation)
        try:
            # Recheck the exact instance immediately before remote execution.
            checked=self.login(operation['leaseId'])
            if checked.get('loginState')!='ssh_authenticated' or checked['address']!=access['address']:
                raise RuntimeError('Machine login changed before installation')
            if recipe['platform']=='Linux':
                package=recipe.get('package')
                if package not in ('git','python3'):raise ValueError('Unsupported Linux package')
                stage('install','刷新软件源并安装 '+package)
                command='sudo -n apt-get update -qq && sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends '+shlex.quote(package)+' && dpkg-query -W -f='+shlex.quote('${Status}\n${Version}')+' '+shlex.quote(package)
                result=subprocess.run(self._ssh_args('pair',access['address'])+[command],capture_output=True,text=True,timeout=900)
                if result.returncode:raise RuntimeError('Linux package installation failed')
                rows=result.stdout.strip().splitlines()
                if len(rows)<2 or rows[-2]!='install ok installed':raise RuntimeError('Linux installed package receipt missing')
                evidence={'installedVersion':rows[-1],'verified':True,'softwareId':recipe['id']}
            else:
                stage('transfer','传送已登记的安装方案')
                source=(Path(__file__).resolve().parents[1]/'windows/software-install.ps1').read_text()
                payload=base64.b64encode(json.dumps(recipe,ensure_ascii=False).encode()).decode()
                script='[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)\n$OutputEncoding=[Console]::OutputEncoding\n'+source+'\n$recipe=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(\''+payload+'\')) | ConvertFrom-Json\n'
                script+='$report={param($item) $item | ConvertTo-Json -Compress -Depth 5 | Write-Output}\n'
                script+='$result=Invoke-SoftwareInstall $recipe $report\n$result | ConvertTo-Json -Compress -Depth 5\nif($result.state -ne "completed"){exit 1}\n'
                local=self.operation_dir/(operation['id']+'.ps1')
                local.write_bytes(b'\xef\xbb\xbf'+script.encode())
                local.chmod(0o600)
                scp=['scp','-i',self.key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                     '-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+self.known_hosts,
                     str(local),'Administrator@'+access['address']+':C:/AgentPairTrial/install-'+operation['id']+'.ps1']
                transfer=subprocess.run(scp,capture_output=True,timeout=45)
                if transfer.returncode:raise RuntimeError('Installation script transfer failed')
                stage('install','远程安装程序执行中')
                command='powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\\AgentPairTrial\\install-'+operation['id']+'.ps1'
                result=subprocess.run(self._ssh_args('Administrator',access['address'])+[command],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=900)
                if result.returncode:raise RuntimeError('Windows installation or version check failed')
                receipts=[]
                for line in result.stdout.splitlines():
                    try:receipts.append(json.loads(line))
                    except ValueError:pass
                final=next((r for r in reversed(receipts) if r.get('state')=='completed'),None)
                if not final:raise RuntimeError('Installer returned no completed receipt')
                from .software_install import verify_receipt
                if not verify_receipt(recipe,final):raise RuntimeError('Installer receipt did not match registered version/hash')
                evidence=final.get('evidence',{})
            operation.update(state='completed',stage='verified',summary='安装与版本验证通过',evidence=evidence)
        except subprocess.TimeoutExpired:
            operation.update(state='interrupted',stage='receipt_missing',summary='连接超时，远程安装可能仍在执行；需核验后再试')
        except Exception as error:
            operation.update(state='failed',stage='failed',summary=str(error)[:200])
        finally:
            self._save_operation(operation)
            with self._operation_lock:self._running_operations.discard(operation['id'])

    def _lease(self, lease_id):
        if not isinstance(lease_id,str) or len(lease_id)!=24 or any(c not in '0123456789abcdef' for c in lease_id):
            raise ValueError('Invalid lease ID')
        path=self.manager.directory/(lease_id+'.json')
        if not path.is_file():raise KeyError('Machine not found')
        lease=json.loads(path.read_text())
        if lease.get('purpose')!='Admin machine console':raise PermissionError('Not a console machine')
        return lease
