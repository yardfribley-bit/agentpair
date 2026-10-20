"""Run each independent Python suite with its correct import roots."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    failed = False
    for folder, package in [(ROOT/'tests'/'legacy', True),(ROOT/'tests', True),(ROOT/'SessionLens'/'tests', False)]:
        paths=[folder.parent,folder,ROOT] if folder.parent.name=='SessionLens' else [ROOT,ROOT/'tests',ROOT/'SessionLens']
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONPATH=os.pathsep.join(map(str,paths)))
        command = [sys.executable, str(ROOT/'ops'/'test_suite.py'), str(folder), str(ROOT) if package else str(folder)]
        print('\nRunning '+str(folder.relative_to(ROOT) if folder!=ROOT else 'root'),flush=True)
        result = subprocess.run(command,cwd=ROOT,env=env)
        failed |= result.returncode != 0
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
