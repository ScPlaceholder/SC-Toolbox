"""Shift-invariant NCC batch classifier.

For each glyph crop, search a small ±2×±1 px window for the best
match against each template. This absorbs the HUD's subpixel wiggle
animation in a SINGLE FRAME — no multi-frame averaging needed.

Operations are pure NumPy, single-threaded (BLAS threads are capped
to 1 in sc_ocr.__init__). For a typical mining HUD scan (12 glyphs
× 10 digit templates × 15 search positions) this takes ~1 ms on
a modern CPU.
"""
from __future__ import annotations

import numpy as np

from .config import SHIFT_SEARCH_X, SHIFT_SEARCH_Y


def classify_batch(
    crops: list[np.ndarray],
    templates: np.ndarray,
    chars: np.ndarray,
) -> list[tuple[str, float]]:
    """Classify a batch of glyph crops via shift-invariant NCC.

    Parameters
    ----------
    crops : list of (H+2*dy, W+2*dx) float32, zero-mean unit-L2 normalized
        Each crop must be larger than the template by ``2*dx × 2*dy``
        pixels so templates can slide within it. Typically produced
        via ``segment.split_glyphs`` which pads to the expected size.
    templates : (N, H, W) float32, zero-mean unit-L2 normalized
        Template pack at the crop's native glyph height.
    chars : (N,) uint32 codepoints corresponding to ``templates``.

    Returns
    -------
    list of (char, confidence) tuples, one per crop.
    """
    if not crops:
        return []
    if templates.ndim != 3 or templates.shape[0] == 0:
        return [(chr(0), 0.0)] * len(crops)

    N, H, W = templates.shape
    dx, dy = SHIFT_SEARCH_X, SHIFT_SEARCH_Y
    results: list[tuple[str, float]] = []

    # Flatten templates once for the per-position dot product.
    # Shape: (N, H*W)
    t_flat = templates.reshape(N, -1)

    for crop in crops:
        if crop.ndim != 2:
            results.append((chr(0), 0.0))
            continue
        CH, CW = crop.shape
        # Expected shape: at least H + 2*dy tall, W + 2*dx wide.
        # If the crop is smaller, fall back to center match.
        max_score = -np.inf
        best_idx = 0

        if CH >= H + 2 * dy and CW >= W + 2 * dx:
            # Exhaustive search window
            for oy in range(2 * dy + 1):
                for ox in range(2 * dx + 1):
                    window = crop[oy:oy + H, ox:ox + W]
                    # NCC = dot(normalized(window), template)
                    w_flat = window.ravel()
                    # Re-normalize the window (windows from an already-
                    # normalized crop aren't themselves unit-norm).
                    w_flat = w_flat - w_flat.mean()
                    norm = np.sqrt((w_flat * w_flat).sum())
                    if norm < 1e-6:
                        continue
                    w_flat = w_flat / norm
                    scores = t_flat @ w_flat  # (N,)
                    i = int(np.argmax(scores))
                    if scores[i] > max_score:
                        max_score = float(scores[i])
                        best_idx = i
        else:
            # Center crop fallback — crop too small for search
            y0 = max(0, (CH - H) // 2)
            x0 = max(0, (CW - W) // 2)
            window = crop[y0:y0 + H, x0:x0 + W]
            if window.shape != (H, W):
                # Pad if even smaller
                padded = np.zeros((H, W), dtype=np.float32)
                padded[:window.shape[0], :window.shape[1]] = window
                window = padded
            w_flat = window.ravel()
            w_flat = w_flat - w_flat.mean()
            norm = np.sqrt((w_flat * w_flat).sum())
            if norm > 1e-6:
                w_flat = w_flat / norm
                scores = t_flat @ w_flat
                best_idx = int(np.argmax(scores))
                max_score = float(scores[best_idx])

        if max_score == -np.inf:
            results.append((chr(0), 0.0))
        else:
            # Clamp NCC to [0, 1] for reporting (negative matches are
            # anticorrelated and shouldn't happen on text).
            conf = max(0.0, min(1.0, max_score))
            results.append((chr(int(chars[best_idx])), conf))

    return results


def ambiguity_gap(scores: np.ndarray, top_idx: int) -> float:
    """Return the score gap between the top match and the runner-up.

    Used for fallback decisions — if the gap is small, the template
    matcher isn't confident even at high absolute score.
    """
    if len(scores) < 2:
        return 1.0
    sorted_scores = np.sort(scores)[::-1]
    return float(sorted_scores[0] - sorted_scores[1])
