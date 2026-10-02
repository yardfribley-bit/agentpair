"""Driver-owned, content-addressed installer cache. Never executes packages."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def cache_key(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('Invalid package hash')
    return value


class PackageCache:
    def __init__(self,directory,max_bytes=2*1024**3):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.max_bytes=max_bytes;self.lock=threading.Lock();self.active=set()

    def path(self,key):return self.directory/(cache_key(key)+'.bin')
    def save(self,record):
        path=self.directory/(cache_key(record['sha256'])+'.json')
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(record));tmp.replace(path)

    def records(self):
        with self.lock:
            rows=[json.loads(p.read_text()) for p in self.directory.glob('*.json')]
            for row in rows:
                if row['state']=='downloading' and row['sha256'] not in self.active:
                    row.update(state='interrupted',error='Cache worker restarted')
                if row['state']=='cached' and not self.path(row['sha256']).is_file():row['state']='missing'
            return rows

    def prepare(self,recipe):
        key=cache_key(recipe.get('sha256'));url=urlparse(recipe.get('url',''))
        if recipe.get('platform')!='Windows' or url.scheme!='https' or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError('Pinned Windows HTTPS package required')
        with self.lock:
            if key in self.active:return {'sha256':key,'state':'downloading'}
            path=self.path(key)
            if path.is_file() and self.hash_file(path)==key:return {'sha256':key,'state':'cached'}
            record={'sha256':key,'state':'downloading','bytes':0,'startedAt':time.time(),'softwareId':recipe['id']}
            self.save(record);self.active.add(key)
        threading.Thread(target=self.download,args=(recipe,record),daemon=True).start()
        return dict(record)

    @staticmethod
    def hash_file(path):
        h=hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''):h.update(block)
        return h.hexdigest()

    def download(self,recipe,record):
        key=record['sha256'];partial=self.directory/(key+'.part')
        try:
            h=hashlib.sha256()
            with urlopen(recipe['url'],timeout=30) as response,partial.open('wb') as output:
                if urlparse(response.geturl()).scheme!='https':raise ValueError('HTTPS downgrade rejected')
                while True:
                    block=response.read(1024*1024)
                    if not block:break
                    record['bytes']+=len(block)
                    if record['bytes']>self.max_bytes:raise ValueError('Package exceeds cache size limit')
                    if time.time()-record['startedAt']>900:raise TimeoutError('Cache download deadline exceeded')
                    output.write(block);h.update(block)
                    with self.lock:self.save(record)
            if h.hexdigest()!=key:raise ValueError('SHA256 mismatch; package not published')
            partial.replace(self.path(key));record.update(state='cached',verifiedAt=time.time())
        except Exception as error:
            record.update(state='failed',error=type(error).__name__)
        finally:
            partial.unlink(missing_ok=True)
            with self.lock:self.save(record);self.active.discard(key)


class PackageNode:
    """Navigator control client. Configuration is private, never exposed to users."""
    def __init__(self,path):self.path=Path(path)
    def config(self):
        if not self.path.is_file():return None
        if self.path.stat().st_mode&0o077:raise PermissionError('Package node config must be 0600')
        config=json.loads(self.path.read_text());url=urlparse(config['url'])
        local=url.scheme=='http' and url.hostname=='127.0.0.1'
        if (url.scheme!='https' and not local) or not url.hostname or url.username or url.password or url.path not in ('','/'):
            raise ValueError('Package node HTTPS origin required')
        public=urlparse(config.get('publicUrl',config['url']))
        if public.scheme!='https' or not public.hostname or public.username or public.password:
            raise ValueError('Public package HTTPS URL required')
        return config
    def call(self,path,data=None):
        config=self.config()
        if not config:raise RuntimeError('尚未配置缓存 Driver')
        request=Request(config['url'].rstrip('/')+path,data=json.dumps(data).encode() if data is not None else None,
                        headers={'Authorization':'Bearer '+config['token'],'Content-Type':'application/json'})
        with urlopen(request,timeout=10) as response:return json.load(response)
    def snapshot(self):
        try:
            if not self.config():return {'state':'unconfigured','items':[]}
            config=self.config()
            result=self.call('/manifest');result.update(state='online',name=config.get('name','缓存 Driver'),
                downloadReady=config.get('deliveryMode')=='direct' and config.get('downloadReady') is True)
            return result
        except Exception:return {'state':'unreachable','items':[]}
    def resolve(self,recipe):
        if recipe.get('platform')!='Windows':return recipe
        snapshot=self.snapshot()
        config=self.config()
        if not config or config.get('deliveryMode')!='direct' or config.get('downloadReady') is not True:
            return recipe
        if any(r['sha256']==recipe['sha256'] and r['state']=='cached' for r in snapshot['items']):
            prefix=config.get('publicUrl',config['url'].rstrip('/')+'/packages')
            return {**recipe,'officialUrl':recipe['url'],'url':prefix.rstrip('/')+'/'+recipe['sha256']}
        return recipe


def handler_for(cache,token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,data):
            body=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def authorized(self):return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
        def do_HEAD(self):self.do_GET()
        def do_GET(self):
            if self.path=='/manifest':
                if not self.authorized():self.send(401,{'error':'Unauthorized'});return
                self.send(200,{'items':cache.records()});return
            if not self.path.startswith('/packages/'):self.send(404,{'error':'Not found'});return
            try:path=cache.path(self.path.removeprefix('/packages/'))
            except ValueError:self.send(400,{'error':'Invalid hash'});return
            if not path.is_file():self.send(404,{'error':'Not cached'});return
            size=path.stat().st_size;start=0;end=size-1;status=200
            requested=self.headers.get('Range')
            if requested:
                match=re.fullmatch(r'bytes=(\d+)-(\d*)',requested)
                if not match:self.send(416,{'error':'Unsupported range'});return
                start=int(match[1]);end=min(int(match[2]) if match[2] else size-1,size-1)
                if start>end:self.send(416,{'error':'Unsatisfiable range'});return
                status=206
            self.send_response(status);self.send_header('Content-Type','application/octet-stream');self.send_header('Accept-Ranges','bytes')
            if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.send_header('Content-Length',str(end-start+1));self.end_headers()
            if self.command=='HEAD':return
            with path.open('rb') as source:
                source.seek(start);remaining=end-start+1
                while remaining:
                    block=source.read(min(1024*1024,remaining))
                    if not block:break
                    self.wfile.write(block);remaining-=len(block)
        def do_POST(self):
            if not self.authorized():self.send(401,{'error':'Unauthorized'});return
            if self.path!='/prepare':self.send(404,{'error':'Not found'});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<16000:raise ValueError('Invalid body')
                result=cache.prepare(json.loads(self.rfile.read(size)));self.send(202,result)
            except (ValueError,KeyError,TypeError):self.send(400,{'error':'Invalid package recipe'})
    return Handler


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',required=True);parser.add_argument('--port',type=int,default=8092)
    args=parser.parse_args();token=os.environ.get('AGENTPAIR_PACKAGE_TOKEN','')
    if len(token)<32:raise SystemExit('AGENTPAIR_PACKAGE_TOKEN must be at least 32 characters')
    ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(PackageCache(args.directory),token)).serve_forever()


if __name__=='__main__':main()
