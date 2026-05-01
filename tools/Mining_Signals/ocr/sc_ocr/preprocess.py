"""Color-aware preprocessing: polarity detection, channel isolation, binarization.

A single pipeline handles five background cases:
- Magenta/pink text on warm bg (mining HUD overcharge): B channel
- Light (sunlit asteroid leak): invert grayscale
- Dark + cyan/green text (normal HUD): G channel
- Dark + orange/red text (warnings): R channel
- Dark + white/mixed text: max-of-channels

Threshold is Otsu on the selected channel. If the background is
noisy (high stddev), a single 3x3 morphological open removes
speckle before thresholding.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
from PIL import Image

Polarity = Literal["auto", "bright_on_dark", "dark_on_bright"]
ColorIsolate = Literal["auto", "g", "r", "b", "max_channel", "invert_gray"]


def isolate_channel(rgb: np.ndarray, mode: ColorIsolate = "auto") -> np.ndarray:
    """Reduce an (H, W, 3) uint8 array to a single-channel uint8 mask
    where text is the bright end of the range.

    In ``"auto"`` mode, picks the channel that maximizes text/bg
    separation based on a quick statistic: take the mean of each
    channel + grayscale luminance, and:

    - Magenta/pink text on warm bg (overcharge HUD): use B
      (R is saturated by both text and bg, but B is high in pink
      and low in orange — the cleanest separation available)
    - If luminance mean > 140: bright background → invert grayscale
    - If G − R > 15 (cyan/green text on dark): use G
    - If R − G > 15 (orange/red text on dark): use R
    - Otherwise: max-of-channels (handles white/mixed text on dark)
    """
    if rgb.ndim == 2:
        return rgb  # already single channel
    assert rgb.ndim == 3 and rgb.shape[2] == 3

    if mode == "auto":
        r = rgb[..., 0]
        b = rgb[..., 2]
        gray = rgb.mean(axis=2)
        lum = gray.mean()
        r_mean = r.mean()
        g_mean = rgb[..., 1].mean()
        # Overcharge HUD: pink text on bright orange background.
        # Both colors saturate R (≈240–255) so R-channel and luminance
        # lose the text contrast, and the lum>140 branch below would
        # incorrectly invert. Distinguish from sunlit (where all three
        # channels co-vary with asteroid texture) by checking that B
        # has substantially more spread than R.
        r_std = float(r.std())
        b_std = float(b.std())
        if r_mean > 180 and b_std > 30 and b_std > r_std * 1.5:
            return b.astype(np.uint8)
        if lum > 140:
            return (255 - gray).astype(np.uint8)
        if g_mean - r_mean > 15:
            return rgb[..., 1].astype(np.uint8)
        if r_mean - g_mean > 15:
            return r.astype(np.uint8)
        return rgb.max(axis=2).astype(np.uint8)

    if mode == "g":
        return rgb[..., 1].astype(np.uint8)
    if mode == "r":
        return rgb[..., 0].astype(np.uint8)
    if mode == "b":
        return rgb[..., 2].astype(np.uint8)
    if mode == "invert_gray":
        return (255 - rgb.mean(axis=2)).astype(np.uint8)
    # default
    return rgb.max(axis=2).astype(np.uint8)


def otsu_threshold(channel: np.ndarray) -> int:
    """Return the Otsu threshold for a uint8 single-channel image.

    Pure NumPy, ~0.3 ms on a 300×100 region.
    """
    hist, _ = np.histogram(channel.ravel(), bins=256, range=(0, 256))
    total = channel.size
    if total == 0:
        return 128
    sum_total = (np.arange(256) * hist).sum()
    sum_bg = 0.0
    w_bg = 0
    max_var = 0.0
    threshold = 0
    for t in range(256):
        w_bg += int(hist[t])
        if w_bg == 0:
            continue
        w_fg = total - w_bg
        if w_fg == 0:
            break
        sum_bg += t * int(hist[t])
        mean_bg = sum_bg / w_bg
        mean_fg = (sum_total - sum_bg) / w_fg
        var = w_bg * w_fg * (mean_bg - mean_fg) ** 2
        if var > max_var:
            max_var = var
            threshold = t
    return threshold


def binarize(
    channel: np.ndarray,
    invert: bool = False,
) -> np.ndarray:
    """Threshold a single-channel image into a binary mask.

    Result is uint8 with values {0, 255}. Text is 255 by default
    (since ``isolate_channel`` already ensures text is bright).
    Set ``invert=True`` if text is expected to be dark on bright.
    """
    thr = otsu_threshold(channel)
    if invert:
        return np.where(channel < thr, 255, 0).astype(np.uint8)
    return np.where(channel > thr, 255, 0).astype(np.uint8)


def denoise_if_needed(channel: np.ndarray, std_threshold: float = 45.0) -> np.ndarray:
    """If the channel has high pixel noise (asteroid artifacts bleeding
    through a translucent panel), apply a single 3x3 morphological
    open to knock out single-pixel speckle.

    Returns the same channel unchanged if noise is low (fast path).
    """
    if channel.std() < std_threshold:
        return channel
    # Morphological open = erode + dilate, 3x3 square structuring element
    return _morph_open_3x3(channel)


def _morph_open_3x3(channel: np.ndarray) -> np.ndarray:
    """Pure NumPy 3x3 morphological open via PIL MinFilter/MaxFilter.

    Not time-critical — falls through only on noisy backgrounds.
    """
    from PIL import ImageFilter
    pil = Image.fromarray(channel, mode="L")
    eroded = pil.filter(ImageFilter.MinFilter(3))
    dilated = eroded.filter(ImageFilter.MaxFilter(3))
    return np.asarray(dilated, dtype=np.uint8)


def preprocess_rgb(
    rgb: np.ndarray,
    isolate: ColorIsolate = "auto",
) -> tuple[np.ndarray, np.ndarray]:
    """Full preprocessing: RGB → (single-channel, binary mask).

    Returns:
        channel : (H, W) uint8 — text-bright single channel
        binary  : (H, W) uint8 — 0/255 binary mask
    """
    channel = isolate_channel(rgb, mode=isolate)
    channel = denoise_if_needed(channel)
    binary = binarize(channel, invert=False)
    return channel, binary
