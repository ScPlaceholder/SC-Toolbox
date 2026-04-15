"""Row and glyph segmentation from a binary mask.

Two levels:

1. **Row detection** — horizontal projection (sum of mask rows).
   Continuous runs of non-empty rows form text bands. Thin bands
   (< MIN_ROW_HEIGHT) are rejected as separator bars.

2. **Glyph split within a row** — connected components on the row
   slice. Components are merged when their x-midpoints are within
   FUSE_DX (reattaches disconnected dots like the `.` in "0.61").
   Each component becomes a glyph crop resized to the template
   height.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image

from .config import CANON_TEMPLATE_H, CANON_TEMPLATE_W, SHIFT_SEARCH_X, SHIFT_SEARCH_Y
from .templates import _normalize

MIN_ROW_HEIGHT = 8
MIN_ROW_GAP = 2
FUSE_DX = 2  # pixels; merge components whose x-centers are within this
MIN_GLYPH_PIXELS = 3  # reject specks


@dataclass
class GlyphCrop:
    """A single glyph ready for classification.

    Attributes
    ----------
    bbox : (x1, y1, x2, y2) in the original binary mask
    normalized : (H+2*dy, W+2*dx) float32 zero-mean unit-L2 crop,
        already padded on all sides by the shift search margin so
        the template matcher can slide within it.
    template_h : int — the H the crop was resized to
    template_w : int — the W the crop was resized to
    """
    bbox: tuple[int, int, int, int]
    normalized: np.ndarray
    template_h: int
    template_w: int


def find_rows(binary: np.ndarray, min_h: int = MIN_ROW_HEIGHT) -> list[tuple[int, int]]:
    """Find non-empty horizontal bands in the binary mask.

    Returns a list of (y_top, y_bottom) pairs, y_bottom exclusive.
    """
    H, W = binary.shape
    row_sum = (binary > 0).sum(axis=1)
    rows: list[tuple[int, int]] = []
    y = 0
    while y < H:
        if row_sum[y] == 0:
            y += 1
            continue
        y_start = y
        while y < H and row_sum[y] > 0:
            y += 1
        y_end = y
        if y_end - y_start >= min_h:
            rows.append((y_start, y_end))
        # Walk past the gap
        while y < H and row_sum[y] == 0:
            y += 1
    # Merge adjacent bands separated by tiny gaps
    merged: list[tuple[int, int]] = []
    for band in rows:
        if merged and (band[0] - merged[-1][1]) <= MIN_ROW_GAP:
            merged[-1] = (merged[-1][0], band[1])
        else:
            merged.append(band)
    return merged


def split_glyphs_in_row(
    binary_row: np.ndarray,
    template_h: Optional[int] = None,
) -> list[GlyphCrop]:
    """Split a single text row into normalized glyph crops.

    Parameters
    ----------
    binary_row : (H, W) uint8 {0, 255}
        Single text row already cropped out of the full mask.
    template_h : int, optional
        Target glyph height. Defaults to CANON_TEMPLATE_H. Used to
        resize each extracted glyph to the template's canonical size.

    Each returned ``GlyphCrop.normalized`` is zero-mean unit-L2
    normalized and padded by ``SHIFT_SEARCH_X``/``SHIFT_SEARCH_Y``
    on each side so the shift-invariant matcher can slide the
    template within it.
    """
    if template_h is None:
        template_h = CANON_TEMPLATE_H
    # Preserve aspect ratio of the canonical template at the target
    # height.
    template_w = max(1, round(template_h * CANON_TEMPLATE_W / CANON_TEMPLATE_H))

    H, W = binary_row.shape
    col_sum = (binary_row > 0).sum(axis=0)

    # Find contiguous non-empty column runs
    spans: list[tuple[int, int]] = []
    x = 0
    while x < W:
        if col_sum[x] == 0:
            x += 1
            continue
        x_start = x
        while x < W and col_sum[x] > 0:
            x += 1
        x_end = x
        if (x_end - x_start) >= 1:
            spans.append((x_start, x_end))
        while x < W and col_sum[x] == 0:
            x += 1

    # Merge spans whose midpoints are within FUSE_DX (reattach dots)
    merged: list[tuple[int, int]] = []
    for s in spans:
        if merged:
            prev = merged[-1]
            if s[0] - prev[1] <= FUSE_DX:
                merged[-1] = (prev[0], s[1])
                continue
        merged.append(s)

    # Build crops
    crops: list[GlyphCrop] = []
    for x1, x2 in merged:
        # Vertical trim: find actual y bounds of the glyph within the row
        col_slice = binary_row[:, x1:x2]
        ys = np.where(col_slice.sum(axis=1) > 0)[0]
        if len(ys) < 1:
            continue
        y1, y2 = int(ys[0]), int(ys[-1]) + 1
        if (x2 - x1) * (y2 - y1) < MIN_GLYPH_PIXELS:
            continue

        crop_raw = col_slice[y1:y2]  # (gh, gw) uint8
        gh, gw = crop_raw.shape
        if gh < 2 or gw < 1:
            continue

        # Resize to template_h × template_w, preserving aspect,
        # padding laterally with background (0) so thin glyphs like
        # '1' or '.' land centered.
        scale = template_h / gh
        new_w = max(1, round(gw * scale))
        pil = Image.fromarray(crop_raw, mode="L").resize(
            (new_w, template_h), Image.LANCZOS,
        )
        arr = np.asarray(pil, dtype=np.float32) / 255.0

        # Place into a canvas of (template_h, template_w), centered x
        canvas = np.zeros((template_h, template_w), dtype=np.float32)
        if new_w >= template_w:
            # Too wide — crop center
            x_src = (new_w - template_w) // 2
            canvas[:, :] = arr[:, x_src:x_src + template_w]
        else:
            x_dst = (template_w - new_w) // 2
            canvas[:, x_dst:x_dst + new_w] = arr

        # Pad by shift-search margin so matcher can slide
        padded = np.zeros(
            (template_h + 2 * SHIFT_SEARCH_Y, template_w + 2 * SHIFT_SEARCH_X),
            dtype=np.float32,
        )
        padded[SHIFT_SEARCH_Y:SHIFT_SEARCH_Y + template_h,
               SHIFT_SEARCH_X:SHIFT_SEARCH_X + template_w] = canvas
        padded = _normalize(padded)

        crops.append(GlyphCrop(
            bbox=(x1, y1, x2, y2),
            normalized=padded,
            template_h=template_h,
            template_w=template_w,
        ))

    return crops
