"""Execute edits only on a dedicated Driver, inside an offline container.

No cloud/model credentials or Docker socket are passed into the container.
"""
import difflib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import threading
import uuid
from .repository import repository_name

PROFILES = {
    'python': ('python:3.12-slim', [['python','-m','compileall','-q','.'],
                                 ['python','-m','unittest','discover','-v']]),
    'node': ('node:22-slim', [['npm','run','build'], ['sh','-c','npm run build && npm test']]),
}


def validate_edits(edits):
    if not isinstance(edits,list) or not 1 <= len(edits) <= 12:
        raise ValueError('Expected 1–12 file edits')
    seen=set(); size=0
    for edit in edits:
        path=edit.get('path',''); content=edit.get('content')
        p=PurePosixPath(path)
        if not path or p.is_absolute() or '..' in p.parts or '\\' in path or any(x.startswith('.') for x in p.parts) or path in seen:
            raise ValueError('Unsafe or duplicate edit path')
        if not isinstance(content,str): raise ValueError('Edits require complete text content')
        size+=len(content.encode());seen.add(path)
    if size>150000: raise ValueError('Edit payload too large')


def execute(evidence, edits, profile):
    if Path('/etc/agentpair-driver').read_text().strip() != 'isolated-driver':
        raise RuntimeError('Execution is restricted to dedicated Driver hosts')
    image, commands=PROFILES[profile]
    validate_edits(edits)
    repo=repository_name(evidence['sourceUrl']); sha=evidence['commit']
    import re
    if not re.fullmatch('[0-9a-f]{40}',sha): raise ValueError('Invalid commit')
    with tempfile.TemporaryDirectory(prefix='agentpair-job-') as tmp:
        root=Path(tmp).resolve()/'repo'; root.mkdir()
        env={'PATH':'/usr/bin:/bin','HOME':tmp,'GIT_CONFIG_NOSYSTEM':'1','GIT_TERMINAL_PROMPT':'0'}
        def git(*args):
            return subprocess.run(['git','-c','core.hooksPath=/dev/null',*args],cwd=root,env=env,
                                  capture_output=True,timeout=60,check=True).stdout.decode()
        git('init');git('fetch','--depth=1','https://github.com/'+repo+'.git',sha)
        git('checkout','--detach','FETCH_HEAD')
        if git('rev-parse','HEAD').strip()!=sha: raise RuntimeError('Checkout mismatch')
        patch=[]
        for edit in edits:
            target=root/edit['path']
            if any((root/Path(*PurePosixPath(edit['path']).parts[:i])).is_symlink()
                   for i in range(1,len(PurePosixPath(edit['path']).parts)+1)):
                raise ValueError('Symlink edit rejected')
            before=target.read_text() if target.exists() else ''
            target.parent.mkdir(parents=True,exist_ok=True);target.write_text(edit['content'])
            patch.extend(difflib.unified_diff(before.splitlines(True),edit['content'].splitlines(True),
                         fromfile='a/'+edit['path'],tofile='b/'+edit['path']))
        # Remove Git metadata before exposing the worktree to untrusted build scripts.
        import shutil
        shutil.rmtree(root/'.git')
        steps=[]
        for command in commands:
            name='agentpair-job-'+uuid.uuid4().hex
            args=['docker','run','--rm','--name',name,'--network','none','--read-only',
                  '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128',
                  '--memory','512m','--cpus','1','--user',f'{os.getuid()}:{os.getgid()}',
                  '--ulimit','fsize=67108864:67108864','--log-driver','none',
                  '--tmpfs','/tmp:rw,nosuid,size=128m','-e','HOME=/tmp',
                  '--tmpfs','/workspace:rw,nosuid,size=256m,mode=1777',
                  '--mount',f'type=bind,src={root},dst=/input,readonly','-w','/workspace',image,
                  'sh','-c','cp -R /input/. /workspace/ && exec "$@"','sh',*command]
            try:
                raw=bytearray()
                process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
                def drain():
                    while True:
                        chunk=process.stdout.read(4096)
                        if not chunk:break
                        raw.extend(chunk[:max(0,32001-len(raw))])
                reader=threading.Thread(target=drain,daemon=True);reader.start()
                try: code=process.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15)
                    process.kill();process.wait();code=124
                reader.join(timeout=5)
                steps.append({'command':command,'exitCode':code,'log':raw[:32000].decode(errors='replace'),
                              'logTruncated':len(raw)>32000})
            finally:
                subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15)
            if code:break
        no_tests=profile=='python' and any('Ran 0 tests' in s['log'] for s in steps)
        return {'status':'passed' if len(steps)==len(commands) and all(s['exitCode']==0 for s in steps) and not no_tests else 'failed',
                'baseCommit':sha,'profile':profile,'steps':steps,'patch':''.join(patch),
                'modifiedFiles':[e['path'] for e in edits],
                'scope':'Configured commands only; passing is not proof of full acceptance. No GitHub push.',
                'isolation':'Dedicated cloud Driver + offline unprivileged container'}
