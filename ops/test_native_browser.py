"""Run real PinchTab -> Lightpanda navigation and DOM interaction checks."""
import http.server
import json
from pathlib import Path
import threading
import urllib.request
import urllib.error

class Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'''<!doctype html><title>AgentPair browser acceptance</title>
<h1>Browser acceptance</h1><button id="expand" onclick="document.getElementById('result').textContent='CLICK_VERIFIED'">Expand details</button><p id="result">WAITING_FOR_CLICK</p>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass

def main():
    server = http.server.HTTPServer(('127.0.0.1', 8091), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    token = json.loads(Path('/home/pair/.pinchtab/config.json').read_text())['server']['token']
    def call(path, data=None):
        req = urllib.request.Request('http://127.0.0.1:9868'+path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                result=json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(str(exc.code)+' '+exc.read().decode()) from exc
        print(path, json.dumps(result), flush=True)
        return result
    nav = call('/navigate', {'url':'http://127.0.0.1:8091/'})
    call('/snapshot')
    before = call('/text')
    assert 'WAITING_FOR_CLICK' in json.dumps(before)
    call('/action', {'kind':'click', 'selector':'#expand'})
    after = call('/text')
    assert 'CLICK_VERIFIED' in json.dumps(after)
    call('/navigate', {'url':'https://example.com', 'tabId':nav['tabId']})
    public = call('/text')
    assert 'Example Domain' in json.dumps(public)
    print('BROWSER_ACCEPTANCE_PASSED', flush=True)
    server.shutdown()

if __name__ == '__main__':
    main()
