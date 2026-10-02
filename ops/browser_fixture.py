"""Offline controlled site and WebLens/Lightpanda processes for cloud smoke."""
import http.server
import subprocess
import threading
import time
from pathlib import Path

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'''<!doctype html><title>AgentPair Browser Trial</title>
<h1>Driver test page</h1><button id="expand" style="width:200px;height:60px" onclick="document.getElementById('result').textContent='CLICK_VERIFIED_20260930'">Expand details</button>
<p id="result">WAITING_FOR_CLICK</p><div style="height:2000px">Scroll test</div>'''
        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass

threading.Thread(target=http.server.HTTPServer(('127.0.0.1',8090),Handler).serve_forever,daemon=True).start()
Path('/tmp/data').mkdir(exist_ok=True)
children=[]
for command in [
    ['/tools/lightpanda','serve','--host','127.0.0.1','--port','9222'],
    ['/tools/weblens','-listen','127.0.0.1:8081','-lp','127.0.0.1:9222',
     '-data','/tmp/data','-map-data','/tmp/data','-geo','/tmp/geo.json','-reports','/tmp/reports']]:
    children.append(subprocess.Popen(command))
while all(p.poll() is None for p in children):time.sleep(1)
raise RuntimeError('Browser component exited')
