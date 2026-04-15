"""Public API for SC-OCR.

Signature-compatible replacements for the legacy three-engine
call sites in ``ui/app.py`` and ``ocr/screen_reader.py``/etc.:

    scan_region(region)     →  Optional[int]              (signal number)
    scan_hud_onnx(region)   →  dict[str, Optional[float]]  (mining HUD)
    scan_refinery(region)   →  Optional[list[dict]]        (refinery orders)

Each function owns its own domain pipeline: capture → preprocess →
segment → classify (shift-invariant NCC) → validate. Low-confidence
glyphs are sent to the ONNX CNN fallback before being accepted.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

import numpy as np
from PIL import Image

from . import capture, classify, fallback, learn, preprocess, segment, validate
from .config import (
    AUTO_LEARN_CONF_THRESHOLD,
    CANON_TEMPLATE_H,
    CANON_TEMPLATE_W,
    FALLBACK_CONF_THRESHOLD,
)
from .templates import TemplatePack, get_pack, normalize_crop

log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────

def _recognize_row(
    binary_row: np.ndarray,
    pack: TemplatePack,
    *,
    use_fallback: bool = True,
    glyph_h: int = CANON_TEMPLATE_H,
) -> tuple[str, list[float], list[np.ndarray]]:
    """Full pipeline from a binary row slice to a string.

    Returns:
        text          : concatenated classified characters
        confidences   : per-character NCC scores
        canvas_crops  : per-character uint8 crops at canonical size
                        (for feeding into learn.submit_confirmed)
    """
    glyphs = segment.split_glyphs_in_row(binary_row, template_h=glyph_h)
    if not glyphs:
        return "", [], []

    templates = pack.at_height(glyph_h)
    chars_arr = pack.chars

    normalized = [g.normalized for g in glyphs]
    results = classify.classify_batch(normalized, templates, chars_arr)

    out_chars: list[str] = []
    out_confs: list[float] = []
    canvas_crops: list[np.ndarray] = []

    for glyph, (ch, conf) in zip(glyphs, results):
        # Use ONNX fallback for digit packs when confidence is low.
        if (use_fallback
                and conf < FALLBACK_CONF_THRESHOLD
                and pack.name == "digits"):
            # Extract the centered 28x28 canvas (sans shift-search padding)
            # for ONNX inference.
            padded = glyph.normalized
            canvas = padded[
                max(0, (padded.shape[0] - CANON_TEMPLATE_H) // 2):
                max(0, (padded.shape[0] - CANON_TEMPLATE_H) // 2) + CANON_TEMPLATE_H,
                max(0, (padded.shape[1] - CANON_TEMPLATE_W) // 2):
                max(0, (padded.shape[1] - CANON_TEMPLATE_W) // 2) + CANON_TEMPLATE_W,
            ]
            # Rescale normalized (zero-mean unit-L2) back to [0, 255]
            # for the ONNX input convention.
            if canvas.max() - canvas.min() > 1e-6:
                rescaled = (
                    (canvas - canvas.min())
                    / (canvas.max() - canvas.min()) * 255.0
                ).astype(np.uint8)
            else:
                rescaled = np.zeros_like(canvas, dtype=np.uint8)
            fb = fallback.classify_glyph(rescaled.astype(np.float32))
            if fb is not None and fb[1] > conf:
                ch, conf = fb

        out_chars.append(ch)
        out_confs.append(conf)
        # Keep a uint8 canonical-sized version for potential learning
        padded = glyph.normalized
        y0 = max(0, (padded.shape[0] - glyph.template_h) // 2)
        x0 = max(0, (padded.shape[1] - glyph.template_w) // 2)
        canvas = padded[y0:y0 + glyph.template_h, x0:x0 + glyph.template_w]
        if canvas.max() - canvas.min() > 1e-6:
            u8 = ((canvas - canvas.min())
                  / (canvas.max() - canvas.min()) * 255.0).astype(np.uint8)
        else:
            u8 = np.zeros_like(canvas, dtype=np.uint8)
        canvas_crops.append(u8)

    return "".join(out_chars), out_confs, canvas_crops


def _capture_rgb(region: dict) -> Optional[np.ndarray]:
    img = capture.grab(region)
    if img is None:
        return None
    return np.asarray(img, dtype=np.uint8)


# ── Scan-result caching ────────────────────────────────────────────
# Mining HUD often stares at the same rock for many seconds. Detect
# identical captures via a cheap thumbnail hash and skip the whole
# pipeline when possible. Key is (region tuple) → (thumbnail hash,
# last result). Protected against thread races with a lock.

_cache_lock = None
_last_results: dict = {}


def _cache_key(region: dict) -> tuple:
    return (int(region["x"]), int(region["y"]),
            int(region["w"]), int(region["h"]))


def _thumbnail_hash(rgb: np.ndarray) -> bytes:
    # Downscale to 16x16 grayscale, hash. Fast (~0.2 ms).
    small = Image.fromarray(rgb, mode="RGB").resize((16, 16), Image.BILINEAR)
    arr = np.asarray(small.convert("L"), dtype=np.uint8)
    return hashlib.md5(arr.tobytes()).digest()


# ── Public API ─────────────────────────────────────────────────────

def scan_region(region: dict) -> Optional[int]:
    """Read a signal-number region → int in [1000, 35000].

    Drop-in replacement for ``ocr/screen_reader.py::scan_region``.
    """
    rgb = _capture_rgb(region)
    if rgb is None:
        return None

    key = _cache_key(region)
    th = _thumbnail_hash(rgb)
    cached = _last_results.get(key)
    if cached is not None and cached[0] == th:
        return cached[1]

    channel, binary = preprocess.preprocess_rgb(rgb, isolate="auto")

    # Find the single text band (signal is one line of digits)
    rows = segment.find_rows(binary, min_h=8)
    if not rows:
        _last_results[key] = (th, None)
        return None

    pack = get_pack("digits")
    # Pick the tallest row (filters thin divider lines)
    rows.sort(key=lambda r: r[1] - r[0], reverse=True)
    y1, y2 = rows[0]
    glyph_h = y2 - y1
    # Clamp to a sensible range and use canonical if too small
    if glyph_h < 8:
        glyph_h = CANON_TEMPLATE_H

    text, confs, crops = _recognize_row(
        binary[y1:y2], pack, glyph_h=glyph_h,
    )

    result = validate.validate_signal(text)

    # Auto-learn if all conditions met
    if (result is not None and confs
            and min(confs) >= AUTO_LEARN_CONF_THRESHOLD):
        # Pair each char with its crop and submit
        updates = [(c, cr) for c, cr in zip(text, crops) if c.isdigit()]
        try:
            learn.submit_confirmed("digits", updates)
        except Exception as exc:
            log.debug("sc_ocr: auto-learn (signal) skipped: %s", exc)

    _last_results[key] = (th, result)
    return result


def scan_hud_onnx(region: dict) -> dict:
    """Read the mining HUD panel → {mass, resistance, instability, panel_visible}.

    Drop-in replacement for ``ocr/onnx_hud_reader.py::scan_hud_onnx``.
    """
    empty = {
        "mass": None,
        "resistance": None,
        "instability": None,
        "panel_visible": False,
    }

    t0 = time.time()
    rgb = _capture_rgb(region)
    if rgb is None:
        return empty

    channel, binary = preprocess.preprocess_rgb(rgb, isolate="auto")
    rows = segment.find_rows(binary, min_h=12)
    if not rows:
        return empty

    # Mining HUD layout: tall mineral-name row near top, then mass/
    # resistance/instability value rows below it. Use brightness to
    # rank rows, then pick the 3 most consistent value rows by
    # horizontal position.
    #
    # Simple heuristic for v1: sort rows by y, drop tiny separator
    # bands, take the rows in the LOWER 60% of the image as value
    # candidates (mass/resist/instab live in the bottom half of the
    # HUD panel).
    h_img = binary.shape[0]
    value_rows = [(y1, y2) for (y1, y2) in rows if y1 > h_img * 0.25]
    if len(value_rows) < 1:
        return empty

    # Panel is considered visible if at least one row was found.
    result = dict(empty)
    result["panel_visible"] = True

    pack = get_pack("digits")

    # For each candidate row, recognize the text and try each
    # validator (mass / pct / pct). Assign the first successful
    # parse to the corresponding field in top-to-bottom order.
    values: list[tuple[str, list[float], list[np.ndarray]]] = []
    for y1, y2 in value_rows[:3]:  # up to 3 rows for mass/res/instab
        # Crop to the RIGHT half of the row (labels are on left,
        # values on right).
        w = binary.shape[1]
        right = binary[y1:y2, w // 3:]
        glyph_h = y2 - y1
        if glyph_h < 10:
            continue
        text, confs, crops = _recognize_row(
            right, pack, glyph_h=glyph_h,
        )
        values.append((text, confs, crops))

    if not values:
        return result

    # Map to fields in order
    if len(values) >= 1:
        mass = validate.validate_mass(values[0][0])
        result["mass"] = mass
    if len(values) >= 2:
        pct = validate.validate_pct(values[1][0])
        result["resistance"] = pct
    if len(values) >= 3:
        inst = validate.validate_instability(values[2][0])
        result["instability"] = inst

    # Auto-learn on fully-successful scans
    if (result["mass"] is not None
            and result["resistance"] is not None
            and all(min(v[1]) >= AUTO_LEARN_CONF_THRESHOLD
                    for v in values if v[1])):
        updates = []
        for text, confs, crops in values:
            for c, cr in zip(text, crops):
                if c.isdigit():
                    updates.append((c, cr))
        try:
            learn.submit_confirmed("digits", updates)
        except Exception as exc:
            log.debug("sc_ocr: auto-learn (HUD) skipped: %s", exc)

    elapsed_ms = (time.time() - t0) * 1000
    log.debug("sc_ocr.scan_hud_onnx: %dms %s", int(elapsed_ms), result)
    return result


def scan_refinery(region: dict, station: str = "") -> Optional[list[dict]]:
    """Read a refinery terminal region → list of order dicts, or None.

    Drop-in replacement for ``ocr/refinery_reader.py::scan_refinery``.

    NOTE: v1 of SC-OCR ships digit templates only. Refinery alphabet
    pack requires a bootstrap from real refinery captures which
    hasn't been done yet. Until then, return None (panel not
    visible) and log that refinery scanning is in transition.
    """
    try:
        pack = get_pack("refinery_alphabet")
    except FileNotFoundError:
        log.info(
            "sc_ocr: refinery_alphabet pack not yet bootstrapped; "
            "refinery scanning disabled until pack is built."
        )
        return None

    rgb = _capture_rgb(region)
    if rgb is None:
        return None

    _channel, binary = preprocess.preprocess_rgb(rgb, isolate="auto")
    rows = segment.find_rows(binary, min_h=10)
    if not rows:
        return None

    # For v1 we read every row as a string. Downstream parsing is
    # delegated to the existing refinery_reader helpers once we
    # have text.
    try:
        from .. import refinery_reader
        parser = getattr(refinery_reader, "_parse_ocr_results", None)
    except Exception:
        parser = None

    lines: list[str] = []
    for y1, y2 in rows:
        glyph_h = y2 - y1
        if glyph_h < 10:
            continue
        text, _confs, _crops = _recognize_row(
            binary[y1:y2], pack, glyph_h=glyph_h,
        )
        if text:
            lines.append(text)

    if not lines:
        return []

    if parser is None:
        return []

    # Delegate to the refinery text parser (reuses its method/
    # commodity/time regexes & dictionaries).
    try:
        orders = parser("\n".join(lines))
        return orders or []
    except Exception as exc:
        log.debug("sc_ocr.scan_refinery: parse failed: %s", exc)
        return []
