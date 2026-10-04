"""Local evidence viewer. Raw content requires the per-start capability token."""
import json
import secrets
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

PAGE = '''<!doctype html><meta charset="utf-8"><title>AgentReins v2 · 采集证据</title>
<style>body{font:15px system-ui;margin:0;background:#eef2f6;color:#192638}header{background:#14263b;color:white;padding:24px}main{padding:24px;max-width:1250px;margin:auto}article{background:white;border:1px solid #ccd6e2;padding:18px;margin:12px 0;border-left:4px solid #2473b8}small{color:#62748b}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto}button,select{padding:8px}#status{margin:16px 0}.notice{background:#fff4db;padding:12px}</style>
<header style="display:flex;align-items:center;gap:18px"><img src="/logo.png" alt="AgentReins" style="width:52px;height:52px;object-fit:contain"><div><b>AgentReins</b><br><small style="color:#b9c9dc">v2 · 本机采集证据</small></div></header><main>
<div class="notice">这里显示实际采集记录。请求中的工具结果证明它被带回模型；进程采样是采样时刻的状态。没有采集到的执行不能自动补成事实。</div>
<div id="status"></div><select id="task"><option value="">全部记录</option></select><div id="items"></div></main>
<script>
const token=location.hash.slice(1)||sessionStorage.getItem('agentreinsToken')||''; sessionStorage.setItem('agentreinsToken',token); history.replaceState(null,'',location.pathname);
let after=0, records=[]; const task=document.getElementById("task"); const labels={'task.query':'用户任务','model.request':'Agent → 模型请求','model.response':'模型 → Agent 响应','tool.call':'模型指示调用工具','tool.result':'工具结果 → 后续模型输入','tool.execution':'工具执行记录','trace.state':'执行记录状态','process.snapshot':'进程与沙箱采样','finding.tool_failure':'工具内部失败','coverage.gap':'采集缺口','coverage.change':'采集源变化','file.snapshot':'文件状态采样','network.sample':'网络采样覆盖','network.connection':'进程网络连接'};
async function api(path){const r=await fetch(path,{headers:{'X-AgentReins-Token':token}});if(!r.ok)throw Error('读取失败 '+r.status);return r.json()}
function el(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n}
function render(){const root=document.getElementById('items');root.replaceChildren();for(const r of records.filter(r=>!task.value||r.task_id===task.value).slice(-200)){const a=el('article');a.append(el('b',labels[r.kind]||r.kind),el('p',r.query||r.name||r.destination||r.remote_endpoint||r.local_endpoint||r.reason||r.status||''),el('small',new Date(r.observed_at*1000).toLocaleString()+' · '+(r.session_id||r.task_id||'未关联任务')));const meta={...r};delete meta.content_ref;a.append(el('pre',JSON.stringify(meta,null,2)));if(r.content_ref){const b=el('button','查看本条原始内容');b.onclick=async()=>{b.disabled=true;try{const v=await api('/api/content?sha='+r.content_ref.sha256);a.append(el('pre',typeof v==='string'?v:JSON.stringify(v,null,2)))}catch(e){a.append(el('p',e.message))} };a.append(b)}root.append(a)}}
task.onchange=render;
async function tick(){try{const s=await api('/api/status');document.getElementById('status').textContent=`已持久化 ${s.events} 条记录 · 内容 ${(s.content_bytes/1048576).toFixed(2)} MiB · 最新序号 ${s.last_seq}`;let batch;do{batch=await api('/api/events?after='+after);for(const r of batch){records.push(r);after=r.seq;if(r.task_id&&!Array.from(task.options).some(o=>o.value===r.task_id)){const o=el('option',(r.query||r.task_id).slice(0,100));o.value=r.task_id;task.append(o)}}}while(batch.length===300);if(batch.length||after!==window.renderedAfter){render();window.renderedAfter=after}}catch(e){document.getElementById('status').textContent=e.message}setTimeout(tick,2000)}tick();
</script>'''


def serve(store, port=0):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            parsed = urlsplit(self.path)
            host = self.headers.get('Host', '')
            if host != '127.0.0.1:'+str(self.server.server_port):
                self.send_error(403)
                return
            if parsed.path == '/logo.png':
                raw, mime = (Path(__file__).parent/'assets/agentreins-logo.png').read_bytes(), 'image/png'
            elif parsed.path == '/':
                raw, mime = PAGE.encode(), 'text/html; charset=utf-8'
            else:
                if not secrets.compare_digest(self.headers.get('X-AgentReins-Token', ''), token):
                    self.send_error(403)
                    return
                params = parse_qs(parsed.query)
                try:
                    if parsed.path == '/api/status':
                        value = store.status()
                    elif parsed.path == '/api/events':
                        value = store.events(int(params.get('after', ['0'])[0]))
                    elif parsed.path == '/api/content':
                        value = store.content(params.get('sha', [''])[0])
                    else:
                        self.send_error(404)
                        return
                    raw, mime = json.dumps(value, ensure_ascii=False).encode(), 'application/json'
                except (ValueError, OSError):
                    self.send_error(400)
                    return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print('http://127.0.0.1:'+str(server.server_port)+'/#'+token, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
