"""
Battle Buddy HUD overlay window.

Supports horizontal (wide bar) and vertical (narrow column) layouts.
Displays:
  ┌──────────────────────────────────────────────────────────┐
  │  PRIMARY 1   PRIMARY 2   SIDEARM/MEDGUN   UTILITY TOOL  │
  │  ▮▮▮ ×3      ▮▮ ×2       ▮ ×1            ▮ ×1          │
  │                          MED ●● OXY ●●  GREN ●●        │
  └──────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QPoint, QTimer, Signal
from PySide6.QtGui import QCursor, QPainter, QPen, QBrush, QColor
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QSizePolicy, QFrame, QSlider,
)

from core.inventory_tracker import LoadoutState, WeaponSlot
from ui.options_popup import load_settings, save_settings
from ui.theme import (
    BG, BG2, BG3, BORDER, BORDER2, FG, FG_DIM, FG_DIMMER,
    ACCENT, ACCENT2, HEADER_BG,
    COLOR_MEDPEN, COLOR_OXYPEN, COLOR_STIM, COLOR_DETOX, COLOR_PEN_OTHER,
    COLOR_GRENADE,
    FONT_TITLE, FONT_BODY,
    ammo_color,
)

_MAX_MAG_PIPS  = 8   # maximum pip segments shown
_PIP_W         = 10  # pip width px
_PIP_H         = 18  # pip height px
_PIP_GAP       = 3   # gap between pips


class _PipBar(QWidget):
    """Horizontal bar of filled/empty pip segments representing spare mag count."""

    def __init__(self, count: int = 0, total: int = _MAX_MAG_PIPS,
                 color: str = ACCENT, parent=None):
        super().__init__(parent)
        self._count = count
        self._total = total
        self._color = QColor(color)
        self._empty = QColor(BG3)
        w = (_PIP_W + _PIP_GAP) * max(total, 1) - _PIP_GAP
        self.setFixedSize(w, _PIP_H)

    def set_count(self, count: int, total: int | None = None, color: str | None = None):
        self._count = count
        if total is not None:
            self._total = total
            w = (_PIP_W + _PIP_GAP) * max(total, 1) - _PIP_GAP
            self.setFixedWidth(w)
        if color is not None:
            self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i in range(self._total):
            x = i * (_PIP_W + _PIP_GAP)
            color = self._color if i < self._count else self._empty
            p.setBrush(QBrush(color))
            p.setPen(QPen(color.darker(130), 0.5))
            p.drawRoundedRect(x, 2, _PIP_W, _PIP_H - 4, 2, 2)
        p.end()


def _label(text: str, size: int = 9, color: str = FG,
           bold: bool = False, font: str = FONT_BODY) -> QLabel:
    lbl = QLabel(text)
    weight = "bold" if bold else "normal"
    lbl.setStyleSheet(
        f"font-family: {font}; font-size: {size}pt; "
        f"color: {color}; background: transparent; font-weight: {weight};"
    )
    return lbl


class _WeaponCard(QWidget):
    """A single weapon slot card showing name, type tag, ammo bar, and mag count."""

    def __init__(self, slot_label: str, parent=None):
        super().__init__(parent)
        self._slot_label = slot_label
        self.setMinimumWidth(140)
        self.setStyleSheet(f"""
            _WeaponCard, QWidget#weaponCard {{
                background-color: {BG2};
                border: 1px solid {BORDER2};
                border-radius: 3px;
            }}
        """)
        self.setObjectName("weaponCard")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(3)

        # Row 1: slot label + weapon name
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self._lbl_slot = _label(slot_label, size=7, color=FG_DIMMER, bold=True)
        self._lbl_name = _label("—", size=9, color=FG)
        row1.addWidget(self._lbl_slot)
        row1.addWidget(self._lbl_name, 1)
        outer.addLayout(row1)

        # Row 2: weapon type tag + ammo type chip
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        self._lbl_type  = _label("", size=7, color=ACCENT2)
        self._lbl_type.setMinimumWidth(0)
        self._lbl_type.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._lbl_ammo  = _label("", size=7, color=FG_DIM)
        row2.addWidget(self._lbl_type)
        row2.addWidget(self._lbl_ammo)
        row2.addStretch(1)
        outer.addLayout(row2)

        # Row 3: pip bar + count label
        row3 = QHBoxLayout()
        row3.setSpacing(6)
        self._pips  = _PipBar(0, _MAX_MAG_PIPS, ACCENT)
        self._lbl_count = _label("—", size=8, color=FG_DIM)
        row3.addWidget(self._pips)
        row3.addWidget(self._lbl_count)
        row3.addStretch(1)
        outer.addLayout(row3)

    def update_weapon(self, weapon: WeaponSlot | None) -> None:
        if weapon is None:
            self._lbl_name.setText("— Empty —")
            self._lbl_name.setStyleSheet(
                f"font-family: {FONT_BODY}; font-size: 9pt; "
                f"color: {FG_DIMMER}; background: transparent;"
            )
            self._lbl_type.setText("")
            self._lbl_ammo.setText("")
            self._pips.set_count(0, _MAX_MAG_PIPS, ACCENT)
            self._lbl_count.setText("—")
            self.setMinimumWidth(140)
            return

        color = ammo_color(weapon.ammo_type)
        self._lbl_name.setText(weapon.display_name)
        self._lbl_name.setStyleSheet(
            f"font-family: {FONT_BODY}; font-size: 9pt; "
            f"color: {FG}; background: transparent; font-weight: bold;"
        )
        type_text = weapon.weapon_type.upper()
        # For utility tools, show the attached module instead of ammo type
        if weapon.module:
            type_text = f"{weapon.weapon_type.upper()} \u2022 {weapon.module}"
        self._lbl_type.setText(type_text)
        ammo_label = weapon.ammo_type.capitalize() if weapon.ammo_type != "unknown" else ""
        if weapon.module:
            ammo_label = ""  # module replaces ammo label for utility tools
        self._lbl_ammo.setText(ammo_label)
        self._lbl_ammo.setStyleSheet(
            f"font-family: {FONT_BODY}; font-size: 7pt; "
            f"color: {color}; background: transparent;"
        )

        mags = weapon.spare_mags
        total_pips = max(mags, 1)
        self._pips.set_count(mags, min(total_pips, _MAX_MAG_PIPS), color)
        self._lbl_count.setText(f"\u00d7{mags} mag{'s' if mags != 1 else ''}")

        # Widen the card if the type label needs more room (e.g. module text)
        self.setMinimumWidth(180 if weapon.module else 140)
        self.adjustSize()


# Category display order + colours + labels
_PEN_CATEGORIES = [
    ("med",   "MED",   COLOR_MEDPEN),
    ("oxy",   "OXY",   COLOR_OXYPEN),
    ("stim",  "STIM",  COLOR_STIM),
    ("detox", "DETOX", COLOR_DETOX),
    ("other", "OTHER", COLOR_PEN_OTHER),
]


class _ConsumableBar(QWidget):
    """Dynamic row showing pen categories, individual pen names, and grenades."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(8, 4, 8, 4)
        self._lay.setSpacing(8)

        # Pre-build category sections (hidden until populated)
        self._cat_widgets: dict[str, tuple[QWidget, QLabel, QLabel]] = {}
        for cat, label_text, color in _PEN_CATEGORIES:
            container = QWidget(self)
            cw_lay = QHBoxLayout(container)
            cw_lay.setContentsMargins(0, 0, 0, 0)
            cw_lay.setSpacing(4)
            lbl_cat = _label(label_text, size=7, color=color, bold=True)
            lbl_detail = _label("", size=7, color=FG_DIM)
            cw_lay.addWidget(lbl_cat)
            cw_lay.addWidget(lbl_detail)
            container.hide()
            self._lay.addWidget(container)
            self._cat_widgets[cat] = (container, lbl_cat, lbl_detail)

        # Separator before grenades
        self._gren_sep = QFrame()
        self._gren_sep.setFrameShape(QFrame.VLine)
        self._gren_sep.setStyleSheet(f"color: {BORDER};")
        self._lay.addWidget(self._gren_sep)

        # Grenades — label + detail (same style as pens)
        self._gren_container = QWidget(self)
        gc_lay = QHBoxLayout(self._gren_container)
        gc_lay.setContentsMargins(0, 0, 0, 0)
        gc_lay.setSpacing(4)
        gc_lay.addWidget(_label("GREN", size=7, color=COLOR_GRENADE, bold=True))
        self._gren_detail = _label("", size=7, color=COLOR_GRENADE)
        gc_lay.addWidget(self._gren_detail)
        self._gren_container.hide()
        self._lay.addWidget(self._gren_container)

        self._lay.addStretch(1)

    def update_consumables(self, state: LoadoutState) -> None:
        by_cat = state.pens_by_category()

        for cat, _label_text, color in _PEN_CATEGORIES:
            container, lbl_cat, lbl_detail = self._cat_widgets[cat]
            pens = by_cat.get(cat, [])
            if pens:
                container.show()
                # Build detail: group by display_name and show counts
                counts: dict[str, int] = {}
                for p in pens:
                    counts[p.display_name] = counts.get(p.display_name, 0) + 1
                parts = []
                for name, n in counts.items():
                    parts.append(f"{name} x{n}" if n > 1 else name)
                lbl_detail.setText(" | ".join(parts))
                lbl_detail.setStyleSheet(
                    f"font-family: {FONT_BODY}; font-size: 7pt; "
                    f"color: {color}; background: transparent;"
                )
            else:
                container.hide()

        # Grenades — show type names with counts
        gren_types = state.grenades_by_type()
        if gren_types:
            self._gren_container.show()
            parts = []
            for name, n in gren_types.items():
                parts.append(f"{name} x{n}" if n > 1 else name)
            self._gren_detail.setText(" | ".join(parts))
        else:
            self._gren_container.hide()


class HudWindow(QWidget):
    """
    Main HUD overlay window.

    orientation: "horizontal" | "vertical"
    """

    # Emitted from background thread via QTimer to update UI on main thread
    _state_ready = Signal(object)

    def __init__(
        self,
        orientation: str = "horizontal",
        opacity: float = 0.92,
        x: int = 60,
        y: int = 880,
        on_options: Callable | None = None,
        on_tutorial: Callable | None = None,
    ):
        super().__init__()
        self._orientation = orientation
        self._opacity     = opacity
        self._on_options  = on_options
        self._on_tutorial = on_tutorial

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool               # no taskbar entry
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(opacity)
        self.move(x, y)

        self._drag_pos = QPoint()
        self._dragging = False

        # Debounce timer for settings saves (opacity slider, position)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._flush_save)
        self._pending_save: dict | None = None

        self._build_ui()
        self._state_ready.connect(self._apply_state)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Outer container with painted border
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._container = QWidget(self)
        self._container.setStyleSheet(f"""
            QWidget {{
                background-color: {BG};
                border: 1px solid {ACCENT};
                border-radius: 4px;
            }}
        """)
        outer.addWidget(self._container)

        root = QVBoxLayout(self._container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_titlebar())
        root.addWidget(self._build_weapon_area())
        root.addWidget(self._build_consumable_bar())

    def _build_titlebar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(26)
        bar.setStyleSheet(f"background-color: {HEADER_BG}; border-bottom: 1px solid {BORDER};")
        bar.setCursor(QCursor(Qt.OpenHandCursor))
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 0, 4, 0)
        lay.setSpacing(4)

        title = _label("\u26a1 BATTLE BUDDY", size=8, color=ACCENT, bold=True, font=FONT_TITLE)
        lay.addWidget(title)
        lay.addStretch(1)

        # Opacity slider
        self._opacity_slider = QSlider(Qt.Horizontal, bar)
        self._opacity_slider.setRange(20, 100)  # 20% – 100%
        self._opacity_slider.setValue(int(self._opacity * 100))
        self._opacity_slider.setFixedWidth(60)
        self._opacity_slider.setFixedHeight(16)
        self._opacity_slider.setToolTip("Opacity")
        self._opacity_slider.setCursor(QCursor(Qt.PointingHandCursor))
        self._opacity_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; background: {BG3}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 10px; height: 10px; margin: -3px 0;
                background: {ACCENT}; border-radius: 5px;
            }}
            QSlider::handle:horizontal:hover {{
                background: #00e0ff;
            }}
            QSlider::sub-page:horizontal {{
                background: {ACCENT2}; border-radius: 2px;
            }}
        """)
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        lay.addWidget(self._opacity_slider)

        # Tutorial button (text, matching other tools' style)
        btn_tutorial = QPushButton("? Tutorial", bar)
        btn_tutorial.setFixedHeight(18)
        btn_tutorial.setToolTip("Open the Battle Buddy tutorial")
        btn_tutorial.setCursor(QCursor(Qt.PointingHandCursor))
        btn_tutorial.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {FG_DIM};
                border: 1px solid {BORDER}; border-radius: 2px;
                font-family: {FONT_BODY}; font-size: 7pt;
                padding: 0 6px;
            }}
            QPushButton:hover {{ color: {ACCENT}; border-color: {ACCENT}; }}
        """)
        if self._on_tutorial:
            btn_tutorial.clicked.connect(self._on_tutorial)
        lay.addWidget(btn_tutorial)

        for icon, tip, handler in [
            ("\u2699", "Options",  self._on_options),
            ("\u2715", "Hide",     self.hide),
        ]:
            btn = QPushButton(icon, bar)
            btn.setFixedSize(22, 20)
            btn.setToolTip(tip)
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {FG_DIM};
                    border: none; font-size: 10pt;
                }}
                QPushButton:hover {{ color: {ACCENT}; }}
            """)
            if handler:
                btn.clicked.connect(handler)
            lay.addWidget(btn)

        self._titlebar = bar
        return bar

    def _build_weapon_area(self) -> QWidget:
        area = QWidget()
        area.setStyleSheet(f"background-color: {BG};")

        if self._orientation == "vertical":
            lay = QVBoxLayout(area)
        else:
            lay = QHBoxLayout(area)
        lay.setContentsMargins(6, 6, 6, 4)
        lay.setSpacing(6)

        self._cards: dict[str, _WeaponCard] = {}
        for slot, label in [
            ("primary_1", "PRIMARY 1"),
            ("primary_2", "PRIMARY 2"),
            ("sidearm",   "SIDEARM"),
            ("utility",   "UTILITY"),
            ("utility_2", "MELEE"),
        ]:
            card = _WeaponCard(label)
            self._cards[slot] = card
            lay.addWidget(card)

        if self._orientation == "horizontal":
            lay.addStretch(1)

        self._weapon_area = area
        return area

    def _build_consumable_bar(self) -> QWidget:
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {BORDER};")

        self._consumable_bar = _ConsumableBar()

        wrapper = QWidget()
        wlay = QVBoxLayout(wrapper)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(0)
        wlay.addWidget(sep)
        wlay.addWidget(self._consumable_bar)
        return wrapper

    # ── State update (called from any thread via signal) ──────────────────────

    def push_state(self, state: LoadoutState) -> None:
        """Thread-safe: post state to main thread."""
        self._state_ready.emit(state)

    def _apply_state(self, state: LoadoutState) -> None:
        for slot, card in self._cards.items():
            card.update_weapon(state.weapons.get(slot))
        self._consumable_bar.update_consumables(state)
        self.adjustSize()

    # ── Orientation switch ────────────────────────────────────────────────────

    def set_orientation(self, orientation: str) -> None:
        if orientation == self._orientation:
            return
        self._orientation = orientation
        # Rebuild weapon area in-place
        old = self._weapon_area
        new = self._build_weapon_area()
        root_lay = self._container.layout()
        idx = root_lay.indexOf(old)
        root_lay.insertWidget(idx, new)
        root_lay.removeWidget(old)
        old.deleteLater()
        self.adjustSize()

    # ── Drag-to-move ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._titlebar.geometry().contains(event.position().toPoint()):
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.pos()
            self._titlebar.setCursor(QCursor(Qt.ClosedHandCursor))
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
            self._titlebar.setCursor(QCursor(Qt.OpenHandCursor))
            self._save_position()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def hideEvent(self, event):
        self._save_position()
        super().hideEvent(event)

    def _save_position(self) -> None:
        """Persist current window position to settings (debounced)."""
        pos = self.pos()
        self._schedule_save({"window_x": pos.x(), "window_y": pos.y()})

    def _on_opacity_changed(self, value: int) -> None:
        """Update window opacity from slider and persist (debounced)."""
        opacity = value / 100.0
        self.setWindowOpacity(opacity)
        self._schedule_save({"opacity": opacity})

    def _schedule_save(self, updates: dict) -> None:
        """Merge updates into pending save and restart debounce timer."""
        if self._pending_save is None:
            self._pending_save = load_settings()
        self._pending_save.update(updates)
        self._save_timer.start()

    def _flush_save(self) -> None:
        """Write pending settings to disk."""
        if self._pending_save is not None:
            save_settings(self._pending_save)
            self._pending_save = None
