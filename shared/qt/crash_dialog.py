"""
Crash log viewer dialog.

Shown automatically when a skill subprocess dies unexpectedly or when the
launcher itself crashes.  Displays the last N lines of the relevant log file
with Copy-to-Clipboard and Open-Folder buttons so the user can easily share
the log when reporting a bug.
"""
from __future__ import annotations

import os
import subprocess
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton,
)

from shared.qt.theme import P

_TAIL_LINES = 400  # how many trailing lines to show


def _tail(path: str, n: int) -> str:
    """Return the last *n* lines of *path* as a single string."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except OSError as exc:
        return f"(could not read log file: {exc})"


class CrashLogDialog(QDialog):
    """Themed dialog that shows a log file with copy / open-folder actions."""

    def __init__(
        self,
        log_path: str,
        skill_name: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent, Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("SC Toolbox — Crash Report")
        self.setMinimumSize(720, 520)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._log_path = log_path

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {P.bg_primary};
                border: 2px solid {P.red};
            }}
            QLabel {{ background: transparent; }}
            QTextEdit {{
                background-color: {P.bg_deepest};
                color: {P.fg};
                font-family: Consolas, monospace;
                font-size: 8pt;
                border: 1px solid {P.border};
                selection-background-color: {P.selection};
                selection-color: {P.fg_bright};
            }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        # ── Header ───────────────────────────────────────────────────────
        label = (
            f"{skill_name.upper()} CRASHED"
            if skill_name else "CRASH DETECTED"
        )
        title = QLabel(f"\u26a0  {label}")
        title.setStyleSheet(f"""
            font-family: Electrolize, Consolas;
            font-size: 11pt; font-weight: bold;
            color: {P.red}; letter-spacing: 2px;
        """)
        lay.addWidget(title)

        path_lbl = QLabel(f"Log: {log_path}")
        path_lbl.setStyleSheet(
            f"font-family: Consolas; font-size: 7pt; color: {P.fg_dim};"
        )
        path_lbl.setWordWrap(True)
        lay.addWidget(path_lbl)

        hint = QLabel(
            "Copy the log below and share it when reporting a bug. "
            "The most recent entries are at the bottom."
        )
        hint.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; color: {P.fg_dim};"
        )
        lay.addWidget(hint)

        # ── Log content ──────────────────────────────────────────────────
        self._editor = QTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setPlainText(_tail(log_path, _TAIL_LINES))

        # Scroll to the bottom so the crash traceback is immediately visible
        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._editor.setTextCursor(cursor)

        lay.addWidget(self._editor, stretch=1)

        # ── Buttons ──────────────────────────────────────────────────────
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(8)

        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.setStyleSheet(_btn_qss(P.accent, P.bg_input))
        copy_btn.clicked.connect(self._copy)
        btn_lay.addWidget(copy_btn)

        folder_btn = QPushButton("Open Log Folder")
        folder_btn.setCursor(Qt.PointingHandCursor)
        folder_btn.setStyleSheet(_btn_qss(P.fg_dim, P.bg_card))
        folder_btn.clicked.connect(self._open_folder)
        btn_lay.addWidget(folder_btn)

        btn_lay.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(_btn_qss(P.red, P.bg_secondary))
        close_btn.clicked.connect(self.accept)
        btn_lay.addWidget(close_btn)

        lay.addLayout(btn_lay)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._editor.toPlainText())

    def _open_folder(self) -> None:
        folder = os.path.dirname(os.path.abspath(self._log_path))
        try:
            subprocess.Popen(["explorer", folder])
        except OSError:
            pass


def _btn_qss(color: str, bg: str) -> str:
    return f"""
        QPushButton {{
            background-color: {bg};
            color: {color};
            border: 1px solid {color};
            font-family: Consolas; font-size: 8pt; font-weight: bold;
            padding: 5px 14px;
        }}
        QPushButton:hover {{
            background-color: {color};
            color: {P.bg_primary};
        }}
    """


def show_crash_dialog(
    log_path: str,
    skill_name: str = "",
    parent=None,
    blocking: bool = False,
) -> None:
    """Create and display the crash log dialog.

    Parameters
    ----------
    log_path:
        Path to the log file to display.
    skill_name:
        Human-readable name used in the header (e.g. ``"Trade Hub"``).
    parent:
        Optional Qt parent widget.
    blocking:
        If True, runs a nested event loop via ``exec()`` — use this when
        called from ``sys.excepthook`` so the dialog stays open while
        the main event loop is unwinding.
    """
    try:
        dlg = CrashLogDialog(log_path, skill_name=skill_name, parent=parent)
        if blocking:
            dlg.exec()
        else:
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
    except Exception:
        pass  # never let the crash dialog itself crash the process
