"""Build the standalone Windows x64 desktop application on Windows."""
from pathlib import Path
import os
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent


def main():
    if sys.platform != 'win32':
        raise SystemExit('Windows binaries must be built on Windows; use the Windows CI job.')
    env = os.environ.copy()
    env['PYINSTALLER_CONFIG_DIR'] = str(ROOT / 'build/pyinstaller-cache')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
               '--name', 'agentreions_doubao', '--distpath', str(ROOT/'dist'),
               '--workpath', str(ROOT/'build'), '--specpath', str(ROOT/'build')]
    icon = ROOT / 'assets/agentreins.ico'
    if icon.is_file():
        command.extend(['--icon', str(icon)])
    command.append(str(ROOT/'desktop_main.py'))
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    bundle = ROOT/'dist/agentreions_doubao'
    qa = ROOT/'build/packaged-qa'
    qa.mkdir(parents=True, exist_ok=True)
    empty_source = qa/'empty-source'
    empty_source.mkdir(exist_ok=True)
    runtime_env = env.copy()
    runtime_env['QT_QPA_PLATFORM'] = 'offscreen'
    subprocess.run([str(bundle/'agentreions_doubao.exe'), '--db', str(qa/'observations.sqlite3'),
                    '--source-root', str(empty_source), '--window-size', '1280x800',
                    '--screenshot', str(qa/'window.png')], env=runtime_env, check=True, timeout=30)
    if not (qa/'window.png').is_file():
        raise RuntimeError('The packaged Windows UI did not produce its verification image.')
    release = ROOT/'release'
    release.mkdir(exist_ok=True)
    with zipfile.ZipFile(release/'agentreions_doubao-Windows-x64.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in bundle.rglob('*'):
            if path.is_file():
                archive.write(path, path.relative_to(bundle.parent))
    print('Windows package and packaged Qt UI verification completed.')


if __name__ == '__main__':
    main()
