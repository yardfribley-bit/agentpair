import html
import json
import os
from pathlib import Path
import sqlite3
import sys

from PySide6.QtCore import Qt, QTimer, QLockFile, QSize
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,QTableWidgetItem,QDialog,QFormLayout,QLineEdit,QCheckBox,QDialogButtonBox,QPlainTextEdit,QMessageBox,QProgressBar,QGroupBox,QFileDialog,QComboBox,QSplitter,QListWidget,QListWidgetItem,QTextBrowser)
from sessionlens.supervision import TaskStore
from sessionlens.desktop import Runtime,defaults,state_root,LABELS

STYLE='''QWidget {font-family: Arial; font-size:14px; color:#233247; background:#f5f7fb;} QMainWindow {background:#f5f7fb;} QLabel {background:transparent;} QLabel#title {font-size:28px;font-weight:700;} QLabel#sub {color:#6a7a91;} QGroupBox {background:white;border:1px solid #dce3ee;border-radius:10px;margin-top:12px;padding:18px;} QGroupBox::title {subcontrol-origin:margin;left:16px;padding:0 5px;font-weight:600;} QPushButton {background:#245bdb;color:white;border:0;border-radius:6px;padding:10px 16px;} QPushButton:disabled {background:#9aaac3;} QTextBrowser,QListWidget,QLineEdit,QPlainTextEdit,QTableWidget {background:white;border:1px solid #dce3ee;border-radius:5px;padding:5px;} QListWidget::item:selected {background:#e9f0fb;color:#202d3d;} QTextBrowser {padding:14px;} QHeaderView::section {background:#edf2fa;padding:9px;border:0;font-weight:600;} QProgressBar {border:0;background:#e4eaf5;height:8px;border-radius:4px;text-align:center;} QProgressBar::chunk {background:#245bdb;border-radius:4px;}'''

class Settings(QDialog):
    def __init__(self,config,parent):
        super().__init__(parent);self.setWindowTitle('采集与上报设置');self.resize(620,360);self.config=config
        form=QFormLayout(self);self.sources={}
        for source,title in [('codex','Codex'),('workbuddy','WorkBuddy')]:
            check=QCheckBox('采集 '+title);check.setChecked(config['sources'][source]['enabled'])
            paths=QLineEdit(';'.join(config['sources'][source]['roots']));form.addRow(check,paths);self.sources[source]=(check,paths)
        self.endpoint=QLineEdit(config.get('endpoint',''));self.endpoint.setPlaceholderText('https://www.chuhaijian.com/api/sessionlens/events');form.addRow('上报接口',self.endpoint)
        self.token=QLineEdit();self.token.setEchoMode(QLineEdit.Password);form.addRow('设备令牌（仅本次运行）',self.token)
        hint=QLabel('目录以分号分隔。开启上报后，会发送所选日志的完整记录。\n未配置接口和令牌时，仅保存在本机；关闭应用停止采集。');hint.setWordWrap(True);form.addRow(hint)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);form.addRow(buttons)
    def result_config(self):
        return {'sources':{s:{'enabled':c.isChecked(),'roots':[x.strip() for x in p.text().split(';') if x.strip()]} for s,(c,p) in self.sources.items()},'endpoint':self.endpoint.text().strip()}

class Window(QMainWindow):
    def __init__(self,root):
        super().__init__();self.root=root;self.runtime=None;self.mode='live';self.selected=None;self.selected_event=None;self.signature=None;self.step_limit=300
        self.setWindowTitle('SessionLens · 任务监督与历史追溯');self.resize(1280,850)
        self.config=defaults()
        try:self.config=json.loads((root/'settings.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        # Initialize schema only; historical projection runs in a worker.
        bootstrap=Runtime(root,self.config)
        self.store=TaskStore(root/'collector.db')
        body=QWidget();self.setCentralWidget(body);layout=QVBoxLayout(body);layout.setContentsMargins(24,20,24,16);layout.setSpacing(16)
        header=QHBoxLayout();title=QLabel('SessionLens');title.setObjectName('title');header.addWidget(title);header.addWidget(QLabel('任务监督与历史追溯'));header.addStretch();self.button=QPushButton('采集状态 / 设置');self.button.clicked.connect(self.configure);header.addWidget(self.button);layout.addLayout(header)
        nav=QHBoxLayout();self.live=QPushButton('监工模式');self.history=QPushButton('历史追溯');self.live.clicked.connect(lambda:self.set_mode('live'));self.history.clicked.connect(lambda:self.set_mode('history'));nav.addWidget(self.live);nav.addWidget(self.history);nav.addStretch();self.status=QLabel('尚未开始采集');nav.addWidget(self.status);layout.addLayout(nav)
        self.toolbar=QWidget();tools=QHBoxLayout(self.toolbar);tools.setContentsMargins(0,0,0,0);self.search=QLineEdit();self.search.setPlaceholderText('搜索任务、文件或问题…');self.search.returnPressed.connect(self.reload);tools.addWidget(self.search);find=QPushButton('查找任务');find.clicked.connect(self.reload);tools.addWidget(find);self.source=QComboBox();self.source.addItems(['全部 Agent','Codex','WorkBuddy']);self.source.currentIndexChanged.connect(self.reload);tools.addWidget(self.source);layout.addWidget(self.toolbar)
        split=QSplitter(Qt.Horizontal);layout.addWidget(split,1)
        left=QWidget();lv=QVBoxLayout(left);lv.setContentsMargins(0,0,0,0);self.list_title=QLabel('当前任务');lv.addWidget(self.list_title);self.task_list=QListWidget();self.task_list.setWordWrap(True);self.task_list.currentRowChanged.connect(self.select_task);lv.addWidget(self.task_list);split.addWidget(left)
        self.middle=QTextBrowser();self.middle.setOpenLinks(False);self.middle.anchorClicked.connect(self.follow_link);split.addWidget(self.middle)
        right=QWidget();rv=QVBoxLayout(right);rv.setContentsMargins(0,0,0,0);rv.addWidget(QLabel('对应证据'));self.proof=QTextBrowser();rv.addWidget(self.proof,1);self.mark=QPushButton('标记待核实');self.mark.clicked.connect(self.mark_event);rv.addWidget(self.mark);raw=QPushButton('查看来源定位与原始片段');raw.clicked.connect(self.details);rv.addWidget(raw);split.addWidget(right);split.setSizes([230,640,330]);split.setChildrenCollapsible(False)
        self.foot=QLabel('只展示已记录的动作；待核实标记不会暂停 Agent。');self.foot.setWordWrap(True);layout.addWidget(self.foot)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(1200);self.set_mode('live')
    def set_mode(self,mode):
        self.mode=mode;self.toolbar.setVisible(mode=='history');self.live.setEnabled(mode!='live');self.history.setEnabled(mode!='history');self.signature=None;self.reload()
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
        source=['','codex','workbuddy'][self.source.currentIndex()]
        try:rows=self.store.tasks(self.search.text() if self.mode=='history' else '',source,self.mode=='live')
        except sqlite3.Error:return
        sig=(self.mode,tuple(rows),self.search.text(),source)
        if sig==self.signature:return
        self.signature=sig;old=self.selected;self.rows=rows;self.task_list.blockSignals(True);self.task_list.clear()
        for row in rows:
            item=QListWidgetItem(row[1].title()+' · '+row[4][:16].replace('T',' ')+'\n'+row[3].replace('\n',' ')[:100]);item.setSizeHint(QSize(210,90));self.task_list.addItem(item)
        self.task_list.blockSignals(False);index=next((i for i,r in enumerate(rows) if r[0]==old),0)
        self.list_title.setText(('当前任务' if self.mode=='live' else '相关任务')+f' · {len(rows)}')
        if rows:self.task_list.setCurrentRow(index);self.select_task(index)
        else:self.selected=None;self.selected_event=None;self.middle.setHtml('<h2>等待任务记录</h2><p>开启采集后会在后台整理历史。也可以更换关键词。</p>');self.proof.setHtml('<p>选择任务后展示对应原文。</p>')
    def select_task(self,index):
        if index<0 or index>=len(getattr(self,'rows',[])):return
        row=self.rows[index]
        if self.selected!=row[0]:self.step_limit=300
        self.selected=row[0];self.steps=self.store.steps(row[0],self.step_limit);esc=html.escape
        last=self.store.db.execute('SELECT event,kind,excerpt,call_id FROM task_steps WHERE task=? ORDER BY seq DESC LIMIT 1',(row[0],)).fetchone()
        outcome=last[2][:450] if last else '尚未记录后续动作'
        title=row[3].splitlines()[0][:90]
        content='<style>a {color:#275eb2;text-decoration:none;} p {line-height:150%;} blockquote {color:#202d3d;}</style>'+f'<h2>{esc(title)}</h2><p style="color:#67778b">{esc(row[1])} · {esc(row[4])} · {esc(row[5])}</p><div style="background:#edf2f8"><h3>{"现在记录到什么" if self.mode=="live" else "这次实际做了什么"}</h3><p>{esc(outcome)}</p></div><h3>你的原始要求</h3><blockquote>{esc(row[3]).replace(chr(10),"<br>")}</blockquote><hr><p style="color:#67778b">按记录顺序展示；较长任务分批展开，原文完整保留。</p><h3>{"已经看到的动作" if self.mode=="live" else "处理经过与依据"}</h3>'
        calls={s[3] for s in self.steps if s[1]=='工具调用' and s[3]};results={s[3] for s in self.steps if s[1]=='工具返回' and s[3]}
        for i,s in enumerate(self.steps):
            focused=s[0]==self.selected_event
            content+=f'<div style="background:{"#e9f0fb" if focused else "#ffffff"};margin:8px;padding:8px"><a href="e:{i}"><b>{i+1} · {esc(s[1])}</b>　E{i+1:03}</a><p>{esc(s[2][:300]).replace(chr(10),"<br>")}</p></div>'
        total=self.store.db.execute('SELECT count(*) FROM task_steps WHERE task=?',(row[0],)).fetchone()[0]
        if total>len(self.steps):content+=f'<p><a href="more:">继续展开 · 已显示 {len(self.steps)} / {total} 个步骤</a></p>'
        gaps=[]
        if total==len(self.steps) and calls-results:gaps.append(f'{len(calls-results)} 个工具调用尚未找到对应返回。')
        if total==len(self.steps) and not any(s[1]=='解题思路' for s in self.steps):gaps.append('这段日志没有记录处理思路，无法据此说明为什么这样做。')
        if row[5]=='结束状态未记录':gaps.append('没有明确的任务结束记录。')
        if gaps:content+='<div style="background:#fff5e7;color:#875820"><h3>这次还有什么没说明白</h3><p>'+ '<br>'.join(gaps)+'</p></div>'
        related=self.store.db.execute('SELECT id,prompt FROM tasks WHERE source=? AND session=? AND id<>? ORDER BY last_row DESC LIMIT 4',(row[1],row[2],row[0])).fetchall()
        if related:
            content+='<hr><h3>同一会话 · 继续追溯前后任务</h3>'
            for identity,prompt in related:content+=f'<p><a href="t:{identity}">{esc(prompt[:100])}</a></p>'
        self.middle.setHtml(content)
        self.foot.setText('日志证据 · 会话 '+row[2]+' · 点击一步核对原文。摘要是记录摘录，不代表独立验证的结论。')
        if self.steps:
            chosen=next((i for i,s in enumerate(self.steps) if s[0]==self.selected_event),len(self.steps)-1);self.select_evidence(chosen)
    def follow_link(self,url):
        target=url.toString()
        if target=='more:':self.step_limit+=300;self.select_task(self.task_list.currentRow())
        elif target.startswith('e:'):self.select_evidence(int(target[2:]));self.select_task(self.task_list.currentRow())
        elif target.startswith('t:'):
            self.mode='history';self.toolbar.show();self.search.clear();self.selected=target[2:];self.signature=None;self.reload()
    def select_evidence(self,index):
        s=self.steps[index];self.selected_event=s[0];e=self.store.evidence(s[0]);esc=html.escape
        loc=e.get('evidence',{});quote=s[2]
        self.proof.setHtml(f'<h3>E{index+1:03} · {esc(s[1])}</h3><p>{esc(str(e.get("timestamp") or "时间未记录"))}</p><pre style="white-space:pre-wrap">{esc(quote[:12000])}</pre><hr><p>来源：{esc(str(loc.get("path","未记录")))}</p><p>字节 {loc.get("byteStart","?")}–{loc.get("byteEnd","?")}</p><p>原始记录保存在本机。此处最多预览 12,000 字符；完整内容可导出。</p>')
        self.mark.setText('取消待核实标记' if self.store.marked(s[0]) else '标记待核实')
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
        if self.runtime:self.runtime.stop.set()
        self.store.close();event.accept()

def main():
    test='--self-test' in sys.argv
    if test:os.environ['QT_QPA_PLATFORM']='offscreen'
    app=QApplication(sys.argv);app.setStyleSheet(STYLE)
    root=Path(__import__('tempfile').mkdtemp(prefix='sessionlens-test-')) if test else state_root();root.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(root/'desktop.lock'));lock.setStaleLockTime(0)
    if not test and not lock.tryLock(0):QMessageBox.information(None,'SessionLens','SessionLens 已在运行。');return 1
    window=Window(root)
    if test:window.close();print('SessionLens desktop self-test passed');return 0
    if '--resume-local' in sys.argv:
        local=json.loads(json.dumps(window.config));local['endpoint']=''
        window.runtime=Runtime(root,local);window.runtime.start();window.button.setText('停止 / 修改设置')

    window.show();return app.exec()

if __name__=='__main__':sys.exit(main())
