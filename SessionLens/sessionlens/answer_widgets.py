"""Small native widgets shared by the evidence-backed answer cards."""
from PySide6.QtCore import Qt, Signal, QSize, QRectF, QPointF, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QToolButton, QVBoxLayout, QHBoxLayout,
    QSizePolicy, QFrame,
)
from .i18n import t

INK = '#202834'
SOFT = '#626d7d'
LINE = '#e6e8ec'
BLUE = '#2563eb'


def short(value, limit=180):
    text = ' '.join(str(value or '').split())
    return text if len(text) <= limit else text[:limit] + '…'


def label(text='', style=''):
    widget = QLabel(str(text or ''))
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    widget.setStyleSheet(style)
    return widget


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            clear(item.layout())


class LineIcon(QWidget):
    """Platform-independent line icons, drawn with the native Qt painter."""
    def __init__(self, kind='folder', color=SOFT, parent=None):
        super().__init__(parent)
        self.kind, self.color = kind, color
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(self.color), 1.15))
        painter.setBrush(Qt.NoBrush)
        if self.kind == 'history':
            painter.drawArc(QRectF(3, 3, 12, 12), 35 * 16, 285 * 16)
            painter.drawLine(2, 3, 2, 7)
            painter.drawLine(2, 7, 6, 7)
            painter.drawLine(9, 5, 9, 9)
            painter.drawLine(9, 9, 12, 11)
        elif self.kind == 'folder':
            path = QPainterPath()
            path.moveTo(2, 5); path.lineTo(7, 5); path.lineTo(9, 7)
            path.lineTo(16, 7); path.lineTo(16, 15); path.lineTo(2, 15)
            path.closeSubpath(); painter.drawPath(path)
        elif self.kind == 'message':
            painter.drawRoundedRect(QRectF(2, 3, 14, 10), 2, 2)
            painter.drawLine(4, 13, 4, 16); painter.drawLine(4, 16, 8, 13)
        elif self.kind == 'model':
            painter.drawRoundedRect(QRectF(4, 3, 10, 12), 4, 4)
            painter.drawLine(9, 3, 9, 15)
            for x, y in ((4, 7), (5, 11), (14, 7), (13, 11)):
                painter.drawLine(x, y, 9, y + 1)
        elif self.kind == 'tool':
            path = QPainterPath()
            path.moveTo(12, 2); path.lineTo(10, 6); path.lineTo(13, 9)
            path.lineTo(16, 6); path.cubicTo(17, 11, 12, 13, 10, 11)
            path.lineTo(5, 16); path.lineTo(2, 13); path.lineTo(7, 8)
            path.cubicTo(5, 5, 8, 1, 12, 2); painter.drawPath(path)
        else:
            path = QPainterPath()
            path.moveTo(4, 2); path.lineTo(11, 2); path.lineTo(15, 6)
            path.lineTo(15, 16); path.lineTo(4, 16); path.closeSubpath()
            painter.drawPath(path); painter.drawLine(11, 2, 11, 6)
            painter.drawLine(11, 6, 15, 6)


class ClickRow(QPushButton):
    """The entire row is a keyboard-accessible button, including its details."""
    def __init__(self, title, detail='', meta='', number='', icon='folder', parent=None):
        super().__init__(parent)
        self.setObjectName('answerRow')
        self.setCursor(Qt.PointingHandCursor)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumWidth(0)
        self.setAccessibleName(str(title))
        self.setToolTip(str(title))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 14, 0, 14)
        row.setSpacing(9)
        if number:
            leading = label(number, 'font-size:12px;color:' + SOFT + ';')
            leading.setFixedWidth(26)
        else:
            leading = QFrame()
            leading.setObjectName('rowIcon')
            leading.setFixedSize(32, 32)
            layout = QVBoxLayout(leading)
            layout.setContentsMargins(7, 7, 7, 7)
            layout.addWidget(LineIcon(icon))
        row.addWidget(leading, 0, Qt.AlignTop)
        content = QVBoxLayout()
        content.setSpacing(5)
        self.title_label = label(short(title, 100), 'font-size:14px;font-weight:600;')
        self.title_label.setProperty('i18nSkip', True)
        self.detail_label = label(short(detail, 190), 'font-size:12px;color:' + SOFT + ';')
        self.meta_label = label(short(meta, 150), 'font-size:12px;color:' + SOFT + ';')
        content.addWidget(self.title_label)
        if detail: content.addWidget(self.detail_label)
        if meta: content.addWidget(self.meta_label)
        row.addLayout(content, 1)
        arrow = label('›', 'font-size:20px;color:' + SOFT + ';')
        arrow.setFixedWidth(15)
        row.addWidget(arrow, 0, Qt.AlignTop)
        for widget in self.findChildren(QWidget):
            widget.setAttribute(Qt.WA_TransparentForMouseEvents)

    def sizeHint(self):
        # QAbstractButton's default hint measures its (empty) button text and
        # ignores this row's native child layout.
        return self.layout().totalSizeHint() if self.layout() else super().sizeHint()

    def minimumSizeHint(self):
        hint = self.layout().totalMinimumSize() if self.layout() else super().minimumSizeHint()
        return QSize(160, max(74, hint.height()))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return max(74, self.layout().totalHeightForWidth(width)) if self.layout() else 74


class Fold(QWidget):
    changed = Signal()
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName('answerFold')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)
        self.toggle = QToolButton()
        self.toggle.setObjectName('foldToggle')
        self.toggle.setText(title)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.RightArrow)
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toggle.toggled.connect(self._toggled)
        layout.addWidget(self.toggle)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        layout.addWidget(self.content)
        self.content.hide()

    def _toggled(self, expanded):
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.content.setVisible(expanded)
        self.updateGeometry()
        self.changed.emit()

    def isExpanded(self):
        return self.toggle.isChecked()


class RelationFlow(QWidget):
    """Finite native animation of observed edges, never inferred API traffic."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('relationFlow')
        self.setMinimumHeight(164)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.names = {'agent': 'Agent 来源未知', 'model': '模型身份未知', 'tool': '工具未记录'}
        self.edges = []
        self.progress = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.valueChanged.connect(self._advance)

    def set_step(self, step, agent=''):
        self.stop()
        self.names = {
            'agent': agent or 'Agent 来源未知',
            'model': step.get('modelLabel') or '模型身份未知',
            'tool': step.get('call') or '工具未记录',
        }
        # Only the projection can authorize a relationship. In particular, a
        # nearby model message is not an edge and does not light the model node.
        self.edges = [edge for edge in step.get('flowEdges', [])
                      if edge.get('from') in self.names and edge.get('to') in self.names]
        self.setAccessibleName('；'.join(
            t(self.names[e['from']]) + ' → ' + t(self.names[e['to']]) + '：' + t(e.get('label', ''))
            for e in self.edges) or '记录未提供可确认的节点关系')
        self.update()

    def play(self, speed=1):
        self.animation.stop()
        self.progress = 0.0
        if self.edges:
            self.animation.setDuration(int(2600 / speed))
            self.animation.start()
        self.update()

    def stop(self):
        self.animation.stop()
        self.progress = 0.0
        self.update()

    def _advance(self, value):
        self.progress = float(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#f1f4f8'))
        painter.drawRoundedRect(self.rect(), 8, 8)
        gap, margin = 12, 16
        width = max(20, (self.width() - 2 * margin - 2 * gap) / 3)
        positions = {}
        font = painter.font(); font.setPixelSize(12); painter.setFont(font)
        focused = set()
        current = None
        if self.edges:
            index = min(len(self.edges) - 1, int(self.progress * len(self.edges)))
            current = self.edges[index]
            focused = {current['from'], current['to']}
        for index, key in enumerate(('agent', 'model', 'tool')):
            rect = QRectF(margin + index * (width + gap), 20, width, 42)
            positions[key] = QPointF(rect.center().x(), rect.bottom() + 2)
            painter.setBrush(QColor('white'))
            painter.setPen(QPen(QColor(BLUE if key in focused else LINE), 1))
            painter.drawRoundedRect(rect, 7, 7)
            painter.setPen(QColor(INK if key != 'model' or self.names[key] != '模型身份未知' else SOFT))
            text = painter.fontMetrics().elidedText(t(self.names[key]) if key != 'tool' else self.names[key], Qt.ElideRight, int(width - 12))
            painter.drawText(rect.adjusted(6, 0, -6, 0), Qt.AlignCenter, text)
        if current:
            start, end = positions[current['from']], positions[current['to']]
            y = 87
            painter.setPen(QPen(QColor('#c4d2e7'), 1.3))
            path = QPainterPath(start)
            path.cubicTo(QPointF(start.x(), y), QPointF(end.x(), y), end)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
            direction = -1 if end.x() < start.x() else 1
            painter.drawLine(end, QPointF(end.x() - direction * 4, end.y() + 7))
            painter.drawLine(end, QPointF(end.x() + direction * 4, end.y() + 7))
            if self.animation.state() == QVariantAnimation.Running:
                fraction = (self.progress * len(self.edges)) % 1
                point = path.pointAtPercent(fraction)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(BLUE))
                painter.drawEllipse(point, 4, 4)
            caption = current.get('label') or '已记录关系'
        else:
            caption = '节点间的原始关系未记录'
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('white'))
        painter.drawRoundedRect(QRectF(16, 108, self.width() - 32, 38), 4, 4)
        painter.setBrush(QColor(BLUE))
        painter.drawRect(QRectF(16, 108, 3, 38))
        painter.setPen(QColor(SOFT))
        painter.drawText(QRectF(28, 108, self.width() - 56, 38), Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(t(caption), Qt.ElideRight, max(10, self.width() - 56)))
