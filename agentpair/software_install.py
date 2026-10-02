"""Versioned installation recipes and verification, not arbitrary remote commands."""
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse


class SoftwareCatalog:
    def __init__(self, path):
        self.path = Path(path)

    def items(self):
        custom = json.loads(self.path.read_text()) if self.path.exists() else {}
        return {'git-linux': {'id': 'git-linux', 'platform': 'Linux', 'name': 'Git',
                             'package': 'git', 'installer': 'apt'},
                'python-linux': {'id': 'python-linux', 'platform': 'Linux', 'name': 'Python 3',
                                'package': 'python3', 'installer': 'apt'}, **custom}

    def register(self, recipe):
        if not isinstance(recipe, dict): raise ValueError('Recipe required')
        sid = recipe.get('id', '')
        if not re.fullmatch(r'[a-z][a-z0-9-]{2,60}', sid): raise ValueError('Invalid software id')
        if recipe.get('platform') != 'Windows' or recipe.get('installer') not in ('inno', 'msi'):
            raise ValueError('Windows supports Inno Setup or MSI')
        url = urlparse(recipe.get('url', ''))
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError('HTTPS download URL required')
        if not re.fullmatch(r'[0-9a-f]{64}', recipe.get('sha256', '')):
            raise ValueError('Pinned SHA256 required')
        for field in ('name', 'displayName', 'version'):
            if not isinstance(recipe.get(field), str) or not 1 <= len(recipe[field]) <= 120:
                raise ValueError('Name, exact registry display name and version required')
        clean = {k: recipe[k] for k in ('id', 'platform', 'installer', 'url', 'sha256', 'name', 'displayName', 'version')}
        rows = json.loads(self.path.read_text()) if self.path.exists() else {}
        rows[sid] = clean
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp'); tmp.write_text(json.dumps(rows, ensure_ascii=False)); tmp.chmod(0o600); tmp.replace(self.path)
        return clean

    def get(self, sid):
        if sid not in self.items(): raise ValueError('Software is not registered')
        from .package_cache import PackageNode
        return PackageNode(self.path.parent/'package-node.private.json').resolve(self.items()[sid])


def verify_receipt(recipe, result):
    evidence = result.get('evidence', {})
    return (isinstance(evidence, dict) and evidence.get('softwareId') == recipe['id']
            and evidence.get('verified') is True and isinstance(evidence.get('installedVersion'), str)
            and bool(evidence['installedVersion'])
            and (recipe['platform'] != 'Windows' or
                 (evidence.get('sha256') == recipe['sha256'] and evidence['installedVersion'] == recipe['version'])))


def install_linux(recipe, emit, run=subprocess.run):
    if recipe.get('platform') != 'Linux' or recipe.get('installer') != 'apt' or recipe.get('package') not in ('git', 'python3'):
        raise ValueError('Unsupported Linux recipe')
    package = recipe['package']
    existing = run(['dpkg-query', '-W', '-f=${Status}\n${Version}', package],
                   capture_output=True, text=True, timeout=30)
    rows = existing.stdout.strip().splitlines()
    if existing.returncode == 0 and len(rows) == 2 and rows[0] == 'install ok installed':
        return {'state': 'completed', 'summary': package + ' 已存在，版本验证通过；未重复安装',
                'evidence': {'softwareId': recipe['id'], 'verified': True, 'installedVersion': rows[1], 'reused': True}}
    def command(args, stage, timeout):
        emit({'state': 'running', 'summary': stage})
        proc = run(args, capture_output=True, text=True, timeout=timeout,
                   env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'DEBIAN_FRONTEND': 'noninteractive'})
        emit({'state': 'running', 'summary': stage, 'stage': stage,
              'log': (proc.stdout + proc.stderr)[-12000:], 'exitCode': proc.returncode})
        if proc.returncode: raise RuntimeError(stage + ' failed; exit=' + str(proc.returncode))
        return proc.stdout.strip()
    command(['apt-get', 'update'], '刷新软件源', 300)
    command(['apt-get', 'install', '-y', '--no-install-recommends', package], '安装软件', 600)
    version = command(['dpkg-query', '-W', '-f=${Status}\n${Version}', package], '验证安装版本', 30)
    rows = version.splitlines()
    if len(rows) != 2 or rows[0] != 'install ok installed': raise RuntimeError('Package verification failed')
    return {'state': 'completed', 'summary': package + ' 已安装并验证版本（未验证 GUI 启动）',
            'evidence': {'softwareId': recipe['id'], 'verified': True, 'installedVersion': rows[1]}}
