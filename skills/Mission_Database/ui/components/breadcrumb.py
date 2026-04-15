"""Breadcrumb navigation bar for folder paths with drag-and-drop support."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel

from shared.qt.theme import P

log = logging.getLogger(__name__)

_MIME_TYPE = "application/x-sctoolbox-card"

_SEG_NORMAL = (
    f"font-family: Consolas; font-size: 9pt; color: {P.fg_dim};"
    f" background: transparent; padding: 0 4px;"
)
_SEG_ACTIVE = (
    f"font-family: Consolas; font-size: 9pt; font-weight: bold;"
    f" color: {P.accent}; background: transparent; padding: 0 4px;"
)
_SEG_DROP_HOVER = (
    f"font-family: Consolas; font-size: 9pt; font-weight: bold;"
    f" color: {P.accent}; background: #1a3030; padding: 0 4px;"
    f" border: 1px solid {P.accent}; border-radius: 3px;"
)


class _BreadcrumbSegment(QLabel):
    """Single breadcrumb segment that accepts drops."""

    clicked = Signal(str)
    dropped = Signal(object, str)  # (dragged_item, folder_path)

    def __init__(self, name: str, path: str, is_last: bool, parent=None):
        super().__init__(name, parent)
        self._path = path
        self._is_last = is_last
        self.setCursor(Qt.PointingHandCursor)
        self.setAcceptDrops(True)
        self._reset_style()

    def _reset_style(self):
        self.setStyleSheet(_SEG_ACTIVE if self._is_last else _SEG_NORMAL)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._path)
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(_MIME_TYPE):
            self.setStyleSheet(_SEG_DROP_HOVER)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._reset_style()
        event.accept()

    def dropEvent(self, event):
        self._reset_style()
        if not event.mimeData().hasFormat(_MIME_TYPE):
            event.ignore()
            return
        source = event.source()
        dragged_item = getattr(source, '_press_data', None) if source else None
        if dragged_item is not None:
            self.dropped.emit(dragged_item, self._path)
            event.acceptProposedAction()
        else:
            event.ignore()


class BreadcrumbBar(QWidget):
    """Clickable breadcrumb path — emits signals for click and drag-drop."""

    folder_clicked = Signal(str)
    # Emitted when a card is dropped onto a breadcrumb segment
    item_dropped_on_folder = Signal(object, str)  # (dragged_item, folder_path)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = "/"
        self.setFixedHeight(28)
        self.setStyleSheet(f"background: transparent;")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 2, 12, 2)
        self._layout.setSpacing(0)
        self._build("/")

    def set_path(self, folder_path: str) -> None:
        self._path = folder_path
        self._build(folder_path)

    def current_path(self) -> str:
        return self._path

    def _build(self, folder_path: str):
        # Clear existing
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        parts = [("Root", "/")]
        if folder_path != "/":
            segments = folder_path.strip("/").split("/")
            for i, seg in enumerate(segments):
                path = "/" + "/".join(segments[: i + 1])
                parts.append((seg, path))

        for idx, (name, path) in enumerate(parts):
            is_last = idx == len(parts) - 1
            seg = _BreadcrumbSegment(name, path, is_last)
            seg.clicked.connect(self.folder_clicked.emit)
            seg.dropped.connect(self.item_dropped_on_folder.emit)
            self._layout.addWidget(seg)

            if not is_last:
                sep = QLabel(" \u203a ")
                sep.setStyleSheet(
                    f"font-family: Consolas; font-size: 9pt; color: {P.fg_disabled};"
                    f" background: transparent;"
                )
                self._layout.addWidget(sep)

        self._layout.addStretch(1)
