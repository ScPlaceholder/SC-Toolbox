"""Main application window for Mining Signals."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading

from PySide6.QtCore import Qt, QTimer, Signal, QObject, Slot, QMetaObject, Q_ARG, Qt as QtConst
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QPushButton, QLineEdit, QHeaderView,
)

from shared.qt.theme import P, apply_theme
from shared.qt.base_window import SCWindow
from shared.qt.title_bar import SCTitleBar
from shared.qt.data_table import SCTable, ColumnDef, SCTableModel
from shared.qt.ipc_thread import IPCWatcher
from shared.platform_utils import set_dpi_awareness
from shared.data_utils import parse_cli_args

from services.sheet_fetcher import SheetFetcher
from services.signal_matcher import SignalMatcher, SignalMatch
from ocr.screen_reader import is_ocr_available, scan_region, tesseract_status

from .scan_bubble import ScanBubble
from .region_selector import RegionSelector
from .display_placer import DisplayPlacer
from .tutorial_popup import TutorialPopup

log = logging.getLogger(__name__)

ACCENT = "#33dd88"
_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mining_signals_config.json",
)

# Rarity tier colours
RARITY_FG: dict[str, str] = {
    "Common": "#8cc63f",
    "Uncommon": "#00bcd4",
    "Rare": "#ffc107",
    "Epic": "#aa66ff",
    "Legendary": "#ff9800",
    "ROC": "#33ccdd",
    "FPS": "#44aaff",
    "Salvage": "#66ccff",
}


def _load_config() -> dict:
    try:
        if os.path.isfile(_CONFIG_FILE):
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "refresh_interval_minutes": 60,
        "scan_interval_seconds": 3,
        "ocr_region": None,
    }


def _save_config(cfg: dict) -> None:
    try:
        tmp = _CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, _CONFIG_FILE)
    except OSError as exc:
        log.warning("Failed to save config: %s", exc)


class _DataLoader(QObject):
    """Loads sheet data in a background thread."""

    data_ready = Signal(list)   # list[dict] of rows
    error = Signal(str)

    def __init__(self, fetcher: SheetFetcher, parent=None) -> None:
        super().__init__(parent)
        self._fetcher = fetcher

    def load(self, force: bool = False) -> None:
        def _run():
            result = self._fetcher.load(force_refresh=force)
            if result.ok:
                self.data_ready.emit(result.data)
            else:
                self.error.emit(result.error or "Unknown error")
        threading.Thread(target=_run, daemon=True).start()


class MiningSignalsApp(SCWindow):
    """Mining Signals tool — reference table + OCR scanner."""

    _scan_value_ready = Signal(int)  # emitted from bg thread, handled on main thread

    def __init__(
        self,
        x: int = 100, y: int = 100,
        w: int = 500, h: int = 550,
        opacity: float = 0.95,
        cmd_file: str | None = None,
    ) -> None:
        super().__init__(
            title="Mining Signals",
            width=w, height=h,
            min_w=350, min_h=250,
            opacity=opacity,
            always_on_top=True,
            accent=ACCENT,
        )
        self.move(x, y)

        self._config = _load_config()
        self._cmd_file = cmd_file
        self._rows: list[dict] = []
        self._matcher = SignalMatcher([])
        self._scan_timer: QTimer | None = None
        self._scan_bubble = ScanBubble()

        # Consecutive-match consensus: require 2 agreeing reads before showing
        self._last_ocr_value: int | None = None
        self._confirmed_value: int | None = None

        # Services
        self._fetcher = SheetFetcher(
            ttl=self._config.get("refresh_interval_minutes", 60) * 60,
        )
        self._loader = _DataLoader(self._fetcher, self)
        self._loader.data_ready.connect(self._on_data_loaded)
        self._loader.error.connect(self._on_data_error)

        self._scan_value_ready.connect(self._on_scan_result)

        self._build_ui()
        self._setup_ipc()

        # Initial data load
        self._loader.load()

        # Auto-refresh timer
        refresh_ms = self._config.get("refresh_interval_minutes", 60) * 60 * 1000
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(lambda: self._loader.load(force=True))
        self._refresh_timer.start(refresh_ms)

    def _build_ui(self) -> None:
        layout = self.content_layout

        # ── Title bar ──
        self._title_bar = SCTitleBar(
            self,
            title="Mining Signals",
            icon_text="",
            accent_color=ACCENT,
            hotkey_text="Shift+9",
            extra_buttons=[("Tutorial", self._show_tutorial)],
        )
        self._title_bar.minimize_clicked.connect(self.showMinimized)
        self._title_bar.close_clicked.connect(self.user_close)
        layout.addWidget(self._title_bar)

        # ── Search bar ──
        self._search_row = QWidget(self)
        search_layout = QHBoxLayout(self._search_row)
        search_layout.setContentsMargins(8, 4, 8, 2)
        search_layout.setSpacing(6)

        search_icon = QLabel("\U0001f50d", self._search_row)
        search_icon.setStyleSheet(f"font-size: 9pt; color: {P.fg_dim}; background: transparent;")
        search_layout.addWidget(search_icon)

        self._search_input = QLineEdit(self._search_row)
        self._search_input.setPlaceholderText("Signal value...")
        self._search_input.textChanged.connect(self._on_search)
        search_layout.addWidget(self._search_input, 1)

        self._search_result = QLabel("", self._search_row)
        self._search_result.setStyleSheet(f"""
            font-family: Electrolize, Consolas, monospace;
            font-size: 9pt; font-weight: bold;
            color: {P.fg_dim}; background: transparent;
        """)
        search_layout.addWidget(self._search_result)

        layout.addWidget(self._search_row)

        # ── OCR controls row 1: scan buttons ──
        self._ocr_row = QWidget(self)
        ocr_layout = QHBoxLayout(self._ocr_row)
        ocr_layout.setContentsMargins(8, 2, 8, 2)
        ocr_layout.setSpacing(6)

        _btn_style = f"""
            QPushButton {{
                font-family: Consolas, monospace;
                font-size: 8pt; font-weight: bold;
                color: {ACCENT}; background: transparent;
                border: 1px solid {ACCENT}; border-radius: 3px;
                padding: 3px 8px;
            }}
            QPushButton:hover {{ background: rgba(51, 221, 136, 0.15); }}
        """

        self._btn_set_region = QPushButton("Set Region", self._ocr_row)
        self._btn_set_region.setCursor(Qt.PointingHandCursor)
        self._btn_set_region.setToolTip("Select screen area where the mining scanner number appears")
        self._btn_set_region.clicked.connect(self._on_set_region)
        self._btn_set_region.setStyleSheet(_btn_style)
        ocr_layout.addWidget(self._btn_set_region)

        self._btn_scan_toggle = QPushButton("Start Scan", self._ocr_row)
        self._btn_scan_toggle.setCursor(Qt.PointingHandCursor)
        self._btn_scan_toggle.setCheckable(True)
        self._btn_scan_toggle.clicked.connect(self._on_scan_toggle)
        self._btn_scan_toggle.setStyleSheet(f"""
            QPushButton {{
                font-family: Consolas, monospace;
                font-size: 8pt; font-weight: bold;
                color: {P.fg}; background: transparent;
                border: 1px solid {P.border}; border-radius: 3px;
                padding: 3px 8px;
            }}
            QPushButton:hover {{ background: rgba(51, 221, 136, 0.15); border-color: {ACCENT}; }}
            QPushButton:checked {{
                color: {P.bg_primary}; background: {ACCENT};
                border-color: {ACCENT};
            }}
        """)
        ocr_layout.addWidget(self._btn_scan_toggle)

        # Inline scan result — primary display, always visible
        self._inline_result = QLabel("", self._ocr_row)
        self._inline_result.setStyleSheet(f"""
            font-family: Electrolize, Consolas, monospace;
            font-size: 11pt; font-weight: bold;
            color: {ACCENT}; background: transparent;
            padding: 0 6px;
        """)
        ocr_layout.addWidget(self._inline_result)

        self._hotkey_hint = QLabel("Shift+9 to hide", self._ocr_row)
        self._hotkey_hint.setStyleSheet(f"""
            font-family: Consolas, monospace;
            font-size: 7pt; color: {P.fg_dim};
            background: transparent;
        """)
        ocr_layout.addWidget(self._hotkey_hint)

        self._btn_set_display = QPushButton("Set Mining Output Display Location", self._ocr_row)
        self._btn_set_display.setCursor(Qt.PointingHandCursor)
        self._btn_set_display.setToolTip("Choose where the result bubble appears on screen")
        self._btn_set_display.clicked.connect(self._on_set_display)
        self._btn_set_display.setStyleSheet(_btn_style)
        ocr_layout.addWidget(self._btn_set_display)

        ocr_layout.addStretch(1)

        self._ocr_status = QLabel("", self._ocr_row)
        self._ocr_status.setStyleSheet(f"font-size: 8pt; color: {P.fg_dim}; background: transparent;")
        ocr_layout.addWidget(self._ocr_status)

        layout.addWidget(self._ocr_row)

        # ── Scan hint (shown during scanning) ──
        self._scan_hint = QLabel(
            "Results can take several seconds to scan. Please stay on target and await results.",
            self,
        )
        self._scan_hint.setStyleSheet(f"""
            font-family: Consolas, monospace;
            font-size: 8pt; font-weight: bold;
            color: {P.fg_bright}; background: transparent;
            padding: 2px 8px;
        """)
        self._scan_hint.setWordWrap(True)
        self._scan_hint.setVisible(False)
        layout.addWidget(self._scan_hint)

        # ── Status bar ──
        self._status_row = QWidget(self)
        status_layout = QHBoxLayout(self._status_row)
        status_layout.setContentsMargins(8, 0, 8, 2)
        self._status_label = QLabel("Loading...", self._status_row)
        self._status_label.setStyleSheet(f"font-size: 8pt; color: {P.fg_dim}; background: transparent;")
        status_layout.addStretch(1)
        status_layout.addWidget(self._status_label)
        layout.addWidget(self._status_row)

        # ── Separator ──
        self._separator = QFrame(self)
        self._separator.setFrameShape(QFrame.HLine)
        self._separator.setFixedHeight(1)
        self._separator.setStyleSheet(f"background-color: {P.border};")
        layout.addWidget(self._separator)

        # ── Signal table ──
        self._table = SCTable(
            columns=[
                ColumnDef("Resource", "name", width=75, fg_color=P.fg_bright),
                ColumnDef("Rarity", "rarity", width=58),
                ColumnDef("1", "1", width=44, alignment=Qt.AlignRight, fg_color=P.fg),
                ColumnDef("2", "2", width=44, alignment=Qt.AlignRight, fg_color=P.fg),
                ColumnDef("3", "3", width=44, alignment=Qt.AlignRight, fg_color=P.fg),
                ColumnDef("4", "4", width=44, alignment=Qt.AlignRight, fg_color=ACCENT),
                ColumnDef("5", "5", width=44, alignment=Qt.AlignRight, fg_color=ACCENT),
                ColumnDef("6", "6", width=44, alignment=Qt.AlignRight, fg_color=ACCENT),
            ],
            parent=self,
            sortable=True,
        )
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self._table, 1)

        # Widgets to hide when scan is active
        # (keep Set Region, scan toggle, and hotkey hint visible)
        self._expanded_widgets = [
            self._search_row, self._status_row,
            self._separator, self._table,
            self._btn_set_display, self._ocr_status,
        ]

        # Update OCR status
        self._update_ocr_status()

    def _setup_ipc(self) -> None:
        """Set up IPC polling for launcher commands."""
        if self._cmd_file:
            self._ipc = IPCWatcher(self._cmd_file, parent=self)
            self._ipc.command_received.connect(self._on_ipc_command)
            self._ipc.start()

    def _on_ipc_command(self, cmd: dict) -> None:
        cmd_type = cmd.get("type", "")
        if cmd_type == "show":
            self.show()
            self.raise_()
        elif cmd_type == "hide":
            self.hide()
        elif cmd_type == "toggle":
            self.toggle_visibility()
        elif cmd_type == "quit":
            QApplication.instance().quit()
        else:
            self.handle_ipc_command(cmd)

    # ── Data loading ──

    def _on_data_loaded(self, rows: list[dict]) -> None:
        self._rows = rows
        self._matcher.update(rows)

        # Build table data with rarity-aware formatting
        table_data: list[dict] = []
        for row in rows:
            entry = dict(row)
            # Store numeric values for sorting
            for col in ("1", "2", "3", "4", "5", "6"):
                entry[col] = int(entry.get(col, 0))
            table_data.append(entry)

        self._table.set_data(table_data)
        self._status_label.setText(f"{len(rows)} resources loaded")
        log.info("UI: loaded %d resources", len(rows))

    def _on_data_error(self, msg: str) -> None:
        self._status_label.setText(f"Error: {msg}")
        log.warning("UI: data load error: %s", msg)

    # ── Search / manual lookup ──

    def _on_search(self, text: str) -> None:
        text = text.strip()
        if not text:
            self._search_result.setText("")
            self._search_result.setStyleSheet(f"""
                font-family: Electrolize, Consolas, monospace;
                font-size: 10pt; font-weight: bold;
                color: {P.fg_dim}; background: transparent; padding: 0 8px;
            """)
            return

        try:
            value = int(text)
        except ValueError:
            self._search_result.setText("Enter a number")
            return

        matches = self._matcher.match_all(value, tolerance=25)
        if matches:
            parts = []
            for m in matches:
                rock_word = "Rock" if m.rock_count == 1 else "Rocks"
                parts.append(f"{m.name} ({m.rock_count}{rock_word[0]})")
            color = RARITY_FG.get(matches[0].rarity, P.fg)
            self._search_result.setText("  |  ".join(parts))
            self._search_result.setStyleSheet(f"""
                font-family: Electrolize, Consolas, monospace;
                font-size: 9pt; font-weight: bold;
                color: {color}; background: transparent;
            """)
        else:
            self._search_result.setText("No match")
            self._search_result.setStyleSheet(f"""
                font-family: Electrolize, Consolas, monospace;
                font-size: 10pt; font-weight: bold;
                color: {P.red}; background: transparent; padding: 0 8px;
            """)

    # ── OCR scanning ──

    def _update_ocr_status(self) -> None:
        region = self._config.get("ocr_region")
        status = tesseract_status()
        if status != "Ready":
            self._ocr_status.setText(status)
            # Still allow scan toggle — Tesseract will auto-download on first scan
            self._btn_scan_toggle.setEnabled(region is not None)
        elif not region:
            self._ocr_status.setText("No scan region set")
            self._btn_scan_toggle.setEnabled(False)
        else:
            self._ocr_status.setText(
                f"Region: {region['x']},{region['y']} "
                f"{region['w']}x{region['h']}"
            )
            self._btn_scan_toggle.setEnabled(True)

    def _show_tutorial(self) -> None:
        self._tutorial = TutorialPopup(self)
        self._tutorial.show()

    def _on_set_region(self) -> None:
        self._region_selector = RegionSelector()
        self._region_selector.region_selected.connect(self._on_region_selected)
        self._region_selector.show()

    def _on_region_selected(self, region: dict) -> None:
        self._config["ocr_region"] = region
        _save_config(self._config)
        self._update_ocr_status()
        log.info("OCR region set: %s", region)

    def _on_set_display(self) -> None:
        self._display_placer = DisplayPlacer()
        self._display_placer.position_selected.connect(self._on_display_selected)
        self._display_placer.show()

    def _on_display_selected(self, pos: dict) -> None:
        self._config["bubble_position"] = pos
        _save_config(self._config)
        log.info("Bubble display position set: (%d, %d)", pos["x"], pos["y"])

    def _on_scan_toggle(self, checked: bool) -> None:
        if checked:
            # Save expanded size before collapsing
            self._expanded_size = (self.width(), self.height())
            self._btn_scan_toggle.setText("Stop Scan")

            # Hide everything except title bar and the scan toggle row
            for w in self._expanded_widgets:
                w.setVisible(False)

            # Shrink window to just title bar + scan controls + inline result + hint
            self.setMinimumHeight(110)
            self.resize(self.width(), 110)

            # Reset consensus state
            self._last_ocr_value = None
            self._confirmed_value = None
            self._inline_result.setText("")
            self._scan_hint.setVisible(True)

            # Start scanning
            interval = self._config.get("scan_interval_seconds", 1) * 1000
            self._scan_timer = QTimer(self)
            self._scan_timer.timeout.connect(self._do_scan)
            self._scan_timer.start(interval)
            self._do_scan()  # immediate first scan
        else:
            self._btn_scan_toggle.setText("Start Scan")
            if self._scan_timer:
                self._scan_timer.stop()
                self._scan_timer = None

            self._scan_hint.setVisible(False)

            # Restore expanded view
            for w in self._expanded_widgets:
                w.setVisible(True)

            self.setMinimumHeight(300)
            if hasattr(self, "_expanded_size"):
                self.resize(*self._expanded_size)

    def _do_scan(self) -> None:
        region = self._config.get("ocr_region")
        if not region:
            return

        def _run():
            value = scan_region(region)
            if value is not None:
                QMetaObject.invokeMethod(
                    self, "_on_scan_result",
                    Qt.QueuedConnection,
                    Q_ARG(int, value),
                )

        threading.Thread(target=_run, daemon=True).start()

    @Slot(int)
    def _on_scan_result(self, value: int) -> None:
        # Fuzzy consecutive-match: accept if two reads are within 5% of each other.
        # This handles minor OCR variation while filtering icon misreads.
        if self._last_ocr_value is not None:
            diff = abs(value - self._last_ocr_value)
            threshold = max(50, int(self._last_ocr_value * 0.05))
            if diff <= threshold:
                # Two reads agree (within tolerance) — use the average
                confirmed = (value + self._last_ocr_value) // 2
                self._last_ocr_value = value
                if confirmed == self._confirmed_value:
                    # Same value — refresh the bubble to prevent fade-out
                    self._scan_bubble._fade_timer.start()
                    return
                self._confirmed_value = confirmed
                value = confirmed
                log.info("Confirmed: %d", value)
            else:
                # Reads disagree — store new value and wait
                self._last_ocr_value = value
                log.debug("Pending: %d (prev was %d, diff=%d)", value, self._last_ocr_value, diff)
                return
        else:
            self._last_ocr_value = value
            log.debug("First read: %d", value)
            return

        self._search_input.setText(str(value))
        matches = self._matcher.match_all(value, tolerance=25)
        if matches:
            log.info("Matched %d result(s) for %d", len(matches), value)

            # Update inline result label (always visible)
            parts = []
            for m in matches:
                rock_word = "R" if m.rock_count == 1 else "R"
                parts.append(f"{m.name} ({m.rock_count}{rock_word})")
            color = RARITY_FG.get(matches[0].rarity, ACCENT)
            inline_text = " | ".join(parts)
            self._inline_result.setText(inline_text)
            self._inline_result.setStyleSheet(f"""
                font-family: Electrolize, Consolas, monospace;
                font-size: 9pt; font-weight: bold;
                color: {color}; background: transparent;
            """)

            # Also show floating bubble
            bubble_pos = self._config.get("bubble_position")
            if bubble_pos:
                anchor_x = bubble_pos["x"]
                anchor_y = bubble_pos["y"]
            else:
                region = self._config.get("ocr_region", {})
                anchor_x = region.get("x", 500) + region.get("w", 200) + 10
                anchor_y = region.get("y", 400)
            try:
                self._scan_bubble.show_matches(matches, anchor_x, anchor_y)
            except Exception as exc:
                log.error("Bubble show_matches failed: %s", exc, exc_info=True)
        else:
            self._inline_result.setText("")
            log.debug("No match for confirmed value %d", value)

    def closeEvent(self, event) -> None:
        if self._scan_timer:
            self._scan_timer.stop()
        self._scan_bubble.hide()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Entry-point helper
# ---------------------------------------------------------------------------

def main() -> None:
    """Launch Mining Signals from the command line."""
    from shared.crash_logger import init_crash_logging
    log = init_crash_logging("mining_signals")
    try:
        set_dpi_awareness()

        parsed = parse_cli_args(sys.argv[1:], {"w": 500, "h": 550})

        app = QApplication(sys.argv)
        apply_theme(app)

        window = MiningSignalsApp(
            x=parsed["x"],
            y=parsed["y"],
            w=parsed["w"],
            h=parsed["h"],
            opacity=parsed["opacity"],
            cmd_file=parsed["cmd_file"],
        )
        window.show()
        window.raise_()
        window.activateWindow()
        sys.exit(app.exec())
    except Exception:
        log.critical("FATAL crash in mining_signals main()", exc_info=True)
        sys.exit(1)
