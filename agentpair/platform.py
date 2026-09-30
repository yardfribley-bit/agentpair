"""Authenticated task workspace. Bind to loopback unless TLS is configured."""
import argparse
import hashlib
import hmac
import json
import os
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sys
import time
from .tasks import TaskEngine, Conflict, Limit
from .transport import NodeBackend, CloudDriverBackend
from .ucloud import UCloudClient
from .resources import ResourceManager
from .jev import JevClient, AdapterClient
from .resource_usage import snapshot
from .web import ASSETS
from .credentials import CredentialStore
from .devices import DeviceStore
from .accounts import Accounts


def handler_for(engine, password, origin, public_demo=False, expires_at=None, username='admin'):
    attempts=[]
    credentials=CredentialStore(Path(engine.db).parent/'credentials')
    devices=DeviceStore(Path(engine.db).parent/'devices.db')
    accounts=Accounts(Path(engine.db).parent/'accounts.db', username)
    engine.accounts=accounts
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, data, cookie=False):
            body=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.headers_common(len(body))
            if cookie: self.send_header('Set-Cookie','agentpair='+cookie+'; HttpOnly; SameSite=Strict; Path=/'+('; Secure' if origin.startswith('https://') else ''))
            self.end_headers(); self.wfile.write(body)

        def headers_common(self, size):
            self.send_header('Content-Length',str(size)); self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")

        def authenticated(self):
            return self.identity() is not None

        def cookie_token(self):
            try:
                cookie=SimpleCookie(self.headers.get('Cookie',''))
                return cookie['agentpair'].value if 'agentpair' in cookie else ''
            except Exception: return ''

        def identity(self):
            return accounts.get(self.cookie_token())

        def admin(self):
            return bool(self.identity() and self.identity()['role']=='admin')

        def do_GET(self):
            paths={'/':'workspace.html','/workspace.js':'workspace.js','/style.css':'style.css','/workspace.css':'workspace.css',
                   '/pair_flow.js':'pair_flow.js','/pair_flow.css':'pair_flow.css','/collaboration.css':'collaboration.css',
                   '/workbench.js':'workbench.js','/workbench.css':'workbench.css'}
            paths['/credentials.js']='credentials.js'
            paths['/team_console.js']='team_console.js'
            paths['/team_console.css']='team_console.css'
            paths.update({'/devices':'devices.html','/devices.js':'devices.js','/devices.css':'devices.css'})
            if self.path=='/downloads/AgentPair-Windows-Setup-0.1.0.exe':
                installer=ASSETS/'AgentPair-Windows-Setup-0.1.0.exe'
                if not installer.is_file():self.respond(503,{'error':'Installer not published yet'});return
                body=installer.read_bytes();self.send_response(200)
                self.send_header('Content-Type','application/octet-stream')
                self.send_header('Content-Disposition','attachment; filename="AgentPair-Windows-Setup-0.1.0.exe"')
                self.headers_common(len(body));self.end_headers();self.wfile.write(body);return
            if self.path=='/downloads/agentpair-windows.ps1':
                body=(ASSETS/'agentpair-windows.ps1').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','application/octet-stream')
                self.send_header('Content-Disposition','attachment; filename="agentpair-windows.ps1"')
                self.headers_common(len(body));self.end_headers();self.wfile.write(body);return
            if self.path in paths:
                try: body=(ASSETS/paths[self.path]).read_bytes()
                except OSError: self.respond(503,{'error':'Static asset unavailable'}); return
                self.send_response(200)
                suffix=Path(paths[self.path]).suffix
                self.send_header('Content-Type',{'.html':'text/html','.js':'application/javascript','.css':'text/css'}[suffix]+'; charset=utf-8')
                self.headers_common(len(body)); self.end_headers(); self.wfile.write(body); return
            try:
                if self.path=='/api/endpoint/tasks':
                    authorization=self.headers.get('Authorization','')
                    if not authorization.startswith('Bearer '): self.respond(401,{'error':'Device token required'});return
                    self.respond(200,devices.pull(authorization[7:]) or {'task':None});return
                if self.path=='/api/balance':
                    if not self.authenticated():self.respond(401,{'error':'Login required'});return
                    self.respond(200,accounts.balance(self.identity()['id']));return
                if self.path=='/api/devices':
                    if not self.authenticated():self.respond(401,{'error':'Login required'});return
                    self.respond(200,{'items':devices.list(self.identity()['id'])});return
                if self.path=='/api/credentials':
                    if not self.authenticated():self.respond(401,{'error':'Login required'});return
                    if not self.admin():self.respond(403,{'error':'Administrator required'});return
                    self.respond(200,credentials.status());return
                if self.path=='/api/session': self.respond(200,{'role':self.identity()['role'] if self.authenticated() else 'viewer','username':self.identity()['name'] if self.authenticated() else None,'csrf':self.identity()['csrf'] if self.authenticated() else None,'budget':engine.usage(),'maxRounds':engine.max_rounds})
                elif self.path=='/api/tasks': self.respond(200,{'items':engine.list(),'budget':engine.usage()})
                elif self.path=='/api/resources': self.respond(200,snapshot(engine))
                elif self.path.startswith('/api/tasks/') and self.path.count('/')==3:
                    self.respond(200,engine.get(self.path.rsplit('/',1)[1]))
                else: self.respond(404,{'error':'Not found'})
            except KeyError: self.respond(404,{'error':'Task not found'})

        def do_POST(self):
            if expires_at is not None and time.time()>=expires_at:
                self.respond(410,{'error':'本次实验已到期'}); return
            # Endpoints use device identities, never browser session cookies.
            if self.path in ('/api/endpoint/enroll','/api/endpoint/report','/api/endpoint/tasks/result'):
                try:
                    if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                        self.respond(415,{'error':'JSON required'});return
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=1048576:
                        self.respond(413,{'error':'Inventory too large'});return
                    data=json.loads(self.rfile.read(size))
                    if not isinstance(data,dict):raise ValueError('Object required')
                    if self.path.endswith('/enroll'):
                        result=devices.enroll(data.get('code'),data.get('name'))
                    elif self.path.endswith('/tasks/result'):
                        authorization=self.headers.get('Authorization','')
                        if not authorization.startswith('Bearer '):raise PermissionError('Device token required')
                        result=devices.complete(authorization[7:],data.get('taskId'),data.get('lease'),data.get('result'))
                    else:
                        authorization=self.headers.get('Authorization','')
                        if not authorization.startswith('Bearer '):raise PermissionError('Device token required')
                        result=devices.report(authorization[7:],data)
                    self.respond(200,result)
                except PermissionError as error:self.respond(401,{'error':str(error)})
                except (ValueError,TypeError,UnicodeError):self.respond(400,{'error':'Invalid endpoint payload'})
                return
            if self.headers.get('Origin')!=origin:
                self.respond(403,{'error':'Origin rejected'}); return
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                self.respond(415,{'error':'JSON required'}); return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=32768: self.respond(413,{'error':'Request too large'}); return
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict): raise ValueError('Object required')
                if self.path in ('/api/login','/api/register'):
                    stamp=time.monotonic()
                    attempts[:]=[t for t in attempts if stamp-t<60]
                    if len(attempts)>=10: self.respond(429,{'error':'Too many login attempts'}); return
                    attempts.append(stamp)
                    if self.path=='/api/register':
                        try: user=accounts.register(data.get('username'),data.get('password'))
                        except ValueError as error:self.respond(400,{'error':str(error)});return
                        token,record=accounts.issue(user)
                        self.respond(201,{'csrf':record['csrf'],'role':'user'},cookie=token);return
                    supplied=data.get('password','')
                    if data.get('username')==username and isinstance(supplied,str) and hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),hashlib.sha256(password.encode()).digest()):
                        user={'id':'admin','name':username,'role':'admin'}
                    else:user=accounts.login(data.get('username'),supplied)
                    if not user:self.respond(401,{'error':'账号或密码错误'});return
                    token,record=accounts.issue(user)
                    self.respond(200,{'csrf':record['csrf'],'role':user['role']},cookie=token);return
                if not self.authenticated(): self.respond(401,{'error':'Login required'}); return
                if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),self.identity()['csrf']):
                    self.respond(403,{'error':'CSRF rejected'}); return
                if self.path=='/api/devices/pairing':
                    self.respond(201,devices.pairing(self.identity()['id']));return
                if self.path=='/api/devices/dispatch':
                    if not self.admin() and not self.authenticated(): self.respond(401,{'error':'Login required'});return
                    self.respond(201,devices.dispatch(self.identity()['id'],data.get('deviceId'),data.get('task',data)));return
                if self.path=='/api/devices/revoke':
                    devices.revoke(data.get('deviceId'),self.identity()['id']);self.respond(200,{'revoked':True});return
                if self.path=='/api/devices/analyze':
                    if not self.admin() and accounts.balance(self.identity()['id'])['remainingCNY']<=0:
                        self.respond(409,{'error':'模型额度不足'});return
                    if data.get('shareConfirmed') is not True:
                        self.respond(400,{'error':'Confirm sharing the selected inventory with task viewers and the model'});return
                    name,context=devices.analysis_context(data.get('deviceId'),data.get('kind'),data.get('index'),data.get('goal'),data.get('selected'),self.identity()['id'])
                    self.respond(201,engine.create('应用分析 · '+name,context,'discussion','','local','none',billing_owner=None if self.admin() else self.identity()['id']));return
                if self.path=='/api/logout':
                    accounts.logout(self.cookie_token())
                    self.send_response(200); self.send_header('Set-Cookie','agentpair=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'); self.headers_common(2); self.end_headers(); self.wfile.write(b'{}'); return
                if not self.admin():self.respond(403,{'error':'Administrator required'});return
                if self.path=='/api/tasks':
                    self.respond(201,engine.create(data.get('title'),data.get('message'),data.get('adapter','discussion'),data.get('target',''),data.get('engineeringMethod','local'),data.get('executionProfile','none'))); return
                if self.path=='/api/credentials':
                    self.respond(200,credentials.save(data));return
                parts=self.path.split('/')
                if len(parts)==5 and parts[1:3]==['api','tasks']:
                    if parts[4]=='messages': self.respond(202,engine.followup(parts[3],data.get('message'))); return
                    if parts[4]=='cancel': self.respond(200,engine.cancel(parts[3])); return
                self.respond(404,{'error':'Not found'})
            except KeyError: self.respond(404,{'error':'Task not found'})
            except PermissionError as error:self.respond(403,{'error':str(error)})
            except (Conflict,Limit) as error: self.respond(409,{'error':str(error)})
            except (ValueError,TypeError,UnicodeError): self.respond(400,{'error':'Invalid task or message; check public IPv4 target and length limits'})

        def log_message(self,*_): pass
    return Handler


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--database',required=True)
    p.add_argument('--driver',required=True)
    p.add_argument('--key',required=True)
    p.add_argument('--known-hosts',required=True)
    p.add_argument('--port',type=int,default=9090)
    p.add_argument('--origin',default='http://127.0.0.1:18080')
    p.add_argument('--public-demo',action='store_true',help='Temporary shared workspace without login')
    args=p.parse_args()
    os.umask(0o077)
    # One-line private SSH stdin bootstrap; never log/store provider credentials.
    private=json.loads(sys.stdin.readline())
    if len(private['password'])<8: raise ValueError('Workspace password too short')
    if 'cloud' in private:
        cloud=private['cloud']
        client=UCloudClient(cloud['publicKey'],cloud['privateKey'],cloud['projectId'],cloud['region'])
        manager=ResourceManager(client,cloud['leaseDirectory'],max_hosts=cloud.get('maxHosts',4),
                                max_seconds=cloud.get('maxSeconds',3600),
                                max_hourly_cny=cloud.get('maxHourlyCNY',1.0))
        backend=CloudDriverBackend(private['relayToken'],manager,args.key,args.known_hosts,
                                   cloud['sshPublicKey'],cloud['firewallId'],cloud['workerRoot'],
                                   cloud.get('zone','cn-bj2-04'))
    else:
        backend=NodeBackend(private['relayToken'],args.driver,args.key,args.known_hosts)
    jev=private.get('jev',{})
    if jev.get('provider')=='system_one_adapter':
        backend.jev=AdapterClient(private['relayToken'],model=jev.get('model','deepseek-v4-flash'),threshold=jev.get('threshold',.8))
    elif jev.get('apiKey'):
        backend.jev=JevClient(jev['apiKey'],model=jev.get('model','jev-latest'),threshold=jev.get('threshold',.8))
    engine=TaskEngine(args.database,backend,budget=None)
    server=ThreadingHTTPServer(('0.0.0.0' if args.public_demo else '127.0.0.1',args.port),handler_for(engine,private['password'],args.origin,args.public_demo,private.get('expiresAt'),private.get('username','admin')))
    print('Authenticated Navigator workspace ready',flush=True)
    try: server.serve_forever()
    finally: server.server_close(); engine.close()


if __name__=='__main__': main()
