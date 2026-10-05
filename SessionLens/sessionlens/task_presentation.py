"""Local evidence projection: tool arguments, call-id links and reasoning frames."""
import json,re
from urllib.parse import urlparse,parse_qsl,unquote
from .supervision import event_text
from .desktop import category
from .task_lineage import task_table,step_table,resolve,history,execution_map
from .message_graph import build,reasoning_bindings

LABELS={'prompt':'生成提示词','resolution':'分辨率','aspect_ratio':'画面比例','enable_audio':'生成音频','output_dir':'保存目录','image':'输入图像','last_image':'结束画面','files':'交付文件','queries':'搜索内容','top_k':'候选数量','command':'执行命令','description':'用途','file_path':'文件路径','path':'路径','old_string':'修改前','new_string':'修改后'}

def arguments(event):
    p=event.get('payload',{});item=p.get('item',p)
    value=item.get('arguments',item.get('input',item.get('command',{}))) if isinstance(item,dict) else {}
    if isinstance(value,str):
        try:value=json.loads(value)
        except ValueError:return {'command':value}
    return value if isinstance(value,dict) else {'input':value}

def readable_fields(args):
    inner=args.get('params',args)
    if isinstance(inner,str):
        try:inner=json.loads(inner)
        except ValueError:inner={'params':inner}
    if not isinstance(inner,dict):inner={'params':inner}
    rows=[]
    for key,value in inner.items():
        if isinstance(value,bool):text='开启' if value else '关闭'
        elif isinstance(value,str):text=value
        else:text=json.dumps(value,ensure_ascii=False)
        rows.append({'label':LABELS.get(key,key),'key':key,'value':text})
    command=str(inner.get('command',''))
    for url in re.findall(r'https?://[^\s\"\'<>]+',command):
        parsed=urlparse(url);rows.append({'label':'本机地址' if parsed.hostname in ('localhost','127.0.0.1','::1') else '外部网址','key':'URL','value':url})
        for k,v in parse_qsl(parsed.query):rows.append({'label':{'lat':'纬度','latitude':'纬度','lon':'经度','longitude':'经度','days':'预报天数','forecast_days':'预报天数','name':'地点','timezone':'时区','current':'实况字段','daily':'每日预报字段'}.get(k,k),'key':k,'value':unquote(v)})
    return rows

def project(db,task_id):
    task_id=resolve(db,task_id);steps=step_table(db)
    task=db.execute('SELECT source,session,prompt,updated FROM '+task_table(db)+' WHERE id=?',(task_id,)).fetchone()
    if not task:raise ValueError('任务不存在')
    count=db.execute('SELECT count(*) FROM '+steps+' WHERE task=?',(task_id,)).fetchone()[0]
    events=[]
    requirements=history(db,task_id);bindings=execution_map(db,task_id)
    plans={}
    for identity in {x['planEvent'] for x in bindings.values() if x.get('planEvent')}:
        raw=db.execute('SELECT event FROM events WHERE id=?',(identity,)).fetchone()
        if raw:plans[identity]=event_text(json.loads(raw[0]))[:16000]
    selected=db.execute('SELECT s.seq,e.event FROM '+steps+' s JOIN events e ON e.id=s.event WHERE s.task=? ORDER BY s.seq LIMIT ?',(task_id,800 if count<=800 else 400)).fetchall()
    if count>800:selected+=db.execute('SELECT s.seq,e.event FROM '+steps+' s JOIN events e ON e.id=s.event WHERE s.task=? ORDER BY s.seq DESC LIMIT 400',(task_id,)).fetchall()[::-1]
    for seq,raw in selected:
        e=json.loads(raw);e['_seq']=seq;events.append(e)
    graph=build(events);decision_links=reasoning_bindings(graph)
    by_id={e['id']:e for e in events};nodes={n['eventId']:n for n in graph['nodes']}
    returns={}
    for edge in graph['edges']:
        if edge['relation']=='call_result':returns.setdefault(edge['from'],[]).append(by_id[edge['to']])
    calls=[];reasoning=[];frames=[]
    for i,e in enumerate(events):
        text=event_text(e)
        if category(e)=='解题思路' and text:
            reasoning.append({'id':e['id'],'text':text[:16000],'truncated':len(text)>16000,'timestamp':e.get('timestamp')})
            next_reason=None
            for x in events[i+1:]:
                if category(x)=='用户提问':break
                if category(x)=='解题思路':next_reason=x;break
            frames.append({'reasoning':reasoning[-1],'call':None,'callIds':[],'returnIds':[],'nextReason':next_reason['id'] if next_reason else None})
        if category(e)=='工具调用':
            args=arguments(e);name=e.get('name') or '未命名工具';inner=args.get('toolName');title=name+(' → '+str(inner) if inner else '')
            linked=returns.get(e['id'],[])
            decision=decision_links.get(e['id'])
            if not decision and not nodes[e['id']]['parentId']:
                # Missing source relationships permit a clearly labelled order
                # candidate, never jumping over a new user or reasoning turn.
                for previous in reversed(events[:i]):
                    if category(previous)=='用户提问':break
                    if category(previous)=='解题思路':
                        if event_text(previous):decision={'reasoningEvent':previous['id'],'basis':'sequence_candidate','path':[previous['id'],e['id']]}
                        break
            calls.append({'id':e['id'],'name':title,'callId':e.get('callId'),'fields':readable_fields(args),'arguments':args,'returns':[{'id':x['id'],'text':event_text(x)[:16000],'truncated':len(event_text(x))>16000} for x in linked],'timestamp':e.get('timestamp'),'requirement':bindings.get(e['id']),'decisionLink':decision})
    for frame in frames:
        related=[c for c in calls if c.get('decisionLink') and c['decisionLink']['reasoningEvent']==frame['reasoning']['id']]
        frame['callIds']=[c['id'] for c in related];frame['call']=frame['callIds'][0] if related else None
        frame['returnIds']=[r['id'] for c in related for r in c['returns']]
    finals=[{'id':e['id'],'text':event_text(e)[:16000]} for e in events if category(e)=='Agent 回复']
    context=[{'id':e['id'],'kind':category(e),'text':event_text(e)[:12000]} for e in events if category(e) in ('用户提问','会话背景')]
    return {'taskId':task_id,'source':task[0],'session':task[1],'prompt':task[2],'updated':task[3],'calls':calls,'reasoning':reasoning,'frames':frames,'replies':finals,'context':context,'total':count,'included':len(events),'requirements':requirements,'plans':plans,'messageGraph':graph}
