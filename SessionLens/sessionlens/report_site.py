"""Local session insight UI from actual AgentPair outputs."""
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote
from .ui import build_pages
ROOT = Path(__file__).resolve().parents[1] / 'runtime/reports'
def build():
    ROOT.mkdir(parents=True, exist_ok=True)
    build_pages(ROOT)
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(ROOT),**kwargs)
    def allowed(self):
        path=unquote(urlparse(self.path).path)
        if path in ('/','/index.html','/sessions.html'):return True
        parts=path.strip('/').split('/')
        return len(parts)==2 and parts[0] not in ('.','..') and parts[1]=='report.html' and (ROOT/parts[0]/'analysis.json').is_file()
    def do_GET(self):
        if not self.allowed():return self.send_error(404)
        build()
        super().do_GET()
    def do_HEAD(self):
        if not self.allowed():return self.send_error(404)
        build()
        super().do_HEAD()
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        super().end_headers()
if __name__=='__main__':
    build()
    ThreadingHTTPServer(('127.0.0.1',18951),Handler).serve_forever()
