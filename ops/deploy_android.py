"""Deploy the Android device-cloud interaction to the persistent Navigator."""
import hashlib
import io
import json
import shlex
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy_persistent_navigator import ssh

FILES = [
    'agentpair/platform.py',
    'agentpair/devices.py',
    'agentpair/mobile_auth.py',
    'agentpair/web_assets/devices.html',
    'agentpair/web_assets/devices.js',
    'agentpair/web_assets/devices_prototype.css',
    'agentpair/web_assets/mobile_auth_ui.js',
    'agentpair/web_assets/AgentPair-Android-0.1.0.apk',
]


def remote_python(code):
    return ssh('python3 -c ' + shlex.quote(code)).decode().strip()


def main():
    for name in FILES:
        if not (ROOT / name).is_file():
            raise FileNotFoundError(name)

    # Do not interrupt either Navigator work or a phone job currently in flight.
    task_check = "import json,sqlite3; c=sqlite3.connect('/var/lib/agentpair/tasks.db'); print(json.dumps([json.loads(r[0]).get('id') for r in c.execute('select data from tasks') if json.loads(r[0]).get('status') in ('queued','running','cancelling')]))"
    if json.loads(remote_python(task_check)):
        raise RuntimeError('Navigator has active tasks; defer deployment')
    driver_check = "import sqlite3,json; c=sqlite3.connect('/var/lib/agentpair/devices.db'); print(json.dumps(c.execute(\"select id from driver_tasks where state in ('queued','assigned','received','running')\").fetchall()))"
    if json.loads(remote_python(driver_check)):
        raise RuntimeError('A device task is active; defer deployment')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = '/opt/agentpair-backups/android-' + stamp + '.tar.gz'
    ssh('mkdir -p /opt/agentpair-backups && tar -czf ' + backup + ' -C /opt/agentpair agentpair')

    bundle = io.BytesIO()
    with tarfile.open(fileobj=bundle, mode='w') as archive:
        for name in FILES:
            archive.add(ROOT / name, arcname=name)
    ssh('tar -xf - -C /opt/agentpair', bundle.getvalue())

    apk_hash = hashlib.sha256((ROOT / FILES[-1]).read_bytes()).hexdigest()
    try:
        preflight = "cd /opt/agentpair && ./venv/bin/python -c 'import agentpair.platform, agentpair.devices, agentpair.mobile_auth'"
        ssh(preflight)
        ssh('systemctl restart agentpair-navigator.service')
        ssh('systemctl is-active --quiet agentpair-navigator.service && curl --retry 8 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:9090/devices >/dev/null && curl --retry 8 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:9090/downloads/AgentPair-Android-0.1.0.apk -o /tmp/AgentPair-Android-0.1.0.apk')
        remote_hash = ssh('sha256sum /tmp/AgentPair-Android-0.1.0.apk').decode().split()[0]
        if remote_hash != apk_hash:
            raise RuntimeError('Published APK hash differs from the locally verified build')
        ssh('rm -f /tmp/AgentPair-Android-0.1.0.apk')
    except Exception:
        ssh('tar -xzf ' + backup + ' -C /opt/agentpair && systemctl restart agentpair-navigator.service')
        raise

    print(json.dumps({'status': 'deployed', 'backup': backup,
                      'apkSha256': apk_hash,
                      'url': 'https://50.118.187.180/downloads/AgentPair-Android-0.1.0.apk'},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
