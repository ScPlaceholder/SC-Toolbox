"""ONNX-based mining HUD OCR — fast mass + resistance extraction.

Uses Mort13's trained CNN model (3KB graph + 1.7MB weights, 13 char classes,
100% validation accuracy) with a row-detection pipeline that's resolution-
independent (no anchor templates needed):

1. Capture the user's configured HUD region
2. Find text rows by horizontal brightness profiling
3. Identify MASS row (row 3) and RESISTANCE row (row 4) by position
4. Crop the right portion of each row (value only, skip label text)
5. Otsu binarize → projection segment → ONNX batch inference

Total pipeline: ~30-80ms per frame including screen capture.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import numpy as np
from PIL import Image

from .screen_reader import _check_tesseract

log = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_MODEL_PATH = os.path.join(_MODULE_DIR, "models", "model_cnn.onnx")
_META_PATH = os.path.join(_MODULE_DIR, "models", "model_cnn.json")

# Online-learned model lives in %LOCALAPPDATA% so the shipped model
# in the app directory is never modified (safe across updates).
try:
    from .online_learner import ONLINE_MODEL_PATH as _ONLINE_MODEL_PATH
except ImportError:
    from pathlib import Path as _Path
    _ONLINE_MODEL_PATH = _Path(os.environ.get("LOCALAPPDATA", "")) / "SC_Toolbox" / "model_cnn_online.onnx"

# Lazy-loaded
_session = None
_char_classes: str = "0123456789.-%"

# Label-row cache: maps (x, y, w, h) region key → (timestamp, rows).
# Tesseract label OCR is expensive (~3 subprocess spawns, ~500 ms)
# but labels don't move within a rock — cache and reuse. Cache is
# cleared when the panel disappears or after TTL expires.
_label_cache: dict[tuple[int, int, int, int], tuple[float, dict]] = {}
_LABEL_CACHE_TTL_SEC = 60.0  # safe upper bound; rocks scan for <60s


def _ensure_model() -> bool:
    global _session, _char_classes
    if _session is not None:
        return True

    # Prefer online-learned model if it exists, else shipped model.
    model_path = (
        str(_ONLINE_MODEL_PATH)
        if _ONLINE_MODEL_PATH.is_file()
        else _MODEL_PATH
    )

    if not os.path.isfile(model_path):
        log.warning("onnx_hud_reader: model not found at %s", model_path)
        return False

    try:
        import onnxruntime as ort
    except ImportError:
        log.warning("onnx_hud_reader: onnxruntime not installed")
        return False

    try:
        import json
        if os.path.isfile(_META_PATH):
            with open(_META_PATH) as f:
                meta = json.load(f)
                _char_classes = meta.get("charClasses", _char_classes)

        _session = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"],
        )
        log.info("onnx_hud_reader: model loaded from %s (%d classes)",
                 os.path.basename(model_path), len(_char_classes))
        return True
    except Exception as exc:
        log.error("onnx_hud_reader: model load failed: %s", exc)
        return False


def hot_swap_model(new_model_path: str) -> bool:
    """Replace the live ONNX inference session with a new model.

    Called by ``online_learner`` after re-exporting updated weights.
    Thread-safe: Python's GIL makes the pointer swap atomic.
    """
    global _session
    try:
        import onnxruntime as ort
        new_session = ort.InferenceSession(
            new_model_path, providers=["CPUExecutionProvider"],
        )
        old = _session
        _session = new_session
        del old
        log.info("onnx_hud_reader: hot-swapped model from %s",
                 os.path.basename(new_model_path))
        return True
    except Exception as exc:
        log.error("onnx_hud_reader: hot-swap failed: %s", exc)
        return False


def is_available() -> bool:
    return _ensure_model()


# ─────────────────────────────────────────────────────────────
# Row detection
# ─────────────────────────────────────────────────────────────

def _find_panel_lines(
    gray: np.ndarray,
    min_width_frac: float = 0.18,
    max_thickness: int = 3,
) -> list[tuple[int, int, int]]:
    """Detect horizontal HUD separator lines.

    The SC scan-results panel is bounded by thin horizontal HUD lines:
    one under the SCAN RESULTS header (above the mineral name) and
    one below the difficulty bar (above COMPOSITION). These lines are:
      - 1-2 px tall (much thinner than text rows, which are 14+ px)
      - span most of the panel width
      - high-contrast vs the panel background
      - rendered HUD chrome → present in EVERY scan, regardless of
        ship variant or HUD color

    Detection is polarity-independent (uses the existing edge mask)
    so light, dark, and noisy backgrounds all work the same.

    Returns a list of ``(y_center, x_left, x_right)`` tuples, sorted
    top-to-bottom. Each tuple gives the line's middle row and its
    horizontal endpoints.

    Notes:
      - Multiple consecutive bright rows are coalesced into one line.
      - Lines shorter than ``min_width_frac`` of image width are
        discarded (filters out cluster bars and short underlines).
      - Lines thicker than ``max_thickness`` are discarded (those are
        actual text rows, not HUD chrome).
    """
    h, w = gray.shape
    if h == 0 or w == 0:
        return []

    mask = _build_text_mask(gray)
    row_density = mask.sum(axis=1)
    min_width = int(w * min_width_frac)

    # Find consecutive runs of high-density rows.
    in_run = False
    run_start = 0
    runs: list[tuple[int, int]] = []
    for y in range(h + 1):
        d = row_density[y] if y < h else 0
        is_hot = d >= min_width
        if is_hot and not in_run:
            in_run = True
            run_start = y
        elif not is_hot and in_run:
            in_run = False
            runs.append((run_start, y))

    lines: list[tuple[int, int, int]] = []
    for y_start, y_end in runs:
        thickness = y_end - y_start
        if thickness == 0 or thickness > max_thickness:
            continue
        # Endpoints: leftmost and rightmost True column anywhere in
        # the line's vertical extent. Use ``any`` so a single broken
        # pixel doesn't truncate the line.
        line_mask = mask[y_start:y_end, :].any(axis=0)
        xs = np.where(line_mask)[0]
        if xs.size == 0:
            continue
        x_left = int(xs[0])
        x_right = int(xs[-1]) + 1
        span = x_right - x_left
        if span < min_width:
            continue
        # ── Continuity check ──
        # Span alone doesn't distinguish a real HUD separator (near-
        # solid, ≥95% of columns lit) from a 1-3 px text slice where
        # letter caps/baselines happen to span wide (e.g. "SCAN RESULTS"
        # at the top of the panel: wide, but ~50-70% lit because of
        # inter-letter gaps). Without this filter, text rows get
        # promoted to HUD lines and the panel-finder anchors the whole
        # geometry at the wrong y, compressing MASS/RESIST/INSTAB
        # boxes onto the header. Require ≥ 80% fill within the span.
        fill = int(line_mask[x_left:x_right].sum())
        if fill < int(span * 0.80):
            continue
        y_center = (y_start + y_end) // 2
        lines.append((y_center, x_left, x_right))
    return lines


# Per-scan cache for _find_panel_lines results. Keyed by id(gray) +
# shape so that the three-way row call (mass, resist, instab) inside
# a single scan only pays the detection cost once.
_panel_lines_cache: tuple[int, tuple[int, int], list[tuple[int, int, int]]] | None = None


def _get_panel_lines_cached(gray: np.ndarray) -> list[tuple[int, int, int]]:
    """Return _find_panel_lines(gray), cached per gray-array identity.

    Three rows in a single scan share the same gray; cache hits keep
    repeated calls free.
    """
    global _panel_lines_cache
    key = (id(gray), gray.shape)
    if _panel_lines_cache is not None:
        cid, cshape, clines = _panel_lines_cache
        if cid == key[0] and cshape == key[1]:
            return clines
    lines = _find_panel_lines(gray)
    _panel_lines_cache = (key[0], key[1], lines)
    return lines


def _build_text_mask(gray: np.ndarray, deviation: int = 35) -> np.ndarray:
    """Return a boolean mask where True means "likely text pixel".

    Auto-detects polarity so it works on BOTH dark and light backgrounds:
    - Dark bg (median < 130): text is BRIGHT → gray > 150
    - Light bg (median >= 130): text is DARK → gray < (median - 30)

    This single fix enables the entire downstream pipeline
    (_find_mineral_row, _find_value_crop, column-density scanning)
    to work on light backgrounds without PaddleOCR.
    """
    del deviation  # kept for API compatibility
    median = float(np.median(gray))
    if median < 130:
        return gray > 150
    else:
        # Light background: text is darker than surroundings.
        # Use local contrast via high-pass filter for robust detection.
        from PIL import Image as _Img, ImageFilter
        blurred = np.asarray(
            _Img.fromarray(gray).filter(ImageFilter.GaussianBlur(radius=5)),
            dtype=np.float32,
        )
        local_contrast = np.abs(gray.astype(np.float32) - blurred)
        return local_contrast > 15


def _find_value_crop(
    img: "Image.Image",
    gray: "np.ndarray",
    y1: int,
    y2: int,
    x_min: int = 0,
) -> "Optional[Image.Image]":
    """Crop the value sub-region of a row.

    SIMPLE PROVEN RECIPE (matches scripts/test_sc_ocr_on_annotations.py
    which reads the digits at 99-100% confidence):

      1. Crop the row strip [y1:y2, :].
      2. Polarity-canonicalize so text is BRIGHT (CNN training convention).
      3. Otsu threshold -> binary.
      4. Project to columns, find contiguous spans (>=2 px wide).
      5. Find the LARGEST gap between consecutive spans -- that's the
         label-to-value separator.
      6. Take all spans to the RIGHT of the largest gap as the value.
      7. Crop with ~4 px margin on each side.

    The previous implementation accumulated ~250 lines of cluster
    width filters, geometric fallbacks, line-mid clamping and
    multi-pass cluster acceptance; none of it improved on the simple
    recipe above for typical HUD content. Fewer moving parts,
    cleaner failure modes, easier to debug.

    Returns None only if the row is degenerate or no spans are found.
    """
    if y2 <= y1 or (y2 - y1) < 4:
        return None
    H, W = gray.shape
    y1 = max(0, y1)
    y2 = min(H, y2)
    if y2 - y1 < 4:
        return None

    # ── User's mental model (matches the annotated panel) ──
    #   GREEN line  = x_min  (just past INSTABILITY: colon)
    #   PURPLE line = W      (panel right edge)
    #   The VALUE LIVES BETWEEN green and purple. There can be NO label
    #   text in this region (we already moved past the colons).
    #
    # Algorithm:
    #   1. Crop the value column [x_min : W] from the row strip.
    #   2. Use MAX-OF-CHANNELS for text detection (so red/green/yellow
    #      text registers as bright — luminance grayscale loses red).
    #   3. Two-tier vertical-density mask (strict for strokes,
    #      permissive for dots, joined by adjacency dilation).
    #   4. Find contiguous text spans.
    #   5. Crop tight around all spans (they're ALL value text since
    #      labels are excluded by x_min).
    x_lo = max(0, x_min) if x_min > 0 else 0
    x_hi = W
    if x_hi - x_lo < 8:
        return None

    # Slice once to the value-column region (saves a copy and bounds
    # all subsequent indices).
    try:
        rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
        detect_region = rgb[y1:y2, x_lo:x_hi].max(axis=2)
    except Exception:
        detect_region = gray[y1:y2, x_lo:x_hi]

    # Polarity-canonicalize so text is the BRIGHT class.
    thr_d = _otsu(detect_region)
    bright_count = int((detect_region > thr_d).sum())
    dark_count = detect_region.size - bright_count
    if dark_count < bright_count:
        detect_canon = (255 - detect_region).astype(np.uint8)
    else:
        detect_canon = detect_region.astype(np.uint8)

    # Binary text mask via Otsu on the canonicalized region.
    thr2 = _otsu(detect_canon)
    binary = (detect_canon > thr2).astype(np.uint8)

    # Two-tier density: strict floor catches text strokes, permissive
    # floor catches dots, joined by adjacency dilation.
    row_h = y2 - y1
    region_w = x_hi - x_lo
    strict_floor = max(4, int(row_h * 0.25))
    permissive_floor = 3
    proj = binary.sum(axis=0)
    strict_hot = proj >= strict_floor
    permissive_hot = proj >= permissive_floor

    if strict_hot.any():
        dilate_radius = 6
        kernel = np.ones(2 * dilate_radius + 1, dtype=np.int32)
        dilated = np.convolve(
            strict_hot.astype(np.int32), kernel, mode="same",
        ) > 0
        hot = dilated & permissive_hot
    else:
        hot = strict_hot

    # Find contiguous text spans (right-to-left scan per the user's
    # spec — purple back toward green — so we naturally identify the
    # rightmost text cluster first).
    spans_rtl: list[tuple[int, int]] = []
    in_run = False
    end = 0
    for i in range(region_w - 1, -1, -1):
        if hot[i] and not in_run:
            in_run = True
            end = i + 1
        elif not hot[i] and in_run:
            in_run = False
            if end - (i + 1) >= 2:
                spans_rtl.append((i + 1, end))
    if in_run and end >= 2:
        spans_rtl.append((0, end))

    if not spans_rtl:
        return None

    # spans_rtl is right-to-left ordered. Convert to left-to-right
    # for cropping bounds.
    spans = list(reversed(spans_rtl))

    # Crop from the GREEN LINE (x_lo) to the rightmost text + margin.
    # Per user spec: "start reading rows right of MASS: green line,
    # slide to purple line, scan back to green to find numerical
    # values". So the LEFT edge stays at x_lo (the green line) — we
    # don't trim inward to where the digits visually start. The
    # RIGHT edge is the rightmost text + small margin (so we don't
    # waste pipeline cycles on empty pixels past the value).
    v_right_local = min(region_w, spans[-1][1] + 4)
    if v_right_local < 4:
        return None

    # LEFT edge = 0 (which maps to x_lo in image coords) — preserves
    # the user's "start at green line" anchor.
    return img.crop((x_lo, y1, x_lo + v_right_local, y2))


# ─────────────────────────────────────────────────────────────
# ONNX inference pipeline
# ─────────────────────────────────────────────────────────────

def _otsu(gray: np.ndarray) -> int:
    """Compute Otsu's optimal binarization threshold."""
    hist, _ = np.histogram(gray.flatten(), bins=256, range=(0, 256))
    total = gray.size
    sum_total = np.sum(np.arange(256) * hist)
    sum_bg, w_bg = 0.0, 0
    max_var, threshold = 0.0, 0
    for t in range(256):
        w_bg += hist[t]
        if w_bg == 0:
            continue
        w_fg = total - w_bg
        if w_fg == 0:
            break
        sum_bg += t * hist[t]
        var = w_bg * w_fg * (sum_bg / w_bg - (sum_total - sum_bg) / w_fg) ** 2
        if var > max_var:
            max_var = var
            threshold = t
    return threshold


def _find_mineral_row(img: Image.Image) -> Optional[tuple[int, int]]:
    """Find the mineral-name row (e.g. 'TORITE (ORE)') via text mask.

    The mineral name is always the topmost wide text row after the
    'SCAN RESULTS' header. Returns (y1, y2) of its brightness band,
    or None if not found.

    Why not a label ('MASS:', 'RESIST:')? Tesseract's label OCR is
    unreliable on bright-background panels where the sunlit asteroid
    bleeds through and corrupts the local background estimate. The
    mineral-name row is visually distinctive regardless of polarity
    because it's a wide, dense text cluster unlike any other row in
    the top half of the panel.
    """
    gray = np.array(img.convert("L"), dtype=np.uint8)
    text_mask = _build_text_mask(gray, deviation=30)
    # Row counts
    row_counts = text_mask.sum(axis=1)
    h = len(row_counts)

    # Build row spans. Min height scales with panel size: at 541px
    # height the threshold is 14px (2.6%); at 130px it's ~8px. This
    # ensures small-panel HUDs (user's native 125x130 crop) don't
    # have their rows filtered out.
    min_row_h = max(6, min(14, int(h * 0.026)))
    rows: list[tuple[int, int, int]] = []  # (y1, y2, peak_count)
    in_row = False
    start = 0
    peak = 0
    for y in range(h + 1):
        val = row_counts[y] if y < h else 0
        if val > 3 and not in_row:
            in_row = True
            start = y
            peak = val
        elif val > 3 and in_row:
            peak = max(peak, val)
        elif val <= 3 and in_row:
            in_row = False
            if y - start >= min_row_h:
                rows.append((start, y, peak))

    if len(rows) < 2:
        return None

    # Typical panel layout after row-detection:
    #   - first wide/dense row (peak >= 60) = "SCAN RESULTS" header
    #   - next wide/dense row (peak >= 60)  = mineral name "TORITE (ORE)"
    #   - then MASS, RESISTANCE, INSTABILITY rows
    #
    # Find the first row matching the header signature and return
    # the NEXT qualifying row as the mineral name.
    # Peak threshold scales with panel width. At 397 px (test fixture),
    # the header peaks at ~117 = 29% of width. At 125 px (user's small
    # panel), the same text peaks at ~36 = 29% of width. Using a
    # proportional threshold handles all panel sizes.
    W = gray.shape[1]
    # "SCAN RESULTS" text width doesn't scale linearly with panel
    # width (same string, different font sizes). At 397px panel it
    # peaks at 117 (29%); at 332px it peaks at 43 (13%). Use 10%
    # as the floor to catch both.
    header_peak_min = max(15, int(W * 0.10))
    mineral_peak_min = max(10, int(W * 0.06))

    header_idx = None
    for i, (y1, y2, peak_cnt) in enumerate(rows):
        if peak_cnt >= header_peak_min and (y2 - y1) <= 40:
            header_idx = i
            break

    if header_idx is None:
        return None

    for y1, y2, peak_cnt in rows[header_idx + 1:]:
        if peak_cnt >= mineral_peak_min and (y2 - y1) <= 40:
            return (y1, y2)
    return None


# Cache of SCAN RESULTS title position per region key. Once we know
# where the title is in the captured region, the entire panel layout
# follows by FIXED PROPORTIONAL OFFSETS — no per-frame guessing.
# Cleared when the panel disappears (handled by callers via
# _label_cache.clear() / _scan_results_anchor_cache.clear()).
_scan_results_anchor_cache: dict[tuple[int, int], tuple[float, dict]] = {}


def _find_scan_results_anchor(img: Image.Image) -> Optional[dict]:
    """Find the SCAN RESULTS title via Tesseract and return geometry.

    The SC mining HUD has a FIXED layout. Once we locate the SCAN
    RESULTS title — large bold static text, easy for Tesseract to
    read reliably across light/dark/noisy backgrounds — every other
    row position is a known proportional offset.

    Returns a dict::

        {
            "title_x": int,   # left edge of "SCAN" word
            "title_y": int,   # top of title text
            "title_h": int,   # title text height
            "title_w": int,   # extent across "SCAN RESULTS"
        }

    or None if Tesseract can't find the title (panel not visible,
    occluded, or PaddleOCR-only path).

    Tesseract is run with a UPPERCASE-letters whitelist (the title
    is always rendered in caps) and PSM 11 (sparse text), which is
    fast and tolerant of HUD backgrounds.
    """
    if not _check_tesseract():
        return None
    try:
        import pytesseract
    except ImportError:
        return None

    # Search the top half of the image — title is always near the top.
    w_img, h_img = img.size
    top = img.crop((0, 0, w_img, min(h_img, max(120, h_img // 2))))
    gray = np.array(top.convert("L"), dtype=np.uint8)

    # Run two polarity variants (dark- and light-background HUDs)
    thr = _otsu(gray)
    variants = [
        np.where(gray > thr, 0, 255).astype(np.uint8),
        np.where(gray < thr, 0, 255).astype(np.uint8),
    ]
    best: Optional[tuple[int, int, int, int]] = None
    for binary in variants:
        binary_pil = Image.fromarray(binary)
        try:
            data = pytesseract.image_to_data(
                binary_pil,
                config=(
                    "--psm 11 -c tessedit_char_whitelist="
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                ),
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            continue
        n = len(data.get("text", []))
        # Collect all "SCAN" and "RESULTS" hits with their bboxes
        scan_hits: list[tuple[int, int, int, int]] = []
        result_hits: list[tuple[int, int, int, int]] = []
        for i in range(n):
            txt = (data["text"][i] or "").strip().upper()
            if not txt or len(txt) < 3:
                continue
            x = int(data["left"][i])
            y = int(data["top"][i])
            ww = int(data["width"][i])
            hh = int(data["height"][i])
            if "SCAN" in txt:
                scan_hits.append((x, y, ww, hh))
            elif "RESULT" in txt:
                result_hits.append((x, y, ww, hh))
        # Find a SCAN+RESULTS pair on roughly the same line
        for sx, sy, sw, sh in scan_hits:
            for rx, ry, rw, rh in result_hits:
                if abs(sy - ry) > max(sh, rh):
                    continue  # not on the same line
                if rx <= sx:
                    continue  # RESULTS should be to the right of SCAN
                title_x = sx
                title_y = min(sy, ry)
                title_h = max(sh, rh)
                title_w = (rx + rw) - sx
                if best is None or title_h > best[3]:
                    best = (title_x, title_y, title_w, title_h)
    if best is None:
        return None
    title_x, title_y, title_w, title_h = best
    return {
        "title_x": title_x,
        "title_y": title_y,
        "title_h": title_h,
        "title_w": title_w,
    }


# ── Fixed proportional offsets from SCAN RESULTS title to each row ──
# These are measured against the title HEIGHT (which scales with
# panel scale automatically). Center y of each row is computed as:
#   row_y_center = title_y + title_h * MULTIPLIER
# Multipliers measured from the 397-px reference panel:
#   title bottom is at title_y + title_h
#   mineral row center ≈ title bottom + 1.6 * title_h
#   mass row center    ≈ title bottom + 3.0 * title_h
#   resist row center  ≈ title bottom + 4.4 * title_h
#   instab row center  ≈ title bottom + 5.8 * title_h
#   outcome bar center ≈ title bottom + 7.4 * title_h
_ROW_OFFSET_MULTS = {
    "_mineral_row": 1.6,
    "mass":         3.0,
    "resistance":   4.4,
    "instability":  5.8,
}
_ROW_HEIGHT_MULT = 0.9   # half-height = 0.9 * title_h
# Label-right (value-column-left anchor) as a fraction of img.width.
# Measured from the reference panel: ~52%.
_VALUE_COL_LEFT_FRAC = 0.52


def _label_rows_from_anchor(
    img: Image.Image, anchor: dict,
) -> dict[str, tuple[int, int, int]]:
    """Compute label rows from the SCAN RESULTS anchor.

    Three-tier strategy, first one to succeed wins:

      A. PURE NCC (preferred) — crop the image to "below the title",
         run NCC label-template matching against MASS / RESISTANCE /
         INSTABILITY. Each match's pixel position IS that row's
         position. The title acts as a y-gate that prevents NCC from
         drifting into COMPOSITION / commodity rows below the data
         area. Most robust against tilted text and arbitrary panel
         scale because each row gets its own deterministic NCC anchor.

      B. MEASURED BANDS (fallback) — horizontal-projection band
         detection seeded from the title position. Works when NCC
         label templates don't match (e.g. unusual rendering /
         missing template) but bands are still distinguishable.

      C. FIXED MULTIPLIERS (deepest fallback) — title_h × static
         offsets from a reference panel. Only fires when bands also
         fail. Less robust but gives SOMETHING to work with.

    Tier A landed because measured bands kept finding the wrong bands
    when the captured region extended through IMPOSSIBLE / COMPOSITION
    / commodity rows (the projection signal under the title spans
    those rows AND the data rows, and band detection couldn't always
    pick the right 4). Per-row NCC is structurally immune to that —
    each row has its own template match.
    """
    title_y = anchor["title_y"]
    title_h = anchor["title_h"]
    title_bottom = title_y + title_h
    eff_title_h = min(int(title_h), 50)
    search_origin = min(img.height, title_y + eff_title_h + 4)

    # ── Tier A: pure NCC for each row (constrained to below title) ──
    # Crop to below the title, run NCC label-template matching, take
    # each match's pixel position as that row's location. Compute
    # label_right per row via GAP-DETECTION (not "rightmost text
    # column") because the matched template region can extend into
    # the value column's leading digit, and a rightmost-text scan
    # would land on the digit's edge instead of the colon.
    #
    # Gap detection finds the colon by looking for the first wide
    # low-density region after the label text. The colon is the
    # right edge of the contiguous label-text run; whatever's past
    # the gap is the value, not the colon.
    try:
        from .sc_ocr import label_match as _lm_rows
        if (img.height - search_origin) >= 60:
            below = img.crop((0, search_origin, img.width, img.height))
            matches = _lm_rows.find_label_positions(below)
            if matches and "mass" in matches:
                # Build a polarity-canonical text mask of the below-title
                # region (used by label_right gap detection per row).
                _below_gray = np.array(below.convert("L"), dtype=np.uint8)
                _below_rgb = np.array(below.convert("RGB"), dtype=np.uint8)
                _below_detect = _below_rgb.max(axis=2).astype(np.uint8)

                def _label_right_via_gap(m: dict) -> int:
                    """Find the colon's right edge by run-coalescing.

                    Procedure:
                      1. Take a Y strip at the matched row's y-range,
                         scan the FULL image width.
                      2. Build a per-column text-density profile.
                      3. Find every "text run" (consecutive above-floor
                         columns).
                      4. Coalesce runs separated by < ``_INTRA_LABEL_GAP``
                         px — that merges inter-letter spaces in MASS /
                         RESISTANCE / INSTABILITY into one continuous
                         label run, but keeps the bigger colon-to-value
                         gap as a separator.
                      5. The FIRST coalesced run is the label (e.g.
                         ``MASS:``). Its right edge is the colon's right
                         edge.

                    The intra-label gap threshold matters: SC's HUD font
                    has 1-3 px between letters, but 8-15 px after the
                    colon before the value. ``_INTRA_LABEL_GAP=5`` sits
                    cleanly between those.
                    """
                    H, W = _below_detect.shape
                    y1 = max(0, m["y"])
                    y2 = min(H, m["y"] + m["h"])
                    if y2 <= y1:
                        return m["x"] + m["w"]
                    region = _below_detect[y1:y2, :]
                    thr = _otsu(region)
                    bright = int((region > thr).sum())
                    if (region.size - bright) < bright:
                        canon = (255 - region).astype(np.uint8)
                    else:
                        canon = region
                    thr2 = _otsu(canon)
                    col_density = (canon > thr2).sum(axis=0)
                    # Density floor: 15% of row height (lower than 25%
                    # so the colon's two dots count as text).
                    floor = max(2, int((y2 - y1) * 0.15))
                    hot = col_density >= floor
                    # Find all hot runs.
                    runs: list[tuple[int, int]] = []
                    in_run = False
                    rs = 0
                    for x in range(W):
                        if hot[x] and not in_run:
                            in_run = True
                            rs = x
                        elif not hot[x] and in_run:
                            in_run = False
                            runs.append((rs, x))
                    if in_run:
                        runs.append((rs, W))
                    if not runs:
                        return m["x"] + m["w"]
                    # Restrict to runs starting at or after the matched
                    # bbox's left edge — the label can't be left of the
                    # match. (Avoids picking up unrelated text far to
                    # the left.)
                    runs = [r for r in runs if r[0] >= max(0, m["x"] - 4)]
                    if not runs:
                        return m["x"] + m["w"]
                    # Coalesce runs separated by small gaps (inter-letter
                    # spaces). 5 px sits between SC's intra-letter gap
                    # (~2 px) and post-colon gap (~10 px).
                    _INTRA_LABEL_GAP = 5
                    coalesced: list[tuple[int, int]] = [runs[0]]
                    for cur_rs, cur_re in runs[1:]:
                        prev_rs, prev_re = coalesced[-1]
                        if (cur_rs - prev_re) <= _INTRA_LABEL_GAP:
                            coalesced[-1] = (prev_rs, cur_re)
                        else:
                            coalesced.append((cur_rs, cur_re))
                    # The first coalesced run is the label. Its right
                    # edge is the colon's right edge.
                    label_end = coalesced[0][1]
                    return int(label_end) + 2

                # ── Compute label_right by finding the VALUE COLUMN ──
                # The HUD left-aligns all 3 values (MASS/RESIST/INSTAB)
                # to a single column. We can find that column's left
                # edge directly by scanning each matched row right-to-
                # left for the rightmost text run — that's the value.
                # The min across rows gives the value column's leftmost
                # x; label_right sits just before it.
                #
                # This avoids depending on template width fitting the
                # actual rendered text (templates can be over-wide when
                # NCC matches at a sub-optimal scale, particularly for
                # the longer RESISTANCE / INSTABILITY templates whose
                # NCC scores are weaker).
                def _value_left_for_row(m: dict) -> Optional[int]:
                    H, W = _below_detect.shape
                    y1 = max(0, m["y"])
                    y2 = min(H, m["y"] + m["h"])
                    if y2 <= y1:
                        return None
                    region = _below_detect[y1:y2, :]
                    thr = _otsu(region)
                    bright = int((region > thr).sum())
                    if (region.size - bright) < bright:
                        canon = (255 - region).astype(np.uint8)
                    else:
                        canon = region
                    thr2 = _otsu(canon)
                    col_density = (canon > thr2).sum(axis=0)
                    # "Solid" letter/digit threshold — much higher than
                    # the floor used for label-text detection. Values
                    # render as solid digits with d ≈ 20-30 in a 32 px
                    # row. Setting threshold to 50% of row height lets
                    # us reliably find digit columns (and ignore the
                    # sparse colon dots, which we DON'T want to mistake
                    # for the value).
                    solid = max(6, int((y2 - y1) * 0.50))
                    is_solid = col_density >= solid
                    # Find the rightmost solid run.
                    runs: list[tuple[int, int]] = []
                    in_run = False
                    rs = 0
                    for x in range(W):
                        if is_solid[x] and not in_run:
                            in_run = True
                            rs = x
                        elif not is_solid[x] and in_run:
                            in_run = False
                            runs.append((rs, x))
                    if in_run:
                        runs.append((rs, W))
                    if not runs:
                        return None
                    # Coalesce runs separated by < 12 px. This merges
                    # multi-digit value clusters (where digits sit ~3-8 px
                    # apart, plus the "." in "32.17"-style values) but
                    # not the larger label-to-value gap (≥15 px on rows
                    # with short labels like MASS).
                    coalesced: list[tuple[int, int]] = [runs[0]]
                    for cur_rs, cur_re in runs[1:]:
                        prev_rs, prev_re = coalesced[-1]
                        if (cur_rs - prev_re) <= 12:
                            coalesced[-1] = (prev_rs, cur_re)
                        else:
                            coalesced.append((cur_rs, cur_re))
                    # The value is the RIGHTMOST coalesced run. Label
                    # text and the value are always separated by a gap
                    # large enough that they don't coalesce, so the
                    # rightmost run IS the value.
                    if not coalesced:
                        return None
                    return int(coalesced[-1][0])

                value_lefts = [
                    v for v in (
                        _value_left_for_row(m) for m in matches.values()
                    ) if v is not None
                ]
                # Compute the MASS-derived fallback up front so we can
                # cross-check the value-column-find result.
                mass = matches["mass"]
                _MASS_FALLBACK = mass["x"] + int(mass["w"] * 0.93)
                if value_lefts:
                    # Take the MIN — leftmost across rows is where the
                    # value column starts. Subtract a margin so the
                    # value crop starts safely BEFORE the leading
                    # digit. 14 px chosen because narrow digits like
                    # "1" have lower-density serifs that don't cross
                    # the "solid" detection threshold; the actual
                    # leftmost pixel of a "1" can be 4-6 px to the
                    # LEFT of where ``_value_left_for_row`` reports
                    # the run starts, AND every caller in api.py adds
                    # ``+6`` to the returned label_right when computing
                    # ``x_min`` for ``_find_value_crop``. Net effective
                    # headroom = 14 − 6 = 8 px, matching the original
                    # design intent and capturing narrow "1"s
                    # consistently. (Was 8 → effective 2 px → chopped
                    # the leading "1" of MASS=11569 / INSTAB=10.82.)
                    candidate = max(0, min(value_lefts) - 14)
                    # Sanity check: a healthy candidate should sit
                    # near the MASS colon. The leading-digit headroom
                    # margin (14 px above) plus a typical 4 px serif
                    # gap can put the candidate up to ~14 px LEFT of
                    # the MASS_FALLBACK colon estimate, so allow a
                    # generous tolerance. Only fall back when the
                    # candidate lands implausibly early (> 16 px left
                    # of MASS_FALLBACK), which suggests the rightmost-
                    # run approach picked up label text instead of
                    # value text (panel too narrow / unusual rendering
                    # / NCC matched poorly).
                    if candidate >= _MASS_FALLBACK - 16:
                        label_right = candidate
                    else:
                        log.debug(
                            "label_rows_from_anchor: value-column-find "
                            "yielded suspicious label_right=%d (< MASS "
                            "fallback %d) — using MASS fallback",
                            candidate, _MASS_FALLBACK,
                        )
                        label_right = _MASS_FALLBACK
                else:
                    label_right = _MASS_FALLBACK
                # Sanity floor: never let label_right exceed 75% of image
                # width — values column always has ≥25% of the panel.
                label_right = min(label_right, int(img.width * 0.75))
                # Per-match diagnostic for the debug log.
                per_match_lr = {
                    k: _label_right_via_gap(m) for k, m in matches.items()
                }

                # Build the result dict. Use NCC matches for Y, synthesize
                # missing rows from MASS using template-height-based pitch.
                _PAD_Y = 4
                mass_match = matches["mass"]
                mass_y_full = search_origin + int(mass_match["y"])
                mass_h = int(mass_match["h"])
                pitch = max(20, int(round(mass_h * 1.4)))

                def _row_box(m: Optional[dict], offset_pitch: int) -> tuple[int, int]:
                    if m is not None:
                        y1 = max(0, search_origin + int(m["y"]) - _PAD_Y)
                        y2 = min(
                            img.height,
                            search_origin + int(m["y"]) + int(m["h"]) + _PAD_Y,
                        )
                    else:
                        cy = mass_y_full + (mass_h // 2) + offset_pitch
                        y1 = max(0, cy - mass_h // 2 - _PAD_Y)
                        y2 = min(img.height, cy + mass_h // 2 + _PAD_Y)
                    return y1, y2

                result: dict[str, tuple[int, int, int]] = {}
                y1, y2 = _row_box(mass_match, 0)
                result["mass"] = (y1, y2, label_right)
                y1, y2 = _row_box(matches.get("resistance"), pitch)
                result["resistance"] = (y1, y2, label_right)
                y1, y2 = _row_box(matches.get("instability"), 2 * pitch)
                result["instability"] = (y1, y2, label_right)
                y1, y2 = _row_box(None, -pitch)  # mineral synthesized
                # Don't let mineral row land above the title bottom.
                if y1 < search_origin - 8:
                    y1 = max(0, search_origin)
                    y2 = min(img.height, search_origin + mass_h)
                result["_mineral_row"] = (y1, y2, label_right)

                log.debug(
                    "label_rows_from_anchor: NCC tier (search_origin=%d, "
                    "matched=%s, per_match_lr=%s, shared_lr=%d)",
                    search_origin,
                    sorted(matches.keys()),
                    per_match_lr, label_right,
                )
                return result
    except Exception as exc:
        log.debug(
            "label_rows_from_anchor: NCC tier failed (%s) — falling "
            "back to measured bands", exc,
        )

    # ── Tier B: measured bands ──
    try:
        gray = np.array(img.convert("L"), dtype=np.uint8)
        search_h = min(img.height - search_origin, 400)
        if search_h >= 80:
            band_strip = gray[search_origin:search_origin + search_h, :]
            text_mask = _build_text_mask(band_strip)
            proj = text_mask.sum(axis=1).astype(np.float32)
            if proj.size >= 9:
                kernel = np.ones(7, dtype=np.float32) / 7.0
                proj = np.convolve(proj, kernel, mode="same")
            if proj.size >= 20 and float(proj.max()) > 0:
                band_thr = max(8.0, float(proj.max()) * 0.12)
                bands_rel: list[tuple[int, int]] = []
                in_band = False
                bs = 0
                for y in range(proj.size):
                    v = float(proj[y])
                    if v >= band_thr and not in_band:
                        in_band = True
                        bs = y
                    elif v < band_thr and in_band:
                        in_band = False
                        bands_rel.append((bs, y))
                if in_band:
                    bands_rel.append((bs, int(proj.size)))
                # Filter to plausible single-text-row heights
                bands_rel = [
                    b for b in bands_rel if 4 <= (b[1] - b[0]) <= 60
                ]
                # Dedupe close-together bands (ascender/x-height splits)
                bands_rel.sort()
                deduped: list[tuple[int, int]] = []
                for b in bands_rel:
                    if deduped:
                        prev = deduped[-1]
                        if ((b[0] + b[1]) // 2 - (prev[0] + prev[1]) // 2) < 12:
                            if (b[1] - b[0]) > (prev[1] - prev[0]):
                                deduped[-1] = b
                            continue
                    deduped.append(b)
                bands_rel = deduped
                # Need at least 4: mineral, mass, resist, instab.
                if len(bands_rel) >= 4:
                    abs_bands = [
                        (search_origin + s, search_origin + e)
                        for s, e in bands_rel[:4]
                    ]
                    # Compute label_right per row by scanning the
                    # leftmost-text run of each band, then take the max
                    # (HUD left-aligns all values to one column).
                    text_mask_full = _build_text_mask(gray)
                    half_w = img.width // 2
                    _GAP = 14
                    per_row_lr: list[int] = []
                    for y1, y2 in abs_bands[1:]:  # skip mineral row
                        col_hot = (
                            text_mask_full[y1:y2, :].sum(axis=0) >= 2
                        )
                        hot = np.where(col_hot[:half_w])[0]
                        if hot.size == 0:
                            continue
                        x_start = int(hot[0])
                        scanned_right = x_start
                        gap_run = 0
                        x = x_start
                        while x < col_hot.shape[0]:
                            if col_hot[x]:
                                scanned_right = x + 1
                                gap_run = 0
                            else:
                                gap_run += 1
                                if gap_run >= _GAP:
                                    break
                            x += 1
                        per_row_lr.append(min(scanned_right, half_w))
                    if per_row_lr:
                        label_right = max(per_row_lr)
                    else:
                        label_right = int(img.width * _VALUE_COL_LEFT_FRAC)

                    keys = ["_mineral_row", "mass", "resistance", "instability"]
                    _PAD = 3
                    result: dict[str, tuple[int, int, int]] = {}
                    for key, (y1, y2) in zip(keys, abs_bands):
                        result[key] = (
                            max(0, y1 - _PAD),
                            min(img.height, y2 + _PAD),
                            label_right,
                        )
                    log.debug(
                        "label_rows_from_anchor: measured bands "
                        "(title_y=%d, title_h=%d, search_origin=%d, "
                        "bands=%d, label_right=%d)",
                        title_y, title_h, search_origin,
                        len(bands_rel), label_right,
                    )
                    return result
    except Exception as exc:
        log.debug(
            "label_rows_from_anchor: measured-bands path failed (%s) "
            "— falling back to fixed multipliers", exc,
        )

    # ── Fallback: fixed proportional offsets ──
    # Used when band detection fails (e.g. capture region too small,
    # tilt corrupts projection too badly to find 4 bands). Less robust
    # than measurement, but at least produces SOMETHING when measurement
    # can't.
    half_h = max(8, int(title_h * _ROW_HEIGHT_MULT * 0.5))
    label_right = int(img.width * _VALUE_COL_LEFT_FRAC)
    result = {}
    for key, mult in _ROW_OFFSET_MULTS.items():
        center_y = title_bottom + int(title_h * (mult - 1.0))
        y1 = max(0, center_y - half_h)
        y2 = min(img.height, center_y + half_h)
        result[key] = (y1, y2, label_right)
    return result


def _find_label_rows_by_hud_grid(
    img: Image.Image,
) -> dict[str, tuple[int, int, int]]:
    """HUD-grid label-row finder — pure geometry, no text detection.

    The SCAN RESULTS panel is a fixed UI grid bounded by two HUD
    chrome separator lines:

        ─── TOP LINE ───  (under "SCAN RESULTS" title)
            Resource (mineral name)
            Mass:        <value>
            Resistance:  <value>
            Instability: <value>
            ( DIFFICULTY )
        ─── BOT LINE ───  (above "COMPOSITION")

    Every row sits at a FIXED FRACTION of the span between these
    two lines. No per-frame band detection, no Tesseract anchor —
    just pick the correct line pair and apply the constants.

    Returns the same dict shape as ``_find_label_rows`` plus the
    special ``_mineral_row`` key. Returns ``{}`` when fewer than
    2 HUD lines are detected (panel not visible / partial capture).
    """
    gray = np.array(img.convert("L"), dtype=np.uint8)
    lines = _get_panel_lines_cached(gray)
    if len(lines) < 2:
        return {}

    # Choose the bracketing line pair. The SCAN RESULTS data area
    # has a span typically 100-400 px depending on capture scale.
    # We pick the FIRST line pair (i, j) where the gap is in that
    # range and i is as high as possible.
    top_y: Optional[int] = None
    bot_y: Optional[int] = None
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            gap = lines[j][0] - lines[i][0]
            if 100 <= gap <= 450:
                top_y = lines[i][0]
                bot_y = lines[j][0]
                break
        if top_y is not None:
            break

    # If no plausible pair, fall back to (lines[0], lines[1]) if
    # they're at least 80 px apart, else give up.
    if top_y is None or bot_y is None:
        if len(lines) >= 2 and (lines[1][0] - lines[0][0]) >= 80:
            top_y = lines[0][0]
            bot_y = lines[1][0]
        else:
            return {}

    span = bot_y - top_y

    # ── Fixed fractions (calibrated from real game panels) ──
    # The data area between the two HUD lines holds 5 rows in
    # roughly even spacing:
    #
    #   ═══ TOP LINE ═══         frac = 0.00
    #   Resource (mineral)       frac = 0.13   ← centered
    #   Mass: <value>            frac = 0.31
    #   Resistance: <value>      frac = 0.49
    #   Instability: <value>     frac = 0.67
    #   ( DIFFICULTY )           frac = 0.86
    #   ═══ BOT LINE ═══         frac = 1.00
    #
    # These are FIXED — the SCAN RESULTS panel is a static UI
    # element. No per-frame guessing.
    _ROW_FRACTIONS = {
        "_mineral_row": 0.13,
        "mass":         0.31,
        "resistance":   0.49,
        "instability":  0.67,
    }

    # Half-row-height: 4.5% of span on each side of center, giving
    # 9% total row height. Row pitch is ~18% of span, so the gap
    # between adjacent rows is ~9% — comfortable margin so the row
    # bands NEVER overlap, which would cause _find_value_crop's
    # y-tightening to grab the wrong row's content.
    half_h = max(8, int(span * 0.045))

    # Value-column-left anchor: a fixed fraction of image width
    # past the longest label. Calibrated to ~52%.
    label_right = int(img.width * 0.52)

    result: dict[str, tuple[int, int, int]] = {}
    for key, frac in _ROW_FRACTIONS.items():
        cy = top_y + int(span * frac)
        result[key] = (
            max(0, cy - half_h),
            min(img.height, cy + half_h),
            label_right,
        )

    log.debug(
        "onnx_hud_reader: HUD grid OK (top=%d, bot=%d, span=%d, "
        "half_h=%d, lr=%d)",
        top_y, bot_y, span, half_h, label_right,
    )

    # Push telemetry to debug overlay.
    try:
        from .sc_ocr import debug_overlay as _dbg
        _dbg.set_hud_lines(lines)
        _dbg.set_panel_finder(
            top_y=top_y,
            mineral_y_top=result["_mineral_row"][0],
            mineral_y_bot=result["_mineral_row"][1],
            mineral_center=(result["_mineral_row"][0] + result["_mineral_row"][1]) // 2,
            pitch=int(span * 0.18),
            bot_line_y=bot_y,
            source="hud_grid",
        )
    except Exception:
        pass

    return result


def _find_label_rows_by_position(
    img: Image.Image,
) -> dict[str, tuple[int, int, int]]:
    """Position-based label-row finder — NO TESSERACT.

    Uses HUD geometry instead of OCR:
      1. Detect the two horizontal HUD separator lines that bracket
         the SCAN RESULTS data area (``_find_panel_lines``).
      2. Run horizontal-projection text-band detection inside the
         band between those lines.
      3. The band always contains exactly 5 text rows in a fixed
         order: [mineral_name, MASS, RESISTANCE, INSTABILITY,
         difficulty_bar]. Assign roles by ORDINAL POSITION — no need
         to read the labels.
      4. For each row, compute the label's right edge (colon
         position) via column-density scan in the left half of the
         row.

    This eliminates Tesseract from the critical row-positioning
    path. Tesseract was the source of "MASS detected at RESISTANCE's
    y" bugs because its LSTM is trained on printed documents and
    misbehaves on bright-sky / colored / anti-aliased HUD text.
    Position-based assignment is structurally immune to that
    failure mode: if 5 bands exist between the lines, they ARE
    [mineral, mass, resist, instab, difficulty].

    Returns the same shape as ``_find_label_rows`` so callers don't
    care which engine produced it. Returns ``{}`` when:
      - Fewer than 2 HUD lines detected (panel not visible)
      - Fewer than 4 text bands between the lines (panel too small
        or sky bleed corrupted the projection)
    """
    gray = np.array(img.convert("L"), dtype=np.uint8)
    lines = _get_panel_lines_cached(gray)
    if not lines:
        return {}

    # ── Multi-anchor row detection ──
    # Single-peak detection was unstable: text rows have ascender +
    # x-height sub-peaks that get counted as separate rows on dark
    # backgrounds, AND merged bands on light backgrounds caused
    # rejections. Multi-anchor approach uses 3 independent anchors:
    #   ANCHOR 1: top HUD line (above mineral name)
    #   ANCHOR 2: mineral-name BAND (first text band below top line)
    #   ANCHOR 3: row pitch (panel-scaled, refined from line pair if
    #             both top and bottom lines are detected)
    # Then MASS/RESIST/INSTAB y-positions are EXTRAPOLATED from the
    # mineral-name anchor using fixed pitch. Only one anchor needs
    # to be correct for the whole geometry to fall out — and the
    # mineral name is the easiest to detect because it's always the
    # FIRST text band right below the top HUD line.
    top_y = lines[0][0]

    # Search starts a few px BELOW top_y to skip the HUD line itself.
    # The HUD line is bright enough (yellow ~180+ gray) to register
    # as text in the polarity mask. Without this offset the very
    # first detected band IS the line, which then becomes "Resource"
    # and shifts every subsequent row assignment by one slot.
    _LINE_SKIP = 5
    search_origin = top_y + _LINE_SKIP
    search_h = min(img.height - search_origin, 250)
    if search_h < 80:
        return {}
    band = gray[search_origin:search_origin + search_h, :]
    text_mask = _build_text_mask(band)
    proj = text_mask.sum(axis=1).astype(np.float32)
    if proj.size < 20 or float(proj.max()) <= 0:
        return {}

    # Heavy smoothing (7-px box) to merge ascender+x-height sub-peaks
    # within one row into a single peak.
    if proj.size >= 9:
        kernel = np.ones(7, dtype=np.float32) / 7.0
        proj = np.convolve(proj, kernel, mode="same")

    # ── DIRECT 5-band detection (no extrapolation) ──
    # The SCAN RESULTS panel between the top and bottom HUD lines
    # contains EXACTLY 5 text bands in a fixed order:
    #   index 0: Resource (mineral name, e.g. "BERYL (RAW)")
    #   index 1: Mass row
    #   index 2: Resistance row
    #   index 3: Instability row
    #   index 4: Outcome bar (EASY / MEDIUM / HARD / EXTREME / IMPOSSIBLE)
    #
    # Previously we tried to extrapolate from the mineral row using a
    # single pitch value. That meant any error in mineral detection
    # cascaded — and on this panel the SCAN RESULTS title's HUD
    # underline kept being picked up as the "first band" instead of
    # the actual mineral name, which shifted every downstream row by
    # one slot.
    #
    # Direct assignment by ordinal position is structurally immune to
    # that whole class of bug: detect all bands, assign by index.
    band_thr = max(8.0, float(proj.max()) * 0.12)
    bands: list[tuple[int, int, float]] = []  # (y_start, y_end, peak)
    in_band = False
    bs = 0
    for y in range(proj.size):
        v = float(proj[y])
        if v >= band_thr and not in_band:
            in_band = True
            bs = y
        elif v < band_thr and in_band:
            in_band = False
            bands.append((bs, y, float(proj[bs:y].max())))
    if in_band:
        bands.append((bs, int(proj.size), float(proj[bs:].max())))

    if not bands:
        log.debug(
            "onnx_hud_reader: position-based — no bands found "
            "below top_line=%d (max_proj=%.1f, threshold=%.1f)",
            top_y, float(proj.max()), band_thr,
        )
        return {}

    # Filter by reasonable height for a single text row.
    # Outcome bar (rendering trick) can be slightly taller (~35 px),
    # so we use a 60 px ceiling. Floor at 4 px to drop any 1-px
    # divider artifacts.
    bands = [b for b in bands if 4 <= (b[1] - b[0]) <= 60]

    if len(bands) < 4:
        log.debug(
            "onnx_hud_reader: position-based — only %d bands found "
            "(need at least 4 for mineral+mass+resist+instab)",
            len(bands),
        )
        return {}

    # If the panel finder over-detected (sometimes happens when
    # a thin separator line between resource and mass survives the
    # height filter), drop bands that are very close to a stronger
    # neighbour: any pair with center-to-center distance < 12 px,
    # keep the taller one.
    bands.sort(key=lambda b: b[0])
    deduped: list[tuple[int, int, float]] = []
    for b in bands:
        if deduped:
            prev = deduped[-1]
            prev_center = (prev[0] + prev[1]) // 2
            this_center = (b[0] + b[1]) // 2
            if (this_center - prev_center) < 12:
                # Merge — keep whichever has the higher peak
                if b[2] > prev[2]:
                    deduped[-1] = b
                continue
        deduped.append(b)
    bands = deduped

    # ── Skip the SCAN RESULTS title if it accidentally got into bands ──
    # When the line detector picks up a decorative element above the
    # SCAN RESULTS title (a known false positive on some captures),
    # top_y lands above the title. The first text band found is then
    # the title itself, NOT the mineral name. Each row assignment
    # below shifts down by one slot: mass→mineral, resist→mass, etc.
    #
    # We use THREE detectors layered top-to-bottom; the first one to
    # fire drops bands[0] and the rest are skipped. Each is
    # independent so a single failure mode (e.g. tilted underline that
    # _find_panel_lines rejects) doesn't disable all three.
    #
    #   Signal A — HUD-LINE-BETWEEN: a HUD line sits strictly between
    #     bands[0] and bands[1]. Most reliable when the underline is
    #     extracted as a line (axis-aligned, full-width).
    #
    #   Signal B — OUTCOME-HEIGHT: with 5 bands, the last one should
    #     be the outcome progress bar (EASY/MEDIUM/HARD/...) which is
    #     ~1.4-2× taller than text rows. If bands[4] is no taller
    #     than the median of bands[1..3], bands[4] is just another
    #     text row — meaning we're seeing [title, mineral, mass,
    #     resist, instab] and the outcome bar fell outside the search
    #     window. Drop bands[0].
    #
    #   Signal C — PITCH-OUTLIER: data rows sit at uniform pitch.
    #     If bands[0]→bands[1] pitch is meaningfully larger than the
    #     median pitch of bands[1..3] consecutive pairs, bands[0] is
    #     the title separated by underline+padding. Backup for cases
    #     where Signals A and B both fail.
    title_dropped = False
    if len(bands) >= 2 and lines:
        b0_end_abs = search_origin + bands[0][1]
        b1_start_abs = search_origin + bands[1][0]
        for ly, _, _ in lines:
            if b0_end_abs < ly < b1_start_abs:
                log.debug(
                    "onnx_hud_reader: dropping bands[0] (SCAN RESULTS "
                    "title) — Signal A: HUD line at y=%d between "
                    "band[0] (ends y=%d) and band[1] (starts y=%d)",
                    ly, b0_end_abs, b1_start_abs,
                )
                bands = bands[1:]
                title_dropped = True
                break

    if not title_dropped and len(bands) >= 5:
        # Signal B: outcome-bar height check.
        band_heights = [b[1] - b[0] for b in bands]
        # Median height of presumed text rows under the assumption
        # that bands[0] is the title (so bands[1..3] are mineral,
        # mass, resist — all text rows of similar height).
        sorted_inner_h = sorted(band_heights[1:4])
        inner_median_h = sorted_inner_h[len(sorted_inner_h) // 2]
        outcome_h = band_heights[4]
        if inner_median_h > 0 and outcome_h <= inner_median_h * 1.15:
            log.debug(
                "onnx_hud_reader: dropping bands[0] (SCAN RESULTS "
                "title) — Signal B: bands[4] height %d <= 1.15 × "
                "median data-row height %d (outcome bar absent — "
                "5 text rows means [title, mineral, mass, resist, "
                "instab])",
                outcome_h, inner_median_h,
            )
            bands = bands[1:]
            title_dropped = True

    if not title_dropped and len(bands) >= 4:
        # Signal C: pitch-outlier check.
        # Use pitches between bands[1..3] consecutive pairs as the
        # reference. With 4+ bands we have at least 2 inner pairs;
        # take their median.
        inner_pitches = [
            bands[i + 1][0] - bands[i][0]
            for i in range(1, min(4, len(bands) - 1))
        ]
        if inner_pitches:
            sorted_inner_p = sorted(inner_pitches)
            median_inner_p = sorted_inner_p[len(sorted_inner_p) // 2]
            pitch_0_to_1 = bands[1][0] - bands[0][0]
            if median_inner_p > 0 and pitch_0_to_1 > median_inner_p * 1.4:
                log.debug(
                    "onnx_hud_reader: dropping bands[0] (SCAN RESULTS "
                    "title) — Signal C: bands[0]→bands[1] pitch %d "
                    "> 1.4 × median inner pitch %d",
                    pitch_0_to_1, median_inner_p,
                )
                bands = bands[1:]
                title_dropped = True

    # Take the first 5 bands — Resource, Mass, Resistance, Instability,
    # Outcome (in that order). If fewer than 5, the outcome bar is
    # presumed missing (rare); we still assign indices 0..3.
    bands = bands[:5]

    # Anchor outputs:
    #   mineral row     = bands[0]
    #   mass / resist / instab rows = bands[1] / bands[2] / bands[3]
    mineral_y_rel, mineral_y_end_rel, _ = bands[0]
    mineral_center_rel = (mineral_y_rel + mineral_y_end_rel) // 2
    mineral_y_abs = search_origin + mineral_center_rel

    # ── DIRECT band assignment (no extrapolation) ──
    # bands[0] = Resource (mineral), bands[1] = Mass,
    # bands[2] = Resistance, bands[3] = Instability,
    # bands[4] = Outcome (if present, only used for telemetry).
    # If we have only 4 bands (outcome bar undetected on a noisy
    # frame), still take indices 1..3 for the value rows.
    label_keys = ["mass", "resistance", "instability"]
    if len(bands) < 4:
        log.debug(
            "onnx_hud_reader: position-based — only %d bands after "
            "dedupe (need 4+ for mass/resist/instab)", len(bands),
        )
        return {}

    # Convert all 5 bands to absolute image coordinates.
    abs_band_rows = [(search_origin + s, search_origin + e) for s, e, _ in bands]

    # Pick mass/resist/instab in order. Add small ±_PAD so character
    # ascenders/descenders aren't clipped (the existing _PAD constant
    # below is used for the same purpose at output time, but we want
    # the box to be a bit wider here so OCR has breathing room).
    target_rows = [
        abs_band_rows[1],   # mass
        abs_band_rows[2],   # resistance
        abs_band_rows[3],   # instability
    ]

    # Compute pitch from the actual measured spacing between detected
    # bands (used by downstream consumers / debug overlay).
    spacings = [
        abs_band_rows[i + 1][0] - abs_band_rows[i][0]
        for i in range(min(3, len(abs_band_rows) - 1))
    ]
    if spacings:
        pitch = int(round(sum(spacings) / len(spacings)))
    else:
        pitch = 30  # fallback

    # Find the bottom HUD line for telemetry only (not used for pitch
    # anymore). Range is generous because some captures stretch the
    # panel vertically.
    bot_line_y: Optional[int] = None
    for ly, _, _ in lines[1:]:
        if 100 <= ly - top_y <= 600:
            bot_line_y = ly
            break

    # Compute label-right (colon position) per row via column-density
    # scan in the left half. Pure NumPy, no OCR.
    text_mask = _build_text_mask(gray, deviation=30)
    half_w = img.width // 2
    _PAD = 3
    _GAP_THRESHOLD = 14
    # Per-key fallback right-edge fractions (used only when the
    # column scan fails to find any label pixels).
    _FALLBACK_RIGHT_FRAC = {"mass": 0.18, "resistance": 0.34, "instability": 0.36}

    # First pass: scan each row's label-right (colon position).
    per_row_label_right: dict[str, int] = {}
    for key, (y1, y2) in zip(label_keys, target_rows):
        col_hot = text_mask[y1:y2, :].sum(axis=0) >= 2
        hot_idxs = np.where(col_hot[:half_w])[0]
        if hot_idxs.size == 0:
            per_row_label_right[key] = int(img.width * _FALLBACK_RIGHT_FRAC[key])
            continue
        x_start = int(hot_idxs[0])
        scanned_right = x_start
        gap_run = 0
        x = x_start
        while x < col_hot.shape[0]:
            if col_hot[x]:
                scanned_right = x + 1
                gap_run = 0
            else:
                gap_run += 1
                if gap_run >= _GAP_THRESHOLD:
                    break
            x += 1
        per_row_label_right[key] = min(scanned_right, half_w)

    # ── Shared value-column anchor ──
    # The HUD left-aligns ALL three values to a SINGLE column whose
    # left edge is past the LONGEST label (INSTABILITY:). MASS,
    # RESISTANCE, and INSTABILITY values therefore all start at the
    # same x. Use the MAX label-right across rows as the shared
    # value-column-left anchor — every row's value crop uses this
    # same x_min downstream.
    shared_label_right = max(per_row_label_right.values())

    result: dict[str, tuple[int, int, int]] = {}
    for key, (y1, y2) in zip(label_keys, target_rows):
        result[key] = (
            max(0, y1 - _PAD),
            min(img.height, y2 + _PAD),
            shared_label_right,
        )
    # ── Surface the mineral-name row too ──
    # Stored under a leading-underscore key so callers iterating the
    # dict for label rows (mass/resistance/instability) won't pick it
    # up by accident. scan_hud_onnx uses this to OCR the mineral
    # name with the alphabet model (or Tesseract as placeholder)
    # and add it to the scan result.
    result["_mineral_row"] = (
        max(0, search_origin + mineral_y_rel - _PAD),
        min(img.height, search_origin + mineral_y_end_rel + _PAD),
        shared_label_right,
    )
    log.debug(
        "onnx_hud_reader: label_rows_by_position OK "
        "(top_line=%d, search_origin=%d, mineral_y=%d, pitch=%d, "
        "bot_line=%s, bands=%d, shared_label_right=%d, mass_y=%d-%d)",
        top_y, search_origin, mineral_y_abs, pitch, bot_line_y,
        len(bands), shared_label_right,
        result["mass"][0], result["mass"][1],
    )
    # Stash telemetry for the debug overlay viewer.
    try:
        from .sc_ocr import debug_overlay as _dbg
        _dbg.set_hud_lines(lines)
        _dbg.set_panel_finder(
            top_y=top_y,
            mineral_y_top=search_origin + (mineral_y_rel or 0),
            mineral_y_bot=search_origin + (mineral_y_end_rel or 0),
            mineral_center=mineral_y_abs,
            pitch=pitch,
            bot_line_y=bot_line_y,
            source="by_position",
        )
    except Exception:
        pass
    return result


def _find_label_rows_by_ncc(
    img: Image.Image,
) -> dict[str, tuple[int, int, int]]:
    """NCC-template-match adapter — wraps ``label_match.find_label_positions``
    into the standard ``_find_label_rows`` return shape.

    Returns the standard dict with keys mass/resistance/instability/
    _mineral_row, where:
      * Each row's y1, y2 = match.y - small_pad, match.y + match.h + small_pad
      * shared label_right = max of (match.x + match.w) across the 3
        matched labels — that's the rightmost colon position, used as
        the value-column-left anchor for ALL rows (HUD values are
        left-aligned to a single column).
      * mineral row is synthesized from MASS row position minus one
        pitch (computed from observed row spacing).

    Returns ``{}`` on no/insufficient matches → caller falls back.
    """
    try:
        from .sc_ocr import label_match
    except Exception as exc:
        log.debug("label_match import failed: %s", exc)
        return {}

    matches = label_match.find_label_positions(img)
    # MASS is the anchor. If we don't have MASS, we have nothing
    # reliable to anchor on — fall back. (Other rows can be missing;
    # we synthesize them from MASS via fixed pitch.)
    if "mass" not in matches:
        log.debug(
            "label_match: MASS not matched — falling back "
            "(matches=%s)", list(matches.keys()),
        )
        return {}

    _PAD_Y = 4

    # Compute shared_label_right = rightmost ACTUAL text column across
    # all matched label regions. Don't trust the template's reported
    # width — bootstrap templates were extracted with padding AND
    # Tesseract often over-estimated the bbox width, so match.x +
    # match.w lands ~30-50 px past the colon (in the value area).
    #
    # For each matched label, scan the matched x-range for the
    # rightmost column with significant text density. That column is
    # the colon. Take the max across labels.
    try:
        gray_full = np.array(img.convert("L"), dtype=np.uint8)
        rgb_full = np.array(img.convert("RGB"), dtype=np.uint8)
        detect_full = rgb_full.max(axis=2).astype(np.uint8)
    except Exception:
        gray_full = None
        detect_full = None

    def _scan_actual_label_right(m: dict) -> int:
        if detect_full is None:
            return m["x"] + m["w"]
        x1 = max(0, m["x"])
        x2 = min(detect_full.shape[1], m["x"] + m["w"])
        y1 = max(0, m["y"])
        y2 = min(detect_full.shape[0], m["y"] + m["h"])
        if x2 <= x1 or y2 <= y1:
            return m["x"] + m["w"]
        region = detect_full[y1:y2, x1:x2]
        # Polarity-canonicalize so text is BRIGHT
        thr = _otsu(region)
        bright = int((region > thr).sum())
        if (region.size - bright) < bright:
            region_canon = (255 - region).astype(np.uint8)
        else:
            region_canon = region
        thr2 = _otsu(region_canon)
        col_density = (region_canon > thr2).sum(axis=0)
        # Rightmost column with at least 25% of region height as text
        floor = max(2, int((y2 - y1) * 0.25))
        idxs = np.where(col_density >= floor)[0]
        if idxs.size == 0:
            return m["x"] + m["w"]
        # Convert local idx to image-coord x and add small margin (the
        # colon's right edge halo).
        return x1 + int(idxs[-1]) + 2

    # ── MASS is the anchor ──
    # MASS_y, MASS_h define the entire panel geometry. Other rows are
    # at fixed proportional offsets from MASS. Other matches (RESIST,
    # INSTAB) are CROSS-CHECKS: if they line up with MASS-derived
    # predictions, great; if not, we trust MASS and synthesize the
    # others. This prevents one bad NCC false-positive (e.g. RESIST
    # matched to asteroid noise far below the real panel) from
    # dragging the whole row stack off-panel.
    mass_match = matches["mass"]
    mass_cy = mass_match["y"] + mass_match["h"] // 2
    mass_h = mass_match["h"]

    # ── Pitch (vertical distance between adjacent rows) ──
    # Three sources, in order of preference:
    #
    # 1. HUD line pair: top_line under SCAN RESULTS + bot_line above
    #    COMPOSITION bracket the data area, which holds 5 rows
    #    (mineral + mass + resist + instab + outcome). pitch =
    #    line_gap / 5. This is MEASURED, not guessed — most reliable.
    #
    # 2. Observed MASS→RESISTANCE distance: if RESISTANCE was also
    #    matched at a plausible position below MASS, take that delta.
    #
    # 3. Fallback: mass_h × 1.0 (template height). Used only when
    #    neither HUD lines nor RESISTANCE match are available.
    pitch: Optional[int] = None
    pitch_source = "fallback"

    # Source 1: HUD line pair
    try:
        gray_for_lines = (
            gray_full if gray_full is not None
            else np.array(img.convert("L"), dtype=np.uint8)
        )
        lines = _get_panel_lines_cached(gray_for_lines)
        if len(lines) >= 2:
            # Find a line pair where the top line is above MASS and
            # the bottom line is below MASS by at least 2 pitches.
            ys = sorted({ly for ly, _, _ in lines})
            top_candidates = [y for y in ys if y < mass_cy]
            bot_candidates = [y for y in ys if y > mass_cy]
            if top_candidates and bot_candidates:
                top_y_line = max(top_candidates)
                bot_y_line = min(bot_candidates)
                # Search for the line pair that brackets the largest
                # plausible data area below mass. The default-and-
                # nearest pair is the safest first guess.
                line_gap = bot_y_line - top_y_line
                # If gap is in plausible range, derive pitch.
                if 80 <= line_gap <= 600:
                    candidate_pitch = int(round(line_gap / 5.0))
                    if 15 <= candidate_pitch <= 90:
                        pitch = candidate_pitch
                        pitch_source = (
                            f"line_pair (top={top_y_line}, "
                            f"bot={bot_y_line}, gap={line_gap})"
                        )
    except Exception as _exc:
        log.debug("label_match: line-pair pitch failed: %s", _exc)

    # Source 2: observed MASS→RESISTANCE distance
    if "resistance" in matches:
        rmass_y = matches["resistance"]["y"] + matches["resistance"]["h"] // 2
        observed_pitch = rmass_y - mass_cy
        # Only trust if plausible AND consistent with line-pair pitch.
        if 15 <= observed_pitch <= 90:
            if pitch is None:
                pitch = observed_pitch
                pitch_source = f"resist_match (observed={observed_pitch})"
            elif abs(observed_pitch - pitch) < pitch * 0.20:
                # Cross-check: observed agrees with line-pair within 20%.
                # Use observed (more direct measurement).
                pitch = observed_pitch
                pitch_source = (
                    f"resist_match (observed={observed_pitch}, "
                    f"line_pair_agreed)"
                )

    # Source 3: fallback to template height
    if pitch is None:
        pitch = max(15, int(round(mass_h * 1.0)))
        pitch_source = f"fallback (mass_h={mass_h})"

    log.debug("label_match: pitch=%d (%s)", pitch, pitch_source)

    # Row half-height MUST be smaller than pitch/2 so adjacent rows
    # don't overlap (which would let RESISTANCE crop pick up the
    # INSTABILITY row below it, INSTABILITY pick up the EASY bar,
    # etc.). Use 40% of pitch — leaves a 20% gap between rows.
    half_h = max(8, int(pitch * 0.40))

    # ── Compute shared_label_right by direct per-row scan ──
    # Values are LEFT-ALIGNED to the column just past the LONGEST
    # label (INSTABILITY:). Don't infer from templates — JUST SCAN
    # EACH ROW for where its label ends in actual pixels.
    #
    # Per row:
    #   1. Crop the row strip
    #   2. Polarity-canonicalize, Otsu threshold
    #   3. Find the FIRST contiguous bright text cluster from the left
    #      (that's the label)
    #   4. The rightmost column of that cluster = the colon
    # shared_label_right = max across all rows
    def _row_label_end(row_y1: int, row_y2: int) -> Optional[int]:
        if detect_full is None:
            return None
        ry1 = max(0, row_y1)
        ry2 = min(detect_full.shape[0], row_y2)
        if ry2 - ry1 < 4:
            return None
        # Use only the LEFT 60% of the image — the value column lives
        # in the right 40% and we don't want to scan into it for the
        # label end.
        rx2 = int(detect_full.shape[1] * 0.60)
        region = detect_full[ry1:ry2, :rx2]
        if region.size == 0:
            return None
        # Polarity-canonicalize so text is BRIGHT
        thr = _otsu(region)
        bright = int((region > thr).sum())
        if (region.size - bright) < bright:
            region_canon = (255 - region).astype(np.uint8)
        else:
            region_canon = region
        thr2 = _otsu(region_canon)
        col_d = (region_canon > thr2).sum(axis=0)
        floor = max(3, int((ry2 - ry1) * 0.25))
        hot = col_d >= floor
        if not hot.any():
            return None
        # Find FIRST contiguous bright run (the label).
        idxs = np.where(hot)[0]
        first_hot = int(idxs[0])
        # Walk right from first_hot, allowing small inter-letter gaps
        # but breaking on the BIG gap between label and value.
        # ``gap >= 5`` matches ``_INTRA_LABEL_GAP`` in the Tier A NCC
        # path: SC's HUD has 1-3 px between letters but 8-15 px after
        # the colon, so 5 cleanly stops at the colon. Using 12 (the
        # historical value) BRIDGED into the leading "1" of values
        # like 11569 / 10.82, chopping the digit off the value crop
        # downstream. See _label_rows_from_anchor for the same pattern.
        last_hot = first_hot
        gap = 0
        i = first_hot
        while i < hot.size:
            if hot[i]:
                last_hot = i
                gap = 0
            else:
                gap += 1
                if gap >= 5:
                    break
            i += 1
        return int(last_hot) + 2  # small margin for anti-alias halo

    # Predict approximate row positions from MASS anchor + pitch
    # (not yet finalized — that's done in the row_offsets loop below,
    # but we need rough y-bounds NOW to scan for label ends).
    rough_pitch = pitch
    rough_half_h = mass_h // 2 + _PAD_Y
    label_ends: list[int] = []
    for mult in (0, 1, 2):  # mass, resistance, instability rows
        cy = mass_cy + mult * rough_pitch
        end = _row_label_end(cy - rough_half_h, cy + rough_half_h)
        if end is not None:
            label_ends.append(end)

    if label_ends:
        shared_label_right = max(label_ends)
    else:
        # Fallback: MASS template's actual colon scan
        shared_label_right = _scan_actual_label_right(mass_match)

    # ── Synthesize all 4 rows from MASS anchor + fixed offsets ──
    #   _mineral_row = MASS_y - pitch
    #   mass         = MASS_y                (the anchor)
    #   resistance   = MASS_y + pitch
    #   instability  = MASS_y + 2 × pitch
    result: dict[str, tuple[int, int, int]] = {}
    row_offsets = [
        ("_mineral_row", -1),
        ("mass",          0),
        ("resistance",    1),
        ("instability",   2),
    ]
    for key, mult in row_offsets:
        cy = mass_cy + mult * pitch
        y1 = max(0, cy - half_h)
        y2 = min(img.height, cy + half_h)
        if y2 - y1 < 6:
            continue
        result[key] = (y1, y2, shared_label_right)

    log.debug(
        "label_match: rows OK (mass_y=%d-%d, resist_y=%d-%d, "
        "instab_y=%d-%d, shared_lr=%d)",
        result["mass"][0], result["mass"][1],
        result["resistance"][0], result["resistance"][1],
        result["instability"][0], result["instability"][1],
        shared_label_right,
    )

    # Telemetry for debug overlay.
    try:
        from .sc_ocr import debug_overlay as _dbg
        _dbg.set_panel_finder(
            top_y=None,
            mineral_y_top=result["_mineral_row"][0],
            mineral_y_bot=result["_mineral_row"][1],
            mineral_center=(result["_mineral_row"][0] + result["_mineral_row"][1]) // 2,
            pitch=pitch,
            bot_line_y=None,
            source="ncc_label_match",
        )
    except Exception:
        pass

    return result


# Per-thread "current region" stash. The OCR pipeline (api.py
# scan_hud_onnx) sets this before calling _find_label_rows so we can
# look up persistent calibration without changing _find_label_rows'
# signature (which has dozens of callers across legacy code paths).
# threading.local() ensures each scan-pool worker sees its own region
# value, preventing cross-contamination when up to 64 scans run in
# parallel.
_thread_local = threading.local()


def _set_current_region(region: Optional[dict]) -> None:
    _thread_local.current_region = region


def _get_current_region() -> Optional[dict]:
    return getattr(_thread_local, "current_region", None)


# Rate-limit calibration-state logging so we don't spam the log on every
# scan. Re-log only when the region changes or the load result flips.
_calibration_log_state: dict = {"key": None, "loaded": None}


def _log_calibration_state(region: dict, cal_result) -> None:
    key = (
        int(region.get("x", 0)), int(region.get("y", 0)),
        int(region.get("w", 0)), int(region.get("h", 0)),
    )
    loaded = bool(cal_result)
    if (
        _calibration_log_state["key"] == key
        and _calibration_log_state["loaded"] == loaded
    ):
        return
    _calibration_log_state["key"] = key
    _calibration_log_state["loaded"] = loaded
    if loaded:
        log.info(
            "calibration: USING saved rows for region=%s fields=%s "
            "(detection skipped)",
            key, sorted(cal_result.keys()),
        )
    else:
        # Inspect the file to give an actionable reason.
        try:
            from .sc_ocr import calibration as _cal
            cal = _cal.load({"x": key[0], "y": key[1], "w": key[2], "h": key[3]})
            if cal is None:
                reason = "no entry for this region key"
            else:
                rows = cal.get("rows") or {}
                missing = [
                    f for f in ("mass", "resistance", "instability")
                    if f not in rows
                ]
                if not rows:
                    reason = "entry exists but rows={} (no rows locked)"
                elif missing:
                    reason = f"missing rows: {missing}"
                else:
                    reason = "rows present but to_label_rows returned None"
        except Exception as exc:
            reason = f"load failed: {exc}"
        log.warning(
            "calibration: NOT applied for region=%s — %s — falling back "
            "to live detection (boxes will move scan-to-scan)",
            key, reason,
        )


def _find_label_rows(img: Image.Image) -> dict[str, tuple[int, int, int]]:
    """Find MASS / RESIST / INSTAB rows.

    Strategy:
      0. CALIBRATION: if the user has saved per-row calibration via
         the Calibration Dialog, return those coordinates DIRECTLY.
         No detection, no drift. This is the steady-state path
         after first-time setup.
      1. NCC label template matching (auto-detect)
      2. Position-based 5-band scan
      3. HUD-grid fractional fallback
      4. Tesseract per-label search (deepest fallback)
    """
    # ── ZEROTH: persistent calibration ──
    # If the user has saved calibration for the current region, use it
    # and skip ALL detection.
    _region = _get_current_region()
    if _region is not None:
        try:
            from .sc_ocr import calibration as _cal
            cal_result = _cal.to_label_rows(
                _region, img.width, img.height, img=img,
            )
            # One-shot diagnostic: log whether calibration is loaded for
            # this region so the user can verify in the log file. The
            # rate-limiter guards against per-scan log spam.
            try:
                _log_calibration_state(_region, cal_result)
            except Exception:
                pass
            if cal_result:
                # Push telemetry to debug overlay
                try:
                    from .sc_ocr import debug_overlay as _dbg
                    # Diagnostic-only: try to also locate the SCAN
                    # RESULTS title via NCC so the gold box is drawn
                    # even when calibration short-circuits the
                    # detection cascade. Lets the user visually check
                    # whether the saved row positions agree with the
                    # live anchor — if the gold box is high above the
                    # cyan rows, the calibration is stale and needs
                    # to be re-locked.
                    _diag_title_box: Optional[tuple[int, int, int, int]] = None
                    try:
                        from .sc_ocr import scan_results_match as _srm_diag
                        _diag_anchor = _srm_diag.find_scan_results_anchor(img)
                        if _diag_anchor is not None:
                            _diag_title_box = (
                                int(_diag_anchor["title_x"]),
                                int(_diag_anchor["title_y"]),
                                int(_diag_anchor["title_w"]),
                                int(_diag_anchor["title_h"]),
                            )
                    except Exception:
                        pass
                    _dbg.set_panel_finder(
                        top_y=None,
                        mineral_y_top=cal_result.get("_mineral_row", (0, 0, 0))[0],
                        mineral_y_bot=cal_result.get("_mineral_row", (0, 0, 0))[1],
                        mineral_center=None,
                        pitch=None,
                        bot_line_y=None,
                        source="calibration",
                        title_box=_diag_title_box,
                    )
                except Exception:
                    pass
                log.debug(
                    "label_rows from calibration: %s",
                    {k: v for k, v in cal_result.items()},
                )
                return cal_result
        except Exception as exc:
            log.debug("calibration lookup failed: %s", exc)

    # ── PRIMARY: SCAN RESULTS title anchor ──
    # The "SCAN RESULTS" title is the most stable feature of the rock-
    # scan panel: large bold static text, always at the top, identical
    # across every rock, every panel scale, every HUD color. Once we
    # locate it, every other row's position is a known proportional
    # offset from the title — no per-frame guessing, no risk of NCC
    # false positives in the COMPOSITION rows below the panel data.
    #
    # This runs BEFORE label-template NCC because NCC can be fooled by
    # COMPOSITION rows when the user's capture region extends past the
    # SCAN RESULTS panel (e.g. tall regions that include "RAW SILICON
    # 245" / "HEPHAESTANITE (RAW) 96" — those texts contain glyphs
    # that NCC-correlate against MASS/RESIST/INSTAB templates and
    # produce convincing-but-wrong row positions).
    #
    # Cache the anchor for 5 s per (image-size) so we only pay the
    # Tesseract cost when the panel first appears or after a position
    # shift. Re-detection on cache expiry handles slow drift; abrupt
    # position changes are caught by the row-validation step
    # downstream (a wildly mis-aligned anchor produces no readable
    # values, which clears the lock cache and re-detects).
    try:
        import time as _t_anchor
        _anchor_key = (img.width, img.height)
        _now = _t_anchor.monotonic()
        _cached = _scan_results_anchor_cache.get(_anchor_key)
        anchor: Optional[dict] = None
        if _cached is not None and (_now - _cached[0]) < 5.0:
            anchor = _cached[1]
        else:
            # Try NCC anchor first (template-based, ~5 ms, tilt-tolerant).
            # Fall through to Tesseract if the NCC template is missing
            # or no scale crosses the confidence threshold.
            try:
                from .sc_ocr import scan_results_match as _srm
                anchor = _srm.find_scan_results_anchor(img)
            except Exception as _srm_exc:
                log.debug(
                    "scan_results_match unavailable, falling back to "
                    "Tesseract anchor: %s", _srm_exc,
                )
                anchor = None
            if anchor is None:
                anchor = _find_scan_results_anchor(img)
            if anchor is not None:
                _scan_results_anchor_cache[_anchor_key] = (_now, anchor)
        if anchor is not None:
            anchor_result = _label_rows_from_anchor(img, anchor)
            if anchor_result:
                # Push telemetry to the debug overlay so the panel
                # finder shows where SCAN RESULTS was located.
                try:
                    from .sc_ocr import debug_overlay as _dbg
                    _mineral = anchor_result.get(
                        "_mineral_row", (0, 0, 0)
                    )
                    # Measured pitch from the actual row geometry —
                    # more accurate than title_h * 1.4 (which is wrong
                    # whenever Tesseract's bbox on a tilted title
                    # inflates title_h).
                    _mass_row = anchor_result.get("mass")
                    if _mineral and _mass_row:
                        _measured_pitch = (
                            (_mass_row[0] + _mass_row[1]) // 2
                            - (_mineral[0] + _mineral[1]) // 2
                        )
                    else:
                        _measured_pitch = int(anchor["title_h"] * 1.4)
                    _dbg.set_panel_finder(
                        top_y=anchor["title_y"],
                        mineral_y_top=_mineral[0],
                        mineral_y_bot=_mineral[1],
                        mineral_center=(_mineral[0] + _mineral[1]) // 2,
                        pitch=_measured_pitch,
                        bot_line_y=None,
                        source="scan_results_anchor",
                        title_box=(
                            int(anchor["title_x"]),
                            int(anchor["title_y"]),
                            int(anchor["title_w"]),
                            int(anchor["title_h"]),
                        ),
                    )
                except Exception:
                    pass
                log.debug(
                    "label_rows from SCAN RESULTS anchor "
                    "(title @ x=%d y=%d w=%d h=%d): %s",
                    anchor["title_x"], anchor["title_y"],
                    anchor["title_w"], anchor["title_h"],
                    {k: v for k, v in anchor_result.items()
                     if not k.startswith("_")},
                )
                return anchor_result
    except Exception as exc:
        log.debug("SCAN RESULTS anchor path failed: %s", exc)

    # ── SECONDARY: NCC label template matching ──
    # Concrete per-row pixel positions from matching the rendered
    # MASS:/RESISTANCE:/INSTABILITY: label templates against the
    # panel image. No geometry inference, no Tesseract subprocess —
    # just NumPy correlation against canonicalized templates.
    # Cached per region in the caller.
    ncc_result = _find_label_rows_by_ncc(img)
    if ncc_result:
        return ncc_result

    # ── TERTIARY: position-based finder (5-band scan from actual text) ──
    pos_result = _find_label_rows_by_position(img)
    if pos_result:
        return pos_result

    # ── QUATERNARY: HUD-line-bracketed grid + fixed fractions ──
    grid_result = _find_label_rows_by_hud_grid(img)
    if grid_result:
        return grid_result

    # ── Tesseract per-label fallback (deepest) ──
    if not _check_tesseract():
        return {}
    try:
        import pytesseract
    except ImportError:
        return {}

    w_img, h_img = img.size
    left = img.crop((0, 0, int(w_img * 0.55), h_img))
    gray = np.array(left.convert("L"), dtype=np.uint8)
    rgb = np.array(left.convert("RGB"), dtype=np.uint8)
    max_ch = rgb.max(axis=2).astype(np.uint8)

    # Three candidate binaries. Text is ALWAYS BLACK in the output —
    # Tesseract is trained on printed-document style (dark ink on
    # white paper) and performs best with that polarity.
    thr_gray = _otsu(gray)
    thr_max = _otsu(max_ch)

    candidates = [
        # (a) Gray Otsu — bright-on-dark HUD: text is above thr, we
        # render above-thr as BLACK so text comes out black.
        ("gray_bright", np.where(gray > thr_gray, 0, 255).astype(np.uint8)),
        # (b) Gray Otsu inverted — dark-on-bright HUD: text is below
        # thr, render below-thr as BLACK.
        ("gray_dark",   np.where(gray < thr_gray, 0, 255).astype(np.uint8)),
        # (c) Max-of-channels Otsu — colored text (red RESISTANCE):
        ("max_bright",  np.where(max_ch > thr_max, 0, 255).astype(np.uint8)),
    ]

    # 4-character prefix matching. Shorter needles tolerate Tesseract
    # mis-reads in the label tail (e.g. 'RESI5TANCE' still matches
    # 'resi'; 'INSTABITY' still matches 'inst'). Also resolution-
    # robust — smaller render sizes lose trailing characters first,
    # but the 4-char stem ('MASS', 'RESI', 'INST') survives at any
    # panel scale where labels are even partially legible.
    targets = {
        "mass":        "mass",
        "resistance":  "resi",
        "instability": "inst",
    }

    best: dict[str, tuple[int, int, int, int]] = {}  # key -> (y1,y2,lbl_left,score)
    for _name, binary in candidates:
        binary_pil = Image.fromarray(binary)
        try:
            data = pytesseract.image_to_data(
                binary_pil,
                config=(
                    "--psm 11 -c tessedit_char_whitelist="
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz:"
                ),
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            continue
        n = len(data.get("text", []))
        for i in range(n):
            text = (data["text"][i] or "").strip().lower()
            # Drop whitespace and punctuation for prefix matching — a
            # small text like 'MASS:' should hit 'mass' even though
            # the strict-lowered form is 'mass:'.
            stripped = "".join(c for c in text if c.isalpha())
            if len(stripped) < 4:
                continue
            text = stripped
            x = int(data["left"][i])
            y = int(data["top"][i])
            h_ = int(data["height"][i])
            for key, needle in targets.items():
                if needle in text:
                    score = len(text)
                    prev = best.get(key)
                    if prev is None or score > prev[3]:
                        best[key] = (y, y + h_, x, score)
                    break

    if not best:
        return {}

    # ─── Anchor-based row reconciliation ───
    # The SC HUD panel has a FIXED vertical layout: MASS, then
    # RESISTANCE one row below, then INSTABILITY one row below that.
    # Per-row Tesseract searches are unreliable because:
    #   - Tesseract sometimes misreads "RESISTANCE" as containing
    #     the "mass" stem (or vice versa)
    #   - Same y-position can match multiple stems
    #   - Frame averaging across HUD jiggle blurs the row boundaries
    # Once we have ANY reliable row anchor, we can compute the others
    # from known relative pixel offsets (panel-scaled).
    #
    # Strategy:
    #   1. Pick the highest-confidence detected row as the anchor.
    #   2. Estimate row spacing from observed inter-row deltas.
    #   3. Override any detected row whose y is wildly off the
    #      expected anchor + N*row_spacing position.
    #
    # We use MASS as the preferred anchor when present (it's the
    # topmost row and most distinctive), else fall back to whichever
    # row scored highest.
    _ROW_ORDER = ["mass", "resistance", "instability"]

    # ─── Multi-anchor row reconciliation ───
    # When multiple rows are detected, use them to MEASURE the actual
    # row spacing (not assume a panel-scaled constant) and to detect
    # outlier detections that don't fit the line through the others.
    # Then clamp final positions against the HUD's top/bottom
    # separator lines (rows can never live outside the data band).

    # Step 1: Estimate row height from whatever was detected (used
    # later for crop padding).
    _heights = [best[k][1] - best[k][0] for k in best]
    raw_row_height = max(8, max(_heights) if _heights else 8)
    row_height = int(raw_row_height * 1.6) + 4

    # Step 2: Compute expected_spacing. If 2+ rows detected, use the
    # MEASURED spacing — this absorbs any panel-scale or HUD-resize
    # variation automatically. Falls back to the panel-scaled
    # constant only when a single row is all we have.
    #
    # IMPORTANT: when 3 rows are detected, use the LONGEST BASELINE
    # (idx 0 to idx 2) divided by 2, NOT the average of adjacent
    # deltas. Averaging adjacent deltas is unstable: if Tesseract
    # confuses two adjacent rows (e.g. detects MASS at RESISTANCE's
    # y), one delta collapses to ~0 and the average halves, which
    # then poisons outlier rejection. The longest-baseline spacing
    # is far less sensitive to a single noisy detection because the
    # bad row contributes only one error to a much larger interval.
    _REF_PANEL_W = 397
    panel_scale = max(0.5, float(img.width) / _REF_PANEL_W)
    _const_spacing = max(raw_row_height + 8, int(30 * panel_scale))

    detected_pairs = sorted(
        (_ROW_ORDER.index(k), best[k][0])
        for k in best
        if k in _ROW_ORDER
    )  # [(idx, y), ...] sorted by row index

    if len(detected_pairs) >= 2:
        # Use the longest baseline for the most robust spacing.
        first_idx, first_y = detected_pairs[0]
        last_idx, last_y = detected_pairs[-1]
        idx_span = max(1, last_idx - first_idx)
        measured_spacing = int(round((last_y - first_y) / idx_span))
        # Sanity-bound against the panel-scaled constant.
        if 0.4 * _const_spacing <= measured_spacing <= 2.0 * _const_spacing:
            expected_spacing = measured_spacing
        else:
            expected_spacing = _const_spacing
    else:
        expected_spacing = _const_spacing

    # Step 3: Outlier rejection. Use the longest-baseline spacing
    # (computed above) and the FIRST AND LAST detected rows as the
    # reference line, then drop any middle row that doesn't fit.
    # This is more robust than median-pivot because the endpoints
    # define the longest baseline; a noisy middle row can't poison
    # the line.
    if len(detected_pairs) == 3:
        first_idx, first_y = detected_pairs[0]
        last_idx, last_y = detected_pairs[-1]
        for idx, y in detected_pairs:
            if idx in (first_idx, last_idx):
                continue
            predicted = first_y + (idx - first_idx) * expected_spacing
            if abs(y - predicted) > expected_spacing * 0.5:
                _outlier_key = _ROW_ORDER[idx]
                log.debug(
                    "onnx_hud_reader: dropping outlier middle row %s (y=%d, "
                    "predicted=%d, spacing=%d)",
                    _outlier_key, y, predicted, expected_spacing,
                )
                best.pop(_outlier_key, None)
        # Also check: if the FIRST and LAST themselves are
        # implausibly close (idx_span * spacing collapsed because
        # one of them was misdetected at the same y as the other),
        # reject the smaller-scoring of the pair.
        if abs(last_y - first_y) < expected_spacing * (last_idx - first_idx) * 0.5:
            _f_score = best[_ROW_ORDER[first_idx]][3] if _ROW_ORDER[first_idx] in best else 0
            _l_score = best[_ROW_ORDER[last_idx]][3] if _ROW_ORDER[last_idx] in best else 0
            _drop_idx = first_idx if _f_score < _l_score else last_idx
            _drop_key = _ROW_ORDER[_drop_idx]
            log.debug(
                "onnx_hud_reader: endpoints collapsed (first_y=%d last_y=%d "
                "span=%d, expected≈%d); dropping lower-score endpoint %s",
                first_y, last_y, last_idx - first_idx,
                (last_idx - first_idx) * expected_spacing, _drop_key,
            )
            best.pop(_drop_key, None)

    # Step 4: Pick the anchor (prefer MASS, else highest-score row).
    if "mass" in best:
        anchor_key = "mass"
    elif best:
        anchor_key = max(best, key=lambda k: best[k][3])
    else:
        # All rows were rejected as outliers — return empty so the
        # caller falls back to mineral-row offset estimation.
        log.debug("onnx_hud_reader: all rows rejected as outliers")
        return {}

    anchor_y, anchor_y2, anchor_left, _ = best[anchor_key]
    anchor_idx = _ROW_ORDER.index(anchor_key)

    # Step 5: HUD-line Y bounds. The two horizontal HUD separator
    # lines bracket the data area. Any row Y outside that band is
    # provably wrong; clamp the anchor before we propagate it.
    _lines = _get_panel_lines_cached(np.array(img.convert("L"), dtype=np.uint8))
    _y_min_bound = 0
    _y_max_bound = img.height
    if len(_lines) >= 2:
        # Top line = first line above the anchor; bottom line = first
        # line below the anchor's last expected row.
        _last_expected_y = anchor_y + (len(_ROW_ORDER) - 1 - anchor_idx) * expected_spacing
        _above = [ly for ly, _, _ in _lines if ly < anchor_y]
        _below = [ly for ly, _, _ in _lines if ly > _last_expected_y]
        if _above:
            _y_min_bound = max(_above)  # closest line above anchor
        if _below:
            _y_max_bound = min(_below)  # closest line below last row

    _Y_PAD_TOP = 4
    for idx, key in enumerate(_ROW_ORDER):
        expected_y = anchor_y + (idx - anchor_idx) * expected_spacing
        # Clamp expected_y inside the HUD-line band (with padding for
        # row height — the row's TOP must be far enough below the top
        # line that the row's BOTTOM doesn't push past the bottom line).
        if expected_y < _y_min_bound:
            expected_y = _y_min_bound + _Y_PAD_TOP
        if expected_y + row_height > _y_max_bound:
            expected_y = _y_max_bound - row_height
        if 0 <= expected_y < img.height - row_height:
            y_top = max(0, expected_y - _Y_PAD_TOP)
            y_bot = expected_y + row_height
            if key in best:
                detected_y = best[key][0]
                if abs(detected_y - expected_y) > expected_spacing * 0.6:
                    best[key] = (
                        y_top,
                        y_bot,
                        best[key][2],
                        best[key][3],
                    )
            else:
                best[key] = (
                    y_top,
                    y_bot,
                    anchor_left,
                    1,
                )

    # Compute real label right edges via column-density on the
    # polarity-independent text mask of the full image. If the mask
    # is too noisy (e.g. asteroid leak), fall back to a fixed
    # right-edge estimate based on label length.
    full_gray = np.array(img.convert("L"), dtype=np.uint8)
    text_mask = _build_text_mask(full_gray, deviation=30)

    result: dict[str, tuple[int, int, int]] = {}
    _PAD = 3
    # Walk-right gap tolerance: inter-letter gaps in SC's HUD font can
    # exceed 5 px (especially at small panel scales), causing the scan
    # to terminate mid-label. Bumped 5 -> 14 so the scan bridges
    # intra-label gaps but still detects the much larger 30-50 px gap
    # between the label's trailing colon and the value's first digit.
    _GAP_THRESHOLD = 14
    # Fixed fallback right edges — from known panel geometry
    _FALLBACK_RIGHTS = {"mass": 110, "resistance": 200, "instability": 205}

    # Panel width heuristic — the hardcoded fallback rights were
    # measured on a 397px-wide reference panel. Scale them if the
    # current panel is wider/narrower. ``left`` was cropped at 55% of
    # img.width so the label column is always in the left half.
    _REF_PANEL_W = 397
    panel_scale = max(0.5, float(img.width) / _REF_PANEL_W)

    for key, (y1, y2, lbl_left, _score) in best.items():
        # Scan hot columns in this row to find the label right edge.
        # The label is darkest immediately after ``lbl_left`` and
        # fades into the gap between label and value. Walk rightward
        # tolerating small gaps inside the label glyphs.
        col_hot = text_mask[y1:y2, :].sum(axis=0) >= 2
        scanned_right = lbl_left
        gap_run = 0
        x = lbl_left
        while x < col_hot.shape[0]:
            if col_hot[x]:
                scanned_right = x + 1
                gap_run = 0
            else:
                gap_run += 1
                if gap_run >= _GAP_THRESHOLD:
                    break
            x += 1

        # Use the scanned edge when it's plausibly past the label —
        # require at least 20 px of label extent. Reject the scan if
        # it ran clear across the row (text_mask bleed from asteroid
        # scene), which we detect by comparing against a scaled cap.
        fallback_right = int(_FALLBACK_RIGHTS[key] * panel_scale)
        scan_extent = scanned_right - lbl_left
        max_plausible = int(min(img.width * 0.45, fallback_right * 1.8))
        if 20 <= scan_extent and scanned_right <= max_plausible:
            lbl_right = scanned_right
        else:
            lbl_right = fallback_right
            log.debug(
                "sc_ocr: label_rows key=%s scan_extent=%d out of "
                "bounds, using scaled fallback=%d (panel_scale=%.2f)",
                key, scan_extent, fallback_right, panel_scale,
            )

        result[key] = (
            max(0, y1 - _PAD),
            min(img.height, y2 + _PAD),
            lbl_right,
        )
    return result


def scan_hud_onnx(region: dict) -> dict[str, Optional[float]]:
    """Capture HUD region and extract mass + resistance + instability.

    Tries SC-OCR first (23ms, no subprocesses). If SC-OCR detects a
    light background (median gray > 130), falls back to the legacy
    Tesseract-based pipeline for label detection. This gives dark-bg
    scans the fast path (95% of gameplay) while keeping light-bg
    scans functional via Tesseract fallback.

    Parameters
    ----------
    region : dict
        Screen region {x, y, w, h} covering the mining scan panel.

    Returns
    -------
    dict with keys:
        - "mass" (float | None)
        - "resistance" (float | None)
        - "instability" (float | None)
        - "panel_visible" (bool): True when the scan panel's mineral-name
          row was located, regardless of whether numeric extraction
          succeeded. Callers use this to distinguish "no panel" (keep
          cached values) from "panel visible but value unreadable"
          (clear stale cache — the rock has changed).
    """
    result: dict[str, Optional[float]] = {
        "mass": None,
        "resistance": None,
        "instability": None,
        "panel_visible": False,
    }

    if not _ensure_model():
        return result

    t0 = time.time()

    # ── SC-OCR ENGINE (primary, legacy disabled) ──
    try:
        from .sc_ocr.api import scan_hud_onnx as _sc_ocr_scan
        sc_result = _sc_ocr_scan(region)
        elapsed = (time.time() - t0) * 1000
        log.info(
            "sc_ocr: mass=%s resistance=%s instability=%s in %.0fms",
            sc_result.get("mass"), sc_result.get("resistance"),
            sc_result.get("instability"), elapsed,
        )
        return sc_result
    except Exception as exc:
        # Include the full traceback so we can locate the actual line
        # that's raising — the bare ``%s`` was masking 1000+ identical
        # ``KeyError: 'instability'`` failures with no line info.
        log.error("sc_ocr failed: %s", exc, exc_info=True)
        return result
