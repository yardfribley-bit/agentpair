"""Versioned installation recipes and verification, not arbitrary remote commands."""
import hashlib
import json
import re
import subprocess
from pathlib import Path, PureWindowsPath
from urllib.parse import urlparse


def portable_entry_point(value):
    """Keep Windows path aliases, ADS and traversal out of trusted recipes."""
    if not isinstance(value, str) or not 1 <= len(value) <= 240 or '\\' in value:
        raise ValueError('Relative portable EXE entry point required (use / separators)')
    parts = value.split('/')
    if any(not part or part in ('.', '..') or part.endswith((' ', '.'))
           or re.search(r'[\x00-\x1f\x7f:*?"<>|]', part)
           or re.fullmatch(r'(CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|CLOCK\$|COM[0-9¹²³]|LPT[0-9¹²³]) *(?:\..*)?', part, re.I)
           for part in parts) or not value.lower().endswith('.exe'):
        raise ValueError('Safe relative portable EXE entry point required')
    return value


def portable_recipe_fingerprint(recipe):
    """Bind the receipt to the exact recipe; cache routing is not its identity."""
    fields = ('id', 'platform', 'installer', 'url', 'sha256', 'name', 'displayName', 'version', 'entryPoint')
    values = {**recipe, 'url': recipe.get('officialUrl') or recipe.get('url', '')}
    body = ''.join(f'{key}:{len(str(values.get(key, "")).encode("utf-8"))}:{values.get(key, "")}\n'
                   for key in fields)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


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
        if not isinstance(sid, str) or not re.fullmatch(r'[a-z][a-z0-9-]{2,60}', sid): raise ValueError('Invalid software id')
        if recipe.get('platform') != 'Windows' or recipe.get('installer') not in ('inno', 'msi', 'portable_zip'):
            raise ValueError('Windows supports Inno Setup, MSI or portable ZIP')
        if not isinstance(recipe.get('url'), str) or re.search(r'[\x00-\x1f\x7f]', recipe['url']):
            raise ValueError('HTTPS download URL required')
        url = urlparse(recipe.get('url', ''))
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError('HTTPS download URL required')
        if not isinstance(recipe.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', recipe['sha256']):
            raise ValueError('Pinned SHA256 required')
        fields = ['id', 'platform', 'installer', 'url', 'sha256', 'name', 'version']
        if recipe['installer'] != 'portable_zip' or 'displayName' in recipe:
            fields.append('displayName')
        for field in ('name', 'version', *(['displayName'] if 'displayName' in fields else [])):
            if (not isinstance(recipe.get(field), str) or not 1 <= len(recipe[field]) <= 120
                    or re.search(r'[\x00-\x1f\x7f]', recipe[field]) or not recipe[field].strip()):
                raise ValueError('Name, version and (for installers) exact registry display name required')
        if recipe['installer'] == 'portable_zip':
            portable_entry_point(sid + '/entry.exe')
            portable_entry_point(recipe.get('entryPoint'))
            fields.append('entryPoint')
        clean = {k: recipe[k] for k in fields}
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
    if not isinstance(result, dict): return False
    evidence = result.get('evidence', {})
    base = (isinstance(evidence, dict) and evidence.get('softwareId') == recipe['id']
            and evidence.get('verified') is True and isinstance(evidence.get('installedVersion'), str)
            and bool(evidence['installedVersion'])
            and (recipe['platform'] != 'Windows' or
                 (evidence.get('sha256') == recipe['sha256'] and evidence['installedVersion'] == recipe['version'])))
    if not base or recipe.get('installer') != 'portable_zip': return base
    try:
        entry = portable_entry_point(recipe.get('entryPoint'))
        directory = PureWindowsPath(evidence.get('installDirectory', ''))
        binary = PureWindowsPath(evidence.get('checkedBinaryPath', ''))
        path_parts = directory.parts[1:]
        safe_directory = (directory.is_absolute() and re.fullmatch(r'[A-Za-z]:\\', directory.anchor)
                          and len(path_parts) >= 4 and path_parts[-3:] == ('AgentPair', 'Software', recipe['id'])
                          and all(part not in ('.', '..') and not part.endswith((' ', '.'))
                                  and not re.search(r'[\x00-\x1f\x7f:*?"<>|]', part) for part in path_parts))
        return bool(safe_directory and binary == directory.joinpath(*entry.split('/'))
                    and evidence.get('entryPoint') == entry
                    and evidence.get('recipeFingerprint') == portable_recipe_fingerprint(recipe)
                    and evidence.get('checkedBinary') is True
                    and isinstance(evidence.get('checkedBinarySha256'), str)
                    and re.fullmatch(r'[0-9a-f]{64}', evidence['checkedBinarySha256'])
                    and type(evidence.get('selfTestExitCode')) is int and evidence['selfTestExitCode'] == 0
                    and evidence.get('selfTestArgument') == '--self-test'
                    and evidence.get('versionSource') == 'pinned_recipe'
                    and evidence.get('binaryVersionVerified') is False
                    and evidence.get('launchVerified') is False)
    except (ValueError, TypeError):
        return False


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
