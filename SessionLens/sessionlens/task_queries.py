"""Local task drill-down and exact recorded-conversation answers."""
import sqlite3,re
from pathlib import Path
from .task_lineage import resolve,task_table
from .interactions import task_interactions,interaction_question
from .task_presentation import project
from .assistant import packet_for_task
from .i18n import answer_language,t

def local_task_query(root,question,previous=None,selected=None):
    previous=previous or {};previous_task=previous.get('taskId')
    if not selected and not interaction_question(question):return None
    detail_question=any(w in question for w in ('怎么做','执行过程','原始要求','需求对话','任务过程','要求、执行','要求和结果')) or bool(re.search(r'\b(?:how (?:was|did|is)|execution process|task process|original (?:request|requirements?)|(?:what|which) tools|show (?:the )?steps|requirements? and results?)\b',question,re.I))
    if selected and not interaction_question(question) and not detail_question:return None
    explicit='workbuddy' if 'workbuddy' in question.lower() else 'codex' if 'codex' in question.lower() else None
    # A referenced project is not a selected task. Don't choose its first task.
    if not selected and (previous.get('projectDetails') or not previous_task):
        if previous.get('projectDetails') and (any(w in question for w in ('这次任务','这个任务','该任务')) or re.search(r'\b(?:this|that|the) task\b',question,re.I)):
            return {'question':question,'selectionNeeded':True,'options':[{'taskId':t['taskId'],'title':t['prompt'],'source':previous['projectDetails']['source'],'updated':t['updated']} for t in previous['projectDetails']['tasks'][:12]],'selectionMessage':'这个项目有多项任务，请确认你指的是哪一项。'}
        return None
    if not selected and not (any(w in question for w in ('这次','这个','该任务','这项','它','用户','模型','对话','交互')) or re.search(r'\b(?:this|that|it|its|user|model|conversation|interaction|turns?)\b',question,re.I)):return None
    with sqlite3.connect((Path(root)/'collector.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
        db.execute('BEGIN');task=resolve(db,selected or previous_task)
        row=db.execute('SELECT source,prompt FROM '+task_table(db)+' WHERE id=?',(task,)).fetchone()
        if not row or explicit and explicit!=row[0]:return None
        # Explicit new entities shouldn't inherit a previous task. Free new
        # subjects go through the normal question-understanding/retrieval path.
        if not selected:
            from .knowledge import explicit_entities
            entities=explicit_entities(question)
            if interaction_question(question):
                entities=[e for e in entities if e not in {'many','user','users','turn','turns','model','models','calls','call','times','conversation','conversations','interactions','messages','number','count','there','api','llm','approval','approvals','confirmations'}]
            if entities and any(e not in row[1].lower() and e not in ('agent','llm','模型') for e in entities):return None
        stats=task_interactions(db,task);packet=packet_for_task(db,task,question)
        view=project(db,task);packet['interactions']=stats
        lang=answer_language(question)
        if lang=='en':
            stats['modelCallsReason']=t(stats['modelCallsReason'],lang)
            if interaction_question(question):text=f'This task has {stats["userTurns"]} linked user turns, including {stats["approvalTurns"]} confirmations and {stats["executionTurns"]} execution instructions. '+stats['modelCallsReason']
            else:text=f'The original request was “{row[1][:180]}”. The records contain {stats["userTurns"]} linked user turns and {stats["toolCalls"]} tool calls; inspect each recorded action and return for details.'
        elif interaction_question(question):text=f'这项需求已关联 {stats["userTurns"]} 轮用户发言，其中确认方案 {stats["approvalTurns"]} 轮、开始执行 {stats["executionTurns"]} 轮。'+stats['modelCallsReason']
        else:text=f'这项任务的原始要求是“{row[1][:180]}”。已关联 {stats["userTurns"]} 轮用户发言，记录了 {stats["toolCalls"]} 次工具调用；具体动作和返回可逐步查看。'
        understanding={'overview':{'text':text,'basis':'recorded','evidenceRefs':[]},'steps':[],'gaps':[stats['modelCallsReason']],'toolExplanations':[]}
        return {'question':question,'taskId':task,'retrievedTaskIds':[task],'retrieved':[{'taskId':task,'title':row[1],'source':row[0]}],
                'packet':packet,'presentation':view,'understanding':understanding,'interactions':stats,'queryKind':'task_interactions' if interaction_question(question) else 'task_detail',
                'engine':'sessionlens.local_tasks.v1','selection':{'version':4,'mode':'selected_task' if selected else 'same_task'}}
