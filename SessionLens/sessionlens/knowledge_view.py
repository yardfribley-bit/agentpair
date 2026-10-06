"""Native project → requirement → execution answers; stable between queries."""
import json,re
from PySide6.QtCore import Qt,Signal,QTimer,QSize
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QFrame,QDialog,QPlainTextEdit,QSizePolicy
from .project_window import task_preview

def compact(text,n=110):
    text=' '.join(task_preview(str(text)).split());return text[:n]+('…' if len(text)>n else '')
def label(text,style=''):
    w=QLabel(str(text));w.setTextFormat(Qt.PlainText);w.setWordWrap(True);w.setStyleSheet(style);w.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred);return w
def clear(layout):
    while layout.count():
        item=layout.takeAt(0)
        if item.widget():item.widget().hide();item.widget().deleteLater()
        elif item.layout():clear(item.layout())
def action_name(name):
    leaf=str(name).split('→')[-1].strip().split('.')[-1]
    return {'Bash':'运行命令','Read':'读取文件','Write':'写入文件','Edit':'修改文件','ToolSearch':'寻找可用工具','VideoGen':'生成视频','present_files':'交付文件','WebSearch':'检索网页','WebFetch':'读取网页','TaskCreate':'创建子任务','TaskUpdate':'更新子任务'}.get(leaf,compact(name,30))
class ActiveStack(__import__('PySide6.QtWidgets',fromlist=['QStackedWidget']).QStackedWidget):
    def sizeHint(self):return self.currentWidget().sizeHint() if self.currentWidget() else QSize(400,200)
    def minimumSizeHint(self):return QSize(200,max(100,self.currentWidget().minimumSizeHint().height())) if self.currentWidget() else QSize(200,100)

class KnowledgeView(QWidget):
    projectRequested=Signal(str)
    taskRequested=Signal(str)
    evidenceRequested=Signal(str)
    questionRequested=Signal(str)
    associationRequested=Signal()
    projectCorrectionRequested=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);self.result={};self.index=0;self.expanded=False;self.signature=None
        self.setStyleSheet('QWidget{background:transparent;} QPushButton{background:transparent;color:#315fc4;border:0;text-align:left;padding:7px 4px;} QPushButton:hover{background:#eaf0fc;} QPushButton:checked{background:#eaf0fc;border:1px solid #315fc4;} QFrame#kbrow{border:0;border-bottom:1px solid #e1e5ec;}')
        self.v=QVBoxLayout(self);self.v.setContentsMargins(0,0,0,0);self.v.setSpacing(18);self.v.setAlignment(Qt.AlignTop)
        self.origin=label('SessionLens 回答','font-size:12px;color:#626c7b;');self.v.addWidget(self.origin)
        self.title=label('','font-size:21px;font-weight:600;');self.v.addWidget(self.title)
        self.summary=label('','font-size:15px;');self.v.addWidget(self.summary)
        self.scope=label('','font-size:12px;color:#626c7b;');self.v.addWidget(self.scope)
        self.body=QWidget();self.body_layout=QVBoxLayout(self.body);self.body_layout.setContentsMargins(0,0,0,0);self.body_layout.setSpacing(18);self.v.addWidget(self.body)
        self.project_label=label('','font-size:12px;color:#626c7b;');self.project_label.hide();self.v.addWidget(self.project_label)
        self.requirement_button=QPushButton();self.requirement_button.clicked.connect(self.dialogues);self.requirement_button.hide();self.v.addWidget(self.requirement_button)
        correction=QHBoxLayout();self.association_button=QPushButton('修正需求关联');self.association_button.clicked.connect(self.associationRequested.emit);correction.addWidget(self.association_button)
        self.project_button=QPushButton('修正项目归属');self.project_button.clicked.connect(self.projectCorrectionRequested.emit);correction.addWidget(self.project_button);correction.addStretch();self.v.addLayout(correction);self.association_button.hide();self.project_button.hide()
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.next_step)
    def load(self,result):
        signature=json.dumps(result,ensure_ascii=False,sort_keys=True)
        if signature==self.signature:return
        self.timer.stop();self.signature=signature;self.result=result;self.index=0;self.expanded=False;self.render()
    def render(self):
        self.setUpdatesEnabled(False)
        try:
            clear(self.body_layout);self.project_label.hide();self.requirement_button.hide();self.association_button.hide();self.project_button.hide()
            r=self.result
            if r.get('projectInventory'):self.inventory(r['projectInventory'])
            elif r.get('projectDetails'):self.project(r['projectDetails'])
            elif r.get('presentation'):self.task(r['presentation'])
            else:self.choices()
        finally:self.setUpdatesEnabled(True)
    def button(self,text,slot,parent_layout=None):
        b=QPushButton(text);b.clicked.connect(slot);(self.body_layout if parent_layout is None else parent_layout).addWidget(b);return b
    def row(self,title,detail,slot,layout):
        frame=QFrame();frame.setObjectName('kbrow');v=QVBoxLayout(frame);v.setContentsMargins(4,8,4,12);v.setSpacing(3)
        b=QPushButton(compact(title,38));b.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed);b.setToolTip(task_preview(str(title)));b.setStyleSheet('font-size:14px;font-weight:500;');b.clicked.connect(slot);v.addWidget(b);v.addWidget(label(detail,'font-size:12px;color:#626c7b;'));layout.addWidget(frame)
    def metric_line(self,stats):
        if not stats:return ''
        return f'用户发言 {stats.get("userTurns",0)} 轮，其中确认 {stats.get("approvalTurns",0)} 轮、开始执行 {stats.get("executionTurns",0)} 轮。模型实际调用次数：暂无法确认。'
    def inventory(self,snapshot):
        c=snapshot['counts'];self.origin.setText('SessionLens 回答 / '+(snapshot.get('source') or '全部 Agent'))
        self.title.setText(f'已识别 {c["identified"]+c["confirmed"]} 个有开发记录的项目目录。')
        self.summary.setText(f'另有 {c["candidate"]} 个目录待确认，尚未计入项目总数。')
        self.scope.setText('依据已采集的源码修改记录；包括二开与实验项目。'+('' if snapshot['complete'] else '历史整理仍在进行。'))
        rows=[p for p in snapshot['projects'] if p['state']!='excluded'];show=rows if self.expanded else rows[:7]
        header=QHBoxLayout();header.addWidget(label('项目名称','font-size:12px;color:#626c7b;'),3);header.addWidget(label('关联开发任务','font-size:12px;color:#626c7b;'),1);header.addWidget(label('源码文件','font-size:12px;color:#626c7b;'),1);self.body_layout.addLayout(header)
        for p in show:
            row=QHBoxLayout();b=QPushButton(p['name']);b.clicked.connect(lambda checked=False,id=p['id']:self.projectRequested.emit(id));row.addWidget(b,3);row.addWidget(label(str(p['taskCount'])),1);row.addWidget(label(str(p['fileCount'])),1);self.body_layout.addLayout(row)
        if len(rows)>7:self.button('收起清单' if self.expanded else f'查看其余 {len(rows)-7} 个项目',self.toggle_expanded)
    def toggle_expanded(self):self.expanded=not self.expanded;self.render()
    def project(self,p):
        self.origin.setText('SessionLens 回答 / '+p['source']);self.title.setText(f'{p["name"]} 已关联 {p["taskCount"]} 个需求任务。')
        self.summary.setText(self.metric_line(p.get('interactions')) or f'记录涉及 {p["fileCount"]} 个源码文件。')
        self.scope.setText('统计覆盖全部已关联记录，确认与执行指令计入用户发言。模型请求、回复分片和工具调用分别统计。')
        columns=QHBoxLayout();left_widget=QWidget();right_widget=QWidget();right_widget.setMinimumWidth(240);left=QVBoxLayout(left_widget);left.setContentsMargins(0,0,0,0);left.setSpacing(8);right=QVBoxLayout(right_widget);right.setContentsMargins(12,0,0,0);right.setSpacing(9);columns.addWidget(left_widget,3);columns.addWidget(right_widget,2);self.body_layout.addLayout(columns)
        left.addWidget(label('这些任务在做什么','font-size:15px;font-weight:600;'))
        tasks=p.get('tasks',[]);shown=tasks if self.expanded else tasks[:6]
        for t in shown:
            stats=t.get('interactions',{});detail=(t.get('updated') or '')[:10]+f' / 用户发言 {stats.get("userTurns",1)} 轮'
            if stats.get('approvalTurns') or stats.get('executionTurns'):detail+=f' / 确认与执行 {stats.get("approvalTurns",0)+stats.get("executionTurns",0)} 轮'
            self.row(t['prompt'],detail,lambda checked=False,id=t['taskId']:self.taskRequested.emit(id),left)
        if len(tasks)>6:self.button('收起任务' if self.expanded else f'展开其余 {len(tasks)-6} 个已读取任务',self.toggle_expanded,left)
        right.addWidget(label('项目里有什么','font-size:15px;font-weight:600;'))
        for c in p.get('components',[])[:7]:
            right.addWidget(label(c['name'],'font-size:13px;'));right.addWidget(label(str(c['fileCount'])+' 个源码文件','font-size:12px;color:#626c7b;'))
        right.addWidget(label('按源码路径归类，目录含义尚未作业务解释。','font-size:12px;color:#626c7b;'));right.addStretch()
        stats=p.get('interactions',{})
        if stats:self.body_layout.addWidget(label(stats['modelCallsReason'],'font-size:12px;color:#626c7b;'))
        if stats.get('modelMessageIdentifiers'):
            self.body_layout.addWidget(label(f'日志提供 {stats["modelMessageIdentifiers"]} 个不同模型消息标识；标识数量未认证为实际 API 调用次数。','font-size:12px;color:#626c7b;'))
    def task(self,d):
        self.origin.setText('SessionLens 回答 / '+d['source']);self.title.setText(compact(d['prompt'],55));r=self.result
        self.summary.setText(compact(r.get('understanding',{}).get('overview',{}).get('text',''),230))
        stats=r.get('interactions') or d.get('interactions') or {};self.scope.setText(self.metric_line(stats))
        ctx=d.get('projectContext') or r.get('packet',{}).get('projectContext') or {};items=ctx.get('projects',[])
        self.project_label.setText('\n'.join(p['name']+(' / '+p['repository'] if p.get('repository') else '') for p in items) or '未关联项目');self.project_label.show()
        self.project_button.show()
        history=d.get('requirements',[]);self.requirement_button.setText(f'查看 {stats.get("userTurns",len(history))} 轮需求与确认');self.requirement_button.setVisible(bool(history))
        self.association_button.setVisible(bool(history))
        callbar=QHBoxLayout();callbar.addWidget(label('执行过程','font-size:15px;font-weight:600;'),1)
        if d.get('calls'):
            replay=QPushButton('暂停回放' if self.timer.isActive() else '回放');replay.clicked.connect(self.toggle_play);callbar.addWidget(replay)
            next_button=QPushButton('下一步');next_button.clicked.connect(self.next_step);callbar.addWidget(next_button)
        self.body_layout.addLayout(callbar)
        calls=d.get('calls',[])
        if not calls:self.body_layout.addWidget(label('当前记录没有工具调用。'));return
        chain=QHBoxLayout();start=max(0,self.index-1);visible=calls[start:start+3]
        for n,c in enumerate(visible,start):
            b=QPushButton(action_name(c['name']));b.setCheckable(True);b.setChecked(n==self.index);b.clicked.connect(lambda checked=False,i=n:self.select_step(i));chain.addWidget(b,1)
        self.body_layout.addLayout(chain)
        self.body_layout.addWidget(label(f'正在查看第 {self.index+1} 个动作，共读取 {len(calls)} 个调用。','font-size:12px;color:#626c7b;'))
        c=calls[self.index];columns=QHBoxLayout();left=QVBoxLayout();right=QVBoxLayout();columns.addLayout(left,3);columns.addLayout(right,2);self.body_layout.addLayout(columns)
        left.addWidget(label('已记录的处理思路','font-size:14px;font-weight:600;'))
        reasoning=next((x['text'] for x in d.get('reasoning',[]) if x['id']==(c.get('decisionLink') or {}).get('reasoningEvent')),None)
        left.addWidget(label(compact(reasoning,240) if reasoning else '这一调用没有找到可关联的思路记录。','font-size:13px;'))
        if reasoning:self.button('查看思路原文',lambda:self.raw('已记录思路',reasoning),left)
        right.addWidget(label('工具参数与返回','font-size:14px;font-weight:600;'))
        right.addWidget(label('工具：'+c['name'],'font-size:12px;color:#626c7b;'))
        fields=c.get('fields',[])[:5]
        for f in fields:right.addWidget(label(f['label']+'：'+compact(f['value'],180),'font-size:13px;'))
        if not fields:right.addWidget(label('没有可读的参数字段。','font-size:13px;'))
        returns=c.get('returns',[]);right.addWidget(label('返回：'+(compact(returns[0]['text'],180) if returns else '没有找到对应的工具返回，结果尚未确认。'),'font-size:13px;'))
        self.button('查看原始参数与返回',lambda:self.raw(c['name'],'参数\n'+json.dumps(c.get('arguments',{}),ensure_ascii=False,indent=2)+'\n\n原始返回\n'+'\n\n'.join(t['text'] for t in returns)),right)
        for event in [c['id']]+[x['id'] for x in returns]:
            ref=next((f['evidenceId'] for f in r.get('packet',{}).get('fragments',[]) if f.get('eventId')==event),None)
            if ref:self.button('核对来源 '+ref,lambda checked=False,id=ref:self.evidenceRequested.emit(id),right)
        self.body_layout.addWidget(label('这里只回放已记录的内容，不执行历史命令。未核验交付物时不显示为已验证成功。','font-size:12px;color:#626c7b;'))
        follow=QHBoxLayout();self.button('追问当时的思路',lambda:self.questionRequested.emit('这个任务当时为什么这样做？'),follow)
        self.button('追问对话与模型交互次数',lambda:self.questionRequested.emit('这个任务用户发了多少轮，Agent 和大模型交互了多少次？'),follow);self.body_layout.addLayout(follow)
    def select_step(self,index):self.timer.stop();self.index=index;self.render()
    def toggle_play(self):
        if self.timer.isActive():self.timer.stop()
        else:self.timer.start()
        self.render()
    def next_step(self):
        count=len(self.result.get('presentation',{}).get('calls',[]))
        if self.index+1>=count:self.timer.stop()
        else:self.index+=1
        self.render()
    def dialogues(self):
        rows=self.result.get('presentation',{}).get('requirements',[])
        self.raw('需求与确认历程','\n\n'.join(str(i)+' / '+r['label']+'\n'+r['text']+'\n关联依据：'+r.get('reason','') for i,r in enumerate(rows,1)))
    def choices(self):
        r=self.result;self.origin.setText('SessionLens 回答');self.title.setText(r.get('projectMessage') or r.get('selectionMessage') or '请确认要了解的任务。');self.summary.clear();self.scope.setText('保留当前问题；没有明确对象时不自动选择第一项。')
        for p in r.get('projectChoices',[]):self.button(p['name']+' / '+p['source'],lambda checked=False,id=p['id']:self.projectRequested.emit(id))
        for t in r.get('options',[]):self.row(t['title'],t['source']+' / '+t['updated'][:10],lambda checked=False,id=t['taskId']:self.taskRequested.emit(id),self.body_layout)
    def raw(self,title,text):
        self.timer.stop()
        dialog=QDialog(self);dialog.setWindowTitle(title);dialog.resize(850,600);v=QVBoxLayout(dialog);edit=QPlainTextEdit();edit.setReadOnly(True);edit.setPlainText(text);v.addWidget(edit);dialog.exec()
    def plain_text(self):return '\n'.join(w.text() for w in self.findChildren(QLabel) if w.isVisibleTo(self))
