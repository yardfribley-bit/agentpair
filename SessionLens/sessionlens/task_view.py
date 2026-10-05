"""Native task lenses with explicitly historical reasoning playback."""
import json,html
from PySide6.QtCore import QTimer,Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QTextBrowser,QDialog,QPlainTextEdit

class TaskView(QWidget):
    evidenceRequested=Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent);self.data={};self.index=0;self.position=0
        self.setStyleSheet('QPushButton{background:#f3f6fa;color:#25496e;border:1px solid #d8e2ed;border-radius:7px;padding:8px;} QTextBrowser{background:#ffffff;border:1px solid #dce4ee;border-radius:10px;padding:18px;}')
        v=QVBoxLayout(self);self.heading=QLabel();self.heading.setWordWrap(True);self.heading.setStyleSheet('font-size:18px;font-weight:600');v.addWidget(self.heading)
        self.summary=QLabel();self.summary.setWordWrap(True);v.addWidget(self.summary)
        tabs=QHBoxLayout()
        for label,key in [('怎么做的','overview'),('当时怎么想','reasoning'),('调用了什么','calls'),('用到什么上下文','context'),('交付与验证','delivery')]:
            b=QPushButton(label);b.clicked.connect(lambda checked=False,k=key:self.select(k));tabs.addWidget(b)
        v.addLayout(tabs)
        self.controls=QWidget();row=QHBoxLayout(self.controls);row.setContentsMargins(0,0,0,0)
        self.play=QPushButton('播放历史思路');self.play.clicked.connect(self.toggle);row.addWidget(self.play)
        next_button=QPushButton('下一段');next_button.clicked.connect(self.next);row.addWidget(next_button)
        self.speed=QComboBox();self.speed.addItems(['1×','2×','4×']);row.addWidget(self.speed)
        original=QPushButton('完整思路原文');original.clicked.connect(self.original);row.addWidget(original);row.addStretch();v.addWidget(self.controls)
        self.body=QTextBrowser();self.body.setMinimumHeight(400);self.body.setOpenLinks(False);self.body.anchorClicked.connect(self.detail);v.addWidget(self.body,1)
        self.timer=QTimer(self);self.timer.setInterval(35);self.timer.timeout.connect(self.tick)
    def load(self,result):
        self.timer.stop();self.result=result;self.data=result['presentation'];self.index=0;self.position=0
        self.heading.setText(self.data['source']+' · '+self.data['prompt']);self.summary.setText(result['understanding']['overview']['text']);self.select('reasoning' if any(w in result['question'] for w in ('思路','思考','reasoning')) else 'calls' if any(w in result['question'] for w in ('工具','调用','参数')) else 'context' if any(w in result['question'] for w in ('上下文','记忆')) else 'delivery' if any(w in result['question'] for w in ('交付','时长')) else 'overview')
    def select(self,key):
        self.timer.stop();self.play.setText('播放历史思路');self.key=key;self.controls.setVisible(True);self.paint()
    def text(self,value):return '<p>'+html.escape(str(value)).replace('\n','<br>')+'</p>'
    def paint(self):
        d=self.data;out='<style>body{color:#24354a;font-size:14px}p{line-height:155%}h3{color:#244f7c}a{color:#3265a8}hr{color:#dce4ee}</style>'
        out+='<p>'+ ' · '.join('<a href="proof:'+html.escape(ref)+'">核对依据 '+html.escape(ref)+'</a>' for ref in self.result['understanding']['overview']['evidenceRefs'][:3])+'</p>'
        if self.key in ('reasoning','overview'):
            frames=d['frames']
            if not frames:out+=self.text('该任务没有记录可回放的思路。')
            else:
                f=frames[self.index];text=f['reasoning']['text'][:400];out+='<h3><span style="color:'+('#2f91bb' if self.timer.isActive() and self.position%2 else '#3b6387')+'">◉</span> 历史思路回放 · '+str(self.index+1)+' / '+str(len(frames))+'</h3>'+self.text(text[:self.position] if self.position else text)
                c=next((c for c in d['calls'] if c['id']==f['call']),None)
                if c:out+='<hr><h3>动作 → 返回 → 后续调整</h3>'+self.text(c['name']+' → '+('已记录返回' if c['returns'] else '缺少返回'))
                next_reason=next((r for r in d['reasoning'] if r['id']==f['nextReason']),None)
                if next_reason:out+=self.text(next_reason['text'][:400])
                out+=self.text('原日志思路摘录；完整内容见“完整思路原文”。')
        if self.key in ('calls','overview'):
            for n,c in enumerate(d['calls']):
                out+='<h3>'+str(n+1)+'. '+html.escape(c['name'])+'</h3>'
                notes=self.result['understanding'].get('toolExplanations',[])
                note=next((x for x in notes if any(f.get('eventId')==c['id'] and f.get('evidenceId') in x.get('evidenceRefs',[]) for f in self.result['packet']['fragments'])),None)
                if note:out+=self.text(note.get('purpose',''))+self.text(note.get('inputSummary',''))+self.text(note.get('outputSummary',''))
                for f in c['fields']:
                    value=f['value']
                    if len(value)>350:value='完整内容共 '+str(len(value))+' 字，可展开参数查看。' if note else value[:350]+'…（展开查看）'
                    out+='<b>'+html.escape(f['label'])+'</b>'+self.text(value)
                out+='<a href="call:'+str(n)+'">完整参数与原始返回</a>'
                for r in c['returns']:
                    try:
                        value=json.loads(r['text']);readable={k:v for k,v in value.items() if k in ('status','mode','videos','files','message','previewed','explanation')} if isinstance(value,dict) else value
                        out+='<h4>工具返回</h4>'
                        if isinstance(readable,dict) and readable:
                            for k,v in readable.items():out+='<b>'+html.escape({'status':'状态','mode':'生成方式','videos':'生成文件','files':'交付文件','message':'执行结果','previewed':'已预览文件','explanation':'说明'}.get(k,k))+'</b>'+self.text({'completed':'已完成','text-to-video':'文字生成视频'}.get(str(v),json.dumps(v,ensure_ascii=False) if not isinstance(v,str) else v))
                        elif not note:out+=self.text(r['text'][:700]+'（完整返回可展开查看）')
                    except ValueError:out+='<h4>工具返回</h4>'+self.text(r['text'][:1800])
                if not c['returns']:out+=self.text('未记录关联返回，不能据此确认执行成功。')
                out+='<hr>'
        if self.key=='context':
            for c in d['context']:out+='<h3>'+html.escape(c['kind'])+'</h3>'+self.text(c['text'])
            if not d['context']:out+=self.text('该任务没有单独记录上下文。')
        if self.key=='delivery':
            out+='<h3>用户要求</h3>'+self.text(d['prompt'])+'<h3>Agent 最后的答复</h3>'+self.text(d['replies'][-1]['text'] if d['replies'] else '未记录最终答复')
            out+='<h3>验证边界</h3>'+self.text('工具报告完成与独立验收是两回事。这里展示日志记录，未重新执行工具或检查交付物。')
        for gap in self.result['understanding'].get('gaps',[]):out+=self.text(gap)
        out+=self.text('本地读取 '+str(d['included'])+' / '+str(d['total'])+' 条记录；长正文可能截取，完整原文可在历史任务中查看。')
        self.body.setHtml(out)
    def toggle(self):
        if self.timer.isActive():self.timer.stop();self.play.setText('继续回放')
        elif self.data.get('frames'):
            if self.position>=min(400,len(self.data['frames'][self.index]['reasoning']['text'])):self.position=0
            self.key='reasoning';self.timer.start();self.play.setText('暂停')
        self.paint()
    def tick(self):
        self.position+=3*(2**self.speed.currentIndex());text=self.data['frames'][self.index]['reasoning']['text'][:400]
        if self.position>=len(text):self.timer.stop();self.play.setText('再次回放')
        self.paint()
    def next(self):
        if self.data.get('frames'):self.index=(self.index+1)%len(self.data['frames']);self.position=0;self.timer.stop();self.paint()
    def show_raw(self,title,text):
        dialog=QDialog(self);dialog.setWindowTitle(title);dialog.resize(900,650);v=QVBoxLayout(dialog);edit=QPlainTextEdit();edit.setReadOnly(True);edit.setPlainText(text);v.addWidget(edit);dialog.exec()
    def original(self):self.show_raw('记录中的思路原文','\n\n'.join(r['text'] for r in self.data.get('reasoning',[])))
    def detail(self,url):
        if url.toString().startswith('proof:'):self.evidenceRequested.emit(url.toString().split(':')[1]);return
        c=self.data['calls'][int(url.toString().split(':')[1])];self.show_raw(c['name'],'参数\n'+json.dumps(c['arguments'],ensure_ascii=False,indent=2)+'\n\n原始返回\n'+'\n\n'.join(r['text'] for r in c['returns']))
