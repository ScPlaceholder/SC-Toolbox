"""Build the shipped digit-template pack from training_data/.

Scans ``training_data/{0-9}/*.png``, averages each class's samples into
a single 28x28 canonical template, pre-normalizes it to zero-mean
unit-variance, and writes the result to
``tools/Mining_Signals/ocr/sc_templates/digits.npz``.

Run once at build time (or whenever training_data grows significantly).
The sc_ocr engine loads the .npz at import — no Python execution of
this script is needed at runtime.

Output format (npz):
    chars   : (N,)  uint32 codepoints
    images  : (N, 28, 28) float32 zero-mean unit-variance
    weights : (N,)  int32 sample count per class
    meta    : json string with build timestamp + source description
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

# ── Paths ──
THIS_DIR = Path(__file__).parent.resolve()
TOOL_DIR = THIS_DIR.parent
TRAINING_DIR = TOOL_DIR / "training_data"
OUT_DIR = TOOL_DIR / "ocr" / "sc_templates"
OUT_PATH = OUT_DIR / "digits.npz"

# ── Config ──
TEMPLATE_H = 28
TEMPLATE_W = 28
# Only digits for now. Punctuation (. - %) templates are bootstrapped
# later from refinery/HUD captures; we don't have clean training_data
# for them yet.
CLASSES = list("0123456789")


def _load_class(class_dir: Path) -> np.ndarray | None:
    """Load all PNGs in class_dir, binarize each, stack as (N, H, W) {0, 1}.

    Binarizing BEFORE averaging preserves the distinguishing features
    of each digit class. Naive grayscale averaging produces soft
    "cloud" templates that cross-correlate too similarly across
    classes (a '0' sample matches the '1' template better than its
    own average). Per-sample binarization via Otsu gives each template
    crisp strokes where the class's pixels are consistently on.
    """
    pngs = sorted(class_dir.glob("*.png"))
    if not pngs:
        return None
    arrs: list[np.ndarray] = []
    for p in pngs:
        try:
            img = Image.open(p).convert("L")
            if img.size != (TEMPLATE_W, TEMPLATE_H):
                img = img.resize((TEMPLATE_W, TEMPLATE_H), Image.LANCZOS)
            raw = np.asarray(img, dtype=np.uint8)
            # Per-sample Otsu binarization (NumPy histogram-based)
            hist, _ = np.histogram(raw.ravel(), bins=256, range=(0, 256))
            total = raw.size
            sum_total = (np.arange(256) * hist).sum()
            sum_bg, w_bg, max_var, thr = 0.0, 0, 0.0, 128
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
                    max_var, thr = var, t
            binary = (raw > thr).astype(np.float32)
            arrs.append(binary)
        except Exception as exc:
            print(f"  skipped {p.name}: {exc}", file=sys.stderr)
    if not arrs:
        return None
    return np.stack(arrs, axis=0)


def _normalize(template: np.ndarray) -> np.ndarray:
    """Zero-mean, unit-L2-norm for NCC. Shape-preserving."""
    t = template - template.mean()
    norm = np.sqrt((t * t).sum())
    if norm < 1e-6:
        return t  # degenerate (blank template), don't divide by zero
    return t / norm


def build() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    chars: list[int] = []
    images: list[np.ndarray] = []
    weights: list[int] = []

    print(f"Building digit templates from {TRAINING_DIR}")
    for cls in CLASSES:
        class_dir = TRAINING_DIR / cls
        if not class_dir.is_dir():
            print(f"  '{cls}': directory missing, skipping")
            continue
        samples = _load_class(class_dir)
        if samples is None:
            print(f"  '{cls}': no samples, skipping")
            continue

        # Pick the MEDOID sample: the single sample whose pairwise
        # correlation with all other samples is highest. This gives
        # us the most "representative" real glyph without the smearing
        # that averaging across variable positions/scales produces.
        #
        # Samples are already binarized (0/1) from _load_class, so
        # correlation between two samples is just the dot product
        # after centering.
        N = samples.shape[0]
        if N == 1:
            prototype = samples[0]
        else:
            # Flatten each sample and center
            flat = samples.reshape(N, -1).astype(np.float32)
            flat = flat - flat.mean(axis=1, keepdims=True)
            norms = np.sqrt((flat * flat).sum(axis=1, keepdims=True))
            norms[norms < 1e-6] = 1.0
            flat = flat / norms
            # Pairwise cosine similarity → (N, N)
            sim = flat @ flat.T
            # Average similarity to all peers (exclude self)
            np.fill_diagonal(sim, 0)
            mean_sim = sim.sum(axis=1) / (N - 1)
            best_i = int(np.argmax(mean_sim))
            prototype = samples[best_i]
        # Pre-normalize for NCC
        normalized = _normalize(prototype)

        chars.append(ord(cls))
        images.append(normalized)
        weights.append(samples.shape[0])
        print(f"  '{cls}': averaged {samples.shape[0]} samples")

    if not chars:
        print("ERROR: no templates built", file=sys.stderr)
        sys.exit(1)

    chars_arr = np.asarray(chars, dtype=np.uint32)
    images_arr = np.stack(images, axis=0).astype(np.float32)
    weights_arr = np.asarray(weights, dtype=np.int32)
    meta = {
        "version": 1,
        "source": "shipped",
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "classes": CLASSES,
        "template_h": TEMPLATE_H,
        "template_w": TEMPLATE_W,
        "normalization": "zero_mean_unit_L2",
    }

    np.savez_compressed(
        OUT_PATH,
        chars=chars_arr,
        images=images_arr,
        weights=weights_arr,
        meta=json.dumps(meta),
    )
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"\nWrote {OUT_PATH} ({size_kb:.1f} KB, {len(chars)} templates)")


if __name__ == "__main__":
    build()
