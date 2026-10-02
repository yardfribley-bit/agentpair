"""Append AppLens hooks without replacing existing WorkBuddy integrations."""
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time

def install(settings,script,python=sys.executable):
    data=json.loads(settings.read_text()) if settings.exists() else {}
    hooks=data.setdefault('hooks',{})
    for event in ('SessionStart','UserPromptSubmit','PreToolUse','PostToolUse','Stop'):
        command=shlex.join([python,str(script.resolve()),event])
        entries=hooks.setdefault(event,[])
        if not any(h.get('command')==command for entry in entries for h in entry.get('hooks',[])):
            entries.append({'matcher':'.*','hooks':[{'type':'command','command':command}]})
    settings.parent.mkdir(parents=True,exist_ok=True)
    backup=settings.with_name(settings.name+'.applens-backup-'+str(time.time_ns()))
    if settings.exists():shutil.copy2(settings,backup);os.chmod(backup,0o600)
    temporary=settings.with_name(settings.name+'.applens-tmp')
    fd=os.open(temporary,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    os.replace(temporary,settings)
    return str(backup)

if __name__=='__main__':
    if '--remove-applens' in sys.argv:
        settings=Path.home()/'.workbuddy/settings.json';data=json.loads(settings.read_text())
        backup=settings.with_name(settings.name+'.applens-backup-'+str(time.time_ns()));shutil.copy2(settings,backup);os.chmod(backup,0o600)
        target=str(Path(__file__).with_name('workbuddy_hook.py').resolve())
        for event,entries in data.get('hooks',{}).items():
            for entry in entries:entry['hooks']=[h for h in entry.get('hooks',[]) if target not in h.get('command','')]
            data['hooks'][event]=[e for e in entries if e.get('hooks')]
        temporary=settings.with_name(settings.name+'.applens-tmp');fd=os.open(temporary,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,indent=2)
        os.replace(temporary,settings);print('AppLens tool hooks removed; other hooks preserved.')
    else:print(json.dumps({'installed':True,'backup':install(Path.home()/'.workbuddy/settings.json',Path(__file__).with_name('workbuddy_hook.py'))}))
