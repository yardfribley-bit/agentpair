"""Build the standalone Intel Mac app using the existing local Qt runtime."""
from pathlib import Path
import os
import plistlib
import subprocess
import sys
from agentreions_doubao import __version__

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / '.venv/bin/python'
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)

if __name__ == '__main__':
    if not PYTHON.is_file():
        raise SystemExit('Install Python, PySide6 Essentials and PyInstaller before building.')
    env = os.environ.copy()
    env['PYINSTALLER_CONFIG_DIR'] = str(ROOT / 'build/pyinstaller-cache')
    command = [str(PYTHON), '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--windowed', '--name', 'agentreions_doubao',
                    '--osx-bundle-identifier', 'com.agentreions.doubao',
                    '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build'),
                    '--specpath', str(ROOT / 'build')]
    icon = ROOT / 'assets/agentreins.icns'
    if icon.is_file():
        command.extend(['--icon', str(icon)])
    command.append(str(ROOT / 'desktop_main.py'))
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    bundle = ROOT / 'dist/agentreions_doubao.app'
    plist_path = bundle / 'Contents/Info.plist'
    with plist_path.open('rb') as stream:
        info = plistlib.load(stream)
    info['CFBundleShortVersionString'] = __version__
    info['CFBundleVersion'] = '1'
    with plist_path.open('wb') as stream:
        plistlib.dump(info, stream)
    # Local developer build: keep a valid ad-hoc signature after setting version.
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(bundle)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
