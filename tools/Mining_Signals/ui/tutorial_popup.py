"""Tutorial popup for Mining Signals."""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QScrollArea, QWidget,
)

from shared.qt.theme import P

ACCENT = "#33dd88"

_SECTION = f"""
    font-family: Electrolize, Consolas, monospace;
    font-size: 10pt; font-weight: bold;
    color: {ACCENT}; background: transparent;
    margin-top: 10px; margin-bottom: 4px;
"""

_BODY = f"""
    font-family: Consolas, monospace;
    font-size: 9pt; color: {P.fg};
    background: transparent;
    line-height: 1.5;
"""

_HINT = f"""
    font-family: Consolas, monospace;
    font-size: 8pt; color: {P.fg_dim};
    background: transparent; font-style: italic;
"""


def _section(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_SECTION)
    lbl.setWordWrap(True)
    return lbl


def _body(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_BODY)
    lbl.setWordWrap(True)
    return lbl


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_HINT)
    lbl.setWordWrap(True)
    return lbl


def _make_tab(widgets: list[QLabel]) -> QScrollArea:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.setSpacing(4)
    for w in widgets:
        layout.addWidget(w)
    layout.addStretch(1)
    scroll = QScrollArea()
    scroll.setWidget(container)
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet(f"background: {P.bg_primary}; border: none;")
    return scroll


class TutorialPopup(QDialog):
    """Multi-tab help dialog for Mining Signals."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.resize(440, 380)
        self.setMinimumSize(300, 250)
        self.setStyleSheet(f"""
            QDialog {{
                background: {P.bg_primary};
                border: 1px solid {ACCENT};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ──
        header = QWidget(self)
        header.setFixedHeight(36)
        header.setStyleSheet(f"background: {P.bg_header};")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 8, 0)

        title = QLabel("MINING SIGNALS  \u2014  TUTORIAL", header)
        title.setStyleSheet(f"""
            font-family: Electrolize, Consolas, monospace;
            font-size: 10pt; font-weight: bold;
            color: {ACCENT}; background: transparent;
            letter-spacing: 2px;
        """)
        h_layout.addWidget(title)
        h_layout.addStretch(1)

        close_btn = QPushButton("x", header)
        close_btn.setFixedSize(28, 24)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 60, 60, 0.15);
                color: #cc6666; border: none; border-radius: 3px;
                font-size: 12pt; font-weight: bold;
            }}
            QPushButton:hover {{
                background: rgba(220, 50, 50, 0.85); color: white;
            }}
        """)
        close_btn.clicked.connect(self.close)
        h_layout.addWidget(close_btn)
        layout.addWidget(header)

        # Drag support
        self._drag_pos = QPoint()
        header.mousePressEvent = self._header_press
        header.mouseMoveEvent = self._header_move

        # ── Tabs ──
        tabs = QTabWidget(self)
        tabs.setStyleSheet(f"""
            QTabBar::tab {{
                padding: 6px 12px; font-size: 8pt; font-weight: bold;
            }}
        """)

        tabs.addTab(self._overview_tab(), "Overview")
        tabs.addTab(self._scanning_tab(), "Scanning")
        tabs.addTab(self._table_tab(), "Signal Table")
        layout.addWidget(tabs)

        # Position near cursor
        cursor = QCursor.pos()
        self.move(cursor.x() - 220, cursor.y() - 190)

    def _overview_tab(self) -> QScrollArea:
        return _make_tab([
            _section("What is Mining Signals?"),
            _body(
                "Mining Signals identifies mining resources by reading "
                "the signal value from your ship's scanner HUD. It matches "
                "the number against a reference table to tell you what "
                "resource is at a location and how many rocks are there."
            ),
            _section("Getting Started"),
            _body(
                "1. Click 'Set Region' and draw a box around the signal\n"
                "   number on your mining scanner HUD.\n"
                "2. Click 'Set Mining Output Display Location' and click\n"
                "   where you want the result bubble to appear.\n"
                "3. Click 'Start Scan' to begin continuous scanning.\n"
                "4. The tool reads the number every 2 seconds and shows\n"
                "   the matched resource in a floating bubble."
            ),
            _section("Data Source"),
            _body(
                "Signal data is fetched from a Google Sheets spreadsheet "
                "and refreshed automatically every hour. The tool works "
                "offline using cached data."
            ),
        ])

    def _scanning_tab(self) -> QScrollArea:
        return _make_tab([
            _section("How Scanning Works"),
            _body(
                "The scanner captures a small area of your screen where "
                "the mining signal number appears. It uses OCR (optical "
                "character recognition) to read the digits, then looks up "
                "the value in the signal table."
            ),
            _section("Tips for Best Results"),
            _body(
                "- Draw the scan region tightly around just the number\n"
                "- Avoid including icons or other HUD elements\n"
                "- The game must be in Borderless Windowed mode\n"
                "- Fullscreen exclusive mode will show a black capture"
            ),
            _section("Compact Mode"),
            _body(
                "When scanning is active, the window collapses to a small "
                "bar showing only the Set Region and Stop Scan buttons. "
                "Press your assigned hotkey to hide/show the tool."
            ),
            _hint(
                "Scanning uses ~7-8% of one CPU core at 2-second intervals. "
                "No files are saved to disk."
            ),
        ])

    def _table_tab(self) -> QScrollArea:
        return _make_tab([
            _section("Signal Table"),
            _body(
                "The table shows all known mining resources with their "
                "signal values for 1 to 6 rocks. Click column headers "
                "to sort. You can also type a number in the search bar "
                "to manually look up a value."
            ),
            _section("Rarity Tiers"),
            _body(
                "Common  -  Most frequently found\n"
                "Uncommon  -  Moderate value\n"
                "Rare  -  High value resources\n"
                "Epic  -  Very valuable\n"
                "Legendary  -  Extremely rare and valuable\n"
                "Salvage  -  Salvage operations"
            ),
            _section("Manual Search"),
            _body(
                "Type a signal value in the search bar at the top of the "
                "expanded window to identify a resource without scanning."
            ),
        ])

    def _header_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()

    def _header_move(self, event):
        if event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
