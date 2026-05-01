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
from PySide6.QtGui import QColor, QFont, QPalette, QPixmap, QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


THIS = Path(__file__).resolve()
TOOL = THIS.parent.parent
sys.path.insert(0, str(TOOL))

from ocr import training_registry  # noqa: E402

GLYPH_DIR = TOOL / "debug_glyphs"
INDEX_PATH = GLYPH_DIR / "latest.json"

# Map an OCR-pipeline field name to the training-registry kind it
# trains. Both HUD-numeric fields share the "hud" model + staging dir.
# Anything not listed (e.g. future signal pipeline fields) defaults
# to "signal" so out-of-range corrections still land somewhere sane;
# unknown kinds are dropped at save time with a warning.
FIELD_TO_KIND: dict[str, str] = {
    "mass": "hud",
    "resistance": "hud",
    "instability": "hud",
    # Mineral name — reading text (letters), not digits. Same staging
    # destination as the HUD digit fields for now; corrections from
    # the live viewer flow into the same training pool.
    "mineral": "hud",
}

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
    """One glyph: upscaled crop image + classified char + confidence +
    (optional) correction input that writes the original 28×28 PNG into
    the appropriate per-class training folder when the user types a
    label.

    The correction input is gated by ``enable_corrections``. End users
    running the toolbox don't train the model, so the ``fix`` text box
    is hidden in the live launcher to avoid confusing them. Developers
    invoking ``scripts/glyph_reader_viewer.py`` directly opt in via the
    ``--corrections`` flag (see ``main()``).
    """

    def __init__(self, parent=None, enable_corrections: bool = False):
        super().__init__(parent)
        self._corrections_enabled = bool(enable_corrections)
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

        # Correction input — only created when corrections are enabled
        # (developer mode). End users see just the upscaled crop +
        # classified char + confidence, no editable field.
        self._fix: Optional[QLineEdit] = None
        self._fix_default_style = ""
        self._reset_timer: Optional[QTimer] = None
        if self._corrections_enabled:
            # User types the correct label here; we save the cached
            # 28×28 PNG into the right class folder. Filtered to
            # [0-9.%] (HUD's full alphabet — signal-only fields just
            # won't accept '.' or '%').
            self._fix = QLineEdit(self)
            self._fix.setMaxLength(1)
            self._fix.setAlignment(Qt.AlignCenter)
            self._fix.setFixedWidth(GLYPH_DISPLAY_PX)
            ff = QFont("Consolas")
            ff.setPointSize(11)
            ff.setBold(True)
            self._fix.setFont(ff)
            self._fix.setValidator(QRegularExpressionValidator(
                QRegularExpression("^[0-9.%]?$"), self,
            ))
            self._fix.setPlaceholderText("fix")
            self._fix_default_style = (
                f"background: #1a1a1a; color: {FG}; "
                f"border: 1px solid {DIM}; border-radius: 2px;"
            )
            self._fix.setStyleSheet(self._fix_default_style)
            self._fix.editingFinished.connect(self._on_correction_committed)
            self._fix.returnPressed.connect(self._on_correction_committed)
            v.addWidget(self._fix)

            # Reset feedback after a short delay so the input is reusable.
            self._reset_timer = QTimer(self)
            self._reset_timer.setSingleShot(True)
            self._reset_timer.timeout.connect(self._reset_fix_input)

        # Cached glyph + provenance for save_correction().
        self._cached_pil: Optional[Image.Image] = None
        self._field: str = ""
        self._source: str = ""
        self._idx: int = -1
        self._read_char: str = ""

        # Make the tile wide enough for the labels.
        self.setFixedWidth(GLYPH_DISPLAY_PX + 8)

    def update_glyph(
        self, img_path: Path, char: str, conf: float,
        field: str = "", source: str = "", idx: int = -1,
    ) -> None:
        try:
            pil = Image.open(img_path).convert("L")
            # Cache a copy of the 28×28 source pixels — the OCR pipeline
            # overwrites `img_path` on every scan, so a delayed correction
            # would otherwise save the wrong glyph.
            self._cached_pil = pil.copy()
            # Upscale with nearest-neighbour to preserve the
            # pixelated character of the 28x28 glyph (LANCZOS would
            # blur it and hide the actual classifier input).
            scaled = pil.resize(
                (GLYPH_DISPLAY_PX, GLYPH_DISPLAY_PX), Image.NEAREST,
            )
            self._img.setPixmap(QPixmap.fromImage(ImageQt(scaled.convert("RGB"))))
        except Exception:
            self._img.setText("(load fail)")
            self._cached_pil = None
        color = _conf_color(conf)
        self._char.setText(char)
        self._char.setStyleSheet(
            f"color: {color}; background: transparent;"
        )
        self._conf.setText(f"{conf:.2f}")
        self._conf.setStyleSheet(
            f"color: {color}; background: transparent;"
        )
        self._field = field
        self._source = source
        self._idx = idx
        self._read_char = char
        # Don't clobber a partially-typed correction the user is mid-
        # editing on this tile (focus check), but do clear stale "✓"
        # markers from a prior save now that we have a fresh glyph.
        if self._fix is not None:
            if not self._fix.hasFocus() and self._fix.text() in ("", "✓"):
                self._fix.setText("")
                self._fix.setStyleSheet(self._fix_default_style)

    def _on_correction_committed(self) -> None:
        if self._fix is None:
            return
        ch = self._fix.text().strip()
        if not ch:
            return
        if self._cached_pil is None or not self._field:
            self._flash_fix_error("no glyph")
            return
        kind = FIELD_TO_KIND.get(self._field)
        if kind is None:
            self._flash_fix_error("unknown field")
            return
        try:
            spec = training_registry.get(kind)
        except Exception:
            self._flash_fix_error("no kind")
            return
        if ch not in spec.label_set:
            self._flash_fix_error("bad char")
            return
        ok = _save_correction(
            self._cached_pil, ch, spec, self._field,
            self._source, self._idx,
        )
        if ok:
            self._flash_fix_saved(ch)
        else:
            self._flash_fix_error("save fail")

    def _flash_fix_saved(self, ch: str) -> None:
        if self._fix is None or self._reset_timer is None:
            return
        self._fix.setText("✓")
        self._fix.setStyleSheet(
            f"background: #143a1a; color: {ACCENT}; "
            f"border: 1px solid {ACCENT}; border-radius: 2px;"
        )
        self._reset_timer.start(900)

    def _flash_fix_error(self, why: str) -> None:
        if self._fix is None or self._reset_timer is None:
            return
        self._fix.setStyleSheet(
            f"background: #3a1414; color: {DANGER}; "
            f"border: 1px solid {DANGER}; border-radius: 2px;"
        )
        self._fix.setToolTip(f"save failed: {why}")
        self._reset_timer.start(1500)

    def _reset_fix_input(self) -> None:
        if self._fix is None:
            return
        self._fix.setText("")
        self._fix.setStyleSheet(self._fix_default_style)
        self._fix.setToolTip("")

    def clear(self) -> None:
        self._img.clear()
        self._char.setText("·")
        self._char.setStyleSheet(f"color: {DIM}; background: transparent;")
        self._conf.setText("—")
        self._conf.setStyleSheet(f"color: {DIM}; background: transparent;")
        self._cached_pil = None
        if self._fix is not None:
            self._fix.setText("")
            self._fix.setStyleSheet(self._fix_default_style)


def _save_correction(
    pil: Image.Image, ch: str, spec, field: str, source: str, idx: int,
) -> bool:
    """Persist a manually-corrected glyph into the spec's per-class
    training folder. Returns True on success.

    Filename: ``user_glyphreader_<unix_ms>_<field>_<source>_<idx>.png``
    — long but unambiguous. The ``user_`` prefix matches the convention
    used by ``extract_labeled_glyphs._save_glyph`` so review/promote
    tooling treats these the same as crops labeled in the offline UI.
    """
    class_map = {".": "dot", "%": "pct"}
    cls = class_map.get(ch, ch)
    out_dir = spec.glyph_staging_dir / cls
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    ts_ms = int(time.time() * 1000)
    safe_field = "".join(c if c.isalnum() else "_" for c in field) or "f"
    safe_source = "".join(c if c.isalnum() else "_" for c in source) or "s"
    out = (
        out_dir
        / f"user_glyphreader_{ts_ms}_{safe_field}_{safe_source}_{idx}.png"
    )
    try:
        pil.save(out)
        return True
    except Exception:
        return False


class _FieldRow(QFrame):
    """One row showing a single (field, source) pair: header + glyph tiles."""

    def __init__(
        self,
        key_label: str,
        parent=None,
        enable_corrections: bool = False,
    ):
        super().__init__(parent)
        self._corrections_enabled = bool(enable_corrections)
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

        # Mean conf badge — shown for whole-crop voters (CRNN, tess,
        # vote, winner) where there are no per-glyph confs to display.
        self._mean_conf = QLabel("", self)
        mcf = QFont("Consolas")
        mcf.setPointSize(9)
        mcf.setBold(True)
        self._mean_conf.setFont(mcf)
        self._mean_conf.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._mean_conf.setStyleSheet(
            f"color: {DIM}; background: transparent;"
        )
        header.addWidget(self._mean_conf)

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

        # Glyph tile row (horizontal). Hidden when the entry is a
        # whole-crop voter (no per-glyph crops to render).
        self._tile_row = QHBoxLayout()
        self._tile_row.setContentsMargins(0, 0, 0, 0)
        self._tile_row.setSpacing(4)
        self._tile_row.addStretch(1)
        self._tile_row_widget = QWidget(self)
        self._tile_row_widget.setLayout(self._tile_row)
        v.addWidget(self._tile_row_widget)

        self._tiles: list[_GlyphTile] = []

    def update_field(self, entry: dict, glyph_dir: Path) -> None:
        joined = str(entry.get("joined", "—"))
        ts = float(entry.get("timestamp", 0.0))
        age_s = max(0, int(time.time() - ts))
        self._joined.setText(repr(joined))
        self._age.setText(f"{age_s}s ago")

        field = str(entry.get("field", ""))
        source = str(entry.get("source", ""))
        glyphs = entry.get("glyphs") or []

        # Whole-crop voter rows (crnn, tesseract, vote, winner) have no
        # per-glyph crops — hide the tile area and show the mean conf
        # in the header instead. CNN rows (primary, secondary) keep the
        # tile display as before.
        if not glyphs:
            self._tile_row_widget.hide()
            mc = entry.get("mean_conf")
            if mc is not None:
                color = _conf_color(float(mc))
                self._mean_conf.setText(f"conf={float(mc):.2f}")
                self._mean_conf.setStyleSheet(
                    f"color: {color}; background: transparent;"
                )
            else:
                self._mean_conf.setText("")
                self._mean_conf.setStyleSheet(
                    f"color: {DIM}; background: transparent;"
                )
            return
        # Has glyphs: standard CNN row, hide the mean-conf badge and
        # show the tile area.
        self._mean_conf.setText("")
        self._tile_row_widget.show()
        # Resize the tile pool to the right count.
        while len(self._tiles) < len(glyphs):
            tile = _GlyphTile(
                self, enable_corrections=self._corrections_enabled,
            )
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
                    field=field,
                    source=source,
                    idx=int(g.get("idx", i)),
                )
                tile.show()
            else:
                tile.hide()


class GlyphReaderViewer(QWidget):
    def __init__(self, enable_corrections: bool = False):
        """Live OCR diagnostic viewer.

        ``enable_corrections``: when True, each glyph tile gets a "fix"
        text input that lets the user submit a corrected label, which
        gets saved as training data for the next model retrain. Default
        False because end users running the toolbox don't train the
        model — the input would just confuse them. Direct script
        invocation (``python scripts/glyph_reader_viewer.py``) opts in
        via the ``--corrections`` flag.
        """
        super().__init__()
        self._corrections_enabled = bool(enable_corrections)
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

        # Help text — slightly different wording in dev mode (where the
        # "fix" input is shown) vs end-user mode (read-only diagnostic).
        if self._corrections_enabled:
            _help = (
                "Per-glyph view of what the OCR pipeline sees and how "
                "it classifies each crop. Updates every 500 ms.\n"
                "Color: green ≥ 0.85 conf · yellow ≥ 0.50 · red < 0.50 "
                "(low conf → downstream gate likely rejects).\n"
                "Type the correct char in the box under any wrong "
                "glyph and press Enter — the 28×28 crop is saved into "
                "the matching training/<class>/ folder for the next "
                "retrain."
            )
        else:
            _help = (
                "Per-glyph view of what the OCR pipeline sees and how "
                "it classifies each crop. Updates every 500 ms.\n"
                "Color: green ≥ 0.85 conf · yellow ≥ 0.50 · red < 0.50 "
                "(low conf → downstream gate likely rejects)."
            )
        sub = QLabel(_help, self)
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
        # Mark the diagnostic heartbeat so the OCR pipeline keeps
        # writing the per-glyph PNGs + voter index this viewer reads.
        # If we don't touch this, the pipeline no-ops every dump call
        # and we'd see stale data.
        try:
            from ocr.sc_ocr import debug_overlay as _dbg
            _dbg.viewer_heartbeat()
        except Exception:
            pass
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

        # Display order:
        #   field group: mineral → mass → resistance → instability
        #     (mineral first because it's at the top of the SCAN
        #     RESULTS panel — matches what the user sees on screen)
        #   within each: primary → secondary → crnn → tesseract → vote
        #                → winner (the value the pipeline actually
        #                returned). Primary/secondary show per-glyph
        #                tiles; the rest are whole-crop voters with
        #                text + mean conf only.
        ordered_keys = sorted(
            fields.keys(),
            key=lambda k: (
                {
                    "mineral": 0,
                    "mass": 1, "resistance": 2, "instability": 3,
                }.get(fields[k].get("field", k), 99),
                {
                    "primary": 0, "secondary": 1,
                    "crnn": 2, "tesseract": 3,
                    "vote": 4, "winner": 5,
                }.get(fields[k].get("source", "primary"), 99),
            ),
        )

        for key in ordered_keys:
            entry = fields[key]
            display_key = (
                f"{entry.get('field', key)} ({entry.get('source', '')})"
            )
            row = self._field_rows.get(key)
            if row is None:
                row = _FieldRow(
                    display_key, self,
                    enable_corrections=self._corrections_enabled,
                )
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
    # Direct script invocation defaults to dev mode (corrections
    # enabled) so the developer can label glyphs for the next retrain.
    # Pass --no-corrections to run in end-user (read-only) mode for
    # testing how players will see the viewer.
    enable_corrections = "--no-corrections" not in sys.argv

    app = QApplication.instance() or QApplication(sys.argv)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(FG))
    palette.setColor(QPalette.Base, QColor("#2a2a2a"))
    palette.setColor(QPalette.Text, QColor(FG))
    palette.setColor(QPalette.Button, QColor("#444"))
    palette.setColor(QPalette.ButtonText, QColor(FG))
    app.setPalette(palette)

    win = GlyphReaderViewer(enable_corrections=enable_corrections)

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
