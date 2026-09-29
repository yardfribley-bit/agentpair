"""Navigator local role, Driver over key-authenticated SSH. No arbitrary commands."""
import json
import subprocess
from .pair_worker import run


class NodeBackend:
    def __init__(self, token, driver, key, known_hosts):
        self.token=token; self.driver=driver; self.key=key; self.known_hosts=known_hosts

    def estimate(self, envelope):
        # Reserve conservatively BEFORE collection/model invocation. Failed calls
        # remain reserved; history-derived rates are not provider monetary caps.
        return 0.10

    def call(self, role, envelope, timeout):
        if role=='navigator': return run(envelope,self.token)
        if role!='driver': raise ValueError('Invalid role')
        private=dict(envelope,relayToken=self.token)
        response=subprocess.run(['ssh','-i',self.key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+self.known_hosts,
            '-o','ConnectTimeout=8','pair@'+self.driver,
            'cd /home/pair/AgentPair && python3 -m agentpair.pair_worker'],
            input=json.dumps(private).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            timeout=min(timeout,150),check=False)
        if response.returncode:
            try: reason=json.loads(response.stderr).get('errorType','WorkerError')
            except (ValueError,TypeError): reason='SSH or worker error'
            raise RuntimeError('Driver: '+str(reason)[:80])
        return json.loads(response.stdout)
