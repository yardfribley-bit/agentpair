"""Local history and collection pages, inside the native SessionLens window."""
import html
import sqlite3
import time
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QSplitter, QListWidget, QListWidgetItem, QTextBrowser, QFrame,
    QGridLayout, QScrollArea, QSizePolicy,
)

from .database import connection
from .i18n import t, localize_widgets
from .theme import badge


def label(text, name='metadata'):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred)
    widget.setTextFormat(Qt.PlainText)
    return widget


def surface():
    frame = QFrame()
    frame.setObjectName('surface')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(10)
    return frame, layout


class HistoryPage(QWidget):
    taskRequested = Signal(str)

    def __init__(self, collector, parent=None):
        super().__init__(parent)
        self.collector = collector
        self.rows, self.selected = [], None
        self.setObjectName('desktopPage')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 22)
        layout.setSpacing(18)
        layout.addWidget(label('历史任务', 'pageTitle'))
        layout.addWidget(label('找回过去的要求、执行记录和交付依据。'))
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t('搜索任务、文件或问题…'))
        self.search.setAccessibleName(t('搜索历史任务'))
        self.search.returnPressed.connect(self.refresh)
        row.addWidget(self.search, 1)
        self.source = QComboBox()
        for name, value in (('全部 Agent', ''), ('Codex', 'codex'), ('WorkBuddy', 'workbuddy')):
            self.source.addItem(t(name), value)
        self.source.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.source)
        find = QPushButton('查找任务')
        find.clicked.connect(self.refresh)
        row.addWidget(find)
        layout.addLayout(row)
        self.notice = label('')
        layout.addWidget(self.notice)
        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.tasks = QListWidget()
        self.tasks.setWordWrap(True)
        self.tasks.setMinimumWidth(210)
        self.tasks.currentRowChanged.connect(self.select)
        self.split.addWidget(self.tasks)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 0, 0, 0)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        detail_layout.addWidget(self.detail, 1)
        self.review = QPushButton('在智能助手回顾')
        self.review.setObjectName('primaryButton')
        self.review.clicked.connect(lambda: self.taskRequested.emit(self.selected) if self.selected else None)
        detail_layout.addWidget(self.review, 0, Qt.AlignLeft)
        self.split.addWidget(detail)
        self.split.setSizes([270, 630])
        layout.addWidget(self.split, 1)
        self.refresh()

    def refresh(self):
        old = self.selected
        try:
            self.rows = self.collector.store.tasks(self.search.text().strip(), self.source.currentData() or '', False)
        except sqlite3.Error:
            self.rows = []
            self.notice.setText(t('本机任务库暂不可用，请稍后重试。'))
        else:
            self.notice.setText(t(f'找到 {len(self.rows)} 个已整理任务') if self.rows else t('没有找到匹配任务。可以换个关键词，或等待历史整理。'))
        self.tasks.blockSignals(True)
        self.tasks.clear()
        for row in self.rows:
            item = QListWidgetItem(str(row[3]).replace('\n', ' ')[:100] + '\n' + {'codex': 'Codex', 'workbuddy': 'WorkBuddy'}.get(row[1], row[1]) + ' · ' + str(row[4])[:16].replace('T', ' '))
            item.setToolTip(str(row[3]))
            self.tasks.addItem(item)
        index = next((i for i, row in enumerate(self.rows) if row[0] == old), 0)
        self.tasks.setCurrentRow(index if self.rows else -1)
        self.tasks.blockSignals(False)
        self.select(index if self.rows else -1)
        localize_widgets(self)

    def select(self, index):
        self.selected = self.rows[index][0] if 0 <= index < len(self.rows) else None
        self.review.setEnabled(bool(self.selected))
        if not self.selected:
            self.detail.setHtml('<h3>' + html.escape(t('选择任务后查看要求与记录。')) + '</h3>')
            return
        from .task_lineage import dialogues
        row = self.rows[index]
        esc = html.escape
        steps = self.collector.store.steps(self.selected, 30)
        turns = dialogues(self.collector.store.db, self.selected, limit=8)
        body = '<h3>' + esc(str(row[3])[:160]) + '</h3><p style="color:#637084">' + esc(str(row[1]) + ' · ' + str(row[4])[:16].replace('T', ' ')) + '</p>'
        body += '<h4>' + esc(t('你的原始要求')) + '</h4><p>' + esc(str(row[3])).replace('\n', '<br>') + '</p>'
        if turns:
            body += '<h4>' + esc(t('需求与反馈')) + '</h4>'
            for turn in turns:
                body += '<p><b>' + esc(str(turn['ordinal']) + ' · ' + t(turn['label'])) + '</b><br>' + esc(str(turn['text'])[:220]).replace('\n', '<br>') + '</p>'
        body += '<h4>' + esc(t('已记录的动作')) + '</h4>'
        for step in steps[:8]:
            # Tuple fields are event ID, kind, excerpt and call ID.
            body += '<p><b>' + esc(t(str(step[1]))) + '</b><br>' + esc(str(step[2])[:240]).replace('\n', '<br>') + '</p>'
        if not steps:
            body += '<p>' + esc(t('尚未记录后续动作')) + '</p>'
        body += '<p style="color:#637084">' + esc(t('只展示当前已整理记录；在智能助手中继续核对过程与原始依据。')) + '</p>'
        self.detail.setHtml(body)


class CollectionPage(QWidget):
    """A readable collection overview. It never starts network/model requests."""
    def __init__(self, collector, parent=None):
        super().__init__(parent)
        self.collector = collector
        self.statistics=None
        self.statistics_at=0
        self.setObjectName('desktopPage')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        page = QWidget()
        page.setObjectName('desktopPage')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 22)
        layout.setSpacing(22)
        row = QHBoxLayout()
        row.addWidget(label('采集与同步', 'pageTitle'), 1)
        self.pause = QPushButton('暂停采集')
        self.pause.clicked.connect(self.toggle_pause)
        row.addWidget(self.pause)
        settings = QPushButton('设置')
        settings.clicked.connect(self.collector.configure)
        row.addWidget(settings)
        layout.addLayout(row)
        layout.addWidget(label('本机记录先保存到任务库，取得平台回执后才标为已接收。'))
        self.health = label('采集尚未启动')
        layout.addWidget(self.health, 0, Qt.AlignLeft)
        self.source_cards = {}
        sources = QHBoxLayout()
        sources.setSpacing(16)
        for source, name in (('codex', 'Codex'), ('workbuddy', 'WorkBuddy')):
            frame, inner = surface()
            inner.addWidget(label(name, 'sectionTitle'))
            state = label('')
            inner.addWidget(state)
            location = label('')
            location.setProperty('i18nSkip', True)
            inner.addWidget(location)
            inner.addStretch()
            self.source_cards[source] = (state, location)
            sources.addWidget(frame, 1)
        layout.addLayout(sources)
        frame, inner = surface()
        inner.addWidget(label('采集到什么', 'sectionTitle'))
        inner.addWidget(label('提问与回复、已记录思路、工具参数与返回、会话背景分别保存。'))
        self.category_labels = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(12)
        from .desktop import LABELS
        meanings={
            '用户提问':'原始要求、补充条件与方案确认',
            'Agent 回复':'回复内容与交付说明',
            '解题思路':'日志中实际记录的 reasoning',
            '工具调用':'工具名称、输入参数、网址与路径',
            '工具返回':'工具输出、文件与错误信息',
            '会话背景':'会话附带背景，不代表完整模型输入',
            '用量与状态':'任务开始、结束及已记录用量',
            '暂未识别':'保留原文，类型尚未确认',
        }
        for index, name in enumerate(LABELS):
            cell=QVBoxLayout();cell.setSpacing(4)
            value = label('')
            self.category_labels[name] = value
            cell.addWidget(value)
            cell.addWidget(label(meanings[name]))
            grid.addLayout(cell, index // 2, index % 2)
        inner.addLayout(grid)
        layout.addWidget(frame)
        frame, inner = surface()
        inner.addWidget(label('本机保存 → 历史整理 → 平台接收', 'sectionTitle'))
        self.saved = label('')
        self.indexed = label('')
        self.upload = label('')
        self.receipt = label('')
        self.queue = label('')
        for item in (self.saved, self.indexed, self.upload, self.receipt, self.queue):
            inner.addWidget(item)
        self.directory = label(str(collector.root))
        self.directory.setProperty('i18nSkip', True)
        inner.addWidget(self.directory)
        row = QHBoxLayout()
        records = QPushButton('打开本机记录')
        records.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.collector.root))))
        row.addWidget(records)
        row.addStretch()
        inner.addLayout(row)
        layout.addWidget(frame)
        self.errors = label('')
        layout.addWidget(self.errors)
        layout.addWidget(label('暂停影响新记录读取和上报；正在处理的批次会先完成，本机历史问答仍可使用。'))
        layout.addStretch()
        scroll.setWidget(page)
        self.refresh()

    def toggle_pause(self):
        runtime = self.collector.runtime
        if runtime and hasattr(runtime, 'set_paused'):
            runtime.set_paused(not runtime.paused.is_set())
        self.refresh()

    def refresh(self,force=False):
        runtime = self.collector.runtime
        status = {}
        if runtime:
            with runtime.lock:
                status = dict(runtime.status)
        paused = bool(runtime and getattr(runtime, 'paused', None) and runtime.paused.is_set())
        running = bool(runtime and not runtime.stop.is_set())
        self.health.setText(t('已暂停采集' if paused else '正在采集' if running else '采集尚未启动'))
        badge(self.health, 'attention' if paused else 'success' if running else 'neutral')
        self.pause.setText(t('恢复采集' if paused else '暂停采集'))
        self.pause.setEnabled(running and hasattr(runtime, 'set_paused'))
        for source, (state, location) in self.source_cards.items():
            cfg = self.collector.config.get('sources', {}).get(source, {})
            roots = cfg.get('roots', [])
            state.setText(t('已启用' if cfg.get('enabled') else '未启用'))
            source_queue=status.get('upload_queue',{}).get('sources',{}).get(source,{})
            latest=source_queue.get('newest_source_time')
            latest_text=datetime.fromtimestamp(latest).strftime('%m-%d %H:%M:%S') if isinstance(latest,(int,float)) else ''
            progress=(t('最新待上报源时间')+' · '+latest_text+'\n') if latest_text else ''
            location.setText(progress+('\n'.join(str(path) for path in roots) or t('尚未配置日志目录')))
            badge(state, 'running' if cfg.get('enabled') else 'neutral')
        # Statistics stay out of the frequent assistant timer path, and the
        # visible collection page refreshes aggregate SQL at most every 5 sec.
        if force or self.statistics is None or time.monotonic()-self.statistics_at>=5:
            counts,total,ack={},0,None
            try:
                with connection(self.collector.root / 'collector.db', timeout=0.2) as db:
                    for source, category, count in db.execute('SELECT source,category,count(*) FROM display_index GROUP BY source,category'):
                        counts[category] = counts.get(category, 0) + count
                    total = db.execute('SELECT count(*) FROM events').fetchone()[0]
                    if runtime:
                        ack = db.execute('SELECT count(*) FROM deliveries WHERE destination=?', (runtime.destination,)).fetchone()[0]
            except sqlite3.Error:
                self.errors.setText(t('本机统计暂不可用，采集记录未被删除。'))
            else:
                self.statistics=(counts,total,ack);self.statistics_at=time.monotonic()
                errors = status.get('errors', {})
                self.errors.setText(t('读取异常：') + '\n'.join(str(value)[:160] for value in list(errors.values())[:3]) if errors else '')
        counts,total,ack=self.statistics or ({},0,None)
        for name, widget in self.category_labels.items():
            widget.setText(t(name) + ' · ' + str(counts.get(name, 0)))
        self.saved.setText(t('本机保存') + ' · ' + str(total))
        self.indexed.setText(t(status.get('task_index', '历史整理状态未知')))
        self.upload.setText(t(status.get('upload', '未配置上报')))
        when = status.get('receipt')
        receipt = datetime.fromtimestamp(when).strftime('%m-%d %H:%M:%S') if isinstance(when, (int, float)) else ''
        self.receipt.setText(t('平台已确认接收') + ' · ' + str(ack) + ((' · ' + receipt) if receipt else '') if ack else t('尚未取得平台回执'))
        queue=status.get('upload_queue',{})
        pending=max(0,total-ack) if isinstance(ack,int) else None
        text=t('最新优先 75% · 历史保底 25% · 空闲份额可互用')
        if pending is not None:text+='\n'+t('本机记录待平台确认')+' · '+str(pending)
        if queue:
            text+=' · '+t('退避重试')+' '+str(queue.get('retrying',0))+' · '+t('超大记录保留本机')+' '+str(queue.get('oversize',0))
            if queue.get('unindexed'):text+='\n'+t('历史上报索引待整理')+' · '+str(queue['unindexed'])
        self.queue.setText(text)
        localize_widgets(self)
