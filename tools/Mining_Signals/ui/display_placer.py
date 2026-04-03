"""Click-to-place overlay for choosing the bubble display position.

Shows a preview bubble that follows the cursor.  Click to lock the
position, ESC to cancel.  Coordinates use Win32 GetCursorPos for
native pixel accuracy.
"""

from __future__ import annotations

import ctypes

from PySide6.QtCore import Qt, QPoint, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QCursor
from PySide6.QtWidgets import QWidget, QApplication, QLabel

from shared.qt.theme import P


def _get_cursor_pos() -> tuple[int, int]:
    """Get cursor position in native screen pixels via Win32."""
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)
    except Exception:
        pos = QCursor.pos()
        return (pos.x(), pos.y())


class DisplayPlacer(QWidget):
    """Fullscreen overlay — click to place the bubble display position."""

    position_selected = Signal(dict)  # {"x": int, "y": int}
    cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.CrossCursor)

        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.virtualGeometry()
            self.setGeometry(geom)
        else:
            self.showFullScreen()

        self._cursor_pos = QPoint(0, 0)

        # Track cursor for preview
        self._track_timer = QTimer(self)
        self._track_timer.setInterval(16)  # ~60fps
        self._track_timer.timeout.connect(self._track_cursor)
        self._track_timer.start()

    def _track_cursor(self) -> None:
        pos = QCursor.pos()
        if pos != self._cursor_pos:
            self._cursor_pos = pos
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)

        # Semi-transparent overlay
        overlay = QColor(0, 0, 0, 100)
        painter.fillRect(self.rect(), overlay)

        # Draw preview bubble at cursor position
        local = self.mapFromGlobal(self._cursor_pos)
        bx, by = local.x(), local.y()
        bw, bh = 260, 72

        # Bubble background
        bg = QColor(P.bg_primary)
        bg.setAlpha(220)
        painter.fillRect(bx, by, bw, bh, bg)

        # Bubble border
        accent = QColor(P.green)
        accent.setAlpha(200)
        painter.setPen(QPen(accent, 2))
        painter.drawRect(bx, by, bw, bh)

        # Preview text
        painter.setPen(QColor(P.green))
        painter.drawText(bx + 14, by + 30, "\u26cf RESOURCE NAME")
        painter.setPen(QColor(P.fg_dim))
        painter.drawText(bx + 14, by + 52, "Rarity  \u00b7  N Rocks")

        # Instruction
        painter.setPen(QColor(P.fg_bright))
        painter.drawText(
            self.rect().center().x() - 150,
            40,
            "Click to place the mining output display. Press ESC to cancel.",
        )

        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # Use Qt global coords so they match QWidget.move()
            global_pos = event.globalPosition().toPoint()
            self.position_selected.emit({"x": global_pos.x(), "y": global_pos.y()})
            self._track_timer.stop()
            self.close()
            event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._track_timer.stop()
            self.cancelled.emit()
            self.close()
            event.accept()
