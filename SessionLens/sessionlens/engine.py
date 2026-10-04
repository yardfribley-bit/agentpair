"""Loopback verification receiver using the platform's SessionStore."""
import argparse
import hmac
from http.server import HTTPServer,BaseHTTPRequestHandler
import json
from pathlib import Path
import sys
from urllib.parse import urlparse,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'AgentPair'))
from agentpair.session_lens import SessionStore

def main():
    p=argparse.ArgumentParser();p.add_argument('--state',type=Path,required=True);p.add_argument('--token-file',type=Path,required=True);p.add_argument('--port',type=int,default=18950)
    args=p.parse_args();token=args.token_file.read_text(encoding='utf-8').strip();store=SessionStore(args.state)
    if len(token)<32:raise ValueError('Strong token required')
    identity={'id':'sessionlens-local','owner':'local-user'}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,value):
            body=json.dumps(value,ensure_ascii=False).encode();self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
        def authorized(self):return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
        def do_POST(self):
            if not self.authorized():self.send(401,{'error':'Unauthorized'});return
            if self.path!='/api/sessionlens/events':self.send(404,{});return
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=4194304:raise ValueError('Invalid size')
                self.send(200,store.ingest(identity,json.loads(self.rfile.read(n))))
            except (ValueError,TypeError,KeyError,AttributeError):self.send(400,{'error':'Invalid batch'})
        def do_GET(self):
            if not self.authorized():self.send(401,{'error':'Unauthorized'});return
            url=urlparse(self.path)
            if url.path=='/api/sessionlens/sessions':self.send(200,{'items':store.sessions(identity['owner'])})
            elif url.path=='/api/sessionlens/report':self.send(200,store.report(identity['owner'],identity['id'],parse_qs(url.query).get('session',[''])[0]))
            else:self.send(404,{})
    print('SessionLens verification engine: 127.0.0.1:'+str(args.port),flush=True)
    HTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
if __name__=='__main__':main()
