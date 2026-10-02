"""Configure a package-only route on the existing Driver HTTPS vhost."""
import base64
from pathlib import Path
import shlex
import subprocess

SCRIPT=r"""
from pathlib import Path
import subprocess,time,os,signal
site=Path('/etc/nginx/conf.d/agentreins-https.conf')
original=site.read_text()
marker='# AgentPair direct package downloads'
if marker not in original:
    if 'location / {' not in original:raise RuntimeError('Expected HTTPS route not found')
    backup=site.with_name(site.name+'.agentpair-backup-'+str(int(time.time())))
    backup.write_text(original)
    route='''    # AgentPair direct package downloads
    location ^~ /packages/ {
        limit_except GET { deny all; }
        proxy_pass http://127.0.0.1:8092;
        proxy_buffering off;
        proxy_read_timeout 120s;
        add_header X-AgentPair-Delivery driver-direct always;
    }
'''
    site.write_text(original.replace('    location / {',route+'    location / {',1))
    check=subprocess.run(['nginx','-t'],capture_output=True)
    if check.returncode:
        site.write_text(original);raise RuntimeError('nginx config rejected; restored original')
    subprocess.run(['systemctl','reload','nginx'],check=True)
env=Path('/home/pair/package-cache.env')
if not env.is_file():raise RuntimeError('Cache token not provisioned')
unit='''[Unit]
Description=AgentPair Driver package cache
After=network-online.target
[Service]
User=pair
WorkingDirectory=/home/pair/package-node
EnvironmentFile=/home/pair/package-cache.env
ExecStart=/usr/bin/python3 -m agentpair.package_cache --directory /home/pair/package-cache
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/pair/package-cache
[Install]
WantedBy=multi-user.target
'''
Path('/etc/systemd/system/agentpair-package-cache.service').write_text(unit)
subprocess.run(['systemctl','stop','agentpair-package-cache.service'],check=False)
# Retire only the old exact package-cache listener owned by pair.
import pwd
uid=pwd.getpwnam('pair').pw_uid
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():continue
    try:
        args=(entry/'cmdline').read_bytes().split(b'\0')
        if entry.stat().st_uid==uid and b'agentpair.package_cache' in args and b'--directory' in args:
            os.kill(int(entry.name),signal.SIGTERM)
    except (OSError,ProcessLookupError):pass
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','agentpair-package-cache.service'],check=True)
print('Driver HTTPS package route configured; existing application route preserved')
"""


def main():
    payload=base64.b64encode(SCRIPT.encode()).decode()
    command="sudo python3 -c "+shlex.quote("import base64;exec(base64.b64decode('"+payload+"'))")
    subprocess.run(['ssh','-tt','-o','StrictHostKeyChecking=yes',
        '-o','UserKnownHostsFile=/private/tmp/agentpair-driver165-knownhosts','-o','ConnectTimeout=8',
        'ubuntu@165.154.254.180',command],check=True)


if __name__=='__main__':main()
