import argparse
import json
import os
from pathlib import Path
import time
import urllib.request
from .core import Collector,digest

def main():
    p=argparse.ArgumentParser(prog='sessionlens')
    p.add_argument('--source',type=Path,default=Path.home()/'.codex/sessions')
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--endpoint',help='Full HTTPS ingestion URL; loopback HTTP also accepted')
    p.add_argument('--once',action='store_true')
    p.add_argument('--interval',type=float,default=3)
    args=p.parse_args()
    from urllib.parse import urlparse
    token=os.environ.get('SESSIONLENS_TOKEN','')
    if args.endpoint:
        url=urlparse(args.endpoint)
        if url.username or url.password or url.fragment or url.query: p.error('Clean endpoint URL required')
        if url.scheme!='https' and not (url.scheme=='http' and url.hostname in ('127.0.0.1','localhost')):p.error('HTTPS required')
        if not token:p.error('SESSIONLENS_TOKEN required')
    collector=Collector(args.state); failures=0
    destination=(args.endpoint or '')+':'+digest(token.encode())
    while True:
        added=0
        paths=[args.source] if args.source.is_file() else sorted(args.source.rglob('rollout-*.jsonl'))
        for path in paths:
            try:added+=collector.scan(path)
            except (OSError,ValueError) as e:print(json.dumps({'captureError':str(e),'path':str(path)}),flush=True)
        uploaded=0
        if args.endpoint:
            try:
                # Bounded work each tick; acknowledged events are not resent.
                for _ in range(20):
                    batch=collector.pending(destination)
                    if not batch:break
                    request=urllib.request.Request(args.endpoint,data=json.dumps({'schemaVersion':1,'events':batch},ensure_ascii=False).encode(),
                        headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
                    with urllib.request.urlopen(request,timeout=15) as response:receipt=json.load(response)
                    expected={e['id'] for e in batch}
                    if set(receipt.get('ids',[]))!=expected:raise ValueError('Receipt mismatch; batch retained')
                    collector.acknowledge(destination,expected);uploaded+=len(batch)
                failures=0
            except Exception as e:
                failures+=1;print(json.dumps({'uploadError':type(e).__name__,'retained':True}),flush=True)
        print(json.dumps({'product':'SessionLens','added':added,'uploaded':uploaded,**collector.counts()}),flush=True)
        if args.once:break
        time.sleep(max(1,args.interval)*min(16,2**min(failures,4)))

if __name__=='__main__':main()
