from datetime import datetime
import html
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading

from PySide6.QtCore import Qt, QTimer, QLockFile, QSize, QObject, Signal
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,QTableWidgetItem,QDialog,QFormLayout,QLineEdit,QCheckBox,QDialogButtonBox,QPlainTextEdit,QMessageBox,QProgressBar,QGroupBox,QFileDialog,QComboBox,QSplitter,QListWidget,QListWidgetItem,QTextBrowser)
from sessionlens.supervision import TaskStore,describe,event_text
from sessionlens.assistant import run as run_assistant
from sessionlens.desktop import Runtime,defaults,state_root,LABELS

STYLE='''QWidget {font-family: Arial; font-size:14px; color:#233247; background:#F7F8FA;} QMainWindow {background:#F7F8FA;} QLabel {background:transparent;} QLabel#title {font-size:28px;font-weight:700;} QLabel#sub {color:#6a7a91;} QGroupBox {background:white;border:1px solid #E6E8EC;border-radius:10px;margin-top:12px;padding:18px;} QGroupBox::title {subcontrol-origin:margin;left:16px;padding:0 5px;font-weight:600;} QPushButton {background:#1769ef;color:white;border:0;border-radius:6px;padding:10px 16px;} QPushButton:checked {background:#e9f0fb;color:#275eb2;border:1px solid #275eb2;} QPushButton:disabled {background:#9aaac3;} QTextBrowser,QListWidget,QLineEdit,QPlainTextEdit,QTableWidget {background:white;border:1px solid #E6E8EC;border-radius:5px;padding:5px;} QListWidget::item:selected {background:#e9f0fb;color:#202d3d;} QTextBrowser {padding:14px;} QHeaderView::section {background:#edf2fa;padding:9px;border:0;font-weight:600;} QProgressBar {border:0;background:#e4eaf5;height:8px;border-radius:4px;text-align:center;} QProgressBar::chunk {background:#1769ef;border-radius:4px;}'''

def display_time(value):
    try:
        if str(value).isdigit():return datetime.fromtimestamp(int(value)/1000).strftime('%m-%d %H:%M')
        return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone().strftime('%m-%d %H:%M')
    except (ValueError,OSError,OverflowError):return '时间未记录'

class AssistantSignals(QObject):
    progress=Signal(str)
    result=Signal(object)
    failed=Signal(str)

class AssociationSignals(QObject):
    progress=Signal(str)
    result=Signal(object)
    failed=Signal(str)

class Settings(QDialog):
    def __init__(self,config,parent):
        super().__init__(parent);self.setWindowTitle('采集与上报设置');self.resize(700,540);self.config=config
        form=QFormLayout(self);self.sources={}
        for source,title in [('codex','Codex'),('workbuddy','WorkBuddy')]:
            check=QCheckBox('采集 '+title);check.setChecked(config['sources'][source]['enabled'])
            paths=QLineEdit(';'.join(config['sources'][source]['roots']));form.addRow(check,paths);self.sources[source]=(check,paths)
        self.endpoint=QLineEdit(config.get('endpoint',''));self.endpoint.setPlaceholderText('https://www.chuhaijian.com/api/sessionlens/events');form.addRow('上报接口',self.endpoint)
        self.token=QLineEdit();self.token.setEchoMode(QLineEdit.Password);form.addRow('设备令牌（仅本次运行）',self.token)
        self.assistant_url=QLineEdit(config.get('assistant',{}).get('url',''));form.addRow('AgentPair 分析接口',self.assistant_url)
        self.assistant_token=QLineEdit(config.get('assistant',{}).get('tokenFile',''));form.addRow('分析授权文件',self.assistant_token)
        self.model_url=QLineEdit(config.get('model',{}).get('url',''));form.addRow('助手模型地址',self.model_url)
        self.model_name=QLineEdit(config.get('model',{}).get('name',''));form.addRow('助手模型名称',self.model_name)
        self.model_credential=QLineEdit(config.get('model',{}).get('credentialFile',''));form.addRow('模型密钥文件',self.model_credential)
        self.embedding_enabled=QCheckBox('启用本机语义检索（不上传日志）');self.embedding_enabled.setChecked(config.get('embedding',{}).get('enabled',False));form.addRow(self.embedding_enabled)
        self.embedding_directory=QLineEdit(config.get('embedding',{}).get('directory',str(state_root()/'models/bge-small-zh-v1.5')));form.addRow('本地 embedding 模型目录',self.embedding_directory)
        hint=QLabel('目录以分号分隔。开启上报后，会发送所选日志的完整记录。\n未配置接口和令牌时，仅保存在本机；关闭应用停止采集。');hint.setWordWrap(True);form.addRow(hint)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);form.addRow(buttons)
    def result_config(self):
        result=dict(self.config)
        result.update({'sources':{s:{'enabled':c.isChecked(),'roots':[x.strip() for x in p.text().split(';') if x.strip()]} for s,(c,p) in self.sources.items()},'endpoint':self.endpoint.text().strip(),'assistant':{'url':self.assistant_url.text().strip(),'tokenFile':self.assistant_token.text().strip()}})
        model=dict(self.config.get('model',{}));model.update(url=self.model_url.text().strip(),name=self.model_name.text().strip(),credentialFile=self.model_credential.text().strip());result['model']=model
        embedding=dict(self.config.get('embedding',{}));embedding.update(enabled=self.embedding_enabled.isChecked(),provider='local-onnx',directory=self.embedding_directory.text().strip());result['embedding']=embedding
        return result

class Window(QMainWindow):
    def __init__(self,root):
        super().__init__();self.root=root;self.runtime=None;self.mode='live';self.selected=None;self.selected_event=None;self.signature=None;self.step_limit=300;self.answers={};self.show_records=False
        self.setWindowTitle('SessionLens · 任务监督与历史追溯');self.resize(1280,850)
        self.config=defaults()
        try:self.config=json.loads((root/'settings.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        # Initialize schema only; historical projection runs in a worker.
        bootstrap=Runtime(root,self.config)
        self.store=TaskStore(root/'collector.db')
        body=QWidget();self.setCentralWidget(body);layout=QVBoxLayout(body);layout.setContentsMargins(24,20,24,16);layout.setSpacing(16)
        header=QHBoxLayout();title=QLabel('SessionLens');title.setObjectName('title');header.addWidget(title);header.addWidget(QLabel('任务监督与历史追溯'));header.addStretch();self.button=QPushButton('采集状态 / 设置');self.button.clicked.connect(self.configure);header.addWidget(self.button);layout.addLayout(header)
        nav=QHBoxLayout();self.live=QPushButton('监工模式');self.history=QPushButton('历史追溯');self.live.setCheckable(True);self.history.setCheckable(True);self.live.clicked.connect(lambda:self.set_mode('live'));self.history.clicked.connect(lambda:self.set_mode('history'));nav.addWidget(self.live);nav.addWidget(self.history);nav.addStretch();self.follow=QCheckBox('跟随最新动作');self.follow.setChecked(True);self.follow.toggled.connect(self.follow_changed);nav.addWidget(self.follow);self.status=QLabel('尚未开始采集');nav.addWidget(self.status);layout.addLayout(nav)
        self.toolbar=QWidget();tools=QHBoxLayout(self.toolbar);tools.setContentsMargins(0,0,0,0);self.search=QLineEdit();self.search.setPlaceholderText('搜索任务、文件或问题…');self.search.returnPressed.connect(self.reload);tools.addWidget(self.search);find=QPushButton('查找任务');find.clicked.connect(self.reload);tools.addWidget(find);self.source=QComboBox();self.source.addItems(['全部 Agent','Codex','WorkBuddy']);self.source.currentIndexChanged.connect(self.reload);tools.addWidget(self.source);layout.addWidget(self.toolbar)
        assistant_bar=QHBoxLayout();self.question=QLineEdit();self.question.setPlaceholderText('问当前任务：当时为什么这样做？用了什么参数？结果可靠吗？')
        self.question.returnPressed.connect(self.ask_assistant);assistant_bar.addWidget(self.question)
        self.ask=QPushButton('理解这次任务');self.ask.clicked.connect(self.ask_assistant);assistant_bar.addWidget(self.ask)
        self.associate=QPushButton('关联多轮对话');self.associate.clicked.connect(self.review_association);assistant_bar.addWidget(self.associate);layout.addLayout(assistant_bar)
        self.analysis_status=QLabel('选择任务后提问 · AgentPair · 仅发送当前任务证据');self.analysis_status.setWordWrap(True);layout.addWidget(self.analysis_status)
        self.assistant_signals=AssistantSignals(self);self.assistant_signals.progress.connect(self.analysis_status.setText);self.assistant_signals.result.connect(self.assistant_ready);self.assistant_signals.failed.connect(self.assistant_failed)
        self.association_signals=AssociationSignals(self);self.association_signals.progress.connect(self.analysis_status.setText);self.association_signals.result.connect(self.association_ready);self.association_signals.failed.connect(self.association_failed)
        split=QSplitter(Qt.Horizontal);layout.addWidget(split,1)
        left=QWidget();lv=QVBoxLayout(left);lv.setContentsMargins(0,0,0,0);self.list_title=QLabel('当前任务');lv.addWidget(self.list_title);self.task_list=QListWidget();self.task_list.setWordWrap(True);self.task_list.currentRowChanged.connect(self.select_task);lv.addWidget(self.task_list);split.addWidget(left)
        self.middle=QTextBrowser();self.middle.setOpenLinks(False);self.middle.anchorClicked.connect(self.follow_link);split.addWidget(self.middle)
        right=QWidget();rv=QVBoxLayout(right);rv.setContentsMargins(0,0,0,0);rv.addWidget(QLabel('对应证据'));self.proof=QTextBrowser();rv.addWidget(self.proof,1);self.mark=QPushButton('标记待核实');self.mark.clicked.connect(self.mark_event);rv.addWidget(self.mark);raw=QPushButton('查看来源定位与原始片段');raw.clicked.connect(self.details);rv.addWidget(raw);split.addWidget(right);split.setSizes([230,640,330]);split.setChildrenCollapsible(False)
        self.foot=QLabel('只展示已记录的动作；待核实标记不会暂停 Agent。');self.foot.setWordWrap(True);layout.addWidget(self.foot)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(1200);self.set_mode('live')
    def review_association(self):
        if not self.selected or not self.associate.isEnabled():return
        from sessionlens.semantic_lineage import candidate_turns
        task=self.selected;model=self.config.get('model',{})
        if not model.get('url') or not model.get('credentialFile'):
            self.analysis_status.setText('请先在设置中配置助手模型，再核对多轮对话。');return
        turns=candidate_turns(self.store.db,task)
        if not turns:return
        dialog=QDialog(self);dialog.setWindowTitle('核对多轮对话的任务归属');dialog.resize(700,540);layout=QVBoxLayout(dialog)
        from urllib.parse import urlparse
        hint=QLabel(f'核对同一会话的 {len(turns)} 轮提问，结合回复、已记录思路和工具片段。\n发送至已配置模型：{urlparse(model["url"]).netloc}。歧义轮次保持独立，原文保留。');hint.setWordWrap(True);layout.addWidget(hint)
        listing=QListWidget()
        for ident,prompt,_ in turns:
            item=QListWidgetItem(prompt[:220]);item.setFlags(item.flags()|Qt.ItemIsUserCheckable);item.setCheckState(Qt.Checked);item.setData(Qt.UserRole,ident);listing.addItem(item)
        layout.addWidget(listing)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.button(QDialogButtonBox.Ok).setText('开始关联');buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()!=QDialog.Accepted:return
        scope=[listing.item(i).data(Qt.UserRole) for i in range(listing.count()) if listing.item(i).checkState()==Qt.Checked]
        if task not in scope:
            self.analysis_status.setText('请保留所选任务，再核对它与其他轮次的关系。');return
        self.start_association(task,scope)
    def start_association(self,task,scope):
        if not self.associate.isEnabled():return
        self.associate.setEnabled(False);self.ask.setEnabled(False);self.follow.setChecked(False)
        model=dict(self.config.get('model',{}));self.analysis_status.setText('正在核对多轮需求，采集继续运行…')
        def work():
            from sessionlens.semantic_lineage import refine
            try:
                with sqlite3.connect(self.root/'collector.db',timeout=10) as db:
                    root=refine(db,model,task,'核对这些轮次是否延续同一用户目标，保留中断、重试和不同任务的边界。',self.association_signals.progress.emit,turn_ids=scope)
                self.association_signals.result.emit({'task':task,'root':root,'scope':scope})
            except Exception as exc:self.association_signals.failed.emit(str(exc)[:250])
        threading.Thread(target=work,daemon=True).start()
    def association_ready(self,result):
        from sessionlens.task_lineage import history,resolve
        self.associate.setEnabled(True);self.ask.setEnabled(True)
        # Only follow the review if the user is still looking at its task.
        if self.selected in result['scope'] or resolve(self.store.db,self.selected)==result['root']:
            self.selected=result['root'];self.selected_event=None
        self.answers.clear();self.signature=None;self.reload()
        count=len(history(self.store.db,result['root']))
        self.analysis_status.setText(f'关联已核对 · 这个需求包含 {count} 轮对话 · 可点击每轮原文检查归属')
    def association_failed(self,error):
        self.associate.setEnabled(True);self.ask.setEnabled(True);self.analysis_status.setText('关联未完成，原始对话仍保留：'+error)
    def ask_assistant(self):
        if not self.selected or not self.ask.isEnabled():return
        self.follow.blockSignals(True);self.follow.setChecked(False);self.follow.blockSignals(False)
        task=self.selected;question=self.question.text().strip() or '这次任务是怎么完成的？解释当时的依据、关键工具参数、返回、调整过程和最后结果。'
        self.ask.setEnabled(False);self.associate.setEnabled(False);self.analysis_status.setText('正在准备当前任务证据…')
        def work():
            try:result=run_assistant(self.root,self.config.get('assistant',{}),task,question,self.assistant_signals.progress.emit);self.assistant_signals.result.emit(result)
            except Exception as exc:self.assistant_signals.failed.emit(str(exc)[:250])
        threading.Thread(target=work,daemon=True).start()
    def assistant_ready(self,result):
        self.ask.setEnabled(True);self.associate.setEnabled(True);self.answers[result['taskId']]=result;self.show_records=False
        self.analysis_status.setText('助手已回答 · AgentPair 已复核 · 点击证据核对原文')
        if self.selected==result['taskId']:self.select_task(self.task_list.currentRow())
    def assistant_failed(self,error):
        self.ask.setEnabled(True);self.associate.setEnabled(True);self.analysis_status.setText('分析未完成：'+error)
    def understanding_html(self,result):
        esc=html.escape;value=result['understanding'];packet=result['packet']
        text='<h3>任务助手</h3><p style="color:#67778b">'+esc(result.get('question',''))+'</p>'
        for block in [value['overview']]+value['steps']:
            if block.get('title'):text+='<h3>'+esc(block['title'])+'</h3>'
            prefix={'inferred':'推断：','unknown':'尚不能确定：','recorded':''}.get(block['basis'],'')
            text+='<p>'+esc(prefix+block['text']).replace('\n','<br>')+'</p><p>'
            text+=' · '.join('<a href="ai:'+esc(ref)+'">'+esc(ref)+' 查看依据</a>' for ref in block['evidenceRefs'])+'</p>'
        if value.get('gaps'):text+='<h3>还不能确定的地方</h3>'+''.join('<p>'+esc(str(x))+'</p>' for x in value['gaps'])
        text+=f'<p style="color:#67778b">基于 {packet["includedRecords"]}/{packet["totalRecords"]} 条任务记录；部分长记录可能截取。</p>'
        text+='<p><a href="records:">'+('收起逐条记录' if self.show_records else '展开逐条记录')+'</a></p>'
        return text
    def set_mode(self,mode):
        self.mode=mode;self.toolbar.setVisible(mode=='history');self.live.setChecked(mode=='live');self.history.setChecked(mode=='history');self.follow.setVisible(mode=='live');self.signature=None;self.reload()
    def follow_changed(self):
        self.signature=None;self.reload()
    def start_local(self):
        local=json.loads(json.dumps(self.config));local['endpoint']=''
        self.runtime=Runtime(self.root,local);self.runtime.start()
    def configure(self):
        if self.runtime:
            with self.runtime.lock:status=dict(self.runtime.status)
            message=status.get('task_index','整理中')+'\n'+status.get('upload','')+'\n本地存储：'+str(self.runtime.path)
            QMessageBox.information(self,'采集状态',message)
            self.runtime.stop.set();self.stopping=True;self.button.setEnabled(False);return
        self.open_settings()
    def open_settings(self):
        dialog=Settings(self.config,self)
        if dialog.exec()!=QDialog.Accepted:return
        self.config=dialog.result_config();(self.root/'settings.json').write_text(json.dumps(self.config,ensure_ascii=False,indent=2),encoding='utf-8');os.chmod(self.root/'settings.json',0o600)
        self.runtime=Runtime(self.root,self.config,dialog.token.text());self.runtime.start()
    def refresh(self):
        if getattr(self,'stopping',False):
            if any(t.is_alive() for t in self.runtime.threads):return
            self.runtime=None;self.stopping=False;self.button.setEnabled(True);self.open_settings();return
        if self.runtime:
            with self.runtime.lock:s=dict(self.runtime.status)
            self.status.setText(s.get('task_index','正在读取日志')+' · '+s.get('upload','仅本地'))
        self.reload()
    def reload(self):
        from sessionlens.task_lineage import resolve
        source=['','codex','workbuddy'][self.source.currentIndex()] if self.mode=='history' else ''
        try:rows=self.store.tasks(self.search.text() if self.mode=='history' else '',source,self.mode=='live')
        except sqlite3.Error:return
        sig=(self.mode,tuple(rows),self.search.text(),source)
        if sig==self.signature:return
        self.signature=sig;old=None if self.mode=='live' and self.follow.isChecked() else resolve(self.store.db,self.selected);
        if self.mode=='live' and self.follow.isChecked():self.selected_event=None
        self.rows=rows;self.task_list.blockSignals(True);self.task_list.clear()
        for row in rows:
            count=self.store.db.execute('SELECT turn_count FROM task_groups WHERE id=?',(row[0],)).fetchone()
            rounds=f' · {count[0]} 轮对话' if count else ''
            item=QListWidgetItem(row[1].title()+' · '+display_time(row[4])+rounds+'\n'+row[3].replace('\n',' ')[:100]);item.setSizeHint(QSize(210,90));self.task_list.addItem(item)
        self.task_list.blockSignals(False);index=next((i for i,r in enumerate(rows) if r[0]==old),0)
        self.list_title.setText(('当前任务' if self.mode=='live' else '相关任务')+f' · {len(rows)}')
        if rows:self.task_list.setCurrentRow(index);self.select_task(index)
        else:self.selected=None;self.selected_event=None;self.middle.setHtml('<h2>当前索引尚未找到匹配任务</h2><p>历史仍在后台整理时，尚未索引的任务暂时无法搜索。这不代表原日志中没有记录。</p>');self.proof.setHtml('<p>选择任务后展示对应原文。</p>')
    def select_task(self,index):
        if index<0 or index>=len(getattr(self,'rows',[])):return
        row=self.rows[index]
        if self.selected!=row[0]:self.step_limit=300
        self.selected=row[0];self.steps=self.store.steps(row[0],self.step_limit,latest=self.mode=='live');esc=html.escape
        last=self.store.db.execute('SELECT event,kind,excerpt,call_id FROM linked_task_steps WHERE task=? ORDER BY seq DESC LIMIT 1',(row[0],)).fetchone()
        outcome=last[2][:450] if last else '尚未记录后续动作'
        title=row[3].splitlines()[0][:90]
        content='<style>a {color:#275eb2;text-decoration:none;} p {line-height:150%;} blockquote {color:#202d3d;}</style>'+f'<h2>{esc(title)}</h2><p style="color:#67778b">{esc(row[1])} · {esc(display_time(row[4]))} · {esc(row[5])}</p><div style="background:#edf2f8"><h3>{"现在记录到什么" if self.mode=="live" else "这次实际做了什么"}</h3><p>{esc(outcome)}</p></div><h3>你的原始要求</h3><blockquote>{esc(row[3]).replace(chr(10),"<br>")}</blockquote><hr><p style="color:#67778b">按记录顺序展示；较长任务分批展开，原文完整保留。</p><h3>{"已经看到的动作" if self.mode=="live" else "处理经过与依据"}</h3>'
        from sessionlens.task_lineage import dialogues,signature
        turns=dialogues(self.store.db,row[0],limit=12);total_turns=turns[0]['totalTurns'] if turns else 0;dialogue_html=f'<h3>需求对话 · {total_turns} 轮</h3>'
        if len(turns)<total_turns:dialogue_html+='<p>显示最初要求和最近 11 轮；全部轮次保留在任务记录中。</p>'
        for turn in turns:
            number=turn['ordinal']
            label={'semantic_inference':'模型判断','confirmed_by_user':'用户修正','provisional':'初步关联'}[turn['association']]
            dialogue_html+=f'<p><b>{number} · {esc(turn["label"])}{ " · 本轮中断" if turn["interrupted"] else ""}</b> <span style="color:#67778b">{label}</span><br><a href="dialogue:{turn["eventId"]}">{esc(turn["text"][:220])}</a></p>'
            for reply in turn['replies']:
                dialogue_html+=f'<p style="color:#67778b">Agent：<a href="dialogue:{reply["eventId"]}">{esc(reply["text"][:150])}</a></p>'
        content=dialogue_html+'<hr>'+content
        if row[0] not in self.answers and (self.root/'assistant.db').exists():
            try:
                with sqlite3.connect(self.root/'assistant.db') as cache:saved=cache.execute('SELECT result FROM answers WHERE task=? ORDER BY rowid DESC LIMIT 1',(row[0],)).fetchone()
                if saved:self.answers[row[0]]=json.loads(saved[0])
            except (sqlite3.Error,ValueError):pass
        answer=self.answers.get(row[0])
        if answer and answer.get('packet',{}).get('lineageSignature')!=signature(self.store.db,row[0]):
            answer=None;self.answers.pop(row[0],None)
        if answer:
            content='<style>a {color:#275eb2;text-decoration:none;}</style><h2>'+esc(title)+'</h2>'+self.understanding_html(answer)+(content if self.show_records else dialogue_html)
            if not self.show_records:
                self.middle.setHtml(content);self.foot.setText('模型解释可以有误。点击每个结论下的证据核对当前任务记录。')
                fragments=answer['packet']['fragments']
                if self.selected_event not in {f['eventId'] for f in fragments}:
                    first=next((f for f in fragments if f['evidenceId']==answer['understanding']['overview']['evidenceRefs'][0]),fragments[0]);self.show_evidence(first['eventId'],first['evidenceId'])
                if row[6]>answer['packet']['revision']:self.analysis_status.setText('这是此前记录的分析；任务有新记录，可再次提问更新解释。')
                return
        calls={s[3] for s in self.steps if s[1]=='工具调用' and s[3]};results={s[3] for s in self.steps if s[1]=='工具返回' and s[3]}
        for i,s in enumerate(self.steps):
            focused=s[0]==self.selected_event
            step_title,step_body=describe(s[1],s[2])
            content+=f'<div style="background:{"#e9f0fb" if focused else "#ffffff"};margin:8px;padding:8px"><a href="e:{i}"><b>{i+1} · {esc(step_title)}</b>　E{i+1:03}</a><p>{esc(step_body[:300]).replace(chr(10),"<br>")}</p></div>'
        total=self.store.db.execute('SELECT count(*) FROM linked_task_steps WHERE task=?',(row[0],)).fetchone()[0]
        if self.mode=='history' and total>len(self.steps):content+=f'<p><a href="more:">继续展开 · 已显示 {len(self.steps)} / {total} 个步骤</a></p>'
        gaps=[]
        if total==len(self.steps) and calls-results:gaps.append(f'{len(calls-results)} 个工具调用尚未找到对应返回。')
        if total==len(self.steps) and not any(s[1]=='解题思路' for s in self.steps):gaps.append('这段日志没有记录处理思路，无法据此说明为什么这样做。')
        if row[5]=='结束状态未记录':gaps.append('没有明确的任务结束记录。')
        if gaps:content+='<div style="background:#fff5e7;color:#875820"><h3>这次还有什么没说明白</h3><p>'+ '<br>'.join(gaps)+'</p></div>'
        related=self.store.db.execute('SELECT id,prompt FROM task_groups WHERE source=? AND session=? AND id<>? ORDER BY last_row DESC LIMIT 4',(row[1],row[2],row[0])).fetchall()
        if related:
            content+='<hr><h3>同一会话 · 继续追溯前后任务</h3>'
            for identity,prompt in related:content+=f'<p><a href="t:{identity}">{esc(prompt[:100])}</a></p>'
        self.middle.setHtml(content)
        self.foot.setText('日志证据 · 会话 '+row[2]+' · 点击一步核对原文。摘要是记录摘录，不代表独立验证的结论。')
        if self.steps:
            chosen=next((i for i,s in enumerate(self.steps) if s[0]==self.selected_event),len(self.steps)-1);self.select_evidence(chosen)
    def follow_link(self,url):
        target=url.toString()
        if target=='records:':self.show_records=not self.show_records;self.select_task(self.task_list.currentRow())
        elif target.startswith('ai:'):
            result=self.answers.get(self.selected,{})
            fragment=next((f for f in result.get('packet',{}).get('fragments',[]) if f['evidenceId']==target[3:]),None)
            if fragment:self.show_evidence(fragment['eventId'],fragment['evidenceId'])
        elif target=='more:':self.step_limit+=300;self.select_task(self.task_list.currentRow())
        elif target.startswith('e:'):
            self.follow.blockSignals(True);self.follow.setChecked(False);self.follow.blockSignals(False)
            self.select_evidence(int(target[2:]));self.select_task(self.task_list.currentRow())
        elif target.startswith('dialogue:'):
            identity=target[len('dialogue:'):]
            if self.store.db.execute('SELECT 1 FROM linked_task_steps WHERE event=? AND task=?',(identity,self.selected)).fetchone():
                self.follow.setChecked(False);self.show_evidence(identity,'对话原文')
        elif target.startswith('t:'):
            self.mode='history';self.toolbar.show();self.search.clear();self.selected=target[2:];self.signature=None;self.reload()
    def select_evidence(self,index):
        self.show_evidence(self.steps[index][0],f'E{index+1:03}')
    def show_evidence(self,identity,ref):
        self.selected_event=identity;e=self.store.evidence(identity);esc=html.escape
        if not e:return
        loc=e.get('evidence',{});quote=event_text(e);call=e.get('callId');linked=''
        if call:
            peers=self.store.db.execute('SELECT e.event FROM linked_task_steps s JOIN events e ON e.id=s.event WHERE s.task=? AND s.call_id=? ORDER BY s.seq',(self.selected,call)).fetchall()
            linked='<hr><h3>这次工具往返</h3>'+''.join('<p><b>'+esc(json.loads(raw)['kind'])+'</b></p><pre style="white-space:pre-wrap">'+esc(event_text(json.loads(raw))[:6000])+'</pre>' for (raw,) in peers)
        self.proof.setHtml(f'<h3>{esc(ref)} · {esc(e.get("name") or e["kind"])}</h3><p>{esc(display_time(e.get("timestamp")))}</p><pre style="white-space:pre-wrap">{esc(quote[:12000])}</pre>{linked}<hr><p>来源：{esc(str(loc.get("path","未记录")))}</p><p>字节 {loc.get("byteStart","?")}–{loc.get("byteEnd","?")}</p><p>完整原文保存在本机，可导出核对。</p>')
        self.mark.setText('取消待核实标记' if self.store.marked(identity) else '标记待核实')
    def mark_event(self):
        if self.selected_event:self.store.mark(self.selected_event);self.mark.setText('取消待核实标记' if self.store.marked(self.selected_event) else '标记待核实')
    def details(self):
        if not self.selected_event:return
        with sqlite3.connect(self.root/'collector.db') as db:record=db.execute('SELECT event FROM events WHERE id=?',(self.selected_event,)).fetchone()
        dialog=QDialog(self);dialog.setWindowTitle('采集原文与证据来源');dialog.resize(900,600);v=QVBoxLayout(dialog);text=QPlainTextEdit();text.setReadOnly(True);raw=record[0]
        text.setPlainText(raw[:100000] if len(raw)>100000 else json.dumps(json.loads(raw),ensure_ascii=False,indent=2));v.addWidget(text);export=QPushButton('导出完整记录');v.addWidget(export)
        def save():
            filename,_=QFileDialog.getSaveFileName(dialog,'导出完整采集记录','sessionlens-record.json','JSON (*.json)')
            if filename:
                try:Path(filename).write_text(raw,encoding='utf-8')
                except OSError as exc:QMessageBox.warning(dialog,'导出失败',str(exc))
        export.clicked.connect(save);dialog.exec()
    def closeEvent(self,event):
        if getattr(self,'hide_on_close',False):self.hide();event.ignore();return
        if self.runtime:self.runtime.stop.set()
        self.store.close();event.accept()

def main():
    embedding_test='--self-test-embedding' in sys.argv
    test='--self-test' in sys.argv or embedding_test
    query_test='--verify-assistant' in sys.argv
    conversation_test='--verify-conversation' in sys.argv
    verify='--verify-ui' in sys.argv or query_test or conversation_test
    if test or verify:os.environ['QT_QPA_PLATFORM']='offscreen'
    app=QApplication(sys.argv);app.setStyleSheet(STYLE)
    root=Path(__import__('tempfile').mkdtemp(prefix='sessionlens-test-')) if test else state_root();root.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(root/'desktop.lock'));lock.setStaleLockTime(0)
    if not test and not verify and not lock.tryLock(0):QMessageBox.information(None,'SessionLens','SessionLens 已在运行。');return 1
    window=Window(root)
    from sessionlens.chat_window import ChatWindow
    chat=ChatWindow(window)
    if test:
        from sessionlens.project_context import ProjectStore
        from sessionlens.knowledge_index import KnowledgeIndex
        from sessionlens.embedding_store import EmbeddingStore
        projects=ProjectStore(root/'project_context.db');knowledge=KnowledgeIndex(root/'knowledge.db',projects=projects);vectors=EmbeddingStore(root/'embeddings.db',projects=projects)
        vectors.close();knowledge.close();projects.close()
        if embedding_test:
            from sessionlens.embedding import load
            directory=sys.argv[sys.argv.index('--self-test-embedding')+1]
            engine=load({'enabled':True,'directory':directory});values=engine.encode(['查询天气','生成视频'])
            assert len(values)==2 and all(len(v)==512 for v in values)
            assert all(abs(sum(x*x for x in v)-1)<.01 for v in values)
            print('Packaged local embedding self-test passed: 512 dimensions, CPU, no network')
        chat.close();print('SessionLens desktop self-test passed');return 0
    if conversation_test:
        from time import monotonic
        from sessionlens import relay_model
        fixture_data=json.loads(Path(sys.argv[sys.argv.index('--verify-conversation')+1]).read_text())
        fixture=fixture_data['steps'] if isinstance(fixture_data,dict) else fixture_data
        output=Path(sys.argv[sys.argv.index('--verify-conversation')+2])
        original_answer=relay_model.answer
        allowed={step['taskId'] for step in fixture if step.get('taskId')}
        def guarded_answer(root,config,q,packet,history):
            if packet['taskId'] not in allowed:raise ValueError('测试选错任务，未发送其他任务数据')
            return original_answer(root,config,q,packet,history)
        relay_model.answer=guarded_answer
        # QA reads the real local task library, but never inserts synthetic chats
        # into the user's recent-conversation list.
        chat.cache.close();chat.cache=sqlite3.connect(':memory:');chat.cache.execute('CREATE TABLE chats(id TEXT PRIMARY KEY,title TEXT,content TEXT,updated INTEGER)')
        chat.refresh_chats();chat.messages=[]
        source=fixture_data.get('source') if isinstance(fixture_data,dict) else None
        chat.source.setCurrentIndex(chat.source.findData(source));chat.show()
        initial=fixture_data.get('initialMessages',[]) if isinstance(fixture_data,dict) else []
        if initial:
            chat.cache.execute('INSERT INTO chats VALUES(?,?,?,?)',('verification','历史问答',json.dumps(initial),1));chat.cache.commit();chat.refresh_chats();chat.open_chat(0)
            assert chat.messages[-1].get('selectionMismatch')
            assert chat.results.currentWidget()==chat.answer
            assert '选错了任务' in chat.answer.toPlainText()
            chat.grab().save(str(output.with_suffix(''))+'-cached.png')
            print('Cached-answer mismatch detected',flush=True)
        state={'index':0,'started':monotonic(),'results':[]};timer=QTimer(chat)
        def submit():
            if fixture[state['index']].get('fresh'):chat.new_chat()
            state['started']=monotonic();chat.input.setPlainText(fixture[state['index']]['question']);chat.send()
            assert chat.results.currentWidget()==chat.answer
            assert 'SSH' not in chat.answer.toPlainText() or 'SSH' in fixture[state['index']]['question']
        def check_conversation():
            if chat.busy and monotonic()-state['started']<240:return
            step=fixture[state['index']];result=chat.messages[-1] if chat.messages else {}
            if not chat.busy and result.get('selectionNeeded') and step.get('chooseTask'):
                assert chat.results.currentWidget()==chat.answer
                chat.grab().save(str(output.with_suffix(''))+'-choices.png')
                offered={option['taskId'] for option in result['options']}
                assert step['taskId'] in offered
                from PySide6.QtCore import QUrl
                chat.evidence(QUrl('choose:'+step['taskId']));state['started']=monotonic();return
            clarification=bool(step.get('selectionMode'))
            if clarification:
                success=not chat.busy and result.get('selectionNeeded') and result.get('selection',{}).get('mode')==step['selectionMode'] and chat.input.toPlainText()==step['question'] and chat.results.currentWidget()==chat.answer
            else:
                success=not chat.busy and result.get('taskId')==step['taskId'] and chat.input.toPlainText()==step['question'] and chat.results.currentWidget()==chat.task_view
            if success and not clarification:
                success=chat.task_view.data['taskId']==step['taskId'] and all(name in [c['name'] for c in chat.task_view.data['calls']] for name in step.get('tools',[]))
            state['results'].append(result)
            chat.grab().save(str(output.with_suffix(''))+'-'+str(state['index'])+'.png')
            print('Conversation step '+str(state['index']+1)+(' passed' if success else ' failed'),flush=True)
            if success and state['index']+1<len(fixture):state['index']+=1;submit();return
            timer.stop();output.write_text(json.dumps(state['results'],ensure_ascii=False));chat.close();app.exit(0 if success else 1)
        timer.timeout.connect(check_conversation);submit();timer.start(250);return app.exec()
    if query_test:
        from time import monotonic
        output=Path(sys.argv[sys.argv.index('--verify-assistant')+1]);question='那次 SSH 动画是怎么生成的，最后做成了吗？'
        from sessionlens import relay_model
        original_answer=relay_model.answer
        def guarded_answer(root,config,q,packet,history):
            if packet['taskId']!='f18fad6ea1ea44d94d706cbf6236c10633bd38bfe1e8689727de4f39ba029dcf':raise ValueError('测试查询选错了任务，未发送其他任务数据')
            return original_answer(root,config,q,packet,history)
        relay_model.answer=guarded_answer
        chat.source.setCurrentIndex(1);chat.input.setPlainText(question);chat.show();chat.send();started=monotonic();timer=QTimer(chat)
        def check_query():
            if chat.busy and monotonic()-started<240:return
            timer.stop()
            success=bool(chat.messages and not chat.messages[-1].get('error') and chat.input.toPlainText()==question and chat.results.currentWidget()==chat.task_view)
            if chat.messages:output.write_text(json.dumps(chat.messages[-1],ensure_ascii=False))
            chat.grab().save('/private/tmp/sessionlens-query-ui.png')
            print('Installed assistant query '+('passed' if success else 'failed'),flush=True)
            if not success and chat.messages:print(chat.messages[-1].get('error','界面显示失败'),flush=True)
            chat.close();app.exit(0 if success else 1)
        timer.timeout.connect(check_query);timer.start(250);return app.exec()
    if verify:
        from PySide6.QtCore import QUrl
        result_path=Path(sys.argv[sys.argv.index('--verify-ui')+1]);result=json.loads(result_path.read_text())
        chat.messages=[result];chat.input.setPlainText(result['question']);chat.render();chat.show();app.processEvents()
        assert chat.input.height()>=72
        assert chat.input.toPlainText()==result['question']
        assert chat.composer.mapTo(chat,chat.composer.rect().topLeft()).y()<chat.answer.mapTo(chat,chat.answer.rect().topLeft()).y()
        reference=result['understanding']['steps'][0]['evidenceRefs'][0]
        chat.evidence(QUrl('proof:0:'+reference));app.processEvents();assert chat.proof_panel.isVisible()
        assert chat.proof.toPlainText().strip()
        chat.proof_panel.hide()
        if result.get('presentation'):
            view=chat.task_view;assert len(view.data['calls'])==3;assert len(view.data['frames'])==3
            view.choose_step(2);view.select('calls');app.processEvents();assert '1080P' in view.plain_text();assert 'VideoGen' in view.plain_text()
            view.choose_step(0);view.select('reasoning');view.toggle();view.tick();assert view.position>0;view.toggle();assert not view.timer.isActive();view.next();assert view.index==1
            view.select('delivery');app.processEvents();assert '验证' in view.plain_text();view.select('overview')
        app.processEvents();chat.grab().save('/private/tmp/sessionlens-installed-ui.png')
        print('Installed UI verification passed: large top input, answer rendering, original evidence')
        chat.close();return 0
    window.start_local()
    chat.show();return app.exec()

if __name__=='__main__':sys.exit(main())
