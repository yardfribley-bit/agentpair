import subprocess
from pathlib import Path
import time

root=Path('/home/pair')
commands=[('lightpanda',['/home/pair/agentpair-lightpanda-linux-20260930','serve','--host','127.0.0.1','--port','9222']),
          ('pinchtab',['/home/pair/agentpair-pinchtab-0.15.2','bridge','--bind','127.0.0.1','--port','9867','--cdp-attach','ws://127.0.0.1:9222/'])]
for name,command in commands:
    with (root/(name+'-trial.log')).open('ab') as log:
        proc=subprocess.Popen(command,stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True)
    print(name,proc.pid,flush=True)
    time.sleep(2)
    print('exit',proc.poll(),flush=True)
