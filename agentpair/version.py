"""Public build provenance; never hide whether local source is dirty."""
import json
from pathlib import Path
import subprocess


def build_version():
    manifest = Path(__file__).with_name('build-info.json')
    if manifest.is_file():
        value = json.loads(manifest.read_text())
        return {key:value.get(key) for key in ('commit','builtAt','sourceSHA256')}
    root = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,stderr=subprocess.DEVNULL,text=True,timeout=2).strip()
        dirty = bool(subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=root,text=True,timeout=2).strip())
    except (OSError,subprocess.SubprocessError):
        commit,dirty = None,True
    return {'commit':commit,'builtAt':None,'dirty':dirty}
