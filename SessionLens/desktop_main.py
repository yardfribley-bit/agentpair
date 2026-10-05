import json
import os
from pathlib import Path
import sqlite3
import sys

from PySide6.QtCore import Qt, QTimer, QLockFile
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,QTableWidgetItem,QDialog,QFormLayout,QLineEdit,QCheckBox,QDialogButtonBox,QPlainTextEdit,QMessageBox,QProgressBar,QGroupBox,QFileDialog)
from sessionlens.desktop import Runtime,defaults,state_root,LABELS

STYLE='''QWidget {font-family: Arial; font-size:14px; color:#233247; background:#f5f7fb;} QMainWindow {background:#f5f7fb;} QLabel {background:transparent;} QLabel#title {font-size:28px;font-weight:700;} QLabel#sub {color:#6a7a91;} QGroupBox {background:white;border:1px solid #dce3ee;border-radius:10px;margin-top:12px;padding:18px;} QGroupBox::title {subcontrol-origin:margin;left:16px;padding:0 5px;font-weight:600;} QPushButton {background:#245bdb;color:white;border:0;border-radius:6px;padding:10px 16px;} QPushButton:disabled {background:#9aaac3;} QLineEdit,QPlainTextEdit,QTableWidget {background:white;border:1px solid #dce3ee;border-radius:5px;padding:5px;} QHeaderView::section {background:#edf2fa;padding:9px;border:0;font-weight:600;} QProgressBar {border:0;background:#e4eaf5;height:8px;border-radius:4px;text-align:center;} QProgressBar::chunk {background:#245bdb;border-radius:4px;}'''

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
        super().__init__();self.root=root;self.runtime=None;self.setWindowTitle('SessionLens');self.resize(1200,820)
        self.config=defaults();configfile=root/'settings.json'
        if configfile.exists():
            try:self.config=json.loads(configfile.read_text(encoding='utf-8'))
            except (OSError,ValueError):pass
        body=QWidget();self.setCentralWidget(body);layout=QVBoxLayout(body);layout.setContentsMargins(28,20,28,20);layout.setSpacing(14)
        header=QHBoxLayout();title=QLabel('SessionLens');title.setObjectName('title');header.addWidget(title);header.addStretch();self.button=QPushButton('设置并开始采集');self.button.clicked.connect(self.configure);header.addWidget(self.button);layout.addLayout(header)
        sub=QLabel('会话日志采集 · Codex / WorkBuddy    |    完整历史回填与实时增量');sub.setObjectName('sub');layout.addWidget(sub)
        pipeline=QGroupBox('采集到平台接收');flow=QHBoxLayout(pipeline);self.stages=[]
        for i,name in enumerate(['发现与读取','本地保存','等待上报','正在发送','平台接收']):
            label=QLabel(name+'\n—');label.setAlignment(Qt.AlignCenter);flow.addWidget(label);self.stages.append(label)
            if i<4:
                arrow=QLabel('→');arrow.setFixedWidth(24);arrow.setAlignment(Qt.AlignCenter);flow.addWidget(arrow)
        layout.addWidget(pipeline);self.progress=QProgressBar();self.progress.setRange(0,100);layout.addWidget(self.progress)
        panels=QHBoxLayout();self.panels={}
        for source,title in [('codex','Codex'),('workbuddy','WorkBuddy')]:
            group=QGroupBox(title+' · 采集内容');v=QVBoxLayout(group);labels={}
            for name in LABELS:
                line=QLabel(name+'    0');v.addWidget(line);labels[name]=line
            panels.addWidget(group);self.panels[source]=(group,labels)
        layout.addLayout(panels)
        layout.addWidget(QLabel('最近采到的内容 · 双击查看原文与来源'))
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['来源','内容分类','内容摘要','会话']);self.table.setSelectionBehavior(QTableWidget.SelectRows);self.table.setEditTriggers(QTableWidget.NoEditTriggers);self.table.horizontalHeader().setStretchLastSection(True);self.table.setColumnWidth(2,600);self.table.cellDoubleClicked.connect(self.details);layout.addWidget(self.table,1)
        self.status=QLabel('尚未启动。采集的是应用落盘日志；未落盘的信息无法从这里获取。');self.status.setWordWrap(True);layout.addWidget(self.status)
        storage=QLabel('本地存储：'+str(root/'collector.db'));storage.setObjectName('sub');storage.setTextInteractionFlags(Qt.TextSelectableByMouse);layout.addWidget(storage)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(1000)
    def configure(self):
        if self.runtime:
            self.runtime.stop.set();self.button.setEnabled(False)
            # Stop old workers without blocking the event loop.
            self.stopping=True;self.pending_settings=True;return
        self.open_settings()
    def open_settings(self):
        dialog=Settings(self.config,self)
        if dialog.exec()!=QDialog.Accepted:return
        self.config=dialog.result_config();(self.root/'settings.json').write_text(json.dumps(self.config,ensure_ascii=False,indent=2),encoding='utf-8');os.chmod(self.root/'settings.json',0o600)
        self.runtime=Runtime(self.root,self.config,dialog.token.text());self.runtime.start();self.button.setText('停止 / 修改设置')
        for s,(group,_) in self.panels.items():group.setVisible(self.config['sources'][s]['enabled'])
    def refresh(self):
        if getattr(self,'stopping',False):
            if any(t.is_alive() for t in self.runtime.threads):return
            self.runtime=None;self.stopping=False;self.button.setEnabled(True);self.button.setText('设置并开始采集');self.open_settings();return
        if not self.runtime:return
        try:s=self.runtime.snapshot()
        except sqlite3.Error:return
        saved=s['saved'];ack=s['ack'];waiting=saved-ack
        values=[f"{s.get('files',0)} 个日志文件",f'{saved} 份记录',f'{waiting} 份未确认',s.get('upload','准备中'),f'{ack} 份已确认']
        for label,name,value in zip(self.stages,['发现与读取','本地保存','等待上报','正在发送','平台接收'],values):label.setText(name+'\n'+value)
        self.progress.setValue(min(100,int(100*s.get('read',0)/max(1,s.get('bytes',1)))))
        counts={(a,b):n for a,b,n in s['categories']}
        for source,(_,labels) in self.panels.items():
            for name,label in labels.items():label.setText(f'{name}    {counts.get((source,name),0)}')
        rows=s['recent'];self.table.setRowCount(len(rows));self.ids=[]
        for r,(identity,source,kind,excerpt,session) in enumerate(rows):
            self.ids.append(identity)
            for col,text in enumerate((source,kind,excerpt,session)):self.table.setItem(r,col,QTableWidgetItem(text))
        errors=s.get('errors',{});error='；'.join(f'{Path(p).name}: {e}' for p,e in list(errors.items())[:2])
        self.status.setText((f'读取进度 {s.get("read",0)/1048576:.1f} / {s.get("bytes",0)/1048576:.1f} MiB · '+s.get('reading','等待日志'))+f'\n大记录：{s["oversize"]} 份已保存本机，因单条上报限制暂未发送（不是存储已满）'+(' · '+error if error else ''))
    def details(self,row,_):
        with sqlite3.connect(self.root/'collector.db') as db:record=db.execute('SELECT event FROM events WHERE id=?',(self.ids[row],)).fetchone()
        dialog=QDialog(self);dialog.setWindowTitle('采集原文与证据来源');dialog.resize(900,600);v=QVBoxLayout(dialog);text=QPlainTextEdit();text.setReadOnly(True)
        raw=record[0]
        if len(raw)>100000:
            v.addWidget(QLabel('记录较大，先显示前 100,000 个字符。完整内容已保存本机，可导出查看。'))
            text.setPlainText(raw[:100000])
        else:text.setPlainText(json.dumps(json.loads(raw),ensure_ascii=False,indent=2))
        v.addWidget(text)
        export=QPushButton('导出完整记录');v.addWidget(export)
        def save():
            filename,_=QFileDialog.getSaveFileName(dialog,'导出完整采集记录','sessionlens-record.json','JSON (*.json)')
            if filename:
                try:Path(filename).write_text(raw,encoding='utf-8')
                except OSError as exc:QMessageBox.warning(dialog,'导出失败',str(exc))
        export.clicked.connect(save);dialog.exec()
    def closeEvent(self,event):
        if self.runtime:self.runtime.stop.set()
        event.accept()

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
        for source,(group,_) in window.panels.items():group.setVisible(local['sources'][source]['enabled'])
    window.show();return app.exec()

if __name__=='__main__':sys.exit(main())
