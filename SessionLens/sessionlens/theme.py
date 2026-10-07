"""Native Qt rendering of the shared AgentPair product design tokens.

The application uses the operating system window chrome; this stylesheet only
styles its client area and is shared by macOS and Windows.
"""

COLORS = {
    'background': '#F7F8FA', 'surface': '#FFFFFF', 'text': '#172334',
    'muted': '#637084', 'border': '#E6E8EC', 'primary': '#2463EB',
    'primarySoft': '#EDF3FF', 'success': '#16744B',
    'successSoft': '#EDF7F1', 'attention': '#956017',
    'attentionSoft': '#FFF6E7', 'danger': '#B52E35', 'dangerSoft': '#FFF1F1',
}
DESKTOP_SIDEBAR = 164
DESKTOP_INSPECTOR = 300

STYLE = '''
QWidget {font-family:"Segoe UI","PingFang SC",Arial;font-size:14px;color:#172334;}
QMainWindow,QWidget#assistantShell,QWidget#assistantMain,QWidget#desktopPage {background:#F7F8FA;}
QLabel {background:transparent;border:0;}
QLabel#pageTitle {font-size:22px;font-weight:600;}
QLabel#sectionTitle {font-size:17px;font-weight:600;}
QLabel#metadata {font-size:12px;color:#637084;}
QWidget#assistantSide {background:white;border-right:1px solid #E6E8EC;}
QWidget#assistantSide QPushButton {background:transparent;border:0;text-align:left;color:#637084;padding:9px 9px;border-radius:7px;}
QWidget#assistantSide QPushButton:hover {background:#F7F8FA;}
QWidget#assistantSide QPushButton:checked {background:#EDF3FF;color:#2463EB;}
QWidget#assistantComposer,QFrame#surface {background:white;border:1px solid #E6E8EC;border-radius:12px;}
QPlainTextEdit#questionInput {border:0;background:transparent;font-size:16px;padding:0;}
QPushButton {background:white;color:#172334;border:1px solid #E6E8EC;border-radius:7px;padding:7px 12px;min-height:20px;font-size:13px;}
QPushButton:hover {background:#F7F8FA;}
QPushButton:focus {border-color:#2463EB;}
QPushButton:checked {background:#EDF3FF;color:#2463EB;border-color:#EDF3FF;}
QPushButton:disabled {color:#919BA9;background:#F7F8FA;}
QPushButton#primaryButton {background:#2463EB;color:white;border-color:#2463EB;font-weight:600;}
QPushButton#primaryButton:hover {background:#1E53C7;}
QPushButton#primaryButton:disabled {background:#A6BBEC;border-color:#A6BBEC;}
QPushButton#textButton {background:transparent;border:0;color:#2463EB;padding:5px 8px;}
QTextBrowser,QPlainTextEdit,QLineEdit,QListWidget,QTableWidget {background:white;border:1px solid #E6E8EC;border-radius:12px;padding:10px;}
QTextBrowser {padding:18px;}
QLineEdit,QComboBox {border:1px solid #E6E8EC;border-radius:7px;padding:7px;background:white;}
QLineEdit:focus,QPlainTextEdit:focus,QComboBox:focus {border-color:#2463EB;}
QWidget#assistantSide QListWidget {background:transparent;border:0;padding:0;font-size:12px;}
QListWidget::item {padding:9px 6px;border-radius:7px;}
QListWidget::item:selected {background:#EDF3FF;color:#2463EB;}
QListWidget::item:hover {background:#F7F8FA;}
QScrollArea {background:#F7F8FA;border:0;}
QScrollBar:vertical {background:transparent;width:9px;margin:2px;}
QScrollBar::handle:vertical {background:#D3D8E0;min-height:24px;border-radius:3px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
QSplitter::handle {background:#E6E8EC;width:1px;}
QSplitter::handle:hover {background:#2463EB;}
QWidget#evidencePanel {background:white;border-left:1px solid #E6E8EC;}
QWidget#evidencePanel QTextBrowser {border:0;border-radius:0;padding:10px;}
QStatusBar {background:white;border-top:1px solid #E6E8EC;min-height:32px;color:#637084;}
QStatusBar::item {border:0;}
QStatusBar QLabel {font-size:12px;color:#637084;}
QGroupBox {background:white;border:1px solid #E6E8EC;border-radius:12px;margin-top:12px;padding:18px;}
QGroupBox::title {subcontrol-origin:margin;left:16px;padding:0 5px;font-weight:600;}
QProgressBar {border:0;background:#EDF3FF;height:8px;border-radius:4px;text-align:center;}
QProgressBar::chunk {background:#2463EB;border-radius:4px;}
QHeaderView::section {background:#F7F8FA;padding:9px;border:0;font-weight:600;}
'''


def badge(widget, tone='neutral'):
    foreground, background = {
        'success': (COLORS['success'], COLORS['successSoft']),
        'attention': (COLORS['attention'], COLORS['attentionSoft']),
        'danger': (COLORS['danger'], COLORS['dangerSoft']),
        'running': (COLORS['primary'], COLORS['primarySoft']),
    }.get(tone, (COLORS['muted'], COLORS['background']))
    widget.setStyleSheet(f'font-size:12px;color:{foreground};background:{background};border-radius:5px;padding:4px 8px;')
