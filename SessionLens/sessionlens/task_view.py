"""Prototype task cards: one page scroll, concise evidence and historical replay."""
import json,re
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,Signal
from PySide6.QtGui import QPainter,QColor,QPen,QPainterPath
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QDialog,QPlainTextEdit,QFrame,QSizePolicy


def short(text,limit=130):
    text=re.sub(r'\s+',' ',str(text)).strip()
    return text if len(text)<=limit else text[:limit]+'…'


def task_title(prompt):
    protocol=re.search(r'(?:描述|展示|介绍)\s*([A-Za-z0-9_-]+)\s*协议',prompt)
    if protocol and any(x in prompt for x in ('动画','视频')):return protocol.group(1).upper()+' 协议'+('动画' if '动画' in prompt else '视频')
    return short(re.sub(r'^(帮我|请帮我|请|麻烦你|帮忙)\s*','',prompt),24)

def label(text,style=''):
    w=QLabel(str(text));w.setTextFormat(Qt.PlainText);w.setWordWrap(True);w.setStyleSheet(style);w.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Minimum);return w

class Brain(QWidget):
    def __init__(self):super().__init__();self.setFixedSize(78,78);self.phase=0;self.active=False
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setPen(QPen(QColor('#c5d8eb'),1));p.setBrush(QColor('#eaf3fb'));p.drawEllipse(4,4,70,70)
        p.setPen(QPen(QColor('#1769ef'),2));path=QPainterPath();path.moveTo(39,57);path.lineTo(39,23)
        for x,y in [(29,28),(23,36),(25,47),(32,54),(49,28),(55,36),(53,47),(46,54)]:
            p.drawEllipse(x-3,y-3,6,6);path.moveTo(39,39);path.lineTo(x,y)
        p.drawPath(path)
        if self.active:
            p.setPen(QPen(QColor('#1769ef'),3));p.drawArc(1,1,76,76,self.phase*16,75*16)

class TaskView(QWidget):
    evidenceRequested=Signal(str)
    associationRequested=Signal()
    projectRequested=Signal()
    contentChanged=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);self.data={};self.index=0;self.position=0;self.key='overview'
        self.setStyleSheet('QWidget{background:transparent;} QFrame#player{background:white;border:1px solid #E6E8EC;border-radius:12px;} QFrame#node{background:white;border:1px solid #dbe3ec;border-radius:7px;} QPushButton{background:white;color:#33516f;border:1px solid #dbe3ec;border-radius:6px;padding:7px 12px;} QPushButton:checked{background:#edf4fb;color:#1769ef;border-color:#1769ef;} QComboBox{background:white;border:1px solid #E6E8EC;border-radius:6px;padding:5px;color:#203044;}')
        v=QVBoxLayout(self);v.setAlignment(Qt.AlignTop);v.setContentsMargins(0,0,0,0);v.setSpacing(14)
        self.heading=label('', 'font-size:15px;font-weight:600;');v.addWidget(self.heading)
        self.original_task=label('');self.original_task.hide();self.meta=label('','font-size:12px;color:#61748c;');meta_row=QHBoxLayout();meta_row.addWidget(self.meta,1);self.delivered=label('');self.trust=label('');meta_row.addWidget(self.delivered);meta_row.addWidget(self.trust);v.addLayout(meta_row)
        project_row=QHBoxLayout();self.project_label=label('未关联项目','font-size:12px;color:#61748c;');project_row.addWidget(self.project_label,1)
        self.project_button=QPushButton('修正项目关联');self.project_button.clicked.connect(lambda:self.projectRequested.emit());project_row.addWidget(self.project_button);v.addLayout(project_row)
        self.requirement_toolbar=QWidget();requirement_row=QHBoxLayout(self.requirement_toolbar);requirement_row.setContentsMargins(0,0,0,0)
        self.requirement_button=QPushButton('查看需求与确认过程');self.requirement_button.clicked.connect(self.requirement_original);requirement_row.addWidget(self.requirement_button);self.requirement_button.hide()
        self.association_button=QPushButton('修正任务关联');self.association_button.clicked.connect(lambda:self.associationRequested.emit());requirement_row.addWidget(self.association_button);self.association_button.hide();requirement_row.addStretch();v.addWidget(self.requirement_toolbar);self.requirement_toolbar.hide()
        self.summary=label('','font-size:17px;color:#203044;');v.addWidget(self.summary)
        tabs=QHBoxLayout();self.tabs={}
        for title,key in [('怎么做的 / 做成了吗','overview'),('当时怎么想','reasoning'),('调用了什么','calls'),('用到什么上下文','context'),('交付与验证','delivery')]:
            b=QPushButton(title);b.setCheckable(True);b.clicked.connect(lambda checked=False,k=key:self.select(k));self.tabs[key]=b;tabs.addWidget(b)
        self.timeline=QWidget();self.timeline_layout=QHBoxLayout(self.timeline);self.timeline_layout.setContentsMargins(0,6,0,6);self.step_buttons=[];v.addWidget(self.timeline)
        self.player=QFrame();self.player.setObjectName('player');pv=QVBoxLayout(self.player);pv.setContentsMargins(16,16,16,16)
        row=QHBoxLayout();self.paneltitle=label('任务回放','font-size:15px;font-weight:600;');row.addWidget(self.paneltitle,1)
        self.play=QPushButton('播放回放');self.play.clicked.connect(self.toggle);row.addWidget(self.play);next_button=QPushButton('下一段');next_button.clicked.connect(self.next);self.next_button=next_button;row.addWidget(next_button)
        self.speed=QComboBox();self.speed.addItems(['1.0×','2.0×']);row.addWidget(self.speed);pv.addLayout(row);pv.addLayout(tabs)
        thinking=QHBoxLayout();thinking.setSpacing(18);self.brain=Brain();thinking.addWidget(self.brain,0,Qt.AlignTop);tokens=QVBoxLayout();self.stage=label('','color:#61748c;font-size:12px;');tokens.addWidget(self.stage);self.reason=label('','border-left:2px solid #1769ef;padding:10px 14px;font-size:15px;');self.reason.setMinimumHeight(70);tokens.addWidget(self.reason);thinking.addLayout(tokens,1);pv.addLayout(thinking)
        flow=QHBoxLayout();self.nodes=[];self.arrows=[];self.node_labels=[]
        for i,title in enumerate(('动作','返回','后续调整')):
            if i:
                arrow=label('→','color:#8294a8;font-size:19px;');self.arrows.append(arrow);flow.addWidget(arrow,0)
            frame=QFrame();frame.setObjectName('node');nv=QVBoxLayout(frame);nv.setContentsMargins(12,12,12,12);node_label=label(title,'color:#61748c;font-size:12px;');self.node_labels.append(node_label);nv.addWidget(node_label);text=label('');nv.addWidget(text);flow.addWidget(frame,1);self.nodes.append((frame,text))
        pv.addLayout(flow);original=QPushButton('查看这一段 reasoning 原文');original.clicked.connect(self.original);self.original_button=original;pv.addWidget(original,0,Qt.AlignLeft);v.addWidget(self.player)
        self.details=QWidget();self.detail_layout=QVBoxLayout(self.details);self.detail_layout.setContentsMargins(0,0,0,0);self.detail_layout.setSpacing(14);pv.addWidget(self.details)
        self.note=label('','font-size:12px;color:#61748c;');v.addWidget(self.note)
        self.timer=QTimer(self);self.timer.setInterval(65);self.timer.timeout.connect(self.tick)
    def load(self,result):
        self.stop();self.result=result;self.data=result['presentation'];self.index=0;self.position=0
        d=self.data;self.heading.hide();self.heading.setText('对应任务 · '+short(d['prompt'],55));self.original_task.setText('用户原话：“'+short(d['prompt'],180)+'”');self.meta.setText({'workbuddy':'WorkBuddy','codex':'Codex'}.get(d['source'],d['source'])+' · '+task_title(d['prompt'])+' · '+str(d.get('updated',''))[:10]+' · 目标：'+short(d['prompt'],24))
        self.meta.setToolTip(d['prompt']);self.make_steps();self.selected_step=0;self.set_status()
        context=d.get('projectContext') or result.get('packet',{}).get('projectContext') or {}
        items=context.get('projects',[])
        if context.get('mode')=='independent':self.project_label.setText('独立任务')
        elif items:
            self.project_label.setText('\n'.join(('项目：' if p['role']=='target' else '参考项目：')+p['name']+(' · 仓库：'+p['repository'].removeprefix('https://') if p.get('repository') else ' · 未记录仓库')+(' · 分支（日志）：'+p['branch'] if p.get('branch') else '') for p in items))
        else:self.project_label.setText('未关联项目')
        self.project_label.setToolTip('工作目录（运行环境）：'+str(context.get('workingDirectory') or '未记录')+'\n'+ '\n'.join('关联依据：'+{'current_filesystem':'当前本机仓库配置检测','recorded':'日志记录','user_confirmed':'用户确认'}.get(p['basis'],p['basis']) for p in items))
        history=d.get('requirements',[]);changes=sum(r['kind']=='revision' for r in history)
        self.requirement_button.setVisible(len(history)>1)
        self.association_button.setVisible(bool(history))
        self.requirement_toolbar.setVisible(bool(history))
        self.requirement_button.setText('需求讨论 '+str(len(history))+' 轮 · 调整 '+str(changes)+' 次 · 查看依据')
        q=result['question'];self.select('context' if any(w in q for w in ('上下文','记忆')) else 'calls' if any(w in q for w in ('工具','调用','参数')) else 'reasoning' if any(w in q for w in ('思路','思考','reasoning')) else 'delivery' if any(w in q for w in ('交付','时长')) else 'overview')
    def stop(self):
        self.timer.stop();self.brain.active=False;self.brain.update();self.play.setText('播放回放')
        for frame,_ in self.nodes:frame.setStyleSheet('')
    def requirement_original(self,call=None):
        rows=self.data.get('requirements',[])
        link=call.get('requirement') if isinstance(call,dict) else None
        if link:
            endpoint=next((r['seq'] for r in rows if r['eventId']==link['turnEvent']),None)
            if endpoint is not None:rows=[r for r in rows if r['seq']<=endpoint]
        text=[]
        for i,row in enumerate(rows,1):
            mark=' · 此次执行前的确认' if link and row['eventId']==link.get('approvalEvent') else ' · 此次执行所依据的需求版本' if link and row['eventId']==link.get('requirementEvent') else ''
            text.append(str(i)+' · '+row['label']+mark+'\n'+row['text'])
        if link and link.get('planEvent'):
            text.append('确认前 Agent 最近提出的内容（根据顺序关联）\n'+self.data.get('plans',{}).get(link['planEvent'],'本次没有读取该原文'))
        self.show_raw('执行依据' if link else '需求与确认过程','\n\n'.join(text)+'\n\n关联依据：前后需求、方案与确认记录。自动关联属于推断，可以在本机修正。')
    def select(self,key):
        self.stop();self.key=key
        for k,b in self.tabs.items():b.setChecked(k==key)
        self.player.setVisible(True);self.player.setMaximumHeight(450 if key in ('overview','reasoning') else 16777215)
        replay=key not in ('context','delivery')
        for w in [self.brain,self.stage,self.reason,self.play,self.next_button,self.speed,self.original_button]+self.arrows+[f for f,_ in self.nodes]:w.setVisible(replay)
        self.paneltitle.setText({'context':'用到什么上下文','delivery':'交付与验证'}.get(key,'任务回放'))
        q=self.result['question']
        if any(w in q for w in ('做成了吗','完成了吗','结果怎么样')):
            self.summary.setText(('已交付，实际效果未核验。' if self.delivered.text()=='已交付' else self.delivered.text()+'。')+'过程：'+' → '.join(s['title'] for s in self.steps)+'。')
        else:self.summary.setText(short(self.result['understanding']['overview']['text'],110))
        self.render_frame();self.render_details();self.contentChanged.emit()
    def call_note(self,call):
        ids={f['evidenceId'] for f in self.result['packet']['fragments'] if f.get('eventId')==call['id']}
        return next((x for x in self.result['understanding'].get('toolExplanations',[]) if ids.intersection(x.get('evidenceRefs',[]))),{})
    def return_brief(self,call):
        if not call['returns']:return '未记录关联返回，不能确认执行成功'
        for r in call['returns']:
            try:value=json.loads(r['text'])
            except ValueError:continue
            if isinstance(value,dict):
                status=value.get('status');files=value.get('videos') or value.get('files')
                if files:return ('工具报告完成；' if status=='completed' else '')+'返回 '+str(len(files))+' 个文件'+('，未记录预览' if value.get('previewed')==[] else '')
                if status:return '返回状态：'+str(status)
        return short(self.call_note(call).get('outputSummary') or call['returns'][0]['text'],90)
    def make_steps(self):
        while self.timeline_layout.count():
            item=self.timeline_layout.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        self.steps=[];self.step_buttons=[];self.step_titles=[]
        for i,c in enumerate(self.data['calls']):
            frame=next((j for j,f in enumerate(self.data['frames']) if c['id'] in f.get('callIds',[f['call']])),None)
            if 'prompt' in c['arguments'].get('params',{}):self.steps.append({'call':i,'frame':frame,'phase':'prepare','title':'写提示词'})
            name=c['name'];title='找工具' if 'ToolSearch' in name else '生成视频' if 'VideoGen' in name else '交付文件' if 'present_files' in name else short(self.call_note(c).get('purpose') or name,12)
            self.steps.append({'call':i,'frame':frame,'phase':'execute','title':title})
        for i,step in enumerate(self.steps):
            if i:
                line=label('────','color:#b7cbed;');line.setContentsMargins(0,14,0,0);line.setFixedHeight(40);self.timeline_layout.addWidget(line,1,Qt.AlignTop)
            cell=QWidget();layout=QVBoxLayout(cell);layout.setContentsMargins(0,0,0,0);layout.setSpacing(8);center=QHBoxLayout();center.addStretch()
            b=QPushButton(str(i+1));b.setCheckable(True);b.setFixedSize(34,34);b.setStyleSheet('QPushButton{border:1px solid #1769ef;border-radius:17px;background:white;color:#1769ef;padding:0;font-size:15px;} QPushButton:checked{background:#1769ef;color:white;}');b.clicked.connect(lambda checked=False,n=i:self.choose_step(n));b.setToolTip('查看这一步；提示词步骤由实际输入参数分拆展示' if step['phase']=='prepare' else '查看这次工具调用及返回');center.addWidget(b);center.addStretch();layout.addLayout(center)
            title=QPushButton(step['title']);title.setStyleSheet('QPushButton{border:0;background:transparent;color:#203044;padding:0;}');title.clicked.connect(lambda checked=False,n=i:self.choose_step(n));layout.addWidget(title);self.timeline_layout.addWidget(cell,2);self.step_buttons.append(b);self.step_titles.append(title)
    def set_status(self):
        delivered=False;failed=False
        for c in self.data['calls']:
            for r in c['returns']:
                try:value=json.loads(r['text'])
                except ValueError:continue
                if not isinstance(value,dict):continue
                failed=failed or value.get('status') in ('failed','error') or bool(value.get('error'))
                delivered=delivered or (value.get('type')=='present_files_result' and bool(value.get('files')) and not value.get('error'))
        self.delivered.setText('失败' if failed else '已交付' if delivered else '结果待确认');self.delivered.setStyleSheet('font-size:12px;padding:6px 10px;border-radius:6px;background:'+('#feecec;color:#b42318;' if failed else '#eaf7ef;color:#238450;' if delivered else '#f2f3f5;color:#667085;'))
        self.trust.setText('未核验');self.trust.setStyleSheet('font-size:12px;padding:6px 10px;border-radius:6px;background:#fff4df;color:#a56509;');self.trust.setToolTip('SessionLens 没有重新执行或检查交付物；可在交付视图核对已有验证记录。')
    def choose_step(self,n):
        self.stop();self.selected_step=n;self.position=0;step=self.steps[n];self.index=step['frame'] if step['frame'] is not None else 0;self.render_frame();self.render_details();self.contentChanged.emit()
    def render_frame(self):
        if not self.steps:self.stage.setText('没有记录工具步骤');self.reason.setText('');self.play.setEnabled(False);return
        step=self.steps[self.selected_step];call=self.data['calls'][step['call']];note=self.call_note(call)
        for i,b in enumerate(self.step_buttons):
            b.setChecked(i==self.selected_step);self.step_titles[i].setStyleSheet('QPushButton{border:0;background:transparent;padding:0;color:'+('#1769ef' if i==self.selected_step else '#203044')+';}')
        frame=self.data['frames'][step['frame']] if step['frame'] is not None else None
        text=frame['reasoning']['text'] if frame else ''
        sentences=[x.strip() for x in re.split(r'[。\n]',text.strip()) if x.strip()]
        if step['phase']=='prepare':sentences=sorted(sentences,key=lambda x:0 if re.search(r'提示词|prompt|构造',x,re.I) else 1)
        self.excerpt=short(sentences[0],100) if sentences else '该步骤没有单独记录思路，下面展示工具输入与返回。'
        basis=(call.get('decisionLink') or {}).get('basis')
        self.stage.setText(('历史思路 · 原始消息链关联' if basis=='source_parent_path' else '历史思路 · 按顺序关联，待核对') if text else '工具记录');self.play.setEnabled(bool(text));self.reason.setText(self.excerpt[:self.position] if self.position else self.excerpt)
        if step['phase']=='prepare':
            params=call['arguments'].get('params',{});specs=[]
            if isinstance(params,dict):
                if params.get('resolution'):specs.append(str(params['resolution']))
                if params.get('aspect_ratio'):specs.append(str(params['aspect_ratio']))
                if 'enable_audio' in params:specs.append('有声' if params['enable_audio'] else '无声')
            values=('准备提示词',' · '.join(specs) or '具体参数可展开查看','将提示词交给生成工具')
        else:
            next_reason=next((r for r in self.data['reasoning'] if frame and r['id']==frame['nextReason']),None)
            values=(call['name'],self.return_brief(call),short(re.split(r'[。\n]',next_reason['text'].strip())[0],65) if next_reason else '后续记录已结束')
        for w,title in zip(self.node_labels,('动作','输入规格','接下来') if step['phase']=='prepare' else ('动作','返回','后续调整')):w.setText(title)
        for (_,w),text in zip(self.nodes,values):w.setText(text)
    def field(self,layout,title,value):
        row=QHBoxLayout();name=label(title,'color:#61748c;font-size:13px;');name.setFixedWidth(110);row.addWidget(name,0,Qt.AlignTop);row.addWidget(label(value),1);layout.addLayout(row)
    def card(self,title,description=''):
        frame=QFrame();frame.setObjectName('callCard');frame.setStyleSheet('QFrame#callCard{border:0;border-bottom:1px solid #dbe3ec;}');layout=QVBoxLayout(frame);layout.setContentsMargins(0,12,0,14);layout.setSpacing(9);layout.addWidget(label(title,'font-size:15px;font-weight:600;'))
        if description:layout.addWidget(label(short(description,110)))
        self.detail_layout.addWidget(frame);return layout
    def render_details(self):
        while self.detail_layout.count():
            item=self.detail_layout.takeAt(0);item.widget().hide();item.widget().deleteLater()
        d=self.data
        if self.key=='calls':
            for i in ([self.steps[self.selected_step]['call']] if self.steps else []):
                c=d['calls'][i]
                note=self.call_note(c);v=self.card(str(i+1)+' · '+c['name'],note.get('purpose',''))
                if c.get('requirement'):
                    b=QPushButton('查看这次执行依据的需求 / 确认');b.clicked.connect(lambda checked=False,call=c:self.requirement_original(call));v.addWidget(b,0,Qt.AlignLeft)
                if note.get('inputSummary'):self.field(v,'输入内容',short(note['inputSummary'],190))
                for f in c['fields']:
                    if f['key'] in ('prompt','command','description','explanation','toolName'):continue
                    value=f['value']
                    if f['key'] in ('files','path','file_path'):value=' / '.join(Path(x).name for x in re.findall(r'[^\s\"\[\],]+',value))
                    self.field(v,f['label'],value if f['key']=='URL' else short(value,180))
                self.field(v,'返回',self.return_brief(c))
                for returned in c['returns']:
                    try:raw=json.loads(returned['text'])
                    except ValueError:continue
                    if not isinstance(raw,dict):continue
                    files=raw.get('videos') or raw.get('files') or []
                    if isinstance(files,list):
                        for file in files[:3]:
                            path=file.get('localPath','') if isinstance(file,dict) else file
                            if isinstance(path,str) and path:self.field(v,'文件',Path(path).name)
                    if raw.get('previewed')==[]:self.field(v,'预览记录','未记录预览检查')
                b=QPushButton('查看完整参数 / 原始返回');b.clicked.connect(lambda checked=False,n=i:self.tool_original(n));v.addWidget(b,0,Qt.AlignLeft)
        elif self.key=='context':
            v=self.card('信息从哪里来','区分用户要求、工具返回和 Agent 自己准备的内容。');self.field(v,'用户明确要求',short(d['prompt'],160))
            if len(d.get('requirements',[]))>1:self.field(v,'后续讨论','还有 '+str(len(d['requirements'])-1)+' 轮确认或调整，可在“查看依据”中核对原话')
            for c in d['calls']:
                if 'prompt' in c['arguments'].get('params',{}):self.field(v,'Agent 准备的内容',short(self.call_note(c).get('inputSummary','提示词内容见工具参数'),180))
            backgrounds=[x for x in d['context'] if x['kind']=='会话背景'];self.field(v,'背景与记忆','有 '+str(len(backgrounds))+' 份背景记录，可查看原文' if backgrounds else '当前任务片段没有单独记录，不能确认完整模型上下文')
            b=QPushButton('查看上下文原文');b.clicked.connect(lambda:self.show_raw('上下文原文','\n\n'.join(x['text'] for x in d['context'])));v.addWidget(b,0,Qt.AlignLeft)
        elif self.key=='delivery':
            v=self.card('目标与结果对照','工具报告完成与文件满足要求分开展示。');self.field(v,'需要交付',short(d['prompt'],160))
            for c in d['calls']:self.field(v,c['name'],self.return_brief(c))
            for gap in self.result['understanding'].get('gaps',[])[:3]:self.field(v,'尚未确认',short(gap,120))
            b=QPushButton('查看 Agent 最终回复');b.clicked.connect(lambda:self.show_raw('最终回复',d['replies'][-1]['text'] if d['replies'] else '没有记录最终回复'));v.addWidget(b,0,Qt.AlignLeft)
        else:pass
        refs=self.result['understanding']['overview']['evidenceRefs']
        if refs and self.key in ('calls','context','delivery'):
            proof=QPushButton('核对原始证据');proof.clicked.connect(lambda:self.evidenceRequested.emit(refs[0]));self.detail_layout.addWidget(proof,0,Qt.AlignLeft)
        self.details.setVisible(bool(self.detail_layout.count()))
        self.note.setText('历史回放 · 工具返回已按调用编号关联'+(' · 部分记录未读取' if d['included']<d['total'] else '')+(' · 前置需求未确认，可修正任务关联' if any(r['kind']=='unresolved' for r in d.get('requirements',[])) else ''))
    def toggle(self):
        if self.timer.isActive():self.stop();self.play.setText('继续回放');return
        if not self.data.get('frames'):return
        if self.position>=len(self.excerpt):self.position=0
        self.brain.active=True;self.timer.start();self.play.setText('暂停');self.reason.setText(self.excerpt[:self.position])
    def tick(self):
        self.position=min(len(self.excerpt),self.position+(6 if self.speed.currentIndex() else 3));self.reason.setText(self.excerpt[:self.position]);self.brain.phase=(self.brain.phase+12)%360;self.brain.update()
        for i,(frame,_) in enumerate(self.nodes):frame.setStyleSheet('QFrame#node{background:'+('#edf4fb' if i==min(2,self.position*3//max(1,len(self.excerpt))) else 'white')+';border:1px solid #cbdced;border-radius:7px;}')
        if self.position==len(self.excerpt):self.stop()
    def next(self):
        if self.steps:self.choose_step((self.selected_step+1)%len(self.steps))
    def plain_text(self):return '\n'.join(w.text() for w in self.findChildren(QLabel) if w.isVisibleTo(self))
    def show_raw(self,title,text):
        dialog=QDialog(self);dialog.setWindowTitle(title);dialog.resize(850,620);v=QVBoxLayout(dialog);edit=QPlainTextEdit();edit.setReadOnly(True);edit.setPlainText(text);v.addWidget(edit);dialog.exec()
    def original(self):
        if self.steps and self.steps[self.selected_step]['frame'] is not None:self.show_raw('这一段思路原文',self.data['frames'][self.steps[self.selected_step]['frame']]['reasoning']['text'])
    def tool_original(self,index):
        c=self.data['calls'][index];self.show_raw(c['name'],'参数\n'+json.dumps(c['arguments'],ensure_ascii=False,indent=2)+'\n\n原始返回\n'+'\n\n'.join(r['text'] for r in c['returns']))
