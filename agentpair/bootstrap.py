"""Private SSH bootstrap: launch workspace with credentials on stdin only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import time


def main():
    os.umask(0o077)
    private=json.load(sys.stdin)
    root=Path('/home/pair/AgentPair')
    # Replace only our exact modules running from this workspace. Never a broad
    # pkill; task restart is recorded as interrupted by TaskEngine.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        try:
            args=(entry/'cmdline').read_bytes().split(b'\x00')
            cwd=(entry/'cwd').resolve()
        except (OSError,PermissionError): continue
        if cwd!=root or b'-m' not in args: continue
        module=args[args.index(b'-m')+1]
        scoped=(module==b'agentpair.platform' and str(root/'runtime'/'tasks.db').encode() in args)
        scoped|=(module==b'agentpair.web' and b'web_snapshot.json' in args)
        if scoped:
            os.kill(int(entry.name),signal.SIGTERM)
    time.sleep(0.5)
    # CLI paths are fixed, not controlled by task text or model output.
    with (root/'platform.log').open('ab') as log:
        child=subprocess.Popen([sys.executable,'-m','agentpair.platform',
            '--database',str(root/'runtime'/'tasks.db'),'--driver','106.75.18.16',
            '--key',str(root/'runtime'/'driver_key'),
            '--known-hosts',str(root/'runtime'/'known_hosts')],
            stdin=subprocess.PIPE,stdout=log,stderr=log,cwd=root,start_new_session=True)
        child.stdin.write((json.dumps(private)+'\n').encode()); child.stdin.close()
    with (root/'web.log').open('ab') as log:
        subprocess.Popen([sys.executable,'-m','agentpair.web','--snapshot','web_snapshot.json',
                          '--bind','0.0.0.0','--port','8080'],
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,cwd=root,start_new_session=True)
    print(json.dumps({'pid':child.pid,'status':'launched'}))


if __name__=='__main__': main()
