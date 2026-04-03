"""Floating HUD bubble that shows the detected mining resource.

Small frameless always-on-top popup positioned near the scan region.
Auto-fades after a configurable duration, or stays until the next
scan updates it.
"""

from __future__ import annotations

import ctypes
import logging

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QLinearGradient, QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel

from shared.qt.theme import P
from services.signal_matcher import SignalMatch

log = logging.getLogger(__name__)

# Rarity → (accent color, label)
RARITY_COLORS: dict[str, str] = {
    "Common": "#8cc63f",
    "Uncommon": "#00bcd4",
    "Rare": "#ffc107",
    "Epic": "#aa66ff",
    "Legendary": "#ff9800",
}

_FADE_DURATION_MS = 4000  # stay visible for 4 seconds
_ANIMATION_MS = 300       # fade-out animation duration


class ScanBubble(QWidget):
    """Floating HUD popup showing a detected mining resource."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(260, 72)

        self._accent = QColor(P.green)
        self._match: SignalMatch | None = None

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        # Resource name (large)
        self._name_label = QLabel("", self)
        self._name_label.setStyleSheet(f"""
            font-family: Electrolize, Consolas, monospace;
            font-size: 14pt;
            font-weight: bold;
            color: {P.fg_bright};
            background: transparent;
        """)
        layout.addWidget(self._name_label)

        # Rarity + rock count (smaller)
        self._detail_label = QLabel("", self)
        self._detail_label.setStyleSheet(f"""
            font-family: Consolas, monospace;
            font-size: 9pt;
            color: {P.fg_dim};
            background: transparent;
        """)
        layout.addWidget(self._detail_label)

        # Auto-fade timer
        self._fade_timer = QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(self._start_fade_out)

    def show_match(self, match: SignalMatch, anchor_x: int, anchor_y: int) -> None:
        """Display a match result near the given screen position."""
        self._match = match
        accent = RARITY_COLORS.get(match.rarity, P.fg)
        self._accent = QColor(accent)

        self._name_label.setText(match.name)
        self._name_label.setStyleSheet(f"""
            font-family: Electrolize, Consolas, monospace;
            font-size: 14pt;
            font-weight: bold;
            color: {accent};
            background: transparent;
        """)

        rock_word = "Rock" if match.rock_count == 1 else "Rocks"
        delta_text = ""
        if match.delta > 0:
            delta_text = f"  (~{match.delta})"
        self._detail_label.setText(
            f"{match.rarity}  \u00b7  {match.rock_count} {rock_word}{delta_text}"
        )

        # Position at the user-chosen display location (Qt coords)
        self.move(anchor_x, anchor_y)
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        log.info("show_match: %s at (%d,%d) actual=(%d,%d)",
                 match.name, anchor_x, anchor_y, self.x(), self.y())

        # Reset fade timer
        self._fade_timer.stop()
        self._fade_timer.start(_FADE_DURATION_MS)
        self.update()

    def _start_fade_out(self) -> None:
        """Gradually fade the bubble out."""
        self._anim = QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(_ANIMATION_MS)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.OutQuad)
        self._anim.finished.connect(self.hide)
        self._anim.start()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()

        # Dark translucent background
        bg = QColor(P.bg_primary)
        bg.setAlpha(220)
        painter.fillRect(0, 0, w, h, bg)

        # Top glow gradient
        glow = QLinearGradient(0, 0, 0, 8)
        gc = QColor(self._accent)
        gc.setAlpha(60)
        glow.setColorAt(0.0, gc)
        gc2 = QColor(self._accent)
        gc2.setAlpha(0)
        glow.setColorAt(1.0, gc2)
        painter.fillRect(0, 0, w, 8, glow)

        # Border
        border = QColor(self._accent)
        border.setAlpha(140)
        painter.setPen(QPen(border, 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Glow bloom
        bloom = QColor(self._accent)
        bloom.setAlpha(20)
        painter.setPen(QPen(bloom, 3))
        painter.drawRect(1, 1, w - 3, h - 3)

        painter.end()
        super().paintEvent(event)
