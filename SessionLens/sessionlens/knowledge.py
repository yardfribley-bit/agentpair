"""Question-driven local retrieval, with model query understanding and cited answers."""
import json,sqlite3
from pathlib import Path
from .assistant import packet_for_task,request_json,run

QUESTION_FACETS={'需求','请求','网址','地址','内容','结果','记录','返回','使用','步骤','成功','原因','时间','模型','参数','思路','过程','具体','发生','什么','哪个','多少','当时','最后','调用','工具','分析','情况','方法','怎么','如何','解决','问题','任务','之前','查找','查询','失败','解决方法','上下文','思维链','推理','理由','命令','文件','路径','输入','输出','回答','回复'}

def query_anchors(question,generic=None):
    import re
    generic=generic or {'workbuddy','codex','问题','解决','任务','之前','查找','工具','怎么','如何','查询','失败','解决方法'}
    anchors=re.findall(r'[a-zA-Z][a-zA-Z0-9_-]{1,}',question)
    # Split question phrases before making bigrams. Filtering afterwards leaves
    # artificial cross-word fragments such as “体请” in “具体请求”.
    filler=QUESTION_FACETS|{'它','这个','那个','这次','那次','什么','哪些','的','了','吗','呢'}
    separator='|'.join(re.escape(word) for word in sorted(filler,key=len,reverse=True))
    for phrase in re.findall(r'[\u4e00-\u9fff]+',question):
        for part in re.split(separator,phrase):anchors.extend(part[i:i+2] for i in range(len(part)-1))
    return list(dict.fromkeys(x.lower() for x in anchors if x.lower() not in generic|QUESTION_FACETS and not (len(x)==2 and any(c in x for c in '的了吗呢怎么哪些')) and not x.startswith(('查','请','帮','我')) and x not in ('那次','成了','是怎','的最','然后','哪些','用了','这次')))[:30]

def retrieve_candidates(db,plan,question,source=None,since=None):
    # Search the actual question as well as the rewrite. A rewrite carrying over
    # the previous topic must not prevent the newly named task being retrieved.
    direct=candidates(db,query_anchors(question),source=source,since=since,question=question)
    rewritten=candidates(db,plan.get('terms',[]),source=source,since=since,question=question)
    rows={row[0]:row for row in rewritten}
    rows.update({row[0]:row for row in direct})
    return sorted(rows.values(),key=lambda r:(-r[6],len(r[1]),-r[5]))[:6]

def answer_mismatch(db,result):
    """Audit stored task binding locally; never send cached history to a model."""
    if result.get('error'):return None
    task_id=result.get('taskId');presentation=result.get('presentation',{});packet=result.get('packet',{})
    if any(value and value!=task_id for value in (presentation.get('taskId'),packet.get('taskId'))):
        return '回答与证据属于不同任务'
    if (result.get('selection') or {}).get('version',0)>=2:return None
    row=db.execute('SELECT prompt FROM tasks WHERE id=?',(task_id,)).fetchone()
    if not row:return None
    anchors=query_anchors(result.get('question',''))
    if not anchors or any(term in row[0].lower() for term in anchors):return None
    found=candidates(db,anchors,question=result.get('question',''))
    if found and found[0][6]>0:return '上次回答选错了任务'
    return None

def select_task(db,plan,question,history,found,source=None,since=None):
    previous=history[-1].get('retrievedTaskIds',[])[:1] if history else []
    row=db.execute('SELECT prompt,source FROM tasks WHERE id=? AND (? IS NULL OR source=?) AND (? IS NULL OR updated>=?)',(previous[0],source,source,since,since)).fetchone() if previous else None
    if row:
        # Within equally relevant matches, preserve the Agent being discussed.
        # This is a preference, not a filter: naming another Agent still switches.
        found=sorted(found,key=lambda candidate:(-candidate[6],len(candidate[1]),candidate[2]!=row[1],-candidate[5]))
    elif found:
        same_title=[candidate for candidate in found if candidate[1].strip().lower()==found[0][1].strip().lower() and candidate[6]==found[0][6]]
        if len({candidate[2] for candidate in same_title})>1:return None,'choose_task'
    anchors=query_anchors(question)
    old_score=sum(x in row[0].lower() for x in anchors) if row else -1
    if row and not anchors and isinstance(plan.get('followup'),bool):return previous[0],'same_task'
    # An explicitly named, better-matching task overrides even an incorrect model followup flag.
    if found and (not row or found[0][6]>old_score):return found[0][0],'new_task'
    if row and plan.get('followup') is True:return previous[0],'same_task'
    if found:return found[0][0],'new_task'
    return None,'not_found'

def candidates(db,terms,limit=6,source=None,since=None,question=None):
    generic={'workbuddy','codex','问题','解决','任务','之前','查找','工具','怎么','如何','查询','失败','解决方法'}
    terms=[x.strip().lower() for x in terms if isinstance(x,str) and 1<len(x.strip())<=60 and x.strip().lower() not in generic][:8]
    if not terms:return []
    expr=' + '.join('(CASE WHEN instr(lower(prompt),?)>0 THEN 5 WHEN instr(lower(search),?)>0 THEN 1 ELSE 0 END)' for _ in terms)
    args=[v for term in terms for v in (term,term)]
    anchors=query_anchors(question or '',generic)
    qexpr=' + '.join('(CASE WHEN instr(lower(prompt),?)>0 THEN 1 ELSE 0 END)' for _ in anchors) or '0'
    args.extend(anchors)
    where='score>0 AND prompt NOT LIKE ?'
    args.append('The following is the Codex agent history%')
    if source:where+=' AND source=?';args.append(source)
    if since:where+=' AND updated>=?';args.append(since)
    rows=db.execute('SELECT id,prompt,source,session,updated,('+expr+') AS score,('+qexpr+') AS query_score FROM tasks WHERE '+where+' ORDER BY query_score DESC,length(prompt),score DESC,updated DESC LIMIT 40',args).fetchall()
    seen=set();result=[]
    for row in rows:
        key=(row[2],row[3],row[1])
        if key in seen:continue
        seen.add(key);result.append(row)
        if len(result)>=limit:break
    return result

def ask(root,config,question,history,progress,source=None,days=0,selected_task=None):
    from .relay_model import call,answer
    from .task_presentation import project
    import datetime
    if not config.get('enabled') or not config.get('credentialFile'):raise ValueError('请先在设置中配置智能助手的模型地址和凭据。')
    progress('正在理解问题，检索本机任务库')
    with sqlite3.connect(Path(root)/'collector.db',timeout=10) as db:
        history=[result for result in history if not result.get('selectionNeeded') and not answer_mismatch(db,result)]
    named_agents=[agent for agent in ('workbuddy','codex') if agent in question.lower()]
    if len(named_agents)==1:source=named_agents[0]
    plan=({'terms':[],'followup':False} if selected_task else call(config,'把问题转为本机历史任务检索词。输出 JSON {"terms":[最多六个有辨识度的短词],"followup":布尔}。不要回答问题。明确提到另一项任务必须 followup=false；只有省略任务名称且延续前项内容才是追问。检索词优先提取本次问题指向的任务，不要把上一项任务的主题带入新的问题。',{'question':question,'previousQuestion':history[-1]['question'] if history else '', 'previousTask':history[-1].get('retrieved',[{}])[0].get('title','') if history and history[-1].get('retrieved') else ''},max_tokens=500))
    with sqlite3.connect(Path(root)/'collector.db',timeout=10) as db:
        since=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=days)).isoformat() if days else None
        found=retrieve_candidates(db,plan,question,source=source,since=since) if not selected_task else []
        selected,mode=(selected_task,'selected_task') if selected_task else select_task(db,plan,question,history,found,source=source,since=since)
        if mode=='choose_task':
            return {'question':question,'selectionNeeded':True,'options':[{'taskId':r[0],'title':r[1],'source':r[2],'updated':r[4]} for r in found], 'selection':{'version':2,'mode':mode}}
        ids=[selected] if selected else []
        if ids and not db.execute('SELECT 1 FROM tasks WHERE id=? AND (? IS NULL OR source=?) AND (? IS NULL OR updated>=?)',(ids[0],source,source,since,since)).fetchone():ids=[]
        if not ids:raise ValueError('没有找到相关任务，请补充任务名称、文件或时间。')
        packet=packet_for_task(db,ids[0]);presentation=project(db,ids[0])
    progress('正在核对这一次任务的思路、工具参数与返回')
    relevant_history=[result for result in history if result.get('taskId')==ids[0]] if mode=='same_task' else []
    understanding=answer(root,config,question,packet,relevant_history)
    return {'question':question,'taskId':ids[0],'retrievedTaskIds':ids,'retrieved':[{'title':packet['prompt'],'source':packet['source']}],'packet':packet,'presentation':presentation,'understanding':understanding,'selection':{'version':2,'mode':mode,'terms':plan.get('terms',[])}}
