"""Native dedicated-VM tools. Workspace checks are not an OS sandbox.

The shell must only be enabled on disposable Driver VMs, never Navigator.
"""
import os
from pathlib import Path
import signal
import subprocess
import threading


class Registry:
    def __init__(self): self.tools={}
    def register(self,name,description,schema,handler,health):
        if name in self.tools: raise ValueError('Duplicate tool')
        self.tools[name]=(description,schema,handler,health)
    def manifest(self):
        result=[]
        for name,(description,schema,handler,health) in self.tools.items():
            try: ready=bool(health())
            except Exception: ready=False
            result.append(dict(name=name,description=description,inputSchema=schema,ready=ready))
        return result
    def call(self,name,args):
        if name not in self.tools: raise ValueError('Unknown tool')
        _,schema,handler,health=self.tools[name]
        if not health(): raise RuntimeError('Tool unavailable')
        if not isinstance(args,dict) or set(args)-set(schema['properties']):raise ValueError('Invalid arguments')
        if any(k not in args for k in schema.get('required',[])):raise ValueError('Missing arguments')
        for key,value in args.items():
            if schema['properties'][key]['type']=='string' and not isinstance(value,str):raise ValueError('Expected string')
        return handler(**args)


def schema(*fields):
    return {'type':'object','properties':{f:{'type':'string'} for f in fields},'required':list(fields),'additionalProperties':False}


def native_tools(root, enable_shell=False):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    registry=Registry()
    def path(value):
        p=(root/value).resolve()
        if not p.is_relative_to(root):raise ValueError('Path outside task workspace')
        return p
    def read(pathname):
        with path(pathname).open('rb') as stream:raw=stream.read(32001)
        return {'text':raw[:32000].decode(errors='replace'),'truncated':len(raw)>32000}
    def write(pathname,content):
        if len(content.encode())>150000:raise ValueError('File too large')
        p=path(pathname);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
        return {'written':pathname,'bytes':len(content.encode())}
    def listing(pathname):
        return {'entries':sorted(p.name for p in path(pathname).iterdir())[:300]}
    registry.register('files.read','Read a workspace file',schema('pathname'),read,lambda:True)
    registry.register('files.write','Create or replace a workspace file',schema('pathname','content'),write,lambda:True)
    registry.register('files.list','List a workspace directory',schema('pathname'),listing,lambda:True)
    if enable_shell:
        def shell(command):
            if len(command)>8000:raise ValueError('Command too long')
            # No inherited API/cloud credentials. This is native VM execution, not confinement.
            env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(root),'LANG':'C.UTF-8'}
            process=subprocess.Popen(['/bin/bash','-lc',command],cwd=root,env=env,
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
            output=bytearray()
            def drain():
                while True:
                    chunk=process.stdout.read(4096)
                    if not chunk:break
                    output.extend(chunk[:max(0,32001-len(output))])
            reader=threading.Thread(target=drain,daemon=True);reader.start()
            timed_out=False
            try:process.wait(timeout=90)
            except subprocess.TimeoutExpired:timed_out=True
            finally:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait();reader.join(timeout=3)
                process.stdout.close()
            return {'exitCode':process.returncode,'timedOut':timed_out,
                    'output':output[:32000].decode(errors='replace'),'truncated':len(output)>32000}
        registry.register('terminal.run','Run a native Linux command; includes git, build and test. Network enabled.',schema('command'),shell,lambda:Path('/bin/bash').exists())
    return registry
