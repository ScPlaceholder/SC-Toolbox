"""Screen capture for SC-OCR.

Single-frame mss grab by default. Optional multi-frame capture with
FFT phase correlation is available but NOT used in the default
pipeline — the shift-invariant matcher in ``classify.py`` handles
HUD wiggle at the classification stage, eliminating the need for
multi-frame averaging (which added 300+ ms of wall latency in the
old pipeline and blurred glyph edges).
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

_MSS_AVAILABLE: Optional[bool] = None


def _check_mss() -> bool:
    global _MSS_AVAILABLE
    if _MSS_AVAILABLE is not None:
        return _MSS_AVAILABLE
    try:
        import mss  # noqa: F401
        _MSS_AVAILABLE = True
    except ImportError:
        _MSS_AVAILABLE = False
        log.warning("sc_ocr.capture: 'mss' not installed — capture disabled")
    return _MSS_AVAILABLE


def grab(region: dict) -> Optional[Image.Image]:
    """Capture a screen region as a PIL RGB image.

    ``region`` must have keys x, y, w, h (integers, native pixels).
    Returns None if mss is unavailable or the grab fails.
    """
    if not _check_mss():
        return None
    import mss

    monitor = {
        "left": int(region["x"]),
        "top": int(region["y"]),
        "width": int(region["w"]),
        "height": int(region["h"]),
    }
    try:
        with mss.mss() as sct:
            grabbed = sct.grab(monitor)
            return Image.frombytes(
                "RGB", grabbed.size, grabbed.bgra, "raw", "BGRX",
            )
    except Exception as exc:
        log.error("sc_ocr.capture: grab failed: %s", exc)
        return None


def grab_multi(region: dict, n: int = 2, delay_ms: int = 20) -> Optional[Image.Image]:
    """Capture n frames and return the phase-aligned average.

    Only used when single-frame capture has unacceptable noise; the
    default pipeline uses ``grab`` for speed. The shift-invariant
    matcher makes multi-frame capture unnecessary for jitter
    compensation, but heavy GPU compositor noise CAN still be
    reduced by brief averaging.

    Runs FFT phase correlation once between frame 2 and frame 1 to
    detect any integer-pixel offset, shifts frame 2 to align, then
    averages.
    """
    if n < 2:
        return grab(region)
    import time

    frames: list[np.ndarray] = []
    for i in range(n):
        img = grab(region)
        if img is None:
            return None
        frames.append(np.asarray(img, dtype=np.float32))
        if i < n - 1 and delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

    # Simple phase correlation on the grayscale of frames 0 and 1.
    # Lock all subsequent frames to frame 0.
    ref = frames[0].mean(axis=2)
    aligned = [frames[0]]
    for f in frames[1:]:
        g = f.mean(axis=2)
        try:
            dy, dx = _phase_shift(ref, g)
            aligned.append(_roll_int(f, dy, dx))
        except Exception:
            aligned.append(f)

    stacked = np.stack(aligned, axis=0)
    mean = stacked.mean(axis=0).clip(0, 255).astype(np.uint8)
    return Image.fromarray(mean, mode="RGB")


def _phase_shift(a: np.ndarray, b: np.ndarray) -> tuple[int, int]:
    """FFT phase correlation → integer (dy, dx) shift from a to b.

    Falls back to (0, 0) on failure.
    """
    try:
        F1 = np.fft.rfft2(a)
        F2 = np.fft.rfft2(b)
        cross = F1 * np.conj(F2)
        mag = np.abs(cross)
        mag[mag < 1e-6] = 1.0
        cross /= mag  # phase only
        corr = np.fft.irfft2(cross, s=a.shape)
        peak = np.unravel_index(int(np.argmax(corr)), corr.shape)
        dy = peak[0]
        dx = peak[1]
        # Fold [0, N) back to [-N/2, N/2)
        if dy > a.shape[0] // 2:
            dy -= a.shape[0]
        if dx > a.shape[1] // 2:
            dx -= a.shape[1]
        return int(dy), int(dx)
    except Exception:
        return 0, 0


def _roll_int(frame: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Integer-pixel shift (roll) without wrap artifacts.

    Pads new edge pixels with 0.
    """
    if dy == 0 and dx == 0:
        return frame
    h, w = frame.shape[:2]
    out = np.zeros_like(frame)
    y_src = slice(max(0, dy), min(h, h + dy))
    x_src = slice(max(0, dx), min(w, w + dx))
    y_dst = slice(max(0, -dy), min(h, h - dy))
    x_dst = slice(max(0, -dx), min(w, w - dx))
    out[y_dst, x_dst] = frame[y_src, x_src]
    return out
