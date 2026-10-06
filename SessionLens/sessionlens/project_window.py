"""Project overview, with compact tasks and on-demand source evidence."""
import json,re,sqlite3
from PySide6.QtCore import Qt,QTimer,Signal
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QLineEdit,QPushButton,QSplitter,QListWidget,QTextBrowser,QTabWidget,QFormLayout
from .project_inventory import ProjectInventory
from .supervision import event_text
from .i18n import language,t,localize_widgets
from .database import connection

def task_preview(text):
    # Keep useful host/user context in summaries; conceal the password tail.
    def tail(match):
        value=match[2];return match[1]+(value[:-4]+'****' if len(value)>4 else '****')
    text=re.sub(r'(\b(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}:[^:\s]+:)([^\s，。]+)',tail,text)
    return re.sub(r'((?:密码|password|api[_-]?key)\s*[:：=]\s*)([^\s，。]+)',tail,text,flags=re.I)

class ProjectWindow(QDialog):
    taskRequested=Signal(str)
    def __init__(self,root,parent=None):
        super().__init__(parent);self.root=root;self.selected=None;self.last_signature=None
        self.setWindowTitle('SessionLens · 项目总览');self.resize(1180,760)
        self.setStyleSheet('QWidget{font-family:Arial;font-size:14px;color:#233247;} QDialog{background:#F7F8FA;} QListWidget,QTextBrowser,QLineEdit,QComboBox{background:white;border:1px solid #E6E8EC;border-radius:8px;padding:8px;} QListWidget::item{padding:9px 6px;border-bottom:1px solid #eff1f4;} QListWidget::item:selected{background:#edf4ff;color:#1769ef;} QPushButton{padding:7px 12px;} QLabel#heading{font-size:20px;font-weight:600;}')
        v=QVBoxLayout(self);v.setContentsMargins(24,22,24,20);v.setSpacing(12)
        heading=QLabel('项目总览');heading.setObjectName('heading');v.addWidget(heading)
        v.addWidget(QLabel('看 Agent 参与了哪些开发，追溯每个项目的要求与修改。'))
        row=QHBoxLayout();self.agent=QComboBox();self.agent.addItem('WorkBuddy','workbuddy');self.agent.addItem('Codex','codex');row.addWidget(self.agent)
        self.search=QLineEdit();self.search.setPlaceholderText('查找项目名称或目录');row.addWidget(self.search,1)
        self.filter=QComboBox();self.filter.addItem('全部开发目录','all');self.filter.addItem('自动识别项目','identified');self.filter.addItem('人工确认项目','confirmed');self.filter.addItem('待确认目录','candidate');self.filter.addItem('已排除','excluded');row.addWidget(self.filter);v.addLayout(row)
        self.counts=QLabel();self.counts.setStyleSheet('font-size:16px;font-weight:600;');v.addWidget(self.counts)
        self.progress=QLabel();self.progress.setWordWrap(True);v.addWidget(self.progress)
        split=QSplitter();self.projects=QListWidget();split.addWidget(self.projects)
        panel=QDialog();p=QVBoxLayout(panel);p.setContentsMargins(14,0,0,0);self.title=QLabel('选择一个项目');self.title.setStyleSheet('font-size:18px;font-weight:600;');p.addWidget(self.title)
        self.meta=QLabel();self.meta.setWordWrap(True);self.meta.setProperty('i18nSkip',True);p.addWidget(self.meta)
        self.tabs=QTabWidget();self.tasks=QListWidget();self.files=QListWidget();self.tabs.addTab(self.tasks,'相关任务');self.tabs.addTab(self.files,'修改依据');p.addWidget(self.tabs,1)
        self.preview=QTextBrowser();self.preview.setMaximumHeight(200);self.preview.hide();p.addWidget(self.preview)
        actions=QHBoxLayout();self.review_button=QPushButton('确认 / 更名 / 合并 / 排除');self.review_button.clicked.connect(self.review);actions.addWidget(self.review_button);actions.addStretch();p.addLayout(actions)
        split.addWidget(panel);split.setSizes([420,700]);v.addWidget(split,1)
        note=QLabel('依据是已采集日志里的源码写入、修改请求；不等于项目已完成。仅查看、安装和文档任务不计入。Shell 内写文件目前不计入此清单。');note.setWordWrap(True);note.setStyleSheet('color:#667085;font-size:12px;');v.addWidget(note)
        self.agent.currentIndexChanged.connect(self.refresh);self.filter.currentIndexChanged.connect(self.refresh);self.search.textChanged.connect(self.refresh)
        self.projects.currentRowChanged.connect(self.details);self.tasks.itemDoubleClicked.connect(self.open_task);self.files.currentRowChanged.connect(self.proof)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(2500);self.refresh()
        localize_widgets(self)
    def connections(self):
        source=sqlite3.connect((self.root/'collector.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)
        return source,ProjectInventory(self.root/'project_inventory.db')
    def refresh(self,*args):
        source=store=None
        try:
            source,store=self.connections();snapshot=store.snapshot(source,self.agent.currentData(),self.search.text())
            c=snapshot['counts'];self.counts.setText(f'已识别 {c["identified"]+c["confirmed"]} 个项目　·　待确认 {c["candidate"]} 个目录　·　已排除 {c["excluded"]} 个')
            self.progress.setText('当前已采历史整理完成；新记录持续归入。' if snapshot['complete'] else '历史正在逐步整理，当前数量还会增加；新记录优先处理。')
            self.rows=[r for r in snapshot['projects'] if self.filter.currentData()=='all' or r['state']==self.filter.currentData()]
            signature=json.dumps([self.rows,language()],sort_keys=True)
            if signature==self.last_signature:return
            self.last_signature=signature;selected=self.selected;self.projects.blockSignals(True);self.projects.clear()
            for r in self.rows:
                state=t({'identified':'项目标记 + 源码记录','confirmed':'人工确认项目','candidate':'待确认目录','excluded':'已排除'}[r['state']])
                self.projects.addItem(r['name']+'\n'+state+' · '+t(f'{r["taskCount"]} 个任务 · {r["fileCount"]} 个源码文件')+'\n'+r['roots'][0])
                self.projects.item(self.projects.count()-1).setToolTip('\n'.join(r['roots']))
            index=next((i for i,r in enumerate(self.rows) if r['id']==selected),0 if self.rows else -1)
            self.projects.setCurrentRow(index);self.projects.blockSignals(False);self.details(index)
        except (sqlite3.Error,OSError) as exc:self.progress.setText('项目整理暂不可用：'+type(exc).__name__)
        finally:
            if source:source.close()
            if store:store.close()
            localize_widgets(self)
    def details(self,index):
        if index<0 or index>=len(self.rows):
            self.selected=None;self.title.setProperty('i18nSkip',False);self.title.setText('没有符合条件的项目');self.meta.clear();self.tasks.clear();self.files.clear();self.preview.hide();self.review_button.setEnabled(False);localize_widgets(self);return
        self.selected=self.rows[index]['id'];self.review_button.setEnabled(True);source=store=None
        try:
            source,store=self.connections();self.detail=store.details(source,self.selected)
            d=self.detail;self.title.setProperty('i18nSkip',True);self.title.setText(d['name']);basis={'user_confirmed':'用户确认','recorded_manifest':'日志记录了目录内的项目清单写入','current_directory':'本机现存项目标记；不能倒推历史仓库'}.get(d['basis'],'仅有源码修改路径，项目身份待确认')
            self.meta.setText('\n'.join(d['roots'])+'\n'+t(f'{d["taskCount"]} 个关联任务 · {d["fileCount"]} 个源码文件 · {d["writes"]} 次写入/修改请求')+'\n'+t('识别依据：')+t(basis))
            self.tasks.clear()
            for task in d['tasks']:
                self.tasks.addItem(task['updated'][:16].replace('T',' ')+'\n'+task_preview(task['prompt'].replace('\n',' '))[:160]);self.tasks.item(self.tasks.count()-1).setToolTip(task_preview(task['prompt']))
            self.files.blockSignals(True);self.files.clear()
            for f in d['evidence']:self.files.addItem(f['path'])
            self.files.blockSignals(False);self.preview.hide()
            self.tabs.setTabText(0,'相关任务'+(' · 最近 100 个' if d['taskCount']>100 else ''));self.tabs.setTabText(1,'修改依据 · 最近 100 条')
        except (sqlite3.Error,ValueError) as exc:self.meta.setText(str(exc))
        finally:
            if source:source.close()
            if store:store.close()
            localize_widgets(self)
    def open_task(self,item):
        index=self.tasks.currentRow()
        if index>=0:self.taskRequested.emit(self.detail['tasks'][index]['taskId'])
    def proof(self,index):
        if index<0:return
        with connection((self.root/'collector.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=.2) as source:
            ident=self.detail['evidence'][index]['eventId'];row=source.execute('SELECT CASE WHEN length(event)<=262144 THEN event END FROM events WHERE id=?',(ident,)).fetchone()
            excerpt=source.execute('SELECT substr(excerpt,1,16000) FROM task_steps WHERE event=?',(ident,)).fetchone() if row and not row[0] else None
        text=event_text(json.loads(row[0]))[:16000] if row and row[0] else t('大记录仅展示索引摘录：')+'\n'+excerpt[0] if excerpt else t('原始证据暂不可用')
        self.preview.setPlainText(text);self.preview.show()
    def review(self):
        if not self.selected:return
        dialog=QDialog(self);dialog.setWindowTitle('修正项目清单');form=QFormLayout(dialog)
        name=QLineEdit(self.detail['name']);form.addRow('显示名称',name);state=QComboBox()
        for label,value in [('确认为开发项目','confirmed'),('保留为待确认目录','candidate'),('排除临时脚本 / 输出目录','excluded'),('恢复自动识别','automatic')]:state.addItem(label,value)
        state.setCurrentIndex(max(0,state.findData(self.detail['state'])));form.addRow('归类',state)
        target=QComboBox();target.setProperty('i18nSkip',True);target.addItem(t('保持独立'),None)
        for r in self.rows:
            if r['id']!=self.selected:target.addItem(r['name']+' · '+r['roots'][0],r['id'])
        form.addRow('合并到已有项目',target);status=QLabel();form.addRow(status);save=QPushButton('保存');form.addRow(save)
        def apply():
            store=None
            try:
                store=ProjectInventory(self.root/'project_inventory.db');store.review(self.selected,state.currentData(),name.text(),target.currentData())
            except (ValueError,sqlite3.Error) as exc:status.setText(str(exc));return
            finally:
                if store:store.close()
            self.last_signature=None;dialog.accept();self.refresh()
        save.clicked.connect(apply);localize_widgets(dialog);dialog.exec()
    def closeEvent(self,event):self.timer.stop();event.accept()
