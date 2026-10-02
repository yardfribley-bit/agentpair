"""Bounded PowerShell diagnostics for this exact authorized acceptance host."""
import base64
import shlex
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy_persistent_navigator import ssh
source="[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);$ProgressPreference='SilentlyContinue';"+sys.argv[1]
encoded=base64.b64encode(source.encode('utf-16le')).decode()
code="""
import subprocess
args=['ssh','-i','/var/lib/agentpair/driver_key','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/var/lib/agentpair/windows_known_hosts','Administrator@106.75.5.214','powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ENCODED']
r=subprocess.run(args,capture_output=True,timeout=55)
print('exit:',r.returncode)
print((r.stdout+r.stderr).decode('utf-8',errors='replace')[-6000:])
""".replace('ENCODED',encoded)
print(ssh('python3 -c '+shlex.quote(code)+' 2>&1; true').decode())
