"""Local-only native desktop visualization for Doubao creation records.

The UI does not make network requests. Collector work is serialized on a worker
thread, and unchanged snapshots never rebuild the user's current view.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap, QRawFont, QTextLayout
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

ACCENT = "#48B5F4"
MUTED = "#8F9CAF"
UI_FONT = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI" if sys.platform == "win32" else "Noto Sans"
MONO_FONT = "Menlo" if sys.platform == "darwin" else "Consolas" if sys.platform == "win32" else "monospace"
FONT_PROBE_ASCII = 'image_to_video SYNTHETIC_QA_MODEL 0123456789 3:4 {}[]()<>:/._=-"\' +@%'
FONT_PROBE_CHINESE = "豆包视频制作可视化完整提示词调用参数工具返回图片来源记录接口请求未取得本机数据目录确认方案合成验收生成结果执行过程"
STATUS_TEXT = {
    "completed": "工具已返回", "delivered": "已交付", "running": "进行中",
    "pending": "等待中", "failed": "失败", "error": "错误", "unknown": "待核验",
    "waiting": "等待确认", "active": "采集中", "idle": "等待新记录",
    "watching": "本机采集中", "source_not_found": "没有发现豆包记录",
    "delivered_unverified": "已生成 · 待核验", "delivered_verified": "已生成 · 已核验",
    "observed": "已记录", "recorded": "已记录",
    "generated_unverified": "视频已生成 · 待核验", "generated_verified": "视频已生成 · 已核验",
    "waiting_confirmation": "等待用户确认",
}
KIND_TEXT = {
    "user": "用户需求", "user_message": "用户需求", "request": "用户需求",
    "assistant": "豆包回复", "assistant_message": "豆包回复", "message": "对话记录",
    "tool_call": "工具调用", "tool_result": "工具返回", "reasoning": "已记录的执行思路",
    "file": "文件活动", "network": "网络活动", "process": "进程活动",
    "user_request": "用户需求", "user_feedback": "用户确认或补充",
}
STYLE_TEMPLATE = """
* { font-family: '__UI_FONT__'; font-size: 13px; color: #DFE6EF; }
QMainWindow, QWidget#root { background: #0B1018; }
QWidget#sidebar { background: #101720; border-right: 1px solid #233041; }
QFrame#card { background: #131D29; border: 1px solid #293648; border-radius: 12px; }
QFrame#toolCard { background: #101B28; border: 1px solid #285575; border-radius: 12px; }
QLabel#muted { color: #8F9CAF; font-size: 12px; }
QLabel#eyebrow { color: #72BBE4; font-size: 11px; font-weight: 600; }
QLabel#title { font-size: 22px; font-weight: 600; color: #F2F6FC; }
QLabel#section { font-size: 15px; font-weight: 600; }
QLabel#brand { font-size: 15px; font-weight: 600; color: #F2F6FC; }
QLabel#status { color: #A3DAFA; background: #183951; border-radius: 9px; padding: 5px 10px; font-size: 11px; }
QLabel#amber { color: #EBC98A; background: #342C20; border-radius: 7px; padding: 5px 8px; font-size: 11px; }
QPushButton, QToolButton { background: #1B2838; border: 1px solid #34455B; border-radius: 7px; padding: 7px 11px; }
QPushButton:hover, QToolButton:hover { border-color: #48B5F4; background: #24364B; }
QPushButton:disabled { color: #586577; border-color: #253142; background: #151F2B; }
QPushButton#primary { background: #2995D6; border: 1px solid #42ADF0; color: #FFFFFF; font-weight: 600; }
QToolButton#disclosure { background: transparent; border: none; text-align: left; padding: 5px 0; color: #97C9E8; }
QListWidget { background: transparent; border: none; outline: none; padding: 0; }
QListWidget::item { border-radius: 8px; padding: 12px 10px; margin-bottom: 4px; color: #B6C3D3; }
QListWidget::item:selected { background: #193248; color: #E0F3FF; border-left: 3px solid #48B5F4; }
QListWidget::item:hover { background: #192535; }
QComboBox { background: #172434; border: 1px solid #34455B; border-radius: 6px; padding: 6px 10px; min-width: 64px; }
QComboBox QAbstractItemView { background: #182536; selection-background-color: #284864; }
QPlainTextEdit { background: #0D151F; color: #D5E1F0; border: 1px solid #293849; border-radius: 8px; padding: 10px; selection-background-color: #276090; }
QPlainTextEdit[monospace="true"] { font-family: '__MONO_FONT__', '__UI_FONT__'; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { width: 8px; background: transparent; margin: 3px; }
QScrollBar::handle:vertical { background: #334258; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 7px; background: transparent; }
QScrollBar::handle:horizontal { background: #334258; border-radius: 3px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QSplitter::handle { background: #0B1018; width: 12px; }
QTabWidget::pane { border: none; background: #0B1018; }
QTabBar::tab { background: #111D2B; border: 1px solid #2A3D52; color: #9FAFC1; border-radius: 7px; padding: 8px 15px; margin-right: 6px; margin-bottom: 10px; }
QTabBar::tab:selected { background: #173E57; color: #E0F4FF; border-color: #48B5F4; }
"""
STYLE = STYLE_TEMPLATE.replace("__UI_FONT__", UI_FONT).replace("__MONO_FONT__", MONO_FONT)


def font_probe(font: QFont, sample: str) -> dict:
    """Verify resolved glyph runs, including the font actually used for fallback.

    Merely enumerating a family is insufficient on Qt's offscreen Windows
    platform: a screenshot can succeed while every character is .notdef.
    """
    raw = QRawFont.fromFont(font)
    # Probe every character across multiline prompt/JSON bodies. Line breaks
    # do not require glyphs and must not stop the single-line QA layout.
    layout = QTextLayout(re.sub(r"[\r\n\t\u2028\u2029]", " ", sample), font)
    layout.beginLayout()
    line = layout.createLine()
    if line.isValid():
        line.setLineWidth(100000)
    layout.endLayout()
    runs = layout.glyphRuns()
    glyphs = [int(glyph) for run in runs for glyph in run.glyphIndexes()]
    resolved = sorted({run.rawFont().familyName() for run in runs})
    valid = raw.isValid() and bool(runs) and all(run.rawFont().isValid() for run in runs)
    return {"requestedFamilies": font.families(), "rawFontFamily": raw.familyName(),
            "resolvedFamilies": resolved, "rawFontValid": raw.isValid(),
            "glyphCount": len(glyphs), "missingGlyphs": glyphs.count(0),
            "passed": bool(valid and glyphs and 0 not in glyphs), "sample": sample}


def font_asset_directories() -> list[Path]:
    directories = [Path(__file__).resolve().parent.parent / "assets/fonts"]
    if getattr(sys, "_MEIPASS", None):
        directories.insert(0, Path(sys._MEIPASS) / "assets/fonts")
    return list(dict.fromkeys(directories))


def initialize_fonts(app: QApplication, *, platform: str | None = None,
                     fonts_dir: Path | None = None, asset_dirs: list[Path] | None = None,
                     diagnostics_path: Path | None = None, force: bool = False) -> dict:
    """Read and register Windows fonts without copying any system font files.

    On Windows only, local OFL assets are a fallback when the available system
    fonts cannot render both ASCII and Chinese. All failures are explicit and
    recorded before raising; CI must not accept a square-glyph screenshot.
    """
    global UI_FONT, MONO_FONT, STYLE
    platform = platform or sys.platform
    cached = app.property("doubaoFontDiagnostics")
    if cached and cached.get("platform") == platform and not force:
        if diagnostics_path:
            diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
            diagnostics_path.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")
        if platform == "win32" and not cached.get("passed"):
            raise RuntimeError(cached.get("error", "Windows font glyph verification failed."))
        return cached
    report = {"platform": platform, "qtPlatform": app.platformName(), "familiesBefore": QFontDatabase.families(),
              "registrations": [], "registrationCount": 0, "candidateProbes": [], "passed": False}

    def save_report():
        report["familiesAfter"] = QFontDatabase.families()
        app.setProperty("doubaoFontDiagnostics", report)
        if diagnostics_path:
            diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
            diagnostics_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def register(path: Path, origin: str) -> list[str]:
        identifier = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(identifier) if identifier >= 0 else []
        report["registrations"].append({"path": str(path), "origin": origin, "fontId": identifier,
                                        "families": families, "registered": identifier >= 0 and bool(families)})
        if identifier >= 0 and families:
            report["registrationCount"] += 1
        return families

    def usable_ui(families: list[str]) -> str | None:
        preferred = ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Noto Sans SC"]
        ordered = [name for name in preferred if name in families] + [name for name in families if name not in preferred]
        for family in dict.fromkeys(ordered):
            font = QFont(family, 12)
            normal = font_probe(font, FONT_PROBE_ASCII + " " + FONT_PROBE_CHINESE)
            font.setWeight(QFont.Weight.DemiBold)
            bold = font_probe(font, FONT_PROBE_ASCII + " " + FONT_PROBE_CHINESE)
            report["candidateProbes"].append({"family": family, "normal": normal, "demiBold": bold})
            if normal["passed"] and bold["passed"]:
                return family
        return None

    if platform == "win32":
        fonts_dir = fonts_dir or Path(os.environ.get("WINDIR", os.environ.get("SystemRoot", r"C:\Windows"))) / "Fonts"
        report["systemFontsDirectory"] = str(fonts_dir)
        registered = []
        for name in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc", "segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "consola.ttf", "consolab.ttf"):
            path = fonts_dir / name
            if path.is_file():
                registered.extend(register(path, "windows_system_read_only"))
        selected = usable_ui(registered)
        if not selected:
            directories = asset_dirs if asset_dirs is not None else font_asset_directories()
            report["fallbackDirectories"] = [str(path) for path in directories]
            for directory in directories:
                if not directory.is_dir():
                    continue
                for path in sorted(directory.iterdir()):
                    if path.suffix.lower() in {".ttf", ".otf", ".ttc"} and path.is_file():
                        registered.extend(register(path, "bundled_open_font"))
            selected = usable_ui(registered)
        if report["registrationCount"] == 0 or not selected:
            report["error"] = ("Windows font initialization failed: no registered font can render ASCII and Chinese. "
                               "Inspect registrations and glyph coverage; do not accept the screenshot.")
            save_report()
            raise RuntimeError(report["error"])
        UI_FONT = selected
        MONO_FONT = "Consolas" if "Consolas" in registered else UI_FONT
    else:
        UI_FONT = "PingFang SC" if platform == "darwin" else "Noto Sans"
        MONO_FONT = "Menlo" if platform == "darwin" else "monospace"
    app.setFont(QFont(UI_FONT, 12))
    ui = font_probe(QFont(UI_FONT, 12), FONT_PROBE_ASCII + " " + FONT_PROBE_CHINESE)
    mono_font = QFont(MONO_FONT, 11)
    mono_font.setFamilies([MONO_FONT, UI_FONT])
    mono = font_probe(mono_font, FONT_PROBE_ASCII + " " + FONT_PROBE_CHINESE)
    if platform == "win32" and not mono["passed"]:
        MONO_FONT = UI_FONT
        mono = font_probe(QFont(UI_FONT, 11), FONT_PROBE_ASCII + " " + FONT_PROBE_CHINESE)
    report.update(selectedUiFamily=UI_FONT, selectedMonoFamily=MONO_FONT,
                  selectedProbes={"ui": ui, "monospace": mono}, passed=ui["passed"] and mono["passed"])
    STYLE = STYLE_TEMPLATE.replace("__UI_FONT__", UI_FONT).replace("__MONO_FONT__", MONO_FONT)
    if platform == "win32" and not report["passed"]:
        report["error"] = "Windows font initialization failed: selected UI or JSON font contains missing glyphs."
        save_report()
        raise RuntimeError(report["error"])
    save_report()
    return report


def plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def path_name(value: str) -> str:
    """Render a captured Windows path even when reviewing it on a Mac."""
    return PureWindowsPath(value).name if "\\" in value else Path(value).name


def display_time(value: Any) -> str:
    if not value:
        return "时间未记录"
    if isinstance(value, (float, int)):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 10**12 else value).strftime("%m/%d %H:%M:%S")
        except (ValueError, OSError):
            return str(value)
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone().strftime("%m/%d %H:%M:%S")
    except ValueError:
        return text[:24]


def feedback_pairs(value: Any) -> list[tuple[str, str]]:
    """Read the app's explicitly quoted user answer, without inferring intent."""
    return re.findall(r'"([^"\n]*)"\s*=\s*"([^"\n]*)"', plain(value))


def history_text(item: dict) -> str:
    pairs = feedback_pairs(item.get("text"))
    if pairs:
        return "\n".join(f"豆包问：{question}\n用户答：{answer}" for question, answer in pairs)
    return plain(item.get("text"))


def build_stages(steps: list[dict]) -> list[dict]:
    """Group adjacent observed records; never invent an unobserved action.

    A media return may be projected as its own result stage. It points at the
    original call record and is not a delivery or a second tool invocation.
    """
    categories = {step.get("callId"): step.get("category") for step in steps if step.get("callId") and step.get("kind") == "tool_call"}
    labels = {"demand": "用户需求", "file_read": "制作准备", "confirmation": "确认方案",
              "video_generation": "视频生成", "image_generation": "图片生成", "image_edit": "图片修改",
              "image_to_video": "图生视频", "media_to_video": "素材成片", "video_edit": "视频修改", "delivery": "交付文件"}
    media_categories = {"video_generation", "image_generation", "image_edit", "image_to_video", "media_to_video", "video_edit"}
    groups = []
    for index, step in enumerate(steps):
        category = step.get("category")
        if step.get("kind") == "user_request":
            category = "demand"
        elif step.get("kind") == "assistant":
            # The source explicitly links an assistant explanation to its call.
            explicit = [categories.get(cid) for cid in step.get("associatedCallIds", []) if categories.get(cid)]
            if len(set(explicit)) == 1:
                category = explicit[0]
            elif groups:
                category = groups[-1]["category"]
        elif step.get("kind") == "user_feedback":
            category = "confirmation"
        if not category:
            category = groups[-1]["category"] if groups else "record"
        if groups and groups[-1]["category"] == category and not groups[-1].get("projection"):
            group = groups[-1]
            group["recordIndices"].append(index)
        else:
            group = {"id": f"stage:{step.get('id', index)}:{category}", "kind": "stage", "category": category,
                     "label": labels.get(category, step.get("label") or "执行记录"), "recordIndices": [index],
                     "time": step.get("time"), "status": step.get("status", "recorded")}
            groups.append(group)
        if step.get("status") in ("pending", "running", "failed"):
            group["status"] = step["status"]
        if category in media_categories and step.get("kind") == "tool_call" and step.get("result") not in (None, ""):
            groups.append({"id": f"stage:{step.get('id', index)}:result", "kind": "stage", "category": "result",
                           "label": "生成结果" if category in {"video_generation", "image_generation", "image_to_video", "media_to_video"} else "修改结果",
                           "recordIndices": [index], "projection": "tool_result", "time": step.get("resultTime"), "status": step.get("status", "recorded")})
    for group in groups:
        if group["category"] == "confirmation":
            rounds = sum(1 for index in group["recordIndices"] if steps[index].get("category") == "confirmation" and steps[index].get("kind") == "tool_call")
            if rounds > 1:
                group["label"] += f" · {rounds}轮"
    return groups


def label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def card(name: str = "card") -> tuple[QFrame, QVBoxLayout]:
    widget = QFrame()
    widget.setObjectName(name)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(18, 17, 18, 17)
    layout.setSpacing(10)
    return widget, layout


def text_box(text: str, height: int = 112, mono: bool = False) -> QPlainTextEdit:
    widget = QPlainTextEdit()
    widget.setReadOnly(True)
    widget.setPlainText(text)
    widget.setMinimumHeight(height)
    widget.setMaximumHeight(height)
    if mono:
        widget.setProperty("monospace", True)
        font = QFont(MONO_FONT, 11)
        font.setFamilies([MONO_FONT, UI_FONT])
        widget.setFont(font)
    return widget


def payload_panel(title: str, value: Any, name: str, height: int = 150, mono: bool = True) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    header = QHBoxLayout()
    header.addWidget(label(title, "section"))
    header.addStretch()
    copy = QPushButton("复制")
    copy.setStyleSheet("padding: 4px 9px; font-size: 11px;")
    content = plain(value)
    copy.clicked.connect(lambda checked=False: QApplication.clipboard().setText(content))
    header.addWidget(copy)
    layout.addLayout(header)
    body = text_box(content, height, mono)
    body.setObjectName(name)
    layout.addWidget(body)
    return widget


def clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        if item.layout():
            clear_layout(item.layout())


class Fold(QWidget):
    def __init__(self, title: str, text: str, mono: bool = False, height: int = 180):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.toggle = QToolButton()
        self.toggle.setObjectName("disclosure")
        self.toggle.setText("▸  " + title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.body = text_box(text, height, mono)
        self.body.hide()
        self.toggle.toggled.connect(lambda on: (self.body.setVisible(on), self.toggle.setText(("▾  " if on else "▸  ") + title)))
        layout.addWidget(self.toggle)
        layout.addWidget(self.body)


class FlowCanvas(QWidget):
    selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.steps: list[dict] = []
        self.current = 0
        self.phase = 0.0
        self.animating = False
        self.setMinimumHeight(92)
        self.setMaximumHeight(92)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.tick)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_steps(self, steps: list[dict], current: int):
        self.steps = steps
        self.current = min(max(current, 0), max(len(steps) - 1, 0))
        self.setMinimumWidth(470)
        self.update()

    def set_current(self, index: int, animate: bool = False):
        self.current = index
        self.set_animation(animate)
        self.update()

    def set_animation(self, animate: bool):
        self.animating = animate
        if animate:
            self.timer.start()
        else:
            self.timer.stop()
        self.update()

    def tick(self):
        self.phase = (self.phase + 0.027) % 1
        self.update()

    def point(self, index: int) -> QPointF:
        width = max(self.width(), self.minimumWidth())
        count = max(len(self.steps), 1)
        span = (width - 84) / max(count - 1, 1)
        return QPointF(42 + span * index if count > 1 else 70, 30)

    def mousePressEvent(self, event):
        if not self.steps:
            return
        selected = min(range(len(self.steps)), key=lambda idx: abs(self.point(idx).x() - event.position().x()))
        self.selected.emit(selected)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.steps:
            painter.setPen(QColor(MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "豆包任务被记录后，执行步骤会出现在这里")
            return
        for idx in range(len(self.steps) - 1):
            left, right = self.point(idx), self.point(idx + 1)
            painter.setPen(QPen(QColor("#294255" if idx >= self.current else "#347DA5"), 2))
            painter.drawLine(QPointF(left.x() + 19, left.y()), QPointF(right.x() - 19, right.y()))
            if self.animating and idx == self.current - 1:
                x = left.x() + 20 + (right.x() - left.x() - 40) * self.phase
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(ACCENT))
                painter.drawEllipse(QPointF(x, left.y()), 3.2, 3.2)
        for idx, step in enumerate(self.steps):
            point = self.point(idx)
            active = idx == self.current
            visited = idx < self.current
            if active and self.animating:
                tint = QColor(ACCENT)
                tint.setAlpha(int(60 * (1 - self.phase)))
                painter.setPen(QPen(tint, 1.6))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                radius = 20 + self.phase * 12
                painter.drawEllipse(point, radius, radius)
            painter.setPen(QPen(QColor(ACCENT if active else "#477C9D" if visited else "#344B60"), 2 if active else 1.4))
            painter.setBrush(QColor("#17374E" if active else "#183044" if visited else "#142231"))
            painter.drawEllipse(point, 17, 17)
            painter.setPen(QColor("#EFF9FF" if active else "#A1B9CD"))
            painter.setFont(QFont(UI_FONT, 11, QFont.Weight.DemiBold))
            painter.drawText(QRectF(point.x() - 17, point.y() - 17, 34, 34), Qt.AlignmentFlag.AlignCenter, str(idx + 1))
            painter.setFont(QFont(UI_FONT, 11, QFont.Weight.DemiBold if active else QFont.Weight.Normal))
            title = step.get("label") or step.get("toolName") or KIND_TEXT.get(step.get("kind"), "已记录步骤")
            if len(title) > 12:
                title = title[:11] + "…"
            painter.drawText(QRectF(point.x() - 65, 53, 130, 22), Qt.AlignmentFlag.AlignCenter, title)
            painter.setFont(QFont(UI_FONT, 9))
            painter.setPen(QColor(MUTED))
            detail = display_time(step.get("time")) if step.get("time") else STATUS_TEXT.get(step.get("status"), "已记录")
            painter.drawText(QRectF(point.x() - 65, 74, 130, 18), Qt.AlignmentFlag.AlignCenter, detail)


class CollectorWorker(QObject):
    ready = Signal(dict)
    error = Signal(str)
    finished = Signal()

    def __init__(self, db_path=None, source_roots=None, epoch=0):
        super().__init__()
        self.db_path = db_path
        self.source_roots = source_roots
        self.collector = None
        self.timer = None
        self.revision = None
        self.fingerprint = None
        self.epoch = epoch

    @Slot()
    def start(self):
        try:
            from .collector import Collector
            self.collector = Collector(db_path=self.db_path, source_roots=self.source_roots)
            # This sidecar contains only explicitly downloaded, locally probed
            # artifacts. Loading it never downloads or contacts a media server.
            sidecar = Path(__file__).resolve().parents[1] / "local-data" / "media-verifications.json"
            if sidecar.is_file():
                for artifact in json.loads(sidecar.read_text(encoding="utf-8")):
                    self.collector.register_artifact_verification(artifact["url"], artifact["path"], artifact["metadata"])
            self.timer = QTimer(self)
            self.timer.setInterval(1000)
            self.timer.timeout.connect(self.poll)
            self.poll()
            self.timer.start()
        except Exception as exc:
            self.error.emit(str(exc))

    @Slot()
    def poll(self):
        try:
            result = self.collector.scan_once()
            fingerprint = (result.get("revision"), result.get("sourceCount"), plain(result.get("errors")))
            if fingerprint != self.fingerprint:
                self.fingerprint = fingerprint
                snapshot = self.collector.snapshot(session_limit=20, step_limit=200)
                self.revision = snapshot.get("revision")
                self.ready.emit({**snapshot, "_collectorEpoch": self.epoch})
        except Exception as exc:
            self.error.emit(str(exc))

    @Slot()
    def stop(self):
        if self.timer:
            self.timer.stop()
        if self.collector:
            self.collector.close()
        self.finished.emit()


class MainWindow(QMainWindow):
    stop_worker = Signal()

    def __init__(self, db_path=None, source_roots=None, snapshot=None, start_collector=True):
        super().__init__()
        if QApplication.instance():
            initialize_fonts(QApplication.instance())
        self.setWindowTitle("agentreions_doubao · 豆包视频制作可视化")
        palette = self.palette()
        for role in [QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.AlternateBase]:
            palette.setColor(role, QColor("#0B1018"))
        for role in [QPalette.ColorRole.WindowText, QPalette.ColorRole.Text]:
            palette.setColor(role, QColor("#DFE6EF"))
        self.setPalette(palette)
        if QApplication.instance():
            QApplication.instance().setPalette(palette)
        self.resize(1500, 960)
        self.setMinimumSize(1060, 680)
        self.setStyleSheet(STYLE)
        self.snapshot_data: dict = {}
        self.sessions: list[dict] = []
        self.selected_session_id = None
        self.selected_index = 0
        self.last_revision = object()
        self.playing = False
        self.step_ids: list[str] = []
        self.stages: list[dict] = []
        self.selected_stage_index = 0
        self.focus_latest = True
        self.current_step_fingerprint = None
        self.artifact_fingerprint = None
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self.replay_next)
        from .collector import default_data_dir
        self.db_path = Path(db_path).expanduser().resolve() if db_path else None
        self.settings_path = (self.db_path.parent if self.db_path else default_data_dir()) / "settings.json"
        self.source_roots = source_roots
        if source_roots is None and self.settings_path.is_file():
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                roots = saved.get("sourceRoots")
                if isinstance(roots, list) and roots and all(isinstance(root, str) for root in roots):
                    self.source_roots = [Path(root) for root in roots]
            except (OSError, ValueError, TypeError):
                pass
        self.live_mode = start_collector
        self.worker_epoch = 0
        self.build_ui()
        self.thread = None
        self.worker = None
        if snapshot is not None:
            self.accept_snapshot(snapshot)
        if start_collector:
            self.start_worker()

    def start_worker(self):
        self.worker_epoch += 1
        self.thread = QThread(self)
        self.worker = CollectorWorker(self.db_path, self.source_roots, self.worker_epoch)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.ready.connect(self.accept_snapshot)
        self.worker.error.connect(self.collector_error)
        self.stop_worker.connect(self.worker.stop)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

    def choose_source_directory(self):
        initial = str(self.source_roots[0]) if self.source_roots else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择豆包数据根目录", initial)
        if not chosen:
            return
        selected = Path(chosen).expanduser().resolve()
        from .collector import default_data_dir
        db = self.db_path or default_data_dir() / "observations.sqlite3"
        if selected == db or selected in db.parents:
            self.statusBar().showMessage("数据目录不能包含本采集器数据库，请选择豆包自身的数据目录。")
            return
        self.stop_play()
        if self.thread and self.thread.isRunning():
            self.stop_worker.emit()
            if not self.thread.wait(5000):
                self.statusBar().showMessage("原采集线程尚未退出，目录未切换；请稍后重试。")
                return
        if self.thread:
            self.thread.deleteLater()
        self.source_roots = [selected]
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"sourceRoots": [str(selected)]}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.settings_path)
        if os.name != "nt":
            self.settings_path.chmod(0o600)
        self.last_revision = object()
        if self.live_mode:
            self.start_worker()
        self.statusBar().showMessage("本机数据目录已切换：" + str(selected))

    def copy_diagnostics(self):
        diagnostic = {"application": "agentreions_doubao", "platform": sys.platform,
                      "configuredSourceRoots": [str(root) for root in self.source_roots or []],
                      "collector": self.snapshot_data.get("collector", {}),
                      "coverage": self.snapshot_data.get("coverage", {})}
        QApplication.clipboard().setText(plain(diagnostic))

    def build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setSpacing(0)
        shell.setContentsMargins(0, 0, 0, 0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(222)
        left = QVBoxLayout(sidebar)
        left.setContentsMargins(18, 24, 18, 18)
        left.setSpacing(14)
        left.addWidget(label("◉  agentreions", "brand"))
        left.addWidget(label("DOUBAO / LOCAL", "eyebrow"))
        left.addSpacing(12)
        left.addWidget(label("视频制作任务", "section"))
        self.session_list = QListWidget()
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_list.currentItemChanged.connect(self.session_changed)
        left.addWidget(self.session_list, 1)
        self.session_count = label("等待豆包记录", "muted")
        left.addWidget(self.session_count)
        directory = QPushButton("数据目录")
        directory.clicked.connect(self.choose_source_directory)
        left.addWidget(directory)
        diagnostic = QPushButton("复制诊断信息")
        diagnostic.clicked.connect(self.copy_diagnostics)
        left.addWidget(diagnostic)
        left.addWidget(label("●  仅在本机采集与保存\n不上传平台 · 不调用分析模型", "muted", True))
        shell.addWidget(sidebar)

        main = QWidget()
        right = QVBoxLayout(main)
        right.setContentsMargins(25, 22, 25, 18)
        right.setSpacing(16)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(5)
        titles.addWidget(label("豆包视频制作", "title"))
        titles.addWidget(label("看清需求、工具参数、素材与交付结果。", "muted"))
        top.addLayout(titles, 1)
        self.collector_state = label("正在连接本机记录", "status")
        top.addWidget(self.collector_state)
        right.addLayout(top)

        request_card, request_layout = card()
        request_layout.setContentsMargins(18, 13, 18, 12)
        request_layout.setSpacing(7)
        header = QHBoxLayout()
        header.addWidget(label("用户需求与后续确认", "eyebrow"))
        header.addStretch()
        self.task_meta = label("", "muted")
        header.addWidget(self.task_meta)
        request_layout.addLayout(header)
        self.request_text = label("等待豆包的第一条视频制作任务。", "section", True)
        request_layout.addWidget(self.request_text)
        self.request_update = label("", "muted", True)
        request_layout.addWidget(self.request_update)
        self.request_update.hide()
        self.run_source = label("", "muted", True)
        request_layout.addWidget(self.run_source)
        self.run_source.hide()
        self.history_fold_container = QVBoxLayout()
        self.history_fold_container.setContentsMargins(0, 0, 0, 0)
        right.addWidget(request_card)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.detail_tabs = QTabWidget()
        self.center_scroll = QScrollArea()
        self.center_scroll.setWidgetResizable(True)
        content = QWidget()
        self.center_layout = QVBoxLayout(content)
        self.center_layout.setContentsMargins(0, 0, 0, 0)
        self.center_layout.setSpacing(16)
        choice_card, choice_layout = card()
        choice_layout.setContentsMargins(14, 10, 14, 10)
        choice_layout.setSpacing(6)
        choice_row = QHBoxLayout()
        choice_row.addWidget(label("实际工具调用", "eyebrow"))
        self.tool_selector = QComboBox()
        self.tool_selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.tool_selector.currentIndexChanged.connect(self.tool_selection_changed)
        choice_row.addWidget(self.tool_selector, 1)
        choice_layout.addLayout(choice_row)
        choice_card.setToolTip("这里是应用内部工具调用；HTTP 接口请求在独立页签中展示。")
        self.center_layout.addWidget(choice_card)
        self.process_scroll = QScrollArea()
        self.process_scroll.setWidgetResizable(True)
        process_content = QWidget()
        self.process_layout = QVBoxLayout(process_content)
        self.process_layout.setContentsMargins(0, 0, 0, 0)
        self.process_layout.setSpacing(14)
        self.process_layout.addLayout(self.history_fold_container)
        self.flow_card, flow_layout = card()
        flow_layout.setContentsMargins(18, 12, 18, 12)
        flow_layout.setSpacing(5)
        controls = QHBoxLayout()
        controls.addWidget(label("已采集执行链路", "section"))
        controls.addStretch()
        self.play_button = QPushButton("▶  播放")
        self.play_button.setObjectName("primary")
        self.play_button.clicked.connect(self.toggle_play)
        self.next_button = QPushButton("下一步  ›")
        self.next_button.clicked.connect(lambda: self.replay_next(manual=True))
        self.speed = QComboBox()
        self.speed.addItems(["0.5×", "1.0×", "2.0×"])
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(self.update_speed)
        controls.addWidget(self.play_button)
        controls.addWidget(self.next_button)
        controls.addWidget(self.speed)
        flow_layout.addLayout(controls)
        self.flow_scroll = QScrollArea()
        self.flow_scroll.setWidgetResizable(True)
        self.flow_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.flow_scroll.setFixedHeight(98)
        self.flow_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.flow = FlowCanvas()
        self.flow.selected.connect(lambda idx: self.select_stage(idx, manual=True))
        self.flow_scroll.setWidget(self.flow)
        flow_layout.addWidget(self.flow_scroll)
        self.flow_hint = label("每个节点对应一条真实记录；点击节点查看这一步。", "muted")
        flow_layout.addWidget(self.flow_hint)
        self.stage_records_layout = QHBoxLayout()
        self.stage_records_layout.setSpacing(6)
        flow_layout.addLayout(self.stage_records_layout)
        self.process_layout.addWidget(self.flow_card)
        preview_card, preview_layout = card()
        preview_layout.addWidget(label("当前过程记录", "section"))
        self.process_preview = text_box("等待记录", 185)
        preview_layout.addWidget(self.process_preview)
        preview_open = QPushButton("查看这条记录的完整详情")
        preview_open.clicked.connect(lambda: self.detail_tabs.setCurrentIndex(0))
        preview_layout.addWidget(preview_open)
        self.process_layout.addWidget(preview_card)
        self.process_layout.addStretch()
        self.process_scroll.setWidget(process_content)
        self.step_card, self.step_layout = card("toolCard")
        self.center_layout.addWidget(self.step_card)
        self.center_layout.addStretch()
        self.center_scroll.setWidget(content)
        self.detail_tabs.addTab(self.center_scroll, "工具详情")
        self.detail_tabs.addTab(self.process_scroll, "执行过程")
        self.interface_scroll = QScrollArea()
        self.interface_scroll.setWidgetResizable(True)
        interface_content = QWidget()
        self.interface_layout = QVBoxLayout(interface_content)
        self.interface_layout.setContentsMargins(0, 0, 0, 0)
        self.interface_layout.setSpacing(12)
        self.interface_scroll.setWidget(interface_content)
        self.detail_tabs.addTab(self.interface_scroll, "接口请求")
        splitter.addWidget(self.detail_tabs)

        self.artifact_scroll = QScrollArea()
        self.artifact_scroll.setWidgetResizable(True)
        self.artifact_scroll.setMinimumWidth(272)
        output = QWidget()
        self.output_layout = QVBoxLayout(output)
        self.output_layout.setContentsMargins(0, 0, 0, 0)
        self.output_layout.setSpacing(12)
        self.artifact_scroll.setWidget(output)
        splitter.addWidget(self.artifact_scroll)
        splitter.setSizes([780, 300])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        right.addWidget(splitter, 1)

        evidence_card, evidence_layout = card()
        evidence_layout.setContentsMargins(16, 8, 16, 8)
        evidence_layout.setSpacing(4)
        evidence_header = QHBoxLayout()
        evidence_header.addWidget(label("本机观察", "section"))
        evidence_header.addStretch()
        self.coverage_label = label("读取已落盘的应用记录；未取得的数据不会补画。", "muted")
        evidence_header.addWidget(self.coverage_label)
        evidence_layout.addLayout(evidence_header)
        self.evidence_summary = label("进程 / 文件 / 网络：等待采集记录", "muted", True)
        evidence_layout.addWidget(self.evidence_summary)
        self.evidence_summary.hide()
        self.evidence_details = QVBoxLayout()
        evidence_layout.addLayout(self.evidence_details)
        right.addWidget(evidence_card)
        self.statusBar().setStyleSheet("QStatusBar { background: #0B1018; color: #718398; border-top: 1px solid #202C3B; padding: 2px; }")
        self.statusBar().showMessage("本机采集 · 应用已记录的信息，不代表模型全部内部过程")
        shell.addWidget(main, 1)
        self.show_empty_step()
        self.render_artifacts(None)
        self.render_interfaces(None)
        self.play_button.setEnabled(False)
        self.next_button.setEnabled(False)

    def session(self) -> dict | None:
        return next((value for value in self.sessions if value.get("id") == self.selected_session_id), None)

    @Slot(dict)
    def accept_snapshot(self, snapshot: dict):
        if "_collectorEpoch" in snapshot and snapshot["_collectorEpoch"] != self.worker_epoch:
            return
        revision = snapshot.get("revision")
        collector = snapshot.get("collector", {})
        state = collector.get("state", "active")
        self.collector_state.setText("●  " + STATUS_TEXT.get(state, "本机采集中"))
        errors = collector.get("errors") or []
        if errors:
            self.collector_error(plain(errors[-1]))
        else:
            self.collector_state.setToolTip("最近读取：" + display_time(collector.get("lastReadAt")))
        if revision is not None and revision == self.last_revision:
            return
        self.last_revision = revision
        self.snapshot_data = snapshot
        self.sessions = snapshot.get("sessions", [])
        self.refresh_sessions()
        session = self.session()
        new_ids = [str(step.get("id", index)) for index, step in enumerate((session or {}).get("steps", []))]
        if new_ids != self.step_ids:
            old_id = self.step_ids[self.selected_index] if self.step_ids and self.selected_index < len(self.step_ids) else None
            self.step_ids = new_ids
            if old_id in new_ids:
                self.selected_index = new_ids.index(old_id)
            self.render_session(reset=False)
        elif session:
            # Result/verification can change without changing a call ID.
            self.render_artifacts(session)
            steps = session.get("steps") or []
            if steps and plain(steps[self.selected_index]) != self.current_step_fingerprint:
                new_stages = build_stages(steps)
                if plain(new_stages) != plain(self.stages):
                    self.render_session(reset=False)
                else:
                    self.render_step(steps[self.selected_index])
            self.task_meta.setText(display_time(session.get("createdAt")) + "  ·  " + STATUS_TEXT.get(session.get("status"), "已记录"))
        self.render_evidence(session)
        self.render_interfaces(session)

    def refresh_sessions(self):
        previous = self.selected_session_id
        current_ids = [self.session_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.session_list.count())]
        new_ids = [session.get("id") for session in self.sessions]
        if current_ids == new_ids:
            for index, session in enumerate(self.sessions):
                self.session_list.item(index).setText(self.session_title(session))
            return
        self.session_list.blockSignals(True)
        self.session_list.clear()
        for session in self.sessions:
            item = QListWidgetItem(self.session_title(session))
            item.setData(Qt.ItemDataRole.UserRole, session.get("id"))
            item.setToolTip(session.get("originalRequest") or session.get("title") or "任务")
            self.session_list.addItem(item)
        self.session_count.setText(f"{len(self.sessions)} 次已采集制作")
        index = new_ids.index(previous) if previous in new_ids else 0
        if new_ids:
            self.session_list.setCurrentRow(index)
            self.selected_session_id = new_ids[index]
        else:
            self.selected_session_id = None
        self.session_list.blockSignals(False)

    @staticmethod
    def session_title(session: dict) -> str:
        title = session.get("title") or session.get("originalRequest") or "未命名任务"
        title = " ".join(str(title).split())
        if len(title) > 15:
            title = title[:14] + "…"
        return title + "\n" + display_time(session.get("createdAt"))

    def session_changed(self, current, previous):
        if not current:
            return
        self.stop_play()
        self.selected_session_id = current.data(Qt.ItemDataRole.UserRole)
        self.selected_index = 0
        self.selected_stage_index = 0
        self.focus_latest = True
        self.step_ids = [str(step.get("id", index)) for index, step in enumerate((self.session() or {}).get("steps", []))]
        self.render_session(reset=True)
        self.render_evidence(self.session())
        self.render_interfaces(self.session())

    def render_session(self, reset=False):
        session = self.session()
        if not session:
            return
        steps = session.get("steps") or []
        original = session.get("originalRequest")
        effective = session.get("effectiveRequest") or {}
        effective_text = effective.get("text") if isinstance(effective, dict) else plain(effective)
        self.request_text.setText(str(effective_text or original or "本段记录没有用户需求；请查看执行证据。").strip())
        self.task_meta.setText(display_time(session.get("createdAt")) + "  ·  " + STATUS_TEXT.get(session.get("status"), "已记录"))
        source_id = session.get("sourceSessionId")
        if source_id:
            ordinal = session.get("sourceRunIndex")
            suffix = f"  ·  同一会话第 {ordinal + 1} 次制作" if isinstance(ordinal, int) else ""
            self.run_source.setText("来源会话  " + str(source_id) + suffix)
            self.run_source.show()
            self.run_source.setToolTip(plain({"制作ID": session.get("runId") or session.get("id"),
                                             "上下文关联": session.get("contextLinkage"),
                                             "源会话证据": session.get("sourceSessionEvidence")}))
        else:
            self.run_source.hide()
        clear_layout(self.history_fold_container)
        history = session.get("requestHistory") or []
        updates = [answer for item in history[1:] for _, answer in feedback_pairs(item.get("text")) if len(answer.strip()) > 5]
        if effective_text and original and effective_text != original:
            self.request_update.setText("最初需求：" + original)
            self.request_update.show()
        elif updates:
            self.request_update.setText("后续补充：" + updates[-1])
            self.request_update.show()
        else:
            self.request_update.hide()
        if len(history) > 1:
            text = "\n\n".join(f"{display_time(item.get('time'))}\n{history_text(item)}" for item in history)
            self.history_fold_container.addWidget(Fold(f"查看需求变化与确认 · {len(history)} 条用户消息", text, height=210))
        self.selected_index = min(self.selected_index, max(len(steps) - 1, 0))
        old_stage_id = self.stages[self.selected_stage_index].get("id") if self.stages and self.selected_stage_index < len(self.stages) else None
        self.stages = build_stages(steps)
        stage_ids = [stage["id"] for stage in self.stages]
        if self.focus_latest and self.stages:
            media_categories = {"video_generation", "image_generation", "image_edit", "image_to_video", "media_to_video", "video_edit"}
            calls = [index for index, step in enumerate(steps) if step.get("kind") == "tool_call"]
            media = [index for index in calls if steps[index].get("category") in media_categories]
            self.selected_index = (media or calls or [len(steps) - 1])[-1]
            self.selected_stage_index = next((index for index, stage in enumerate(self.stages) if self.selected_index in stage["recordIndices"]), 0)
            self.focus_latest = False
        elif not reset and old_stage_id in stage_ids:
            self.selected_stage_index = stage_ids.index(old_stage_id)
        else:
            self.selected_stage_index = next((index for index, stage in enumerate(self.stages) if self.selected_index in stage["recordIndices"]), 0)
        self.flow.set_steps(self.stages, self.selected_stage_index)
        limited = " · 本段记录显示数量受限，完整源文件可在证据中查看" if session.get("truncated") else ""
        self.flow_hint.setText(f"{len(self.stages)} 个实际阶段 · {len(steps)} 条执行记录" + limited)
        self.play_button.setEnabled(bool(steps))
        sequence = self.replay_sequence()
        self.next_button.setEnabled(bool(sequence) and (self.selected_stage_index, self.selected_index) != sequence[-1])
        self.render_stage_records()
        self.refresh_tool_selector()
        self.render_step(steps[self.selected_index] if steps else None)
        self.render_artifacts(session)
        self.render_interfaces(session)
        if reset:
            self.center_scroll.verticalScrollBar().setValue(0)
            self.flow_scroll.horizontalScrollBar().setValue(0)

    def select_stage(self, index: int, manual=False):
        if not self.stages:
            return
        self.selected_stage_index = min(max(index, 0), len(self.stages) - 1)
        self.select_step(self.stages[self.selected_stage_index]["recordIndices"][0], manual=manual, stage_index=self.selected_stage_index)

    def refresh_tool_selector(self):
        self.tool_selector.blockSignals(True)
        self.tool_selector.clear()
        for index, step in enumerate((self.session() or {}).get("steps") or []):
            if step.get("kind") == "tool_call" or step.get("toolName"):
                caption = f"{index + 1} · {step.get('toolName') or '工具名未记录'} · {STATUS_TEXT.get(step.get('status'), '已记录')}"
                self.tool_selector.addItem(caption, index)
        selected = self.tool_selector.findData(self.selected_index)
        self.tool_selector.setCurrentIndex(selected)
        self.tool_selector.blockSignals(False)
        self.tool_selector.setEnabled(self.tool_selector.count() > 0)

    def tool_selection_changed(self, index):
        if index < 0:
            return
        record_index = self.tool_selector.itemData(index)
        if isinstance(record_index, int):
            self.select_step(record_index, manual=True)
            self.detail_tabs.setCurrentIndex(0)
            self.center_scroll.verticalScrollBar().setValue(0)

    def render_stage_records(self):
        while self.stage_records_layout.count():
            item = self.stage_records_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not self.stages:
            return
        stage = self.stages[self.selected_stage_index]
        steps = (self.session() or {}).get("steps") or []
        for record_index in stage["recordIndices"]:
            step = steps[record_index]
            title = "返回与核验" if stage.get("projection") else "回答" if step.get("kind") == "user_feedback" else "说明" if step.get("kind") == "assistant" else step.get("label") or "记录"
            button = QPushButton(f"{record_index + 1} · {title}")
            button.setToolTip(step.get("toolName") or step.get("summary") or title)
            button.setStyleSheet("padding: 5px 7px; font-size: 11px;" + ("border-color: #48B5F4; color: #DBF3FF; background: #1B3B52;" if record_index == self.selected_index else ""))
            button.clicked.connect(lambda checked=False, value=record_index, group=self.selected_stage_index: self.select_step(value, manual=True, stage_index=group))
            self.stage_records_layout.addWidget(button)
        self.stage_records_layout.addStretch()

    def select_step(self, index: int, manual=False, stage_index=None):
        session = self.session()
        steps = (session or {}).get("steps") or []
        if not steps:
            return
        if manual:
            self.stop_play()
        self.selected_index = min(max(index, 0), len(steps) - 1)
        if stage_index is not None:
            self.selected_stage_index = stage_index
        else:
            self.selected_stage_index = next((idx for idx, stage in enumerate(self.stages) if self.selected_index in stage["recordIndices"]), 0)
        self.flow.set_current(self.selected_stage_index, self.playing)
        sequence = self.replay_sequence()
        self.next_button.setEnabled(bool(sequence) and (self.selected_stage_index, self.selected_index) != sequence[-1])
        self.flow_hint.setText(f"{len(self.stages)} 个实际阶段 · {len(steps)} 条执行记录 · 当前记录 {self.selected_index + 1}")
        self.render_stage_records()
        self.tool_selector.blockSignals(True)
        self.tool_selector.setCurrentIndex(self.tool_selector.findData(self.selected_index))
        self.tool_selector.blockSignals(False)
        self.render_step(steps[self.selected_index])

    def replay_sequence(self):
        return [(stage_index, record_index) for stage_index, stage in enumerate(self.stages) for record_index in stage["recordIndices"]]

    def show_empty_step(self):
        clear_layout(self.step_layout)
        self.step_layout.addWidget(label("每一步，都会有依据", "section"))
        self.step_layout.addWidget(label("运行豆包制作视频后，这里会展示实际工具调用、提示词、参数和返回。尚未采集到的步骤会保持为空。", "muted", True))

    def render_step(self, step: dict | None):
        if not step:
            self.show_empty_step()
            return
        scroll = self.center_scroll.verticalScrollBar().value()
        self.current_step_fingerprint = plain(step)
        clear_layout(self.step_layout)
        kind = step.get("kind")
        result_projection = bool(self.stages and self.stages[self.selected_stage_index].get("projection") == "tool_result")
        header = QHBoxLayout()
        header.addWidget(label("工具返回与本机核验" if result_projection else KIND_TEXT.get(kind, "已记录步骤"), "eyebrow"))
        header.addStretch()
        header.addWidget(label(display_time(step.get("time")), "muted"))
        self.step_layout.addLayout(header)
        title = (step.get("toolName") or "工具") + " · 生成结果" if result_projection else step.get("toolName") or step.get("label") or KIND_TEXT.get(kind, "执行步骤")
        self.step_layout.addWidget(label(title, "section", True))
        self.process_preview.setPlainText(plain(step.get("summary") or step.get("content") or title))
        if step.get("callId"):
            self.step_layout.addWidget(label("调用标识  " + str(step["callId"]), "muted", True))
        if step.get("nativeOnly"):
            self.step_layout.addWidget(label("这一步来自本机执行日志；日志只保存了部分调用字段。", "amber"))
        summary = "" if result_projection else step.get("summary") or ""
        if kind == "user_feedback":
            pairs = feedback_pairs(step.get("content") or summary)
            if pairs:
                summary = "\n\n".join(f"豆包问：{question}\n用户答：{answer}" for question, answer in pairs)
        if summary and kind != "tool_call":
            short = str(summary)[:460] + ("…" if len(str(summary)) > 460 else "")
            summary_label = label(short, None, True)
            summary_label.setStyleSheet("line-height: 1.6; color: #C4D2E2;")
            self.step_layout.addWidget(summary_label)
            if len(str(summary)) > 460:
                self.step_layout.addWidget(Fold("展开这条完整记录", str(summary), height=260))
        if step.get("rawContent"):
            self.step_layout.addWidget(Fold("查看反馈原始记录", plain(step.get("rawContent")), mono=True, height=170))
        arguments = decode(step.get("arguments"))
        references = step.get("inputReferences") or []
        if references:
            for reference in references:
                identity = reference.get("url") or reference.get("path") or reference.get("id")
                self.step_layout.addWidget(label("输入素材  " + str(identity or "原始标识未取得"), None, True))
                if reference.get("matchStatus") == "unresolved" and not reference.get("path"):
                    self.step_layout.addWidget(label("素材对应关系尚未定位；以上地址/ID来自真实调用参数。", "muted", True))
        if step.get("toolName") == "interaction.ask" and isinstance(arguments, dict):
            for question in arguments.get("questions") or []:
                self.step_layout.addWidget(label(question.get("display_message") or "确认问题", "section", True))
                options = question.get("options") or []
                if options:
                    self.step_layout.addWidget(label("可选项：" + "  /  ".join(opt.get("label", "") for opt in options), "muted", True))
        elif arguments is not None and arguments != "":
            self.add_arguments(arguments, step.get("prompt"))
        elif step.get("prompt"):
            self.step_layout.addWidget(label("完整提示词", "muted"))
            self.step_layout.addWidget(text_box(plain(step.get("prompt")), 134))
        if kind == "tool_call" or step.get("toolName"):
            if "arguments" not in step or step.get("arguments") is None:
                self.step_layout.addWidget(label("调用参数：本记录未取得", "amber"))
            else:
                argument_title = "调用参数 · 日志已记录字段" if step.get("nativeOnly") else "完整调用参数"
                self.step_layout.addWidget(payload_panel(argument_title, step.get("arguments"), "toolArguments", 175))
        result = decode(step.get("result"))
        native_status_only = isinstance(result, dict) and result.get("contentCoverage") == "not_recorded_in_native_log"
        if native_status_only:
            self.step_layout.addWidget(label("工具执行状态：" + str(result.get("status") or "未记录"), "section"))
            self.step_layout.addWidget(label("完整工具返回正文：本机日志未保存；当前只取得执行状态。", "amber"))
        elif result is not None and result != "":
            result_title = label("工具实际返回", "section")
            self.step_layout.addWidget(result_title)
            readable = self.result_summary(result)
            pairs = feedback_pairs(result)
            if pairs:
                readable = "\n\n".join(f"用户回答：{answer}" for _, answer in pairs)
            self.step_layout.addWidget(text_box(readable, min(190, max(80, len(readable.splitlines()) * 23 + 30))))
            self.step_layout.addWidget(payload_panel("完整工具返回 · 原始内容", step.get("result"), "toolResult", 185))
            if result_projection:
                for artifact in step.get("artifacts") or []:
                    verified = artifact.get("verified") or {}
                    if verified:
                        self.step_layout.addWidget(label("本机核验：" + self.metadata_text(verified), "section", True))
                        self.step_layout.addWidget(Fold("查看完整媒体核验结果", plain(verified), mono=True, height=190))
        elif kind == "tool_call" or step.get("toolName"):
            message = "工具尚未返回" if step.get("status") == "pending" else "工具返回正文：本记录未取得"
            self.step_layout.addWidget(label(message, "amber"))
        evidence = step.get("evidence") or []
        if evidence:
            evidence_text = "\n\n".join(
                f"来源：{item.get('source', '应用记录')}\n文件：{item.get('path', '未提供')}"
                + (f"\n行号：{item.get('line')}" if item.get('line') else "")
                + (f"\nSHA-256：{item.get('sha256')}" if item.get('sha256') else "")
                for item in evidence
            )
            first = evidence[0]
            self.step_layout.addWidget(label("证据文件  " + str(first.get("path") or "未提供") + ("  ·  行 " + str(first["line"]) if first.get("line") else ""), "muted", True))
            self.step_layout.addWidget(Fold(f"全部证据来源 · {len(evidence)} 项", evidence_text, mono=True, height=150))
        QTimer.singleShot(0, lambda: self.center_scroll.verticalScrollBar().setValue(scroll))

    def render_material_flow(self, step: dict):
        inputs = step.get("inputReferences") or []
        outputs = step.get("outputReferences") or step.get("artifacts") or []
        row = QHBoxLayout()
        row.setSpacing(8)
        def material_summary(references, default):
            if not references:
                return default
            texts = []
            for reference in references:
                kind = {"image": "图片", "video": "视频", "audio": "音频", "file": "文件"}.get(reference.get("kind"), "素材")
                identity = reference.get("id") or (path_name(reference["path"]) if reference.get("path") else reference.get("url"))
                text = kind + (" · " + str(identity)[:25] if identity else "")
                if reference.get("matchStatus") == "unresolved":
                    text += "（素材未定位）"
                elif reference.get("matchStatus") == "ambiguous":
                    text += "（对应关系待确认）"
                texts.append(text)
            return "\n".join(texts[:3]) + (f"\n另有 {len(texts)-3} 项" if len(texts) > 3 else "")
        for title, text, references in [
            ("输入", material_summary(inputs, "文字提示词" if step.get("prompt") else "输入素材未记录"), inputs),
            ("实际工具", step.get("toolName") or "已记录工具", []),
            ("输出", material_summary(outputs, "输出素材未记录"), outputs),
        ]:
            if row.count():
                row.addWidget(label("→", "eyebrow"))
            tile, box = card()
            box.setContentsMargins(10, 8, 10, 8)
            box.setSpacing(3)
            box.addWidget(label(title, "muted"))
            content = label(text, None, True)
            content.setToolTip(plain(references))
            box.addWidget(content)
            row.addWidget(tile, 1)
        self.step_layout.addLayout(row)
        if inputs:
            self.step_layout.addWidget(Fold("查看输入素材标识与对应证据", plain(inputs), mono=True, height=160))

    def add_arguments(self, arguments: Any, fallback_prompt=None):
        if not isinstance(arguments, dict):
            return
        names = {
            "duration": "视频时长", "duration_seconds": "视频时长", "video_duration": "视频时长",
            "aspect_ratio": "画面比例", "ratio": "画面比例", "resolution": "分辨率", "model": "生成模型",
            "model_name": "生成模型", "enable_audio": "生成音频", "seed": "随机种子",
            "model_version": "生成模型", "fileName": "读取文件",
            "input_image": "输入图片", "image_url": "输入图片", "video_url": "输入视频", "file_path": "文件路径",
        }
        prompt_keys = {"prompt", "text", "description", "negative_prompt"}
        priority = ["duration", "duration_seconds", "video_duration", "ratio", "aspect_ratio", "model_version", "model", "model_name", "resolution", "enable_audio"]
        ordered = [key for key in priority if key in arguments] + [key for key in arguments if key not in priority]
        visible = {key: arguments[key] for key in ordered if key not in prompt_keys}
        rows = QHBoxLayout()
        rows.setSpacing(8)
        for index, (key, value) in enumerate([(key, value) for key, value in visible.items() if key in priority][:4]):
            tile, tile_layout = card()
            tile_layout.setContentsMargins(12, 10, 12, 10)
            tile_layout.setSpacing(4)
            tile_layout.addWidget(label(names.get(key, key), "muted"))
            rendered = "开启" if value is True else "关闭" if value is False else plain(value)
            if "duration" in key and (isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit()):
                rendered += " 秒"
            short = rendered if len(rendered) < 55 else rendered[:52] + "…"
            content = label(short, "section", True)
            content.setToolTip(rendered)
            tile_layout.addWidget(content)
            rows.addWidget(tile, 1)
        if visible:
            self.step_layout.addLayout(rows)
        prompt = arguments.get("prompt") or arguments.get("text") or arguments.get("description") or fallback_prompt
        if prompt:
            self.step_layout.addWidget(payload_panel("完整提示词 · 传给工具的内容", prompt, "toolPrompt", 160, False))
        if arguments.get("negative_prompt"):
            self.step_layout.addWidget(Fold("负面提示词", plain(arguments["negative_prompt"]), height=110))

    @staticmethod
    def result_summary(value: Any) -> str:
        if isinstance(value, dict):
            names = {"status": "状态", "message": "说明", "error": "错误", "url": "产物地址", "video_url": "视频地址", "image_url": "图片地址", "path": "文件路径", "duration": "工具报告时长", "width": "宽度", "height": "高度", "format": "格式", "resolution": "工具报告分辨率"}
            rows = []
            for key, val in value.items():
                if isinstance(val, (dict, list)):
                    nested = MainWindow.result_summary(val)
                    rows.append(f"{names.get(key, key)}\n{nested}")
                else:
                    rows.append(f"{names.get(key, key)}：{val}")
            return "\n".join(rows)
        if isinstance(value, list):
            return "\n\n".join(MainWindow.result_summary(item) for item in value)
        if isinstance(value, str):
            generated = re.search(r"(video|image|audio)\s*\(([^)]*)\)\s*generated\.\s*(https?://\S+)", value)
            if generated:
                kind, attributes, url = generated.groups()
                name = {"video": "视频", "image": "图片", "audio": "音频"}[kind]
                readable_attributes = re.sub(r"([\d.]+)s\b", r"\1 秒", attributes).replace("mp4", "MP4").replace("x", " × ")
                return f"生成结果：{name}已生成\n工具报告：{readable_attributes}\n产物地址：{url}"
        return plain(value)

    def render_artifacts(self, session: dict | None):
        fingerprint = plain({"id": (session or {}).get("id"),
                             "artifacts": [(step.get("id"), step.get("toolName"), step.get("artifacts"), step.get("outputReferences"), step.get("inputReferences")) for step in (session or {}).get("steps", [])],
                             "mediaRelations": (session or {}).get("mediaRelations")})
        if fingerprint == self.artifact_fingerprint:
            return
        self.artifact_fingerprint = fingerprint
        old_scroll = self.artifact_scroll.verticalScrollBar().value()
        clear_layout(self.output_layout)
        self.output_layout.addWidget(label("素材与交付", "section"))
        input_keys = set()
        for step in (session or {}).get("steps", []):
            for reference in step.get("inputReferences") or []:
                identity = reference.get("url") or reference.get("path") or reference.get("id")
                if not identity or identity in input_keys:
                    continue
                input_keys.add(identity)
                input_card, input_layout = card()
                input_layout.addWidget(label("输入素材 · " + {"image": "图片", "video": "视频", "audio": "音频"}.get(reference.get("kind"), "文件"), "eyebrow"))
                source_path = reference.get("path")
                verified = reference.get("verified") or {}
                preview_path = verified.get("posterPath") or source_path
                if preview_path and Path(preview_path).is_file() and Path(preview_path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    preview = QLabel()
                    preview.setPixmap(QPixmap(preview_path).scaled(238, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    input_layout.addWidget(preview)
                    input_layout.addWidget(label("已取得本机预览 · 仅读取本地文件", "muted", True))
                else:
                    input_layout.addWidget(label("素材地址已记录，本机预览尚未取得", "muted", True))
                input_layout.addWidget(label(str(identity), None, True))
                copy_input = QPushButton("复制素材地址 / ID")
                copy_input.clicked.connect(lambda checked=False, text=str(identity): QApplication.clipboard().setText(text))
                input_layout.addWidget(copy_input)
                input_layout.addWidget(label("用于  " + str(step.get("toolName") or "已记录工具"), "muted", True))
                self.output_layout.addWidget(input_card)
        artifacts = []
        keys = set()
        for index, step in enumerate((session or {}).get("steps", [])):
            for artifact in step.get("artifacts") or []:
                key = (step.get("id"), artifact.get("path") or artifact.get("url") or plain(artifact))
                if key not in keys:
                    keys.add(key)
                    artifacts.append((step, artifact, index + 1))
        relations = (session or {}).get("mediaRelations") or []
        if relations:
            relation_card, relation_box = card()
            relation_box.addWidget(label("素材流转", "eyebrow"))
            step_names = {step.get("id"): step.get("toolName") or step.get("label") or "已记录步骤" for step in (session or {}).get("steps", [])}
            for edge in relations:
                source = step_names.get(edge.get("fromStepId"), "素材来源")
                target = step_names.get(edge.get("toStepId"), "素材使用")
                relation_box.addWidget(label(source + "  →  " + target, None, True))
            relation_box.addWidget(Fold("素材关联证据", plain(relations), mono=True, height=180))
            self.output_layout.addWidget(relation_card)
        if not artifacts:
            empty, box = card()
            box.addWidget(label("还没有产物记录", "section"))
            box.addWidget(label("生成图片、参考素材和视频返回后，会显示它们的真实地址与属性。", "muted", True))
            self.output_layout.addWidget(empty)
        for number, (step, artifact, step_index) in enumerate(artifacts):
            output, layout = card()
            kind = artifact.get("kind", "file")
            names = {"video": "视频", "image": "图片", "file": "文件", "audio": "音频"}
            layout.addWidget(label(f"{names.get(kind, kind)}  {number + 1}", "eyebrow"))
            if artifact.get("role") == "intermediate":
                layout.addWidget(label("制作过程中的产物", "muted"))
            if (artifact.get("delivery") or {}).get("status") == "delivered":
                layout.addWidget(label("已取得交付记录", "status"))
            local_path = artifact.get("path")
            url = artifact.get("url")
            verified = artifact.get("verified") or {}
            poster = verified.get("posterPath") if isinstance(verified, dict) else None
            preview_path = poster if poster and Path(poster).is_file() else local_path if local_path and Path(local_path).is_file() and Path(local_path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else None
            if preview_path:
                preview = QLabel()
                preview.setPixmap(QPixmap(preview_path).scaled(238, 142, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(preview)
            else:
                preview = label("▷" if kind == "video" else "▧", None)
                preview.setStyleSheet("font-size: 42px; color: #4A91BA; background: #0E1823; border-radius: 8px; padding: 16px;")
                preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(preview)
            reported = artifact.get("reported") or {}
            layout.addWidget(label("工具报告", "muted"))
            layout.addWidget(label(self.metadata_text(reported) or "没有报告媒体属性", None, True))
            if verified:
                layout.addWidget(label("本机独立核验", "muted"))
                layout.addWidget(label(self.metadata_text(verified), None, True))
            else:
                layout.addWidget(label("尚未独立核验", "amber"))
            if local_path:
                name = path_name(local_path)
                layout.addWidget(label(name, "muted", True))
                if Path(local_path).is_file():
                    open_button = QPushButton("本机播放" if kind == "video" else "打开本机文件")
                    open_button.clicked.connect(lambda checked=False, path=local_path: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
                    layout.addWidget(open_button)
            if url:
                layout.addWidget(label("产物地址", "muted"))
                layout.addWidget(label(url, None, True))
                copy = QPushButton("复制产物地址")
                copy.clicked.connect(lambda checked=False, value=url: QApplication.clipboard().setText(value))
                layout.addWidget(copy)
            layout.addWidget(label(f"来自第 {step_index} 步  " + (step.get("toolName") or step.get("label") or "已采集记录"), "muted", True))
            self.output_layout.addWidget(output)
        scope, scope_layout = card()
        scope_layout.addWidget(label("采集边界", "section"))
        scope_layout.addWidget(label("展示已落盘的对话、工具调用及返回。云端内部操作、未记录的模型输入和隐藏推理不在当前观察范围。", "muted", True))
        self.output_layout.addWidget(scope)
        self.output_layout.addStretch()
        QTimer.singleShot(0, lambda: self.artifact_scroll.verticalScrollBar().setValue(old_scroll))

    @staticmethod
    def metadata_text(metadata: Any) -> str:
        if not isinstance(metadata, dict):
            return plain(metadata)
        values = []
        duration = metadata.get("duration") or metadata.get("durationSeconds") or metadata.get("duration_seconds")
        if duration is not None:
            values.append(f"{duration} 秒")
        if metadata.get("width") and metadata.get("height"):
            values.append(f"{metadata['width']} × {metadata['height']}")
        elif metadata.get("resolution"):
            values.append(str(metadata["resolution"]))
        if metadata.get("format"):
            values.append(str(metadata["format"]).upper())
        if not values:
            return MainWindow.result_summary(metadata)
        return "  ·  ".join(values)

    def render_evidence(self, session: dict | None):
        clear_layout(self.evidence_details)
        processes = (session or {}).get("processes") or []
        connections = (session or {}).get("connections") or []
        source_paths = self.snapshot_data.get("collector", {}).get("sourcePaths") or []
        self.evidence_summary.setText(
            f"{len(processes)} 个进程观察  ·  {len(connections)} 条连接记录  ·  {len(source_paths)} 个记录来源"
            + "  ｜  进程与任务的关系仅按已采集证据展示"
        )
        detailed = {
            "进程观察": processes,
            "连接记录": connections,
            "本机记录来源": source_paths,
        }
        fold = Fold("展开本机观察证据", plain(detailed), mono=True, height=150)
        fold.toggle.setStyleSheet("padding: 2px 0; font-size: 11px;")
        self.evidence_details.addWidget(fold)
        coverage = self.snapshot_data.get("coverage") or {}
        if isinstance(coverage, dict):
            self.coverage_label.setToolTip(plain(coverage))
            if coverage.get("processes") == "not_monitored_by_this_collector":
                self.evidence_summary.setText(f"{len(source_paths)} 个本机记录来源  ·  按调用 ID 关联文件读取与工具执行  ·  进程 / 网络尚未接入实时监控")

    def render_interfaces(self, session: dict | None):
        """Render only an explicit HTTP evidence collection, never tool URLs."""
        clear_layout(self.interface_layout)
        overview, box = card()
        box.addWidget(label("实际接口请求", "section"))
        box.addWidget(label("工具参数、HTTP 请求和产物下载地址分别展示。生成工具名不代表生成接口地址。", "muted", True))
        collection = (session or {}).get("httpEvidence") or (session or {}).get("interfaces") or (session or {}).get("netEvents") or {}
        if isinstance(collection, dict):
            records = collection.get("records") or collection.get("events") or []
            coverage = collection.get("coverage") or {}
        elif isinstance(collection, list):
            records, coverage = collection, {}
        else:
            records, coverage = [], {}
        # Snapshot-level records must carry an explicit run identity or call ID;
        # a shared conversation alone cannot distinguish two creation runs.
        if not records and session:
            calls = {str(step.get("callId")) for step in session.get("steps", []) if step.get("callId")}
            global_collection = self.snapshot_data.get("httpEvidence") or self.snapshot_data.get("netEvents") or {}
            global_records = global_collection.get("records", []) if isinstance(global_collection, dict) else global_collection if isinstance(global_collection, list) else []
            for record in global_records:
                association = record.get("association") or {}
                if record.get("runId") == session.get("id") or str(association.get("callId")) in calls:
                    records.append(record)
        actual = [record for record in records if isinstance(record, dict) and record.get("url") and (record.get("method") or record.get("statusCode") is not None or record.get("source"))]
        if not actual:
            box.addWidget(label("本次制作尚未取得可关联的 HTTP 请求记录", "amber"))
            box.addWidget(label("生成接口地址、HTTP 方法、请求头、请求体和响应体目前均未取得。已有 image_to_video 等工具参数及视频 CDN 地址，可在工具详情和素材区查看。", None, True))
            count = collection.get("parsedHttpRecords", coverage.get("parsedHttpRecords")) if isinstance(collection, dict) else None
            if count is not None:
                box.addWidget(label(f"本机日志中已识别 {count} 条 HTTP 记录，当前制作未取得明确关联。", "muted", True))
        else:
            box.addWidget(label(f"{len(actual)} 条与当前制作有证据关联的接口记录", "status"))
        self.interface_layout.addWidget(overview)
        for record in actual:
            entry, layout = card()
            method = record.get("method") or "方法未记录"
            purpose = "产物传输" if record.get("purpose") == "artifact_transfer" else "HTTP 请求 · 用途待确认"
            layout.addWidget(label(purpose, "eyebrow"))
            layout.addWidget(label(method + "  " + str(record["url"]), "section", True))
            state = "状态码：" + str(record.get("statusCode")) if record.get("statusCode") is not None else "HTTP 状态码未记录"
            if record.get("elapsedMs") is not None:
                state += "  ·  " + str(record["elapsedMs"]) + " ms"
            layout.addWidget(label(state, "muted", True))
            query = record.get("queryParameters")
            if query:
                layout.addWidget(payload_panel("地址中的查询参数", query, "httpQuery", 100))
            for key, title in [("requestHeaders", "已采集请求头"), ("responseHeaders", "已采集响应头")]:
                headers = record.get(key)
                if headers:
                    layout.addWidget(payload_panel(title, headers, key, 100))
                else:
                    layout.addWidget(label(title + "：未取得", "muted"))
            for key, title in [("requestBody", "HTTP 请求体"), ("responseBody", "HTTP 响应体")]:
                body = record.get(key)
                if isinstance(body, dict) and "status" in body:
                    body_status = body.get("status")
                    if body_status == "not_recorded":
                        layout.addWidget(label(title + "：源记录未保存正文", "amber"))
                    elif body_status == "opaque_protobuf":
                        layout.addWidget(label(title + "：二进制 protobuf，目前没有经过核验的解码结果", "amber"))
                    else:
                        if body.get("truncated"):
                            layout.addWidget(label(title + "：采集片段被截取，完整正文未取得", "amber"))
                        layout.addWidget(payload_panel(title, body.get("value"), key, 160))
                elif body is not None:
                    layout.addWidget(payload_panel(title, body, key, 160))
                else:
                    layout.addWidget(label(title + "：未取得", "amber"))
            layout.addWidget(payload_panel("接口关联及来源证据", {"association": record.get("association"), "source": record.get("source") or record.get("evidence")}, "httpEvidence", 120))
            self.interface_layout.addWidget(entry)
        if coverage:
            self.interface_layout.addWidget(Fold("接口采集范围", plain(coverage), mono=True, height=150))
        linkage = (session or {}).get("contextLinkage")
        if linkage:
            self.interface_layout.addWidget(Fold("当前制作与来源会话的关联证据", plain({"runId": session.get("id"), "sourceSessionId": session.get("sourceSessionId"), "contextLinkage": linkage, "sourceSessionEvidence": session.get("sourceSessionEvidence")}), mono=True, height=180))
        self.interface_layout.addStretch()

    def toggle_play(self):
        if self.playing:
            self.stop_play()
            return
        steps = (self.session() or {}).get("steps") or []
        if not steps:
            return
        sequence = self.replay_sequence()
        if (self.selected_stage_index, self.selected_index) == sequence[-1]:
            self.selected_stage_index, self.selected_index = sequence[0]
        self.playing = True
        self.play_button.setText("Ⅱ  暂停")
        self.flow.set_animation(True)
        self.select_step(self.selected_index, stage_index=self.selected_stage_index)
        self.update_speed()
        self.play_timer.start()

    def stop_play(self):
        self.playing = False
        self.play_timer.stop()
        self.flow.set_animation(False)
        self.play_button.setText("▶  播放")

    def replay_next(self, manual=False):
        sequence = self.replay_sequence()
        current = (self.selected_stage_index, self.selected_index)
        if not sequence or current == sequence[-1]:
            self.stop_play()
            return
        position = sequence.index(current) if current in sequence else -1
        stage_index, record_index = sequence[position + 1]
        self.select_step(record_index, manual=manual, stage_index=stage_index)

    def update_speed(self):
        multiplier = [0.5, 1.0, 2.0][self.speed.currentIndex()]
        self.play_timer.setInterval(int(2100 / multiplier))

    @Slot(str)
    def collector_error(self, message: str):
        self.collector_state.setText("⚠  读取遇到问题")
        self.collector_state.setToolTip(message)
        self.statusBar().showMessage("采集错误：" + message[:250])

    def closeEvent(self, event):
        self.stop_play()
        if self.thread and self.thread.isRunning():
            self.stop_worker.emit()
            if not self.thread.wait(4000):
                event.ignore()
                self.statusBar().showMessage("正在完成本机读取，请稍后关闭。")
                return
        event.accept()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="agentreions_doubao native local desktop")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--source-root", action="append", type=Path)
    parser.add_argument("--snapshot", type=Path, help="read one local snapshot for offline inspection")
    parser.add_argument("--screenshot", type=Path, help="save local UI QA screenshot and exit")
    parser.add_argument("--font-diagnostics", type=Path, help="write font registrations and actual glyph coverage for local QA")
    parser.add_argument("--select-tool", help="focus the first observed tool containing this name, for QA")
    parser.add_argument("--window-size", help="local visual QA size, for example 1280x800")
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("agentreions_doubao")
    app.setOrganizationName("agentreions")
    diagnostics_path = args.font_diagnostics
    if diagnostics_path is None and args.screenshot:
        diagnostics_path = args.screenshot.with_suffix(".fonts.json")
    if diagnostics_path is None and sys.platform == "win32":
        from .collector import default_data_dir
        diagnostics_path = (args.db.parent if args.db else default_data_dir()) / "font-diagnostics.json"
    try:
        initialize_fonts(app, diagnostics_path=diagnostics_path)
    except RuntimeError as exc:
        if sys.stderr is not None:
            print(str(exc), file=sys.stderr)
        return 3
    palette = app.palette()
    for role in [QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.AlternateBase]:
        palette.setColor(role, QColor("#0B1018"))
    for role in [QPalette.ColorRole.WindowText, QPalette.ColorRole.Text]:
        palette.setColor(role, QColor("#DFE6EF"))
    app.setPalette(palette)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8")) if args.snapshot else None
    window = MainWindow(args.db, args.source_root, snapshot=snapshot, start_collector=not bool(snapshot))
    if not args.screenshot and app.primaryScreen():
        available = app.primaryScreen().availableGeometry()
        window.resize(max(1060, min(1480, int(available.width() * 0.94))), max(680, min(960, int(available.height() * 0.92))))
    if args.window_size:
        width, height = [int(part) for part in args.window_size.lower().split("x", 1)]
        window.resize(width, height)
    window.show()
    if args.select_tool and snapshot:
        for idx, step in enumerate((window.session() or {}).get("steps", [])):
            if args.select_tool.lower() in str(step.get("toolName", "")).lower():
                window.select_step(idx)
                break
    if args.screenshot:
        def save():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            # Probe the actual styled controls as well as the startup font.
            # QTextLayout exposes .notdef (glyph 0), which PNG existence cannot.
            report = app.property("doubaoFontDiagnostics")
            controls = []
            for name in ("toolPrompt", "toolArguments", "toolResult"):
                widget = window.findChild(QPlainTextEdit, name)
                if widget and widget.toPlainText():
                    sample = "".join(dict.fromkeys(widget.toPlainText().replace("\n", " ").replace("\t", " ")))
                    controls.append({"objectName": name, **font_probe(widget.font(), sample)})
            report["renderedControlProbes"] = controls
            report["renderedControlsPassed"] = all(item["passed"] for item in controls)
            if diagnostics_path:
                diagnostics_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            failed = sys.platform == "win32" and not report["renderedControlsPassed"]
            if not failed and not window.grab().save(str(args.screenshot)):
                failed = True
            window.close()
            app.exit(3 if failed else 0)
        QTimer.singleShot(1800, save)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
