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
from .transport import NodeBackend
from .web import ASSETS


def handler_for(engine, password, origin):
    session=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
    attempts=[]
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, data, cookie=False):
            body=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.headers_common(len(body))
            if cookie: self.send_header('Set-Cookie','agentpair='+session+'; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers(); self.wfile.write(body)

        def headers_common(self, size):
            self.send_header('Content-Length',str(size)); self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")

        def authenticated(self):
            try:
                cookie=SimpleCookie(self.headers.get('Cookie',''))
                return 'agentpair' in cookie and hmac.compare_digest(cookie['agentpair'].value,session)
            except Exception: return False

        def do_GET(self):
            paths={'/':'workspace.html','/workspace.js':'workspace.js','/style.css':'style.css','/workspace.css':'workspace.css',
                   '/pair_flow.js':'pair_flow.js','/pair_flow.css':'pair_flow.css'}
            if self.path in paths:
                try: body=(ASSETS/paths[self.path]).read_bytes()
                except OSError: self.respond(503,{'error':'Static asset unavailable'}); return
                self.send_response(200)
                suffix=Path(paths[self.path]).suffix
                self.send_header('Content-Type',{'.html':'text/html','.js':'application/javascript','.css':'text/css'}[suffix]+'; charset=utf-8')
                self.headers_common(len(body)); self.end_headers(); self.wfile.write(body); return
            if not self.authenticated(): self.respond(401,{'error':'Login required'}); return
            try:
                if self.path=='/api/session': self.respond(200,{'csrf':csrf,'budget':engine.usage(),'maxRounds':engine.max_rounds})
                elif self.path=='/api/tasks': self.respond(200,{'items':engine.list(),'budget':engine.usage()})
                elif self.path.startswith('/api/tasks/') and self.path.count('/')==3:
                    self.respond(200,engine.get(self.path.rsplit('/',1)[1]))
                else: self.respond(404,{'error':'Not found'})
            except KeyError: self.respond(404,{'error':'Task not found'})

        def do_POST(self):
            if self.headers.get('Origin')!=origin:
                self.respond(403,{'error':'Origin rejected'}); return
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                self.respond(415,{'error':'JSON required'}); return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=32768: self.respond(413,{'error':'Request too large'}); return
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict): raise ValueError('Object required')
                if self.path=='/api/login':
                    stamp=time.monotonic()
                    attempts[:]=[t for t in attempts if stamp-t<60]
                    if len(attempts)>=10: self.respond(429,{'error':'Too many login attempts'}); return
                    attempts.append(stamp)
                    supplied=data.get('password','')
                    if not isinstance(supplied,str) or not hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),hashlib.sha256(password.encode()).digest()):
                        self.respond(401,{'error':'Login failed'}); return
                    self.respond(200,{'csrf':csrf},cookie=True); return
                if not self.authenticated(): self.respond(401,{'error':'Login required'}); return
                if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),csrf):
                    self.respond(403,{'error':'CSRF rejected'}); return
                if self.path=='/api/logout':
                    self.send_response(200); self.send_header('Set-Cookie','agentpair=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'); self.headers_common(2); self.end_headers(); self.wfile.write(b'{}'); return
                if self.path=='/api/tasks':
                    self.respond(201,engine.create(data.get('title'),data.get('message'),data.get('adapter','discussion'),data.get('target',''))); return
                parts=self.path.split('/')
                if len(parts)==5 and parts[1:3]==['api','tasks']:
                    if parts[4]=='messages': self.respond(202,engine.followup(parts[3],data.get('message'))); return
                    if parts[4]=='cancel': self.respond(200,engine.cancel(parts[3])); return
                self.respond(404,{'error':'Not found'})
            except KeyError: self.respond(404,{'error':'Task not found'})
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
    args=p.parse_args()
    os.umask(0o077)
    # One-line private SSH stdin bootstrap; never log/store provider credentials.
    private=json.loads(sys.stdin.readline())
    if len(private['password'])<20: raise ValueError('Workspace password too short')
    backend=NodeBackend(private['relayToken'],args.driver,args.key,args.known_hosts)
    engine=TaskEngine(args.database,backend)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(engine,private['password'],args.origin))
    print('Authenticated Navigator workspace ready',flush=True)
    try: server.serve_forever()
    finally: server.server_close(); engine.close()


if __name__=='__main__': main()
