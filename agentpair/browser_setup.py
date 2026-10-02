"""Native preparation for the reused WebLens browser client."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

def main():
    if Path('/etc/agentpair-driver').read_text().strip()!='isolated-driver':raise RuntimeError('Driver required')
    domains=json.load(sys.stdin)
    if not domains or not all(isinstance(d,str) and all(c.isalnum() or c in '.-' for c in d) for d in domains):raise ValueError('Explicit URL domains required')
    root=Path('/home/pair/AgentPair/browser-assets')
    (root/'domains.json').write_text(json.dumps(domains))
    owned={str(root/n).encode() for n in ('lightpanda','pinchtab','cdp-proxy','browserkit')}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():continue
        try:cmd=(entry/'cmdline').read_bytes().split(b'\0')
        except OSError:continue
        if cmd and cmd[0] in owned:
            try:os.kill(int(entry.name),15)
            except ProcessLookupError:pass
    time.sleep(.5)
    with (root/'lightpanda.log').open('ab') as log:
        subprocess.Popen([str(root/'lightpanda'),'serve','--host','127.0.0.1','--port','9222'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    for _ in range(20):
        try:
            with socket.create_connection(('127.0.0.1',9222),timeout=1):pass
            print(json.dumps({'ready':True,'client':'agentpair-browserkit','domains':domains}));return
        except OSError:time.sleep(.5)
    raise RuntimeError('Lightpanda did not start')

if __name__=='__main__':main()
