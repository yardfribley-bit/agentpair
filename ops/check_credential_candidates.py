"""Read-only detector validation on deployed captured data; output counts only."""
import sys,json,shlex
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import deploy_persistent_navigator as remote
remote.KNOWN='/private/tmp/vmess-50.118.187.180-known_hosts'
source=(Path(__file__).resolve().parents[1]/'agentpair/credential_threats.py').read_text()
code="import json,sqlite3,collections;ns={'__package__':'agentpair'};exec("+repr(source)+",ns);db=sqlite3.connect('file:/var/lib/agentpair/devices.db?mode=ro',uri=True);db.row_factory=sqlite3.Row;counts=collections.Counter()\n"
code+="for row in db.execute(\"SELECT c.data,d.snapshot FROM applens_model_context c JOIN devices d ON d.id=c.device_id WHERE d.revoked=0\"):\n r=json.loads(row['data']);os=json.loads(row['snapshot']).get('os');kinds=set()\n try:values=list(ns['segments'](json.loads(r['body'])))\n except (ValueError,RecursionError):values=[('',r['body'],None)]\n for _,text,_ in values:\n  kinds.update(f[2] for f in ns['candidates'](text))\n for kind in kinds:counts[(os,r['source'],kind)]+=1\nprint(json.dumps([{'platform':k[0],'source':k[1],'kind':k[2],'records':v} for k,v in counts.items()]))"
print(remote.ssh('cd /opt/agentpair && python3 -c '+shlex.quote(code)).decode())
