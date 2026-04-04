"""Tutorial popup for Mining Signals — matches the Craft Database format."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from shared.qt.theme import P

TOOL_COLOR = "#33dd88"
_BRACKET_LEN = 18

# ── Shared rich-text style fragments ─────────────────────────────────────

_B   = f"font-family: Consolas; color: {P.fg}; font-size: 9pt; line-height: 1.5;"
_DIM = f"color: {P.fg_dim};"
_ACC = f"color: {TOOL_COLOR};"
_GRN = f"color: {P.green};"
_YLW = f"color: {P.yellow};"

_FONT = "font-family: Electrolize, Consolas;"

_C_START = TOOL_COLOR
_C_SCAN  = "#44aaff"
_C_TABLE = "#ffb347"
_C_TIPS  = "#cc88ff"


def _h3(text: str, color: str) -> str:
    return f'<h3 style="{_FONT} color: {color};">{text}</h3>'


def _h4(text: str, color: str) -> str:
    return f'<h4 style="{_FONT} color: {color};">{text}</h4>'


def _html(body: str) -> str:
    return f'<div style="{_B}">{body}</div>'


_TAB_GETTING_STARTED = _html(f"""
{_h3("Welcome to Mining Signals", _C_START)}
<p>This tool identifies mining resources by reading the signal value from
your ship's scanner HUD. It matches the number against a reference table
to tell you what resource is at a location and how many rocks are there.</p>

{_h4("Quick Setup", _C_START)}
<ol>
  <li>Click <b>Set Region</b> and draw a box around the signal number
      on your mining scanner HUD.</li>
  <li>Click <b>Set Mining Output Display Location</b> and click
      where you want the result bubble to appear.</li>
  <li>Click <b>Start Scan</b> to begin continuous scanning.</li>
</ol>
<p>The tool reads the number every 2 seconds and shows the matched
resource in a floating bubble.</p>

{_h4("Data Source", _C_START)}
<p>Signal data is fetched from a Google Sheets spreadsheet and refreshed
automatically every hour. The tool works offline using cached data.</p>

{_h4("Hotkey", _C_START)}
<p>The default global hotkey to show / hide this window is
<b>Shift + 9</b>. You can reassign it in the SC Toolbox settings.</p>
""")

_TAB_SCANNING = _html(f"""
{_h3("Scanning", _C_SCAN)}

{_h4("How It Works", _C_SCAN)}
<p>The scanner captures a small area of your screen where the mining
signal number appears. It uses OCR (optical character recognition) to
read the digits, then looks up the value in the signal table.</p>

{_h4("Set Region", _C_SCAN)}
<p>Click <b>Set Region</b> to draw a box around the signal number on
your HUD. Draw it <b>tightly around the number</b> &mdash; avoid
including icons or other HUD elements. The pin icon to the left of the
number is automatically filtered out.</p>

{_h4("Display Location", _C_SCAN)}
<p>Click <b>Set Mining Output Display Location</b> to choose where the
result bubble appears. A preview follows your cursor &mdash; click to
lock the position.</p>

{_h4("Compact Mode", _C_SCAN)}
<p>When scanning is active, the window collapses to a small bar showing
only the <b>Set Region</b> and <b>Stop Scan</b> buttons. Press your
assigned hotkey to hide/show the tool entirely.</p>

{_h4("Requirements", _C_SCAN)}
<ul>
  <li>Star Citizen must run in <b>Borderless Windowed</b> mode</li>
  <li>Fullscreen exclusive mode will show a black capture</li>
  <li><span style="{_DIM}">Scanning uses ~7-8% of one CPU core at
      2-second intervals. No files are saved to disk.</span></li>
</ul>
""")

_TAB_TABLE = _html(f"""
{_h3("Signal Table", _C_TABLE)}

{_h4("Reading the Table", _C_TABLE)}
<p>The table shows all known mining resources with their signal values
for 1 to 6 rocks. Click column headers to sort by any column.</p>

{_h4("Manual Search", _C_TABLE)}
<p>Type a signal value in the search bar to identify a resource without
scanning. When multiple resources share the same value, all matches
are shown.</p>

{_h4("Rarity Tiers", _C_TABLE)}
<ul>
  <li><b>Common</b> &mdash; Most frequently found</li>
  <li><b>Uncommon</b> &mdash; Moderate value</li>
  <li><b>Rare</b> &mdash; High value resources</li>
  <li><b>Epic</b> &mdash; Very valuable</li>
  <li><b>Legendary</b> &mdash; Extremely rare and valuable</li>
  <li><b>Salvage</b> &mdash; Salvage operations</li>
</ul>

{_h4("Overlapping Values", _C_TABLE)}
<p>Some resources share the same signal value at different rock counts.
When the scanner detects an ambiguous value, the result bubble lists
<b>all possible matches</b> so you can narrow it down based on context.</p>
""")

_TAB_TIPS = _html(f"""
{_h3("Tips &amp; Troubleshooting", _C_TIPS)}

{_h4("Best OCR Accuracy", _C_TIPS)}
<ul>
  <li>Draw the scan region <b>tightly</b> around just the number</li>
  <li>The number should be clearly visible (not obscured by other HUD elements)</li>
  <li>Larger scan regions take longer to process</li>
</ul>

{_h4("No Match Found?", _C_TIPS)}
<p>If the scanner reads a number but finds no match, it may be:</p>
<ul>
  <li>A value not yet in the spreadsheet</li>
  <li>An OCR misread &mdash; try re-drawing the region more tightly</li>
  <li>A non-mining signal (e.g. distance, mass, etc.)</li>
</ul>

{_h4("Always-on-Top", _C_TIPS)}
<p>The window stays above Star Citizen so you can reference it in-game.
Drag the title bar to reposition it.</p>

{_h4("Data Refresh", _C_TIPS)}
<p>Cached data refreshes automatically every hour. Restart the tool to
force a fresh fetch from the spreadsheet.</p>
""")

_TABS = [
    ("Getting Started", _TAB_GETTING_STARTED),
    ("Scanning", _TAB_SCANNING),
    ("Signal Table", _TAB_TABLE),
    ("Tips", _TAB_TIPS),
]


# ── Close button ─────────────────────────────────────────────────────────


class _CloseBtn(QPushButton):
    def __init__(self, parent=None):
        super().__init__("x", parent)
        self.setObjectName("tutClose")
        self.setFixedSize(32, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""
            QPushButton#tutClose {{
                background: rgba(255, 60, 60, 0.15);
                color: #cc6666;
                border: none;
                border-radius: 3px;
                font-family: Consolas;
                font-size: 13pt;
                font-weight: bold;
                padding: 0px;
                margin: 2px;
                min-height: 0px;
            }}
            QPushButton#tutClose:hover {{
                background-color: rgba(220, 50, 50, 0.85);
                color: #ffffff;
            }}
        """)


# ── Scrollable tab content ───────────────────────────────────────────────


def _make_tab(html: str, parent: QWidget) -> QScrollArea:
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setStyleSheet(f"""
        QScrollArea {{ background: transparent; border: none; }}
        QScrollBar:vertical {{
            background: {P.scrollbar_bg}; width: 6px; border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {P.scrollbar_handle}; min-height: 20px; border-radius: 3px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    """)
    lbl = QLabel(html)
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.RichText)
    lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    lbl.setStyleSheet(f"background: transparent; padding: 16px; color: {P.fg};")
    lbl.setOpenExternalLinks(True)
    scroll.setWidget(lbl)
    return scroll


# ── Tutorial popup ───────────────────────────────────────────────────────


class TutorialPopup(QDialog):
    """Tabbed tutorial popup for Mining Signals.

    Singleton: a second call just raises the existing window.
    """

    _instance: Optional[TutorialPopup] = None

    def __new__(cls, parent: Optional[QWidget] = None):
        if cls._instance is not None and cls._instance.isVisible():
            cls._instance.raise_()
            cls._instance.activateWindow()
            return cls._instance
        instance = super().__new__(cls)
        cls._instance = instance
        return instance

    def __init__(self, parent: Optional[QWidget] = None):
        if getattr(self, "_initialised", False):
            return
        self._initialised = True

        super().__init__(parent)
        self._drag_pos: QPoint | None = None

        self.setWindowTitle("Mining Signals — Tutorial")
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(560, 460)
        self.setMinimumSize(420, 320)

        # Centre near parent
        if parent:
            pg = parent.geometry()
            x = pg.x() + (pg.width() - 560) // 2
            y = pg.y() + (pg.height() - 460) // 2
            self.move(max(0, x), max(0, y))

        self._build()
        self.show()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        frame = QWidget(self)
        frame.setStyleSheet("background-color: rgba(11, 14, 20, 230);")
        frame_lay = QVBoxLayout(frame)
        frame_lay.setContentsMargins(0, 0, 0, 0)
        frame_lay.setSpacing(0)

        # ── Title bar
        title_bar = QWidget(frame)
        title_bar.setFixedHeight(34)
        title_bar.setStyleSheet(f"background-color: {P.bg_header};")
        tb_lay = QHBoxLayout(title_bar)
        tb_lay.setContentsMargins(12, 0, 4, 0)
        tb_lay.setSpacing(8)

        title_lbl = QLabel("MINING SIGNALS  \u2014  TUTORIAL", title_bar)
        title_lbl.setStyleSheet(
            f"font-family: Electrolize, Consolas, monospace;"
            f"font-size: 11pt; font-weight: bold;"
            f"color: {TOOL_COLOR}; letter-spacing: 2px; background: transparent;"
        )
        tb_lay.addWidget(title_lbl)
        tb_lay.addStretch(1)

        close_btn = _CloseBtn(title_bar)
        close_btn.clicked.connect(self.close)
        tb_lay.addWidget(close_btn)

        frame_lay.addWidget(title_bar)

        # ── Tabbed content
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background: transparent;
            }}
            QTabBar::tab {{
                background: {P.bg_secondary};
                color: {P.fg_dim};
                border: none;
                padding: 6px 14px;
                font-family: Consolas;
                font-size: 9pt;
                font-weight: bold;
            }}
            QTabBar::tab:selected {{
                background: #0e2220;
                color: {TOOL_COLOR};
            }}
            QTabBar::tab:hover:!selected {{
                color: {P.fg};
            }}
        """)

        for tab_title, html in _TABS:
            tabs.addTab(_make_tab(html, tabs), tab_title)

        frame_lay.addWidget(tabs, 1)
        outer.addWidget(frame)

    # ── Paint: border + corner brackets ─────────────────────────────────

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()

        edge = QColor(TOOL_COLOR)
        edge.setAlpha(100)
        painter.setPen(QPen(edge, 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        bl = _BRACKET_LEN
        bracket = QColor(TOOL_COLOR)
        bracket.setAlpha(200)
        painter.setPen(QPen(bracket, 2))
        painter.drawLine(0, 0, bl, 0)
        painter.drawLine(0, 0, 0, bl)
        painter.drawLine(w - 1, 0, w - 1 - bl, 0)
        painter.drawLine(w - 1, 0, w - 1, bl)
        painter.drawLine(0, h - 1, bl, h - 1)
        painter.drawLine(0, h - 1, 0, h - 1 - bl)
        painter.drawLine(w - 1, h - 1, w - 1 - bl, h - 1)
        painter.drawLine(w - 1, h - 1, w - 1, h - 1 - bl)
        painter.end()

    # ── Drag support ─────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ── Cleanup ──────────────────────────────────────────────────────────

    def closeEvent(self, event):
        TutorialPopup._instance = None
        self._initialised = False
        super().closeEvent(event)
