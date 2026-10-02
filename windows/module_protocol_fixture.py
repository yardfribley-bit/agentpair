"""Loopback-only server using the production device store for Windows CI."""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentpair.devices import DeviceStore

p = argparse.ArgumentParser()
p.add_argument('--directory', required=True)
p.add_argument('--pid', required=True, type=int)
p.add_argument('--started-at', required=True)
args = p.parse_args()
root = Path(args.directory)
store = DeviceStore(root / 'devices.db')
identity = store.enroll(store.pairing()['code'], 'CI Windows')
store.report(identity['token'], {'os':'Windows','osVersion':'CI-Windows2022',
    'architecture':'AMD64','hostRuntimeVersion':'5.1','processes':[],'applications':[]})
tasks = [store.dispatch('admin', identity['deviceId'], {
    'goal': 'Protocol acceptance', 'action': 'run_module', 'moduleId': module,
    'parameters': {'pid': args.pid, 'startedAt': args.started_at}})['taskId']
    for module in ('process_details', 'process_tcp')]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, status, body):
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer ' + identity['token']:
            return self.reply(401, {})
        if self.path == '/api/endpoint/tasks':
            return self.reply(200, {'task': store.pull(identity['token'])})
        if self.path == '/assert':
            items = [store.task('admin', task) for task in tasks]
            ok = all(x['state'] == 'completed' and x['result']['evidence']['output']['target']['pid'] == args.pid for x in items)
            return self.reply(200 if ok else 500, {'passed': ok, 'states': [x['state'] for x in items]})
        if self.path == '/reuse':
            for module in ('process_details','process_tcp'):
                queued=store.dispatch('admin',identity['deviceId'],{'goal':'Replay verified collection',
                    'action':'run_module','moduleId':module,'parameters':{'pid':args.pid,'startedAt':args.started_at}})
                item=store.task('admin',queued['taskId'])
                if not item['payload'].get('experienceRef'): return self.reply(500,{'error':'Missing experience'})
                tasks.append(queued['taskId'])
            return self.reply(200,{'accepted':True})
        if self.path == '/experiences':
            items=store.experiences.list('admin')
            passed=len(items)==2 and all(x['successfulRuns']==2 for x in items)
            return self.reply(200 if passed else 500,{'passed':passed})
        self.reply(404, {})
    def do_POST(self):
        if self.path != '/api/endpoint/tasks/result': return self.reply(404, {})
        if self.headers.get('Authorization') != 'Bearer ' + identity['token']:
            return self.reply(401, {})
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        try:
            self.reply(200, store.complete(identity['token'], data['taskId'], data['lease'], data['result']))
        except (ValueError, PermissionError): self.reply(400, {'error': 'Rejected result'})


server = HTTPServer(('127.0.0.1', 0), Handler)
(root / 'ready.json').write_text(json.dumps({'server': 'http://127.0.0.1:' + str(server.server_port), 'identity': identity}))
server.serve_forever()
