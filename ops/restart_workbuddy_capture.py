"""User-authorized controlled restart; no global proxy/trust changes."""
import os,socket,subprocess,time,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    port=18893
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',port))==0:raise RuntimeError('Capture port already occupied; refusing to adopt an unknown listener')
    storage=Path.home()/'Library/Application Support/AppLens/telemetry';storage.mkdir(parents=True,exist_ok=True,mode=0o700)
    ca=Path.home()/'.mitmproxy/mitmproxy-ca-cert.pem'
    if not ca.exists():raise RuntimeError('Per-process CA missing')
    environment=os.environ.copy();environment['APPLENS_WORKBUDDY_NETWORK_JSONL']=str(storage/'workbuddy-network.jsonl')
    log=(storage/'proxy.log').open('ab');os.chmod(storage/'proxy.log',0o600)
    proxy=subprocess.Popen(['/usr/local/bin/mitmdump','--listen-host','127.0.0.1','--listen-port',str(port),'--set','connection_strategy=lazy','--scripts',str(ROOT/'macos/workbuddy_network_capture.py')],env=environment,stdout=log,stderr=log,start_new_session=True)
    for _ in range(50):
        if proxy.poll() is not None:raise RuntimeError('Audit proxy failed')
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',port))==0:break
        time.sleep(.1)
    else:proxy.terminate();raise RuntimeError('Audit proxy not ready')
    # WorkBuddy rewrites argv/process titles; match executable paths, not argv text.
    def targets():
        text=subprocess.check_output(['ps','-axo','uid=,pid=,ppid=,comm='],text=True)
        return [int(parts[1]) for line in text.splitlines() if len(parts:=line.strip().split(None,3))==4 and int(parts[0])==os.getuid() and parts[3]=='/Applications/WorkBuddy.app/Contents/MacOS/Electron']
    for pid in targets():
        try:os.kill(pid,15)
        except ProcessLookupError:pass
    for _ in range(50):
        if not targets():break
        time.sleep(.1)
    else:proxy.terminate();raise RuntimeError('WorkBuddy has not exited; no force-kill performed')
    environment.pop('APPLENS_WORKBUDDY_NETWORK_JSONL',None)
    environment.update({'HTTPS_PROXY':f'http://127.0.0.1:{port}','HTTP_PROXY':f'http://127.0.0.1:{port}','NODE_EXTRA_CA_CERTS':str(ca)})
    # WorkBuddy 5.6.x explicitly sets Electron to DIRECT if http.proxy is absent,
    # overriding Chromium command-line flags. Preserve all unrelated settings.
    settings=Path.home()/'.workbuddy/settings.json'
    data=json.loads(settings.read_text()) if settings.exists() else {}
    backup=storage/'workbuddy-proxy-settings-original.json'
    if not backup.exists():
        original={k:{'present':k in data,'value':data.get(k)} for k in ('http.proxy','http.proxySupport')}
        with backup.open('x') as stream:json.dump(original,stream)
        os.chmod(backup,0o600)
    if settings.exists():
        snapshot=storage/f'workbuddy-settings-backup-{time.time_ns()}.json'
        shutil.copy2(settings,snapshot);os.chmod(snapshot,0o600)
    data['http.proxy']=f'http://127.0.0.1:{port}'
    data.pop('http.proxySupport',None)
    temporary=settings.with_suffix('.applens-tmp')
    with temporary.open('w') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
    os.chmod(temporary,0o600);os.replace(temporary,settings)
    # Do not disable TLS validation, change system trust, or proxy other applications.
    # Electron's resolveProxy/PAC result has precedence over the CLI environment.
    # Configure this application only so its workers receive the audit proxy too.
    app=subprocess.Popen(['/Applications/WorkBuddy.app/Contents/MacOS/Electron',
                          f'--proxy-server=http://127.0.0.1:{port}',
                          '--proxy-bypass-list=localhost;127.0.0.1;[::1]'],
                         env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    time.sleep(2)
    if app.poll() is not None:proxy.terminate();raise RuntimeError('New WorkBuddy instance exited; restart not verified')
    print(f'WorkBuddy restart verified (PID {app.pid}); owned audit proxy PID {proxy.pid}, loopback port {port}. System proxy and trust unchanged.')
if __name__=='__main__':main()
