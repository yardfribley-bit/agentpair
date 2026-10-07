"""Run the real platform UI against isolated, clearly labelled test fixtures.

No production stores, model API, endpoint collector or cloud provider is used.
The preview binds to loopback, keeps all state in a temporary directory and
allows exercising login, platform queries, devices and original evidence.
"""
import argparse
import hashlib
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'SessionLens'))

from agentpair.devices import DeviceStore
from agentpair.platform import handler_for
from agentpair.session_lens import SessionStore
from agentpair.tasks import TaskEngine


class PreviewBackend:
    """Deterministic planning only; task execution never leaves this machine."""
    def estimate(self, envelope):
        return .001

    def call(self, role, envelope, timeout):
        if envelope['mode'] == 'plan':
            return {'answer': {'summary': '本地界面验收：读取隔离测试数据',
                               'tool': {'name': 'none'}}, 'usage': {}}
        return {'answer': {'summary': '这是隔离验收环境，没有连接模型或执行外部任务。',
                           'finalAnswer': '这是隔离验收环境，没有连接模型或执行外部任务。',
                           'verdict': 'pass'}, 'usage': {}}


def fixtures(engine, root):
    devices = DeviceStore(root / 'devices.db')
    sessions = SessionStore(root / 'session-lens.db')
    evidence = []
    for platform, name, source in (
        ('macOS', '验收设备 · Mac Intel', 'workbuddy'),
        ('Windows', '验收设备 · Windows', 'codex'),
    ):
        identity = devices.enroll(devices.pairing()['code'], name)
        devices.report(identity['token'], {'os': platform,
            'architecture': 'x86_64', 'processes': [], 'applications': [
                {'name': 'WorkBuddy' if source == 'workbuddy' else 'Codex',
                 'version': 'fixture', 'publisher': 'UI acceptance', 'processNames': []}]})
        goal = '界面验收：整理库存盘点摘要'
        body = json.dumps({'messages': [
            {'role': 'system', 'content': '仓库为界面验收用的虚构项目 orchard。\n'
             '安全发现验收用虚构云主机登录：192.0.2.18 root/Jz8kR4n7Yp2!'},
            {'role': 'user', 'content': '<user_query>' + goal + '</user_query>'},
            {'role': 'tool', 'content': '物料,数量\n支架,12\n螺母,34'},
        ]}, ensure_ascii=False)
        request_id = hashlib.sha256((platform + body).encode()).hexdigest()
        devices.ingest_model_context(identity['token'], {'requests': [{
            'id': request_id, 'source': 'workbuddy_network_context',
            'body': body, 'timestamp': time.time(), 'destination': 'https://model.example.invalid/v1/chat/completions',
        }]})
        session = 'fixture-inventory-' + source
        rows = [('message', 'user', {'content': goal}),
                ('tool_call', None, {'name': 'Read', 'arguments': {'file_path': '/fixture/orchard/inventory.csv'}}),
                ('tool_result', None, {'output': '物料,数量\n支架,12\n螺母,34'}),
                ('message', 'assistant', {'content': '已整理盘点摘要：支架12件、螺母34件。'})]
        events = []
        for i, (kind, role, payload) in enumerate(rows):
            event = {'schemaVersion': 1, 'id': hashlib.sha256((session + str(i)).encode()).hexdigest(),
                'source': source, 'sessionId': session, 'kind': kind, 'role': role,
                'timestamp': time.time() - 20 + i, 'payload': payload,
                'evidence': {'path': '/fixture/' + session + '.jsonl', 'fileIdentity': session,
                             'epoch': 0, 'byteStart': i * 100, 'byteEnd': (i + 1) * 100}}
            if kind in ('tool_call', 'tool_result'):
                event['callId'] = session + '-read'
            if kind == 'tool_call':
                event['name'] = 'Read'
            events.append(event)
        sessions.ingest({'id': identity['deviceId'], 'owner': 'admin'}, {'schemaVersion': 1, 'events': events})
        evidence.extend(events)
    task = engine.create('界面验收 · 查询设备', '查看我的设备列表', owner='admin')
    engine.process(task['id'])
    from sessionlens.ui import publish
    folder = root / 'insights'
    folder.mkdir()
    packet = {'sessionId': 'fixture-inventory', 'recordCount': len(evidence),
        'start': '2026-10-07T02:00:00Z', 'end': '2026-10-07T02:01:00Z',
        'recordKinds': {'message': 4, 'tool_call': 2, 'tool_result': 2},
        'limitations': ['本页面使用隔离验收数据。没有连接模型，没有分析真实用户会话。'],
        'fragments': [{'evidenceId': 'E' + str(i + 1).zfill(3), 'eventId': event['id'],
                       'kind': event['kind'], 'tool': event.get('name'), 'source': event['evidence'],
                       'truncated': False, 'excerpt': json.dumps(event['payload'], ensure_ascii=False)}
                      for i, event in enumerate(evidence)]}
    report = {'goal': '界面验收：整理库存盘点摘要', 'title': '库存盘点摘要 · 隔离验收数据',
        'summary': '读取库存清单并整理摘要。此页面用于核对展示，不是模型生成的分析结果。',
        'outcome': '已生成摘要；没有独立核验原始清单。', 'findings': [],
        'story': [{'title': '读取清单', 'action': 'Read 读取 inventory.csv',
                   'result': '支架12件，螺母34件', 'evidenceRefs': ['E002', 'E003']},
                  {'title': '整理摘要', 'action': '依据工具返回整理数量',
                   'result': '交付库存摘要', 'evidenceRefs': ['E004']}],
        'goodPractices': [], 'limitations': packet['limitations']}
    analysis = {'status': 'completed', 'messages': [{'stage': 'review', 'answer': {'report': report}}]}
    session_folder = folder / 'fixture-inventory'
    session_folder.mkdir()
    (session_folder / 'evidence.json').write_text(json.dumps(packet, ensure_ascii=False), encoding='utf-8')
    (session_folder / 'analysis.json').write_text(json.dumps(analysis, ensure_ascii=False), encoding='utf-8')
    publish(folder)
    return task['id']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18956)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='agentpair-unified-ui-') as temporary:
        root = Path(temporary)
        origin = 'http://127.0.0.1:' + str(args.port)
        engine = TaskEngine(root / 'tasks.db', PreviewBackend(), budget=None, start=False)
        base = handler_for(engine, 'fixture-password-only', origin)
        task = fixtures(engine, root)
        engine.thread = threading.Thread(target=engine._loop, daemon=True)
        engine.thread.start()

        class PreviewHandler(base):
            def do_GET(self):
                path = unquote(urlsplit(self.path).path)
                if path.startswith('/session-insights/'):
                    base_folder = (root / 'insights').resolve()
                    relative = path.removeprefix('/session-insights/') or 'index.html'
                    page = (base_folder / relative).resolve()
                    if not page.is_relative_to(base_folder) or page.suffix != '.html' or not page.is_file():
                        self.respond(404, {'error': 'No fixture page at this path'})
                        return
                    body = page.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.headers_common(len(body))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                super().do_GET()

        server = ThreadingHTTPServer(('127.0.0.1', args.port), PreviewHandler)
        print(json.dumps({'url': origin, 'task': task,
                          'fixtureLogin': {'username': 'admin', 'password': 'fixture-password-only'},
                          'scope': 'isolated fixtures, no real data, no model or cloud'}, ensure_ascii=False), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            engine.close()


if __name__ == '__main__':
    main()
