"""ONNX CNN fallback for low-confidence template matches.

Keeps the shipped ``ocr/models/model_cnn.onnx`` model around as a
safety net. Only invoked when a glyph's NCC confidence is below
``FALLBACK_CONF_THRESHOLD`` or the top-two template scores are
within a small ambiguity gap.

If the user installed an online-learned model at
``%LOCALAPPDATA%/SC_Toolbox/model_cnn_online.onnx``, that is
preferred (same behavior as the legacy onnx_hud_reader).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np

from .config import ONNX_MODEL_PATH

log = logging.getLogger(__name__)

# Lazy-loaded
_session = None
_char_classes: str = "0123456789.-%"

_ONLINE_MODEL_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "SC_Toolbox", "model_cnn_online.onnx",
)


def _ensure_model() -> bool:
    global _session, _char_classes
    if _session is not None:
        return True

    path = _ONLINE_MODEL_PATH if os.path.isfile(_ONLINE_MODEL_PATH) else str(ONNX_MODEL_PATH)
    if not os.path.isfile(path):
        log.debug("sc_ocr.fallback: ONNX model not found at %s", path)
        return False

    try:
        import onnxruntime as ort
    except ImportError:
        log.debug("sc_ocr.fallback: onnxruntime not installed")
        return False

    try:
        import json
        meta_path = os.path.join(os.path.dirname(path), "model_cnn.json")
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
                _char_classes = meta.get("charClasses", _char_classes)

        # Single-threaded to respect the 7% CPU budget
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        _session = ort.InferenceSession(
            path,
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        log.info("sc_ocr.fallback: ONNX loaded (%s)", os.path.basename(path))
        return True
    except Exception as exc:
        log.warning("sc_ocr.fallback: ONNX load failed: %s", exc)
        return False


def classify_glyph(crop_28: np.ndarray) -> Optional[tuple[str, float]]:
    """Run a single 28x28 glyph through the ONNX CNN.

    Expects a uint8 or float32 grayscale image, shape (28, 28),
    with text BRIGHT on a dark background (post-preprocess).
    Returns (char, confidence) or None if the model isn't loaded.
    """
    if not _ensure_model():
        return None

    if crop_28.shape != (28, 28):
        return None

    # Normalize to [0, 1] float32 (ONNX model's input convention)
    arr = crop_28.astype(np.float32)
    if arr.max() > 1.0:
        arr = arr / 255.0
    arr = arr.reshape(1, 1, 28, 28)

    try:
        inp_name = _session.get_inputs()[0].name
        logits = _session.run(None, {inp_name: arr})[0][0]  # (13,)
        # Softmax
        logits = logits - logits.max()
        exp = np.exp(logits)
        probs = exp / exp.sum()
        idx = int(np.argmax(probs))
        return _char_classes[idx], float(probs[idx])
    except Exception as exc:
        log.debug("sc_ocr.fallback: inference failed: %s", exc)
        return None
