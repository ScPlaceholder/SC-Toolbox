"""Paths and constants for SC-OCR.

All filesystem paths are resolved once at import and exposed as
module-level constants. Paths that hold per-user data live under
``%LOCALAPPDATA%/SC_Toolbox/sc_ocr/`` so installer upgrades never
wipe learned templates or profile overrides.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Shipped assets (read-only, replaced by installer) ──────────────
OCR_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = OCR_DIR / "sc_templates"
PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
ONNX_MODEL_PATH = OCR_DIR / "models" / "model_cnn.onnx"
# Optional sibling 28×28 classifier trained on polarity-INVERTED
# crops (dark text on light bg).  When present, the secondary voter
# in api._ocr_value_crop classifies the inverted version of the
# primary's crops with this model — true peer voter, decorrelated
# from the canonical model by both polarity and weights.  Absent
# until the user trains it via:
#     python scripts/make_inverted_dataset.py
#     python -m ocr.train_model --inverted
ONNX_MODEL_PATH_INV = OCR_DIR / "models" / "model_cnn_inv.onnx"
CRNN_MODEL_PATH = OCR_DIR / "models" / "model_crnn.onnx"
CRNN_META_PATH = OCR_DIR / "models" / "model_crnn.json"
# Optional secondary CRNN — if present, runs in parallel with the
# primary CRNN and their outputs are ensembled (highest-confidence
# read wins). Train with e.g. different init or different augmentation
# schedule to get decorrelated errors; copy the resulting
# model_crnn.onnx + .json to these filenames to activate.
CRNN2_MODEL_PATH = OCR_DIR / "models" / "model_crnn_v2.onnx"
CRNN2_META_PATH = OCR_DIR / "models" / "model_crnn_v2.json"

# ── Per-user writable state ────────────────────────────────────────
_LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
USER_ROOT = Path(_LOCALAPPDATA) / "SC_Toolbox" / "sc_ocr"
USER_TEMPLATES_DIR = USER_ROOT / "templates"
USER_PROFILES_DIR = USER_ROOT / "profiles"
USER_RESERVOIR_DIR = Path(_LOCALAPPDATA) / "SC_Toolbox" / "digit_reservoir"

# ── Algorithm tunables ─────────────────────────────────────────────
# NCC score below which the ONNX fallback is consulted.
FALLBACK_CONF_THRESHOLD = 0.72
# NCC score above which an auto-learn EMA update is permitted
# (also requires validator to pass).
AUTO_LEARN_CONF_THRESHOLD = 0.92

# Shift-invariant NCC search window (in pixels from centered crop).
SHIFT_SEARCH_X = 2
SHIFT_SEARCH_Y = 1

# Canonical template size. Templates are stored at this height;
# at scan time they're resized on-demand per detected glyph height
# via an LRU cache.
CANON_TEMPLATE_H = 28
CANON_TEMPLATE_W = 28

# Maximum EMA sample count before learning slows to α=0.05.
# α = 1 / min(weight + 1, SATURATION), so after this many samples
# the template becomes stable.
EMA_SATURATION = 20

# Debounce window for user-template writes (seconds).
TEMPLATE_WRITE_DEBOUNCE_S = 1.0


def ensure_user_dirs() -> None:
    """Create the per-user dirs on first use."""
    USER_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    USER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
