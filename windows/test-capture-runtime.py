"""Windows packaged runtime: real TLS proxy, full body, pause gate, no internet target."""
import base64,hashlib,http.client,http.server,json,os,pathlib,socket,ssl,subprocess,tempfile,threading,time
ROOT=pathlib.Path(__file__).resolve().parents[1]
class Endpoint(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200);self.end_headers();self.wfile.write(b'{}')
    def log_message(self,*args):pass
with tempfile.TemporaryDirectory(prefix='applens-wire-') as temp:
    folder=pathlib.Path(temp);server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Endpoint)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    # Route the synthetic target locally after the production capture addon runs.
    redirect=folder/'fixture.py';redirect.write_text('def request(flow):\n    flow.request.scheme="http"\n    flow.request.host="127.0.0.1"\n    flow.request.port='+str(server.server_port)+'\n')
    capture=folder/'network.jsonl';gate=folder/'enabled';gate.write_text('enabled')
    env={**os.environ,'APPLENS_WORKBUDDY_NETWORK_JSONL':str(capture),'APPLENS_CAPTURE_ENABLED_FILE':str(gate)}
    binary=ROOT/'windows/build/capture/mitmdump.exe'
    with (folder/'proxy.log').open('wb') as log:
        process=subprocess.Popen([str(binary),'--listen-host','127.0.0.1','--listen-port',str(port),'--set','connection_strategy=lazy','--set','upstream_cert=false','--set','confdir='+str(folder/'ca'),'-s',str(ROOT/'macos/workbuddy_network_capture.py'),'-s',str(redirect)],env=env,stdout=log,stderr=log)
        try:
            ca=folder/'ca/mitmproxy-ca-cert.pem'
            for _ in range(120):
                if process.poll() is not None:raise AssertionError('Proxy startup failed')
                with socket.socket() as probe:
                    if ca.exists() and probe.connect_ex(('127.0.0.1',port))==0:break
                time.sleep(.5)
            else:raise AssertionError('Proxy not ready')
            body=json.dumps({'model':'fixture-model','messages':[{'role':'system','content':'S'*150000},{'role':'user','content':'END_MARKER'}]}).encode()
            def send():
                conn=http.client.HTTPSConnection('127.0.0.1',port,context=ssl.create_default_context(cafile=str(ca)),timeout=20)
                conn.set_tunnel('copilot.tencent.com',443)
                conn.request('POST','/v2/chat/completions',body,{'Content-Type':'application/json','Authorization':'Bearer fixture-not-to-be-stored'})
                response=conn.getresponse();assert response.status==200;response.read();conn.close()
            send();rows=[json.loads(line) for line in capture.read_text().splitlines()]
            assert len(rows)==1
            r=rows[0];assert base64.b64decode(r['requestBodyBase64'])==body
            assert r['requestSHA256']==hashlib.sha256(body).hexdigest()
            assert r['declaredContentLength']==r['capturedWireBodyBytes']==len(body)>100000
            assert 'fixture-not-to-be-stored' not in capture.read_text()
            gate.unlink();send();assert len(capture.read_text().splitlines())==1
        finally:
            # PyInstaller one-file binaries spawn a child; terminating only the
            # bootstrap PID leaves that child holding the Windows log handle.
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
            else:process.terminate()
            process.wait(timeout=20);server.shutdown();server.server_close()
print('PASS: packaged Windows proxy, verified TLS, complete >150KB body, hash/length, no headers, pause gate, process cleanup')
