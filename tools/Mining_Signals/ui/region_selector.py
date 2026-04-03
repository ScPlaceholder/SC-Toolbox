"""Fullscreen overlay for selecting the OCR capture region.

The user clicks and drags to define a rectangle on screen.  The
selected region is returned via a Qt signal.  Coordinates are
in native screen pixels (matching what ``mss`` captures).
"""

from __future__ import annotations

import ctypes

from PySide6.QtCore import Qt, QRect, QPoint, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QCursor
from PySide6.QtWidgets import QWidget, QApplication

from shared.qt.theme import P


def _get_cursor_pos() -> tuple[int, int]:
    """Get the cursor position in native screen pixels via Win32 API.

    This bypasses any Qt coordinate scaling and gives raw pixel
    coordinates that match what ``mss`` captures.
    """
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)
    except Exception:
        pos = QCursor.pos()
        return (pos.x(), pos.y())


class RegionSelector(QWidget):
    """Translucent fullscreen overlay — drag to select a rectangle."""

    region_selected = Signal(dict)  # {"x": int, "y": int, "w": int, "h": int}
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

        # Cover the entire virtual desktop (all monitors)
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.virtualGeometry()
            self.setGeometry(geom)
        else:
            self.showFullScreen()

        # Store raw native pixel coordinates from Win32 API
        self._origin_native: tuple[int, int] | None = None
        self._current_native: tuple[int, int] | None = None

        # Visual rect in widget coordinates (for painting)
        self._rect: QRect = QRect()
        self._dragging = False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)

        # Semi-transparent dark overlay
        overlay = QColor(0, 0, 0, 140)
        painter.fillRect(self.rect(), overlay)

        if not self._rect.isNull():
            # Clear the selected region (punch a hole)
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(self._rect, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

            # Draw selection border
            accent = QColor(P.green)
            accent.setAlpha(220)
            painter.setPen(QPen(accent, 2))
            painter.setBrush(QBrush(QColor(51, 221, 136, 30)))
            painter.drawRect(self._rect)

            # Draw size label with native pixel dimensions
            if self._origin_native and self._current_native:
                nx = min(self._origin_native[0], self._current_native[0])
                ny = min(self._origin_native[1], self._current_native[1])
                nw = abs(self._current_native[0] - self._origin_native[0])
                nh = abs(self._current_native[1] - self._origin_native[1])
                label = f"{nw} x {nh}  @ ({nx}, {ny})"
            else:
                label = f"{self._rect.width()} x {self._rect.height()}"
            painter.setPen(QColor(P.fg_bright))
            painter.drawText(
                self._rect.x(), self._rect.y() - 6, label,
            )

        # Instruction text
        painter.setPen(QColor(P.fg_bright))
        painter.drawText(
            self.rect().center().x() - 180,
            40,
            "Click and drag to select the scanner region. Press ESC to cancel.",
        )

        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._origin_native = _get_cursor_pos()
            self._rect = QRect(
                event.position().toPoint(),
                event.position().toPoint(),
            )
            self._dragging = True
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._current_native = _get_cursor_pos()
            origin = self._rect.topLeft()
            # Update visual rect for painting
            self._rect = QRect(
                origin, event.position().toPoint(),
            ).normalized()
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            end_native = _get_cursor_pos()

            if self._origin_native:
                ox, oy = self._origin_native
                ex, ey = end_native
                x = min(ox, ex)
                y = min(oy, ey)
                w = abs(ex - ox)
                h = abs(ey - oy)

                if w > 10 and h > 10:
                    self.region_selected.emit({
                        "x": x, "y": y, "w": w, "h": h,
                    })
            self.close()
            event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()
            event.accept()
