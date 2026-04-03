"""Tutorial popup for Battle Buddy — matches the DPS Calculator format."""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QScrollArea, QWidget,
)

from ui.theme import (
    BG, BG2, BG3, BORDER, FG, FG_DIM, ACCENT, HEADER_BG,
    FONT_TITLE, FONT_BODY,
)

_SECTION = f"""
    font-family: {FONT_TITLE};
    font-size: 10pt; font-weight: bold;
    color: {ACCENT}; background: transparent;
    margin-top: 10px; margin-bottom: 4px;
"""

_BODY = f"""
    font-family: {FONT_BODY};
    font-size: 9pt; color: {FG};
    background: transparent;
    line-height: 1.5;
"""

_HINT = f"""
    font-family: {FONT_BODY};
    font-size: 8pt; color: {FG_DIM};
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


def _build_tab(widgets: list[QWidget]) -> QScrollArea:
    page = QWidget()
    page.setStyleSheet(f"background-color: {BG};")
    lay = QVBoxLayout(page)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(2)
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setStyleSheet(f"""
        QScrollArea {{ background-color: {BG}; border: none; }}
        QScrollArea > QWidget > QWidget {{ background-color: {BG}; }}
    """)
    scroll.setWidget(page)
    return scroll


class TutorialPopup(QDialog):
    """Multi-tab tutorial popup for Battle Buddy.  Draggable."""

    _instance: "TutorialPopup | None" = None

    @classmethod
    def show_tutorial(cls, parent=None) -> "TutorialPopup":
        if cls._instance is None or not cls._instance.isVisible():
            cls._instance = cls(parent)
        cls._instance.show()
        cls._instance.raise_()
        cls._instance.activateWindow()
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(520, 440)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {BG};
                border: 1px solid {ACCENT};
                border-radius: 4px;
            }}
        """)
        self._drag_pos = QPoint()
        self._dragging = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header (drag handle)
        hdr = QWidget(self)
        hdr.setFixedHeight(34)
        hdr.setCursor(QCursor(Qt.OpenHandCursor))
        hdr.setStyleSheet(f"background-color: {HEADER_BG}; border-bottom: 1px solid {BORDER};")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(12, 0, 6, 0)

        title = QLabel("\U0001f4e1  Battle Buddy — Tutorial", hdr)
        title.setStyleSheet(f"""
            font-family: {FONT_TITLE}; font-size: 10pt;
            font-weight: bold; color: {ACCENT}; background: transparent;
        """)
        hdr_lay.addWidget(title)
        hdr_lay.addStretch(1)

        btn_close = QPushButton("\u2715", hdr)
        btn_close.setFixedSize(26, 22)
        btn_close.setCursor(QCursor(Qt.PointingHandCursor))
        btn_close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {FG_DIM}; border: none; font-size: 11pt; }}
            QPushButton:hover {{ color: #ff5533; }}
        """)
        btn_close.clicked.connect(self.close)
        hdr_lay.addWidget(btn_close)
        self._hdr = hdr
        root.addWidget(hdr)

        # Tabs
        tabs = QTabWidget(self)
        tabs.setStyleSheet(f"""
            QTabBar::tab {{
                background-color: {BG2}; color: {FG_DIM};
                border: none; border-bottom: 2px solid transparent;
                padding: 5px 10px;
                font-family: {FONT_BODY}; font-size: 8pt; font-weight: bold;
            }}
            QTabBar::tab:hover {{ color: {FG}; background-color: {BG3}; }}
            QTabBar::tab:selected {{
                color: {ACCENT}; border-bottom-color: {ACCENT};
                background-color: {BG};
            }}
            QTabWidget::pane {{ background-color: {BG}; border: none; }}
        """)

        tabs.addTab(self._tab_overview(),    "Overview")
        tabs.addTab(self._tab_weapons(),     "Weapons")
        tabs.addTab(self._tab_consumables(), "Consumables")
        tabs.addTab(self._tab_options(),     "Options")
        tabs.setDocumentMode(False)
        root.addWidget(tabs, 1)

        # Force dark background on tab content area
        self.setStyleSheet(self.styleSheet() + f"""
            QScrollArea {{ background-color: {BG}; }}
            QScrollArea > QWidget > QWidget {{ background-color: {BG}; }}
            QTabWidget::pane {{ background-color: {BG}; border: none; }}
        """)

    # ── Tab content ──────────────────────────────────────────────────────────

    def _tab_overview(self) -> QScrollArea:
        return _build_tab([
            _section("What is Battle Buddy?"),
            _body(
                "Battle Buddy is a real-time HUD overlay that reads your "
                "Star Citizen Game.log and automatically detects what weapons "
                "and consumables you have equipped — no manual input needed."
            ),
            _hint("The HUD appears automatically when you join the Persistent Universe."),

            _section("How It Works"),
            _body(
                "Star Citizen logs every inventory attachment event when you spawn. "
                "Battle Buddy parses these events to build a live picture of:\n"
                "\u2022  Your 2 primary weapons and their spare mag counts\n"
                "\u2022  Your sidearm or med gun\n"
                "\u2022  Your utility tool (multitool, tractor, repair tool)\n"
                "\u2022  Medpens, oxypens, and grenades"
            ),

            _section("Getting Started"),
            _body(
                "1. Set your Game.log path in Options \u2192 Log Path\n"
                "2. Launch Star Citizen\n"
                "3. Battle Buddy shows your HUD automatically when you join the PU\n"
                "4. Equip or swap gear — the HUD updates within seconds"
            ),
            _hint("If the HUD doesn\u2019t appear, check that the log path in Options is correct."),
        ])

    def _tab_weapons(self) -> QScrollArea:
        return _build_tab([
            _section("Weapon Slots"),
            _body(
                "Battle Buddy tracks four weapon slots:\n"
                "\u2022  Primary 1 \u2014 first stocked weapon (back/holster slot 1)\n"
                "\u2022  Primary 2 \u2014 second stocked weapon (back/holster slot 2)\n"
                "\u2022  Sidearm \u2014 pistol or med gun in the hip holster\n"
                "\u2022  Utility \u2014 multitool, tractor beam, or repair tool"
            ),
            _hint("Weapons are detected automatically from the log when you spawn."),

            _section("Spare Magazine Counter"),
            _body(
                "The number of spare magazines on your armour is shown next to "
                "each weapon. Each filled segment \u25ae represents one spare mag.\n\n"
                "Colour indicates ammo type:\n"
                "\u2022  Cyan \u2014 Energy\n"
                "\u2022  Amber \u2014 Ballistic\n"
                "\u2022  Purple \u2014 Distortion"
            ),

            _section("Weapon Type Detection"),
            _body(
                "Battle Buddy reads the weapon class name directly from the log "
                "to determine its type (Pistol, Rifle, Sniper, LMG, Shotgun, etc.) "
                "and its ammo category. No external database is needed."
            ),
            _hint(
                "Armour swaps (changing your suit) are detected automatically \u2014 "
                "Battle Buddy will not falsely count an armour swap as pen usage."
            ),
        ])

    def _tab_consumables(self) -> QScrollArea:
        return _build_tab([
            _section("Medpens & Oxypens"),
            _body(
                "Your leg armour typically holds up to 2 medpens and 2 oxypens. "
                "Battle Buddy shows filled \u25cf dots for each pen slot:\n"
                "\u2022  Green dots \u2014 Medpens\n"
                "\u2022  Blue dots  \u2014 Oxypens"
            ),
            _hint(
                "When you use a pen, the slot clears. "
                "When you swap armour, all 4 pen slots clear at once \u2014 "
                "Battle Buddy tells the difference and won\u2019t count a swap as usage."
            ),

            _section("Grenades"),
            _body(
                "Grenade slots on your armour are tracked individually. "
                "The counter decrements each time a grenade entity disappears "
                "from your loadout."
            ),

            _section("Utility Tool Ammo"),
            _body(
                "Multitools, tractor beams, and repair tools carry a magazine. "
                "The spare magazine count for your utility slot works the same "
                "as primary weapons \u2014 it counts separate magazine entities "
                "attached to your armour."
            ),
        ])

    def _tab_options(self) -> QScrollArea:
        return _build_tab([
            _section("Log Path"),
            _body(
                "Set the full path to your Star Citizen Game.log file. "
                "The default location is:\n"
                "C:/StarCitizen/LIVE/Game.log\n\n"
                "If you use a custom install path, update this in Options."
            ),
            _hint("Changes take effect immediately after saving."),

            _section("HUD Orientation"),
            _body(
                "\u2022  Horizontal \u2014 weapon slots side-by-side in a wide bar "
                "(suited for bottom of screen)\n"
                "\u2022  Vertical \u2014 weapon slots stacked in a narrow column "
                "(suited for left or right edge)"
            ),

            _section("Opacity"),
            _body(
                "Adjust the HUD window transparency in the WingmanAI skill "
                "configuration (default 0.92). Lower values let more of the "
                "game show through."
            ),

            _section("Auto-show on Join PU"),
            _body(
                "When enabled, the HUD appears automatically the moment "
                "Battle Buddy detects you loading into the Persistent Universe. "
                "Disable this if you prefer to show it manually via voice command."
            ),
        ])

    # ── Drag-to-move ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._hdr.geometry().contains(event.position().toPoint()):
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.pos()
            self._hdr.setCursor(QCursor(Qt.ClosedHandCursor))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self._hdr.setCursor(QCursor(Qt.OpenHandCursor))
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def show_relative_to(self, widget: QWidget) -> None:
        pos = widget.mapToGlobal(QPoint(0, widget.height() + 4))
        x = max(0, pos.x() - self.width() + widget.width())
        self.move(x, pos.y())
        self.show()
        self.raise_()
        self.activateWindow()
