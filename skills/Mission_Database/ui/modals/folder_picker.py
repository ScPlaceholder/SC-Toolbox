"""Folder picker modal — tree view for selecting a destination folder."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QInputDialog,
)

from shared.qt.theme import P
from ui.modals.base import ModalBase


class FolderPickerModal(ModalBase):
    """Tree-view dialog for choosing a target folder."""

    folder_selected = Signal(str)

    def __init__(self, parent, inventory, exclude_path: str | None = None):
        self._inventory = inventory
        self._exclude = exclude_path
        self._selected: str = "/"
        super().__init__(parent, title="Move to Folder", width=380, height=400)
        self._build_ui()
        self.show()

    def _build_ui(self):
        layout = self.body_layout

        # Close button
        close_row = QHBoxLayout()
        close_row.setContentsMargins(0, 4, 4, 0)
        close_row.addStretch(1)
        close_btn = QPushButton("x")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {P.fg_dim}; border: none;"
            f" font-family: Consolas; font-size: 13pt; font-weight: bold; }}"
            f"QPushButton:hover {{ color: {P.red}; }}"
        )
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        hint = QLabel("Select a destination folder:")
        hint.setStyleSheet(
            f"font-family: Consolas; font-size: 9pt; color: {P.fg_dim};"
            f" background: transparent; padding: 4px 16px;"
        )
        layout.addWidget(hint)

        # Scrollable tree area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {P.bg_primary}; }}")
        inner = QWidget()
        inner.setStyleSheet(f"background: {P.bg_primary};")
        self._tree_layout = QVBoxLayout(inner)
        self._tree_layout.setContentsMargins(8, 4, 8, 4)
        self._tree_layout.setSpacing(2)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        self._btn_widgets: list[QPushButton] = []
        self._populate_tree()

        # Bottom buttons
        bottom = QHBoxLayout()
        bottom.setContentsMargins(16, 8, 16, 8)
        bottom.setSpacing(8)

        new_btn = QPushButton("+ New Folder")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.setStyleSheet(
            f"QPushButton {{ background: {P.bg_card}; color: {P.fg};"
            f" border: 1px solid {P.accent}; border-radius: 3px;"
            f" padding: 4px 10px; font-family: Consolas; font-size: 8pt; }}"
            f"QPushButton:hover {{ color: {P.accent}; }}"
        )
        new_btn.clicked.connect(self._new_folder)
        bottom.addWidget(new_btn)
        bottom.addStretch(1)

        confirm_btn = QPushButton("Move Here")
        confirm_btn.setCursor(Qt.PointingHandCursor)
        confirm_btn.setStyleSheet(
            f"QPushButton {{ background: {P.accent}; color: {P.bg_primary};"
            f" border: none; border-radius: 3px;"
            f" padding: 5px 16px; font-family: Consolas; font-size: 9pt; font-weight: bold; }}"
            f"QPushButton:hover {{ background: #55bbff; }}"
        )
        confirm_btn.clicked.connect(self._confirm)
        bottom.addWidget(confirm_btn)

        layout.addLayout(bottom)

    def _populate_tree(self):
        # Clear
        while self._tree_layout.count():
            item = self._tree_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._btn_widgets.clear()

        tree = self._inventory.get_folder_tree()
        self._add_folder_row("/", tree, 0)
        self._tree_layout.addStretch(1)

    def _add_folder_row(self, path: str, tree: dict, depth: int):
        if self._exclude and path == self._exclude:
            return
        fdata = tree.get(path, {})
        name = fdata.get("name", path)
        bp_count = len(fdata.get("blueprints", []))

        btn = QPushButton(f"\U0001f4c1  {name}  ({bp_count})")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(self._row_style(path == self._selected))
        btn.setContentsMargins(depth * 20, 0, 0, 0)

        # Indent via layout margin trick
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(depth * 20 + 8, 0, 8, 0)
        rl.setSpacing(0)
        rl.addWidget(btn, 1)

        p = path
        btn.clicked.connect(lambda checked=False, _p=p: self._select(_p))
        self._tree_layout.addWidget(row)
        self._btn_widgets.append(btn)

        for child_name in fdata.get("children", []):
            child_path = self._inventory.child_folder_path(path, child_name)
            self._add_folder_row(child_path, tree, depth + 1)

    def _row_style(self, selected: bool) -> str:
        if selected:
            return (
                f"QPushButton {{ background: #1a3030; color: {P.accent};"
                f" border: 1px solid {P.accent}; border-radius: 3px;"
                f" padding: 4px 8px; font-family: Consolas; font-size: 9pt;"
                f" text-align: left; }}"
            )
        return (
            f"QPushButton {{ background: {P.bg_card}; color: {P.fg};"
            f" border: 1px solid {P.border}; border-radius: 3px;"
            f" padding: 4px 8px; font-family: Consolas; font-size: 9pt;"
            f" text-align: left; }}"
            f"QPushButton:hover {{ border-color: {P.accent}; color: {P.accent}; }}"
        )

    def _select(self, path: str):
        self._selected = path
        self._populate_tree()

    def _confirm(self):
        self.folder_selected.emit(self._selected)
        self.close()

    def _new_folder(self):
        name, ok = QInputDialog.getText(
            self, "New Folder", "Folder name:",
            text="New Folder",
        )
        if not ok or not name or not name.strip():
            return
        try:
            new_path = self._inventory.create_folder(self._selected, name.strip())
            self._selected = new_path
            self._populate_tree()
        except ValueError as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Error", str(e))
