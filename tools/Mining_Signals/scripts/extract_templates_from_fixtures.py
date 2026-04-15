"""Extract clean per-digit glyph crops from known-ground-truth HUD captures.

For each fixture image with known values (mass/resistance/instability),
run the legacy segmentation to get individual glyph crops, then label
each crop by position against the expected character sequence.

Writes 28x28 uint8 PNGs to ``tools/Mining_Signals/training_data_clean/{0-9}/``
with meaningful filenames so we can inspect and curate later.

Usage:
    python scripts/extract_templates_from_fixtures.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make ocr.onnx_hud_reader importable
THIS = Path(__file__).resolve()
TOOL = THIS.parent.parent
sys.path.insert(0, str(TOOL))

import numpy as np
from PIL import Image

from ocr.onnx_hud_reader import (  # noqa: E402
    _ensure_model, _find_label_rows, _find_mineral_row, _find_value_crop, _otsu,
)

FIXTURES: list[tuple[str, dict]] = [
    # (path relative to TOOL, {field: expected_str})
    ("debug_cleaned.png",
     {"mass": "111558", "resistance": "96", "instability": "731.84"}),
    ("_sample_light_torite_impossible.png",
     {"mass": "8261", "resistance": "31", "instability": "196.22"}),
    ("_sample_light_torite_medium.png",
     {"mass": "5521", "resistance": "30", "instability": "126.39"}),
    ("_sample_light_iron_impossible.png",
     {"mass": "24700", "resistance": "0", "instability": "18.22"}),
    ("_sample_light_rawice_easy.png",
     {"mass": "4373", "resistance": "0", "instability": "0.00"}),
]

OUT_DIR = TOOL / "training_data_clean"


def _fallback_rows(img):
    """Copy of the fallback-row logic in onnx_hud_reader for when labels
    aren't detected (light backgrounds, etc.)."""
    mineral = _find_mineral_row(img)
    if mineral is None:
        return {}
    mr_center = (mineral[0] + mineral[1]) // 2
    H_HALF = 15
    OFFSETS = {"mass": 43, "resistance": 82, "instability": 120}
    RIGHTS = {"mass": 110, "resistance": 200, "instability": 205}
    out = {}
    for f in ("mass", "resistance", "instability"):
        c = mr_center + OFFSETS[f]
        out[f] = (max(0, c - H_HALF),
                  min(img.height, c + H_HALF),
                  RIGHTS[f])
    return out


def _segment_spans(binary):
    """Vertical-projection segmentation → column span list."""
    proj = np.sum(binary > 0, axis=0)
    w = binary.shape[1]
    spans: list[tuple[int, int]] = []
    in_c = False
    start = 0
    for x in range(w + 1):
        v = proj[x] if x < w else 0
        if v > 0 and not in_c:
            in_c = True; start = x
        elif v == 0 and in_c:
            in_c = False
            if x - start >= 2:  # keep thin glyphs like '1' and '.'
                spans.append((start, x))
    return spans


def _extract_glyphs(value_crop):
    """For a value crop (PIL RGB), return list of 28x28 uint8 glyphs."""
    gray = np.array(value_crop.convert("L"), dtype=np.uint8)
    # Auto-detect polarity: median > 140 = bright background
    if np.median(gray) > 140:
        gray = 255 - gray
    thr = _otsu(gray)
    binary = (gray > thr).astype(np.uint8) * 255

    spans = _segment_spans(binary)
    out: list[np.ndarray] = []
    for x1, x2 in spans:
        ys = np.where(np.any(binary[:, x1:x2] > 0, axis=1))[0]
        if len(ys) < 2:
            continue
        ya, yb = int(ys[0]), int(ys[-1]) + 1
        glyph = gray[ya:yb, x1:x2].astype(np.float32)
        pad = 2
        padded = np.full(
            (glyph.shape[0] + pad * 2, glyph.shape[1] + pad * 2),
            255.0, dtype=np.float32,
        )
        padded[pad:pad + glyph.shape[0], pad:pad + glyph.shape[1]] = glyph
        pil = Image.fromarray(padded.astype(np.uint8)).resize(
            (28, 28), Image.BILINEAR,
        )
        out.append(np.asarray(pil, dtype=np.uint8))
    return out


def _label_glyphs(glyphs, expected_str):
    """Align a sequence of extracted glyphs against the expected
    character string.

    If the glyph count matches exactly, 1:1 mapping.
    If there's ONE extra glyph, it's assumed to be a trailing char
    not in the expected string (e.g. '%' on resistance, '.' on
    instability is already in the string though) — skip the last.
    Otherwise return None (unreliable alignment).
    """
    expected_chars = list(expected_str)
    if len(glyphs) == len(expected_chars):
        return list(zip(expected_chars, glyphs))
    if len(glyphs) == len(expected_chars) + 1:
        # Usually a trailing '%' picked up as an extra glyph
        return list(zip(expected_chars, glyphs[:-1]))
    return None


def extract_all() -> dict[str, list[tuple[str, np.ndarray]]]:
    """Returns per-class lists of (source_tag, uint8_28x28_crop)."""
    if not _ensure_model():
        print("ONNX model unavailable", file=sys.stderr)
        sys.exit(1)

    per_class: dict[str, list[tuple[str, np.ndarray]]] = {
        c: [] for c in "0123456789"
    }

    for relpath, vals in FIXTURES:
        path = TOOL / relpath
        if not path.is_file():
            print(f"  skip missing: {path}")
            continue
        img = Image.open(path).convert("RGB")
        gray = np.array(img.convert("L"), dtype=np.uint8)

        rows = _find_label_rows(img)
        if not rows:
            rows = _fallback_rows(img)
        if not rows:
            print(f"  {relpath}: no rows detected, skipping")
            continue

        for field, expected in vals.items():
            entry = rows.get(field)
            if entry is None:
                continue
            y1, y2, lbl_right = entry
            x_min = max(0, lbl_right + 6)
            value_crop = _find_value_crop(img, gray, y1, y2, x_min=x_min)
            if value_crop is None:
                continue
            glyphs = _extract_glyphs(value_crop)
            aligned = _label_glyphs(glyphs, expected)
            if aligned is None:
                print(f"  {relpath}/{field}: alignment failed "
                      f"({len(glyphs)} glyphs vs '{expected}')")
                continue
            for ch, crop in aligned:
                if ch in per_class:
                    per_class[ch].append((f"{path.stem}_{field}", crop))
            print(f"  {relpath}/{field}: +{len(aligned)} glyphs")

    return per_class


def save(per_class: dict[str, list[tuple[str, np.ndarray]]]):
    OUT_DIR.mkdir(exist_ok=True)
    for ch, entries in per_class.items():
        if not entries:
            continue
        d = OUT_DIR / ch
        d.mkdir(exist_ok=True)
        for i, (tag, crop) in enumerate(entries):
            Image.fromarray(crop, mode="L").save(d / f"{tag}_{i}.png")
    total = sum(len(v) for v in per_class.values())
    print(f"\nWrote {total} clean glyphs across {sum(1 for v in per_class.values() if v)} classes to {OUT_DIR}")
    for ch in "0123456789":
        print(f"  '{ch}': {len(per_class[ch])}")


if __name__ == "__main__":
    per_class = extract_all()
    save(per_class)
