"""Native project, decision and observable-process answer cards."""
import json
from pathlib import PurePath

from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QFrame,
    QDialog, QPlainTextEdit, QSizePolicy, QStackedWidget, QTabWidget, QComboBox,
)

from .answer_presentation import present
from .answer_widgets import label, short, clear, ClickRow, Fold, RelationFlow, LineIcon
from .i18n import t, language, localize_widgets


def compact(text, n=110):
    return short(text, n)


class ActiveStack(QStackedWidget):
    def sizeHint(self):
        return self.currentWidget().sizeHint() if self.currentWidget() else QSize(400, 200)

    def minimumSizeHint(self):
        widget = self.currentWidget()
        return QSize(200, max(100, widget.minimumSizeHint().height())) if widget else QSize(200, 100)


class KnowledgeView(QWidget):
    projectRequested = Signal(str)
    taskRequested = Signal(str)
    evidenceRequested = Signal(str)
    questionRequested = Signal(str)
    associationRequested = Signal()
    projectCorrectionRequested = Signal()
    contentChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('nativeKnowledge')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.result, self.data = {}, {}
        self.signature, self.index, self.expanded = None, 0, False
        self.step_buttons, self.input_labels = [], []
        self.flow = None
        self.setStyleSheet('''
            QWidget#nativeKnowledge {background:#ffffff;border:1px solid #E6E8EC;border-radius:12px;color:#172334;}
            QWidget#nativeKnowledge QWidget {background:transparent;}
            QWidget#nativeKnowledge QLabel {background:transparent;border:0;color:#172334;}
            QWidget#nativeKnowledge QPushButton {background:#ffffff;border:1px solid #E6E8EC;border-radius:7px;padding:7px 12px;font-size:12px;color:#2463EB;}
            QWidget#nativeKnowledge QPushButton:hover {background:#F7F8FA;}
            QWidget#nativeKnowledge QPushButton:focus {border-color:#2463EB;}
            QWidget#nativeKnowledge QPushButton:disabled {color:#a3aab5;}
            QWidget#nativeKnowledge QPushButton#answerRow {background:transparent;border:0;border-bottom:1px solid #E6E8EC;border-radius:0;padding:0;text-align:left;}
            QWidget#nativeKnowledge QPushButton#answerRow:hover {background:#f7f9fc;}
            QWidget#nativeKnowledge QPushButton#answerRow:focus {background:#EDF3FF;}
            QWidget#nativeKnowledge QFrame#rowIcon {background:#F7F8FA;border:0;border-radius:7px;}
            QWidget#nativeKnowledge QFrame#answerMeta {border:0;border-bottom:1px solid #E6E8EC;border-radius:0;}
            QWidget#nativeKnowledge QWidget#answerFold {border:0;border-top:1px solid #E6E8EC;border-radius:0;}
            QWidget#nativeKnowledge QToolButton#foldToggle {border:0;background:transparent;text-align:left;color:#2463EB;font-size:12px;padding:2px 0;}
            QWidget#nativeKnowledge QToolButton#foldToggle:hover {color:#174dcc;}
            QWidget#nativeKnowledge QPushButton#stepButton {background:transparent;border:0;border-radius:0;text-align:left;color:#637084;padding:4px 3px 9px;font-size:12px;}
            QWidget#nativeKnowledge QPushButton#stepButton:checked {color:#2463EB;background:#EDF3FF;border-radius:6px;}
            QWidget#nativeKnowledge QPushButton#stepButton:hover {background:#F7F8FA;}
            QWidget#nativeKnowledge QComboBox {background:white;border:1px solid #E6E8EC;border-radius:6px;padding:6px;font-size:12px;color:#172334;}
            QWidget#nativeKnowledge QTabWidget::pane {border:0;}
            QWidget#nativeKnowledge QTabBar::tab {background:transparent;border:0;border-bottom:2px solid transparent;padding:9px 10px;font-size:12px;color:#637084;}
            QWidget#nativeKnowledge QTabBar::tab:selected {color:#2463EB;border-bottom-color:#2463EB;}
        ''')
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(22, 22, 22, 22)
        self.v.setSpacing(9)
        self.v.setAlignment(Qt.AlignTop)
        origin_row = QHBoxLayout()
        origin_row.setSpacing(8)
        origin_row.addWidget(LineIcon('history', '#2463EB'))
        self.origin = label('任务历史', 'font-size:12px;color:#637084;')
        origin_row.addWidget(self.origin, 1)
        self.source = label('', 'font-size:11px;color:#637084;')
        self.source.setProperty('i18nSkip', True)
        self.source.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        origin_row.addWidget(self.source)
        self.v.addLayout(origin_row)
        self.title = label('', 'font-size:22px;font-weight:600;')
        self.v.addWidget(self.title)
        self.summary = label('', 'font-size:14px;color:#637084;')
        self.v.addWidget(self.summary)
        self.meta_frame = QFrame()
        self.meta_frame.setObjectName('answerMeta')
        meta = QHBoxLayout(self.meta_frame)
        meta.setContentsMargins(0, 5, 0, 16)
        meta.setSpacing(10)
        self.scope = label('', 'font-size:12px;color:#637084;')
        meta.addWidget(self.scope, 1)
        self.status = label('')
        self.status.setWordWrap(False)
        self.status.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        meta.addWidget(self.status)
        self.v.addWidget(self.meta_frame)
        self.body = QWidget()
        self.body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 8, 0, 0)
        self.body_layout.setSpacing(18)
        self.body_layout.setAlignment(Qt.AlignTop)
        self.v.addWidget(self.body)
        self.timer = QTimer(self)
        self.timer.setInterval(3500)
        self.timer.timeout.connect(self.next_step)
        # Retain the public context label used by project-association dialogs.
        self.project_label = label('', 'font-size:12px;color:#637084;')
        self.project_label.setProperty('i18nSkip', True)
        self.project_label.hide()
        self.v.addWidget(self.project_label)

    def load(self, result):
        signature = language() + '|' + json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
        if signature == self.signature:
            return
        self.stop()
        self.signature, self.result = signature, result
        self.data = present(result)
        self.index, self.expanded = 0, False
        self.render()

    def render(self):
        self.setUpdatesEnabled(False)
        try:
            clear(self.body_layout)
            self.flow, self.step_buttons = None, []
            self.project_label.hide()
            data = self.data
            kind = data.get('kind', 'home')
            self.origin.setText('任务历史' if kind.startswith('task') else '项目与任务')
            raw = self.result.get('presentation', {}) or self.result.get('projectDetails', {}) or self.result.get('projectInventory', {})
            source = raw.get('source', '')
            self.source.setText({'workbuddy': 'WorkBuddy', 'codex': 'Codex'}.get(source, source))
            self.source.setVisible(bool(source))
            self.title.setText(short(data.get('title') or '从项目或需求继续了解', 100))
            self.summary.setText(short(data.get('summary'), 320))
            self.summary.setVisible(bool(data.get('summary')))
            self.scope.setText(' · '.join(str(item) for item in data.get('meta', []) if item))
            status = data.get('status') or {}
            self.status.setText(status.get('text', ''))
            color, background = {
                'green': ('#16744B', '#EDF7F1'),
                'warning': ('#956017', '#FFF6E7'),
                'error': ('#B52E35', '#FFF1F1'),
            }.get(status.get('tone'), ('#637084', '#F7F8FA'))
            self.status.setStyleSheet(f'font-size:11px;color:{color};background:{background};border-radius:5px;padding:3px 7px;')
            self.status.setVisible(bool(status.get('text')))
            self.meta_frame.setVisible(bool(self.scope.text() or status.get('text')))
            if kind in ('projects', 'home', 'choices'):
                self._projects(data.get('projects', []), kind)
            elif kind == 'project':
                self._project()
            elif kind.startswith('task'):
                self._task(kind)
            else:
                self._projects(data.get('projects', []), kind)
            self._followups(data.get('followups', []))
            if data.get('notice'):
                self.body_layout.addWidget(label(short(data['notice'], 340), 'font-size:11px;color:#637084;'))
            if kind.startswith('task'):
                self._identity_fold()
        finally:
            self.setUpdatesEnabled(True)
        localize_widgets(self)
        self.updateGeometry()
        self.contentChanged.emit()

    def _button(self, text, slot, layout=None):
        button = QPushButton(text)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(slot)
        (layout if layout is not None else self.body_layout).addWidget(button)
        return button

    def _fold(self, title, layout=None):
        fold = Fold(title)
        fold.changed.connect(self.contentChanged.emit)
        (layout if layout is not None else self.body_layout).addWidget(fold)
        return fold

    def _row_action(self, row):
        self.stop()
        task_id = row.get('taskId')
        if task_id:
            self.taskRequested.emit(str(task_id))
        else:
            self.projectRequested.emit(str(row.get('projectId') or row.get('id') or ''))

    def _projects(self, rows, kind):
        if not rows:
            return
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(0)
        for index, row in enumerate(rows[:6]):
            detail = row.get('description') or row.get('latest') or ''
            latest = row.get('latest') if row.get('description') else ''
            meta = ' · '.join(str(x) for x in row.get('meta', []) if x)
            button = ClickRow(row.get('name') or row.get('title') or '', detail, latest or meta,
                              number=f'{index + 1:02}' if row.get('taskId') else '')
            button.clicked.connect(lambda checked=False, item=row: self._row_action(item))
            grid.addWidget(button, index // 2, index % 2)
        for column in range(2):
            grid.setColumnStretch(column, 1)
        self.body_layout.addLayout(grid)
        if len(rows) > 6:
            fold = self._fold(f'查看其余 {len(rows) - 6} 个已读取项目与任务')
            more = QGridLayout()
            more.setHorizontalSpacing(24)
            more.setVerticalSpacing(0)
            for index, row in enumerate(rows[6:]):
                button = ClickRow(row.get('name', ''), row.get('description') or row.get('latest') or '',
                                  ' · '.join(str(x) for x in row.get('meta', []) if x))
                button.clicked.connect(lambda checked=False, item=row: self._row_action(item))
                more.addWidget(button, index // 2, index % 2)
            fold.content_layout.addLayout(more)

    def _project(self):
        rows = self.data.get('projects', [])
        for index, row in enumerate(rows[:6]):
            detail = row.get('description') or row.get('latest') or ''
            button = ClickRow(row.get('name') or row.get('title') or '', detail,
                              ' · '.join(str(x) for x in row.get('meta', []) if x), number=f'{index + 1:02}')
            button.clicked.connect(lambda checked=False, item=row: self._row_action(item))
            self.body_layout.addWidget(button)
        if len(rows) > 6:
            fold = self._fold(f'查看其余 {len(rows) - 6} 个需求任务')
            for index, row in enumerate(rows[6:], 7):
                button = ClickRow(row.get('name', ''), row.get('description') or row.get('latest') or '',
                                  ' · '.join(str(x) for x in row.get('meta', []) if x), number=f'{index:02}')
                button.clicked.connect(lambda checked=False, item=row: self._row_action(item))
                fold.content_layout.addWidget(button)
        if self.data.get('files'):
            fold = self._fold('查看项目内容范围')
            self._file_rows(self.data['files'], fold.content_layout)

    def _task(self, kind):
        requirements = self.data.get('requirements', [])
        context = self.result.get('presentation', {}).get('projectContext') or self.result.get('packet', {}).get('projectContext') or {}
        projects = context.get('projects', [])
        self.project_label.setText('\n'.join(str(item.get('name', '')) + (' / ' + item['repository'] if item.get('repository') else '') for item in projects))
        self.project_label.setToolTip(self.project_label.text())
        # The single metadata row already names known projects; repository
        # details remain available under the raw-record fold.
        if kind != 'task_process' and requirements:
            quote = label('“' + short(requirements[0].get('text'), 180) + '”',
                          'font-size:14px;background:#EDF3FF;border-left:3px solid #2463EB;border-radius:0;padding:8px 13px;')
            quote.setProperty('i18nSkip', True)
            self.body_layout.addWidget(quote)
        if kind == 'task_process':
            self._process()
        elif kind == 'task_reason':
            self._decisions()
        self._proof_fold()
        if kind == 'task_process' and self.data.get('artifacts'):
            fold = self._fold('最后做成了吗？查看交付与核验')
            for artifact in self.data['artifacts'][:12]:
                path = artifact.get('path') or artifact.get('url') or artifact.get('name') or artifact.get('value') or ''
                fold.content_layout.addWidget(label(short(path, 240), 'font-size:13px;'))
                if artifact.get('description'):
                    fold.content_layout.addWidget(label(short(artifact['description'], 220), 'font-size:12px;color:#637084;'))
                self._refs(artifact.get('refs', []), fold.content_layout)

    def _decisions(self):
        row = QHBoxLayout()
        row.setSpacing(16)
        requirements = self.data.get('requirements', [])
        requirement = requirements[0] if requirements else {}
        reason_step = next((step for step in self.data.get('steps', []) if step.get('reasoning')), {})
        decision = next((item for item in self.data.get('decisions', [])
                         if item.get('text') and item.get('refs') and item.get('basis') != 'unknown'), {})
        if reason_step:
            reason_title = '已读取的思路记录'
            reason_text = reason_step['reasoning']
            if reason_step.get('reasoningAssociation') == 'sequence_candidate':
                reason_title = '按顺序找到的候选思路'
                reason_text = '关联待核对：' + reason_text
        elif decision:
            reason_title = decision.get('title') or '记录支持的考虑'
            reason_text = ('分析推断：' if decision.get('basis') == 'inferred' else '') + decision['text']
        else:
            reason_title = '修改原因尚未确认'
            reason_text = '没有找到可支持这次修改原因的关联思路记录。'
        has_recorded_decision = bool(decision)
        files = self.data.get('files', [])
        names = list(dict.fromkeys(PurePath(str(item.get('path', '')).replace('\\', '/')).name for item in files if item.get('path')))
        columns = [
            {'label': '用户想解决什么', 'title': requirement.get('label') or '原始用户要求',
             'text': requirement.get('text') or '当前记录未提供可关联的原始要求。'},
            {'label': '如何考虑这次修改', 'title': reason_title, 'text': reason_text},
            {'label': '执行涉及哪些部分', 'title': '、'.join(names[:3]) or '修改文件尚未确认',
             'text': '；'.join(str(item.get('path', '')) + '（' + t(item.get('description') or '记录路径') + '）'
                              for item in files[:3]) or '没有找到可确认的修改路径；工具调用记录不等于修改已完成。'},
        ]
        for index, decision in enumerate(columns):
            frame = QFrame()
            frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            if index:
                frame.setStyleSheet('QFrame{border:0;border-left:1px solid #E6E8EC;}')
            column = QVBoxLayout(frame)
            column.setContentsMargins(16 if index else 0, 0, 0, 0)
            column.setSpacing(7)
            head = QHBoxLayout()
            head.setSpacing(5)
            head.addWidget(LineIcon(('message', 'model', 'file')[index]))
            head.addWidget(label(decision.get('label'), 'font-size:11px;color:#637084;'), 1)
            column.addLayout(head)
            column.addWidget(label(short(decision.get('title'), 65), 'font-size:15px;font-weight:600;'))
            body = label(short(decision.get('text'), 220), 'font-size:12px;color:#637084;')
            if (index == 0 and requirement.get('text')) or (index == 1 and (reason_step or has_recorded_decision)) or (index == 2 and names):
                body.setProperty('i18nSkip', True)
            column.addWidget(body)
            column.addStretch()
            row.addWidget(frame, 1)
        self.body_layout.addLayout(row)

    def _proof_fold(self):
        requirements = self.data.get('requirements', [])
        self.proof_fold = self._fold(f'查看依据 · {len(requirements)} 次已读取用户发言、思路与修改文件')
        tabs = QTabWidget()
        tabs.setObjectName('proofTabs')
        tabs.setDocumentMode(True)
        self.proof_fold.content_layout.addWidget(tabs)
        for title, key in (('需求与反馈', 'requirements'), ('当时的考虑', 'reasoning'), ('修改文件', 'files')):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 12, 0, 0)
            layout.setSpacing(10)
            if key == 'requirements':
                for index, requirement in enumerate(requirements[:12], 1):
                    history = label(f'{index:02}  ' + short(requirement.get('text'), 280), 'font-size:13px;')
                    history.setProperty('i18nSkip', True)
                    layout.addWidget(history)
                    detail = ' · '.join(str(requirement.get(k) or '') for k in ('label', 'time', 'reason') if requirement.get(k))
                    layout.addWidget(label(short(detail, 220), 'font-size:11px;color:#637084;'))
                    self._refs(requirement.get('refs', []), layout)
                if requirements:
                    self.requirement_button = self._button(f'查看 {len(requirements)} 轮完整需求与确认历程', self.dialogues, layout)
                else:
                    layout.addWidget(label('未读取到可关联的用户发言。', 'font-size:12px;color:#637084;'))
            elif key == 'files':
                self._file_rows(self.data.get('files', []), layout)
            else:
                reasons = self.result.get('presentation', {}).get('reasoning', [])
                if reasons:
                    for reason in reasons[:8]:
                        history = label(short(reason.get('text'), 280), 'font-size:13px;')
                        history.setProperty('i18nSkip', True)
                        layout.addWidget(history)
                        self._button('查看思路原文', lambda checked=False, item=reason: self.raw('已记录思路', item.get('text', '')), layout)
                else:
                    layout.addWidget(label('未读取到可关联的思路原文。', 'font-size:12px;color:#637084;'))
                for decision in self.data.get('decisions', []):
                    if decision.get('basis'):
                        basis = {'recorded': '记录支持', 'inferred': '分析推断', 'unknown': '尚未确认',
                                 'source_parent_path': '原始消息链关联', 'sequence_candidate': '按记录顺序候选关联，待核对'}.get(decision['basis'], decision['basis'])
                        layout.addWidget(label(short(basis, 240), 'font-size:11px;color:#637084;'))
                    self._refs(decision.get('refs', []), layout)
            tabs.addTab(page, title)
        tabs.currentChanged.connect(lambda index: self.contentChanged.emit())

    def _file_rows(self, files, layout):
        for item in files[:14]:
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(LineIcon('folder' if str(item.get('path', '')).endswith('/') else 'file'), 0, Qt.AlignTop)
            column = QVBoxLayout()
            column.setSpacing(3)
            path = label(short(item.get('path'), 230), 'font-size:13px;')
            path.setProperty('i18nSkip', True)
            column.addWidget(path)
            if item.get('description'):
                column.addWidget(label(short(item['description'], 180), 'font-size:12px;color:#637084;'))
            row.addLayout(column, 1)
            layout.addLayout(row)
            self._refs(item.get('refs', []), layout)
        if not files:
            layout.addWidget(label('没有可确认的修改文件记录。', 'font-size:12px;color:#637084;'))
        if len(files) > 14:
            self._button(f'查看全部 {len(files)} 个文件记录', lambda: self.raw('修改文件记录', '\n\n'.join(
                str(item.get('path', '')) + '\n' + str(item.get('description', '')) for item in files)), layout)

    def _refs(self, refs, layout):
        # Only packet evidence identifiers can be opened by ChatWindow. Event
        # identifiers remain in raw-record dialogs rather than broken links.
        known = {fragment.get('evidenceId') for fragment in self.result.get('packet', {}).get('fragments', [])}
        refs = list(dict.fromkeys(str(ref) for ref in refs if ref and ref in known))
        if not refs:
            return
        row = QHBoxLayout()
        row.setSpacing(6)
        for ref in refs[:4]:
            self._button('核对来源 ' + ref, lambda checked=False, identity=ref: self._request_evidence(identity), row)
        row.addStretch()
        layout.addLayout(row)

    def _request_evidence(self, identity):
        self.stop()
        self.evidenceRequested.emit(identity)

    def _process(self):
        steps = self.data.get('steps', [])
        header = QHBoxLayout()
        header.setSpacing(6)
        header.addWidget(label('一次任务里的内容往返', 'font-size:15px;font-weight:600;'), 1)
        self.play_button = self._button('播放过程', self.toggle_play, header)
        self.next_button = self._button('下一步', self.advance_manually, header)
        self.speed = QComboBox()
        self.speed.addItems(['1×', '2×'])
        self.speed.setAccessibleName('播放速度')
        self.speed.currentIndexChanged.connect(self._speed_changed)
        header.addWidget(self.speed)
        self.body_layout.addLayout(header)
        if not steps:
            self.play_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.body_layout.addWidget(label('当前记录没有工具调用。', 'font-size:13px;color:#637084;'))
            return
        chain = QHBoxLayout()
        chain.setSpacing(8)
        for _ in range(min(4, len(steps))):
            button = QPushButton()
            button.setObjectName('stepButton')
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            button.clicked.connect(lambda checked=False, item=button: self.select_step(item.property('stepIndex')))
            chain.addWidget(button, 1)
            self.step_buttons.append(button)
        self.body_layout.addLayout(chain)
        self.flow = RelationFlow()
        self.body_layout.addWidget(self.flow)
        self.route_label = label('', 'font-size:12px;color:#637084;')
        self.body_layout.addWidget(self.route_label)
        columns = QHBoxLayout()
        columns.setSpacing(24)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(8)
        right.setSpacing(8)
        self.input_heading = label('', 'font-size:12px;color:#637084;')
        left.addWidget(self.input_heading)
        self.input_labels = []
        for _ in range(5):
            field = label('', 'font-size:13px;')
            field.setProperty('i18nSkip', True)
            left.addWidget(field)
            self.input_labels.append(field)
        left.addStretch()
        right.addWidget(label('返回了什么', 'font-size:12px;color:#637084;'))
        self.return_label = label('', 'font-size:13px;')
        self.return_label.setProperty('i18nSkip', True)
        self.next_label = label('', 'font-size:12px;color:#637084;')
        right.addWidget(self.return_label)
        right.addWidget(self.next_label)
        right.addStretch()
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        self.body_layout.addLayout(columns)
        self.step_details = self._fold('这一轮的思路与后续内容关联')
        self.reasoning_label = label('', 'font-size:13px;')
        self.reasoning_label.setProperty('i18nSkip', True)
        self.reasoning_basis = label('', 'font-size:11px;color:#637084;')
        self.step_details.content_layout.addWidget(self.reasoning_label)
        self.step_details.content_layout.addWidget(self.reasoning_basis)
        self.reasoning_button = self._button('查看这一轮思路原文', self._step_reasoning, self.step_details.content_layout)
        raw_fold = self._fold('查看参数与原始返回位置')
        self._button('打开原始参数与返回', self._step_raw, raw_fold.content_layout)
        refs_row = QHBoxLayout()
        self.step_ref_buttons = []
        for _ in range(4):
            button = self._button('', lambda checked=False: None, refs_row)
            button.clicked.disconnect()
            button.clicked.connect(lambda checked=False, item=button: self._request_evidence(item.property('evidenceId')))
            button.hide()
            self.step_ref_buttons.append(button)
        refs_row.addStretch()
        raw_fold.content_layout.addLayout(refs_row)
        self.play_state = label('', 'font-size:11px;color:#637084;')
        self.body_layout.addWidget(self.play_state)
        self._update_step()

    def refresh_language(self):
        # Rebuild only readable field labels. Keep timers, animation progress,
        # selected steps, raw records and opened evidence in place.
        if self.flow is not None:self._update_step(refresh_flow=False)
        else:localize_widgets(self)

    def _update_step(self, animate=False, refresh_flow=True):
        steps = self.data.get('steps', [])
        if not steps or self.flow is None:
            return
        self.index = max(0, min(self.index, len(steps) - 1))
        step = steps[self.index]
        start = min(max(0, self.index - 1), max(0, len(steps) - 4))
        for offset, button in enumerate(self.step_buttons):
            index = start + offset
            item = steps[index]
            button.setProperty('stepIndex', index)
            prefix = '✓' if index < self.index else str(index + 1)
            button.setText(prefix + '  ' + short(item.get('title') or item.get('call'), 23))
            button.setToolTip(str(item.get('title') or item.get('call') or ''))
            button.setChecked(index == self.index)
            button.setAccessibleName(f'第 {index + 1} 步，' + str(item.get('title') or item.get('call') or ''))
        if refresh_flow:self.flow.set_step(step, step.get('agentLabel') or self.source.text())
        if animate:
            self.flow.play(self._speed())
        self.route_label.setText(short(step.get('route'), 230))
        self.input_heading.setText(short(step.get('call'), 95) + ' · 输入')
        fields = step.get('inputFields', [])
        for index, widget in enumerate(self.input_labels):
            if index < len(fields):
                field = fields[index]
                widget.setText(t(field.get('label', '参数')) + (': ' if language() == 'en' else '：') + short(field.get('value'), 220))
                widget.show()
            elif index == 0:
                widget.setText(t('没有可读的参数字段。'))
                widget.show()
            else:
                widget.hide()
        returned = step.get('returnText') or '没有找到对应的工具返回，结果尚未确认。'
        # Translate adapter field labels only for structured returns. Plain
        # historical return text remains untouched, even if it matches UI text.
        call = next((item for item in self.result.get('presentation', {}).get('calls', [])
                     if item.get('id') == step.get('eventId')), {})
        structured = bool(call.get('returns'))
        for item in call.get('returns', []):
            try:
                structured = structured and isinstance(json.loads(item.get('text', '')), (dict, list))
            except (TypeError, ValueError):
                structured = False
        return_lines = []
        for line in returned.splitlines():
            key, separator, value = line.partition('：')
            if structured and separator:
                return_lines.append(t(key) + (': ' if language() == 'en' else '：') + value)
            else:
                return_lines.append(t(line) if not call.get('returns') else line)
        self.return_label.setText(short('\n'.join(return_lines), 360))
        self.next_label.setText(short(step.get('nextText') or '后续内容关联未知。', 260))
        self.reasoning_label.setText(short(step.get('reasoning') or t('这一调用没有找到可关联的思路记录。'), 480))
        self.reasoning_basis.setText(str(step.get('reasoningBasis') or '思路与工具调用的原始关系未记录。'))
        self.reasoning_button.setEnabled(bool(step.get('reasoning')))
        known = {fragment.get('evidenceId') for fragment in self.result.get('packet', {}).get('fragments', [])}
        refs = list(dict.fromkeys(ref for ref in step.get('refs', []) if ref in known))
        for index, button in enumerate(self.step_ref_buttons):
            if index < len(refs):
                button.setText('核对来源 ' + str(refs[index]))
                button.setProperty('evidenceId', refs[index])
                button.show()
            else:
                button.hide()
        self.next_button.setEnabled(self.index < len(steps) - 1)
        self.play_button.setText('暂停' if self.timer.isActive() else '播放过程')
        prefix = '正在回放' if self.timer.isActive() else '当前'
        self.play_state.setText(f'{prefix}：第 {self.index + 1} / {len(steps)} 步 · 仅展示已记录的关系')
        localize_widgets(self)
        self.updateGeometry()
        self.contentChanged.emit()

    def _speed(self):
        return 2 if hasattr(self, 'speed') and self.speed.currentIndex() == 1 else 1

    def _speed_changed(self):
        self.timer.setInterval(int(3500 / self._speed()))
        if self.timer.isActive() and self.flow:
            self.flow.play(self._speed())

    def select_step(self, index):
        self.stop()
        self.index = int(index or 0)
        self._update_step(animate=True)

    def advance_manually(self):
        self.stop()
        self.next_step()

    def toggle_play(self):
        if self.timer.isActive():
            self.stop()
            return
        steps = self.data.get('steps', [])
        if not steps:
            return
        if self.index == len(steps) - 1:
            self.index = 0
        self.timer.start(int(3500 / self._speed()))
        self._update_step(animate=True)

    def next_step(self):
        steps = self.data.get('steps', [])
        if not steps:
            self.stop()
            return
        if self.index + 1 >= len(steps):
            self.stop()
            return
        self.index += 1
        self._update_step(animate=True)

    def stop(self):
        self.timer.stop()
        if self.flow:
            self.flow.stop()
        if hasattr(self, 'play_button'):
            try:
                self.play_button.setText(t('播放过程'))
                if hasattr(self, 'play_state'):
                    steps = self.data.get('steps', [])
                    self.play_state.setText(t(f'当前：第 {self.index + 1} / {len(steps)} 步 · 仅展示已记录的关系'))
            except RuntimeError:
                pass  # A previous card may have been deferred for deletion.

    def hideEvent(self, event):
        self.stop()
        super().hideEvent(event)

    def _step_reasoning(self):
        step = self.data.get('steps', [])[self.index]
        self.raw('已记录思路', str(step.get('reasoning') or '') + '\n\n关联依据：' + str(step.get('reasoningBasis') or '未知'))

    def _step_raw(self):
        step = self.data.get('steps', [])[self.index]
        event_id = step.get('eventId')
        call = next((item for item in self.result.get('presentation', {}).get('calls', []) if item.get('id') == event_id), {})
        args = call.get('arguments') or {field.get('label'): field.get('value') for field in step.get('inputFields', [])}
        returns = '\n\n'.join(str(item.get('text') or '') for item in call.get('returns', [])) or str(step.get('returnText') or '')
        text = '工具：' + str(step.get('call') or '') + '\n调用记录：' + str(event_id or '未记录')
        text += '\ncallId：' + str(step.get('callId') or '未记录')
        text += '\n\n参数\n' + json.dumps(args, ensure_ascii=False, indent=2)
        text += '\n\n原始返回\n' + returns
        text += '\n\n返回关联\n' + json.dumps(step.get('returnRelations', []), ensure_ascii=False, indent=2)
        text += '\n\n思路关联\n' + json.dumps(step.get('reasoningRelations', []), ensure_ascii=False, indent=2)
        text += '\n\n后续关联\n' + json.dumps(step.get('nextRelations', []), ensure_ascii=False, indent=2)
        self.raw('工具参数与原始返回', text)

    def _followups(self, followups):
        if not followups:
            return
        row = QHBoxLayout()
        row.setSpacing(8)
        for question in followups[:3]:
            self._button(short(t(question), 55) + '  ↗', lambda checked=False, q=question: self._ask(t(q)), row)
        row.addStretch()
        self.body_layout.addLayout(row)

    def _ask(self, question):
        self.stop()
        self.questionRequested.emit(str(question))

    def _identity_fold(self):
        fold = self._fold('查看任务标识与原始记录位置')
        raw = self.result.get('presentation', {})
        details = [
            '任务：' + str(self.data.get('taskId') or raw.get('taskId') or '未记录'),
            '会话：' + str(raw.get('session') or '未记录'),
            '来源：' + (self.source.text() or '未记录'),
        ]
        if self.project_label.text():
            details.append('项目：' + self.project_label.text())
        fold.content_layout.addWidget(label('\n'.join(details), 'font-size:12px;color:#637084;'))
        row = QHBoxLayout()
        self.association_button = self._button('修正需求关联', lambda: self.associationRequested.emit(), row)
        self.project_button = self._button('修正项目归属', lambda: self.projectCorrectionRequested.emit(), row)
        row.addStretch()
        fold.content_layout.addLayout(row)

    def dialogues(self):
        rows = self.data.get('requirements', [])
        text = '\n\n'.join(
            f'{index} / {row.get("label", "用户发言")}\n{row.get("text", "")}\n关联依据：{row.get("reason") or "未记录"}'
            for index, row in enumerate(rows, 1))
        self.raw('需求与确认历程', text)

    def raw(self, title, text):
        self.stop()
        if getattr(self,'raw_handler',None):
            self.raw_handler(title,text)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(t(title))
        dialog.resize(850, 600)
        layout = QVBoxLayout(dialog)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(str(text))
        layout.addWidget(edit)
        dialog.exec()

    def plain_text(self):
        from PySide6.QtWidgets import QLabel
        return '\n'.join(widget.text() for widget in self.findChildren(QLabel) if widget.isVisibleTo(self))
