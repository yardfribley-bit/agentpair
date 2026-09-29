"""Read-only Navigator dashboard for allowlisted public website experiments."""
import argparse
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ASSETS = Path(__file__).parent / 'web_assets'


def project_run(directory):
    """Never expose arbitrary artifacts, credentials or enterprise audit context."""
    def read(name):
        path = directory / name
        if not path.exists():
            return {}
        if path.stat().st_size > 2_000_000:
            raise ValueError('Artifact too large')
        return json.loads(path.read_text(encoding='utf-8'))
    manifest = read('manifest.json')
    if manifest.get('target') != 'http://102.68.79.149/':
        raise ValueError('Dashboard only supports this public experiment')
    plan, driver, review = read('plan.json'), read('driver.json'), read('review.json')
    def findings(answer):
        return [{k: f.get(k) for k in ('topic', 'claim', 'confidence', 'evidenceRefs', 'reason')}
                for f in answer.get('findings', []) if isinstance(f, dict)]
    evidence = [{k: e.get(k) for k in ('ref', 'kind', 'url', 'status', 'headers', 'title',
                'markers', 'bodySHA256', 'truncated', 'country', 'startAddress', 'endAddress',
                'registryURL', 'note', 'errorType') if k in e}
                for e in driver.get('evidence', {}).get('evidence', [])]
    stages = []
    for stage, role, name, artifact in [('plan', 'Navigator', 'plan.json', plan),
            ('driver', 'Driver', 'driver.json', driver), ('review', 'Navigator', 'review.json', review)]:
        usage = artifact.get('analysis', artifact).get('usage', {}) or {}
        stages.append({'id': stage, 'role': role, 'complete': bool(artifact),
                       'artifactWrittenAt': (directory/name).stat().st_mtime if artifact else None,
                       'tokens': usage.get('total_tokens', 0),
                       'model': artifact.get('analysis', artifact).get('returnedModel')})
    return {'runID': manifest.get('runID'), 'target': manifest['target'],
            'status': manifest.get('status'), 'retainUntilUTC': manifest.get('retainUntilUTC'),
            'hourlyQuoteCNY': manifest.get('hourlyQuoteCNY'),
            'estimatedModelUpperCostCNY': manifest.get('estimatedModelUpperCostCNY'),
            'hosts': [{k: h.get(k) for k in ('role', 'ip', 'deleted')} for h in manifest.get('hosts', [])],
            'stages': stages, 'plan': {k: plan.get('answer', {}).get(k) for k in ('plans', 'selectedPlanID', 'reason')},
            'evidence': evidence, 'driverFindings': findings(driver.get('analysis', {}).get('answer', {})),
            'reviewFindings': findings(review.get('answer', {})),
            'corrections': review.get('answer', {}).get('corrections', []),
            'limitations': driver.get('evidence', {}).get('limitations', []),
            'humanReview': 'RouterOS 仅为页面标题指纹，模型将真实后端置信度标为 high 仍属过度推断。IP 注册国家不等于物理位置。',
            'mode': 'public_site_fixed_tools', 'viewMode': 'artifact_polling'}


def handler_for(directory, snapshot=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if self.path == '/api/run':
                    if snapshot:
                        data = json.loads(snapshot.read_text(encoding='utf-8'))
                        if data.get('target') != 'http://102.68.79.149/':
                            raise ValueError('Invalid snapshot scope')
                        data['viewMode'] = 'uploaded_snapshot'
                    else:
                        data = project_run(directory)
                    body = json.dumps(data, ensure_ascii=False).encode()
                    mime = 'application/json; charset=utf-8'
                elif self.path in ('/', '/app.js', '/style.css'):
                    name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[self.path]
                    body = (ASSETS / name).read_bytes()
                    mime = {'index.html': 'text/html', 'app.js': 'application/javascript', 'style.css': 'text/css'}[name]+'; charset=utf-8'
                else:
                    self.send_error(404); return
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers(); self.wfile.write(body)
            except (ValueError, OSError, TypeError):
                self.send_error(503, 'Run data unavailable')
        def log_message(self, *_):
            pass
    return Handler


def main():
    p = argparse.ArgumentParser()
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--run-dir', type=Path)
    source.add_argument('--snapshot', type=Path)
    p.add_argument('--bind', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8080)
    args = p.parse_args()
    if args.run_dir:
        project_run(args.run_dir)
    server = ThreadingHTTPServer((args.bind, args.port), handler_for(args.run_dir, args.snapshot))
    print('Navigator dashboard ready', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
