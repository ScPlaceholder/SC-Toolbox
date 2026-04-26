"""Live glyph reader — visualizes what the OCR pipeline SEES vs READS.

Polls ``debug_glyphs/latest.json`` every 500 ms. The OCR pipeline
(``ocr/sc_ocr/api.py:_classify_crops``) writes one PNG per glyph
crop plus a JSON index whenever it runs. This viewer renders each
field's glyphs as a row of upscaled images with the classifier's
output and confidence stamped underneath, color-coded by confidence:

  GREEN   conf >= 0.85   — pipeline trusts this read
  YELLOW  0.50 <= conf   — borderline; downstream confidence-gate
                            may reject it
  RED     conf < 0.50    — low confidence, classifier is guessing

You watch this side-by-side with the actual game HUD to immediately
see which digits are being misread, which glyphs the segmenter
dropped, and whether the binarization step is producing clean crops
or garbage. It's the visual companion to the ``sc_ocr.diag`` log
lines — same data, but with the actual pixels.

Both classifier paths emit data:
  * ``primary``   — the strict-confidence-gated path that returns
                    immediately if ``min(conf) >= 0.85``
  * ``secondary`` — the parallel-vote path that runs alongside
                    Tesseract / CRNN

Run with::

    python scripts/glyph_reader_viewer.py

or via ``training_data_panels/LAUNCH_GlyphReader.bat``.

Cross-process single-instance: only one viewer at a time.
"""
from __future__ import annotations

import json
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


THIS = Path(__file__).resolve()
TOOL = THIS.parent.parent
sys.path.insert(0, str(TOOL))

GLYPH_DIR = TOOL / "debug_glyphs"
INDEX_PATH = GLYPH_DIR / "latest.json"

POLL_MS = 500
HISTORY_LEN = 8
GLYPH_DISPLAY_PX = 72  # render each 28x28 glyph at this size

# Theme — matches the rest of the toolbox tools.
ACCENT = "#33dd88"
WARN = "#ddc833"
DANGER = "#ff4444"
DIM = "#888888"
BG = "#1e1e1e"
PANEL_BG = "#2a2a2a"
FG = "#e0e0e0"


def _conf_color(conf: float) -> str:
    """Map a classifier confidence to a status color."""
    if conf >= 0.85:
        return ACCENT
    if conf >= 0.50:
        return WARN
    return DANGER


# ─────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────


class _GlyphTile(QWidget):
    """One glyph: upscaled crop image + classified char + confidence."""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(2)

        self._img = QLabel(self)
        self._img.setFixedSize(GLYPH_DISPLAY_PX, GLYPH_DISPLAY_PX)
        self._img.setAlignment(Qt.AlignCenter)
        self._img.setStyleSheet(
            f"background: #111; border: 1px solid {DIM};"
        )
        v.addWidget(self._img)

        self._char = QLabel("?", self)
        cf = QFont("Consolas")
        cf.setPointSize(14)
        cf.setBold(True)
        self._char.setFont(cf)
        self._char.setAlignment(Qt.AlignCenter)
        v.addWidget(self._char)

        self._conf = QLabel("0.00", self)
        sf = QFont("Consolas")
        sf.setPointSize(8)
        self._conf.setFont(sf)
        self._conf.setAlignment(Qt.AlignCenter)
        v.addWidget(self._conf)

        # Make the tile wide enough for the labels.
        self.setFixedWidth(GLYPH_DISPLAY_PX + 8)

    def update_glyph(self, img_path: Path, char: str, conf: float) -> None:
        try:
            pil = Image.open(img_path).convert("L")
            # Upscale with nearest-neighbour to preserve the
            # pixelated character of the 28x28 glyph (LANCZOS would
            # blur it and hide the actual classifier input).
            scaled = pil.resize(
                (GLYPH_DISPLAY_PX, GLYPH_DISPLAY_PX), Image.NEAREST,
            )
            self._img.setPixmap(QPixmap.fromImage(ImageQt(scaled.convert("RGB"))))
        except Exception:
            self._img.setText("(load fail)")
        color = _conf_color(conf)
        self._char.setText(char)
        self._char.setStyleSheet(
            f"color: {color}; background: transparent;"
        )
        self._conf.setText(f"{conf:.2f}")
        self._conf.setStyleSheet(
            f"color: {color}; background: transparent;"
        )

    def clear(self) -> None:
        self._img.clear()
        self._char.setText("·")
        self._char.setStyleSheet(f"color: {DIM}; background: transparent;")
        self._conf.setText("—")
        self._conf.setStyleSheet(f"color: {DIM}; background: transparent;")


class _FieldRow(QFrame):
    """One row showing a single (field, source) pair: header + glyph tiles."""

    def __init__(self, key_label: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"background: {PANEL_BG}; border-radius: 4px;"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(4)

        # Header: field name + joined string + timestamp
        header = QHBoxLayout()
        self._name = QLabel(key_label.upper(), self)
        nf = QFont("Consolas")
        nf.setPointSize(10)
        nf.setBold(True)
        self._name.setFont(nf)
        self._name.setStyleSheet(
            f"color: {ACCENT}; background: transparent;"
        )
        header.addWidget(self._name)

        self._joined = QLabel("—", self)
        jf = QFont("Consolas")
        jf.setPointSize(14)
        jf.setBold(True)
        self._joined.setFont(jf)
        self._joined.setStyleSheet(
            f"color: {FG}; background: transparent;"
        )
        header.addWidget(self._joined, 1)

        self._age = QLabel("", self)
        af = QFont("Consolas")
        af.setPointSize(8)
        self._age.setFont(af)
        self._age.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._age.setStyleSheet(
            f"color: {DIM}; background: transparent;"
        )
        header.addWidget(self._age)
        v.addLayout(header)

        # Glyph tile row (horizontal)
        self._tile_row = QHBoxLayout()
        self._tile_row.setContentsMargins(0, 0, 0, 0)
        self._tile_row.setSpacing(4)
        self._tile_row.addStretch(1)
        v.addLayout(self._tile_row)

        self._tiles: list[_GlyphTile] = []

    def update_field(self, entry: dict, glyph_dir: Path) -> None:
        joined = str(entry.get("joined", "—"))
        ts = float(entry.get("timestamp", 0.0))
        age_s = max(0, int(time.time() - ts))
        self._joined.setText(repr(joined))
        self._age.setText(f"{age_s}s ago")

        glyphs = entry.get("glyphs") or []
        # Resize the tile pool to the right count.
        while len(self._tiles) < len(glyphs):
            tile = _GlyphTile(self)
            # Insert before the trailing stretch so all tiles stay
            # left-aligned.
            self._tile_row.insertWidget(len(self._tiles), tile)
            self._tiles.append(tile)
        # Hide unused tiles.
        for i, tile in enumerate(self._tiles):
            if i < len(glyphs):
                g = glyphs[i]
                tile.update_glyph(
                    glyph_dir / g.get("img", ""),
                    g.get("char", "?"),
                    float(g.get("conf", 0.0)),
                )
                tile.show()
            else:
                tile.hide()


class GlyphReaderViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Glyph Reader — live OCR vision diagnostic")
        self.setMinimumSize(880, 620)
        self.setStyleSheet(f"background: {BG}; color: {FG};")

        self._last_mtime = 0.0
        self._field_rows: dict[str, _FieldRow] = {}
        self._history: deque = deque(maxlen=HISTORY_LEN)

        self._move_pause_until = 0.0
        self._move_pause_seconds = 0.4

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)
        self._tick()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._move_pause_until = time.monotonic() + self._move_pause_seconds

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # Header
        title = QLabel("GLYPH READER", self)
        tf = QFont("Consolas")
        tf.setPointSize(13)
        tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(f"color: {ACCENT}; background: transparent;")
        root.addWidget(title)

        sub = QLabel(
            "Per-glyph view of what the OCR pipeline sees and how it "
            "classifies each crop. Updates every 500 ms.\n"
            "Color: green ≥ 0.85 conf · yellow ≥ 0.50 · red < 0.50 "
            "(low conf → downstream gate likely rejects).",
            self,
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"color: {DIM}; font-size: 9pt; background: transparent;"
        )
        root.addWidget(sub)

        self._status_lbl = QLabel("waiting for first scan…", self)
        self._status_lbl.setStyleSheet(
            f"color: {DIM}; font-family: Consolas; font-size: 9pt; "
            f"background: transparent;"
        )
        root.addWidget(self._status_lbl)

        # Scrollable region for field rows.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"background: {BG};")
        wrapper = QWidget()
        wrapper.setStyleSheet(f"background: {BG};")
        self._rows_layout = QVBoxLayout(wrapper)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        self._rows_layout.addStretch(1)
        scroll.setWidget(wrapper)
        root.addWidget(scroll, 1)

        # History panel
        sep = QFrame(self)
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {DIM}; background: {DIM};")
        root.addWidget(sep)

        hist_label = QLabel("HISTORY", self)
        hf = QFont("Consolas")
        hf.setPointSize(9)
        hf.setBold(True)
        hist_label.setFont(hf)
        hist_label.setStyleSheet(
            f"color: {DIM}; background: transparent;"
        )
        root.addWidget(hist_label)

        self._history_lbl = QLabel("", self)
        self._history_lbl.setStyleSheet(
            f"color: {FG}; font-family: Consolas; font-size: 9pt; "
            f"background: transparent;"
        )
        self._history_lbl.setWordWrap(False)
        root.addWidget(self._history_lbl)

    # ──────────────────────────────────────────
    # Polling + render
    # ──────────────────────────────────────────

    def _tick(self) -> None:
        # Skip while user is dragging the window.
        if time.monotonic() < self._move_pause_until:
            return
        if not INDEX_PATH.is_file():
            self._status_lbl.setText(
                f"(no data yet — waiting for {INDEX_PATH.name} from "
                "the OCR pipeline)"
            )
            return
        try:
            mtime = INDEX_PATH.stat().st_mtime
        except Exception:
            return
        if mtime == self._last_mtime:
            return
        self._last_mtime = mtime
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                index = json.load(f)
        except Exception as exc:
            self._status_lbl.setText(f"(read failed: {exc})")
            return
        self._render(index)

    def _render(self, index: dict) -> None:
        ts = float(index.get("timestamp", 0.0))
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "?"
        delta = max(0, int(time.time() - ts)) if ts else -1
        fields = index.get("fields") or {}
        self._status_lbl.setText(
            f"latest: {ts_str}  ({delta}s ago)  ·  "
            f"{len(fields)} field/source entries"
        )

        # Display order: primary first, secondary second; mass before
        # resistance before instability, with each field's primary
        # next to its secondary if both fired.
        ordered_keys = sorted(
            fields.keys(),
            key=lambda k: (
                {"mass": 0, "resistance": 1, "instability": 2}.get(
                    fields[k].get("field", k), 99
                ),
                {"primary": 0, "secondary": 1}.get(
                    fields[k].get("source", "primary"), 99
                ),
            ),
        )

        for key in ordered_keys:
            entry = fields[key]
            display_key = (
                f"{entry.get('field', key)} ({entry.get('source', '')})"
            )
            row = self._field_rows.get(key)
            if row is None:
                row = _FieldRow(display_key, self)
                # Insert before the trailing stretch.
                self._rows_layout.insertWidget(
                    len(self._field_rows), row,
                )
                self._field_rows[key] = row
            row.update_field(entry, GLYPH_DIR)

        # History line: append a one-liner per scan.
        line = "  ".join(
            f"{fields[k].get('field', k)[:4]}={fields[k].get('joined', '?'):>6}"
            for k in ordered_keys
        )
        ts_h = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "?"
        self._history.append(f"{ts_h}  {line}")
        self._history_lbl.setText("\n".join(self._history))


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(FG))
    palette.setColor(QPalette.Base, QColor("#2a2a2a"))
    palette.setColor(QPalette.Text, QColor(FG))
    palette.setColor(QPalette.Button, QColor("#444"))
    palette.setColor(QPalette.ButtonText, QColor(FG))
    app.setPalette(palette)

    win = GlyphReaderViewer()

    # Cross-process single-instance: only one viewer at a time.
    import importlib as _il
    _il.invalidate_caches()
    from mining_shared.single_instance import SingleInstance
    guard = SingleInstance("glyph_reader", win)
    if not guard.acquire():
        return 0
    win._single_instance = guard

    win.show()
    win.raise_()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
