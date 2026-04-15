"""SC-OCR: custom OCR engine for Star Citizen HUD/terminal text.

Replaces the previous three-engine stack (Tesseract + ONNX CNN +
PaddleOCR sidecar) with a single surgical pipeline of cheap
deterministic stages. Designed for a constrained alphabet on a
known sci-fi font at user-defined rectangles.

Pipeline:
    capture → preprocess → segment → classify → validate → (learn)

Public API preserves the legacy call signatures so ``ui/app.py``
doesn't need to change:

    from ocr.sc_ocr.api import scan_region, scan_hud_onnx, scan_refinery

See plan at ``.claude/plans/bright-swimming-piglet.md``.
"""
from __future__ import annotations

# Cap BLAS threads BEFORE importing numpy (via any downstream). The
# previous Paddle pipeline spun up OMP thread pools per BLAS call and
# used all available CPU cores, which hit 90% on user machines during
# scans. 28×28 NCC does not benefit from threading — the overhead
# dominates. Single-threaded numpy is fastest for our glyph sizes.
import os as _os
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
             "NUMEXPR_NUM_THREADS"):
    _os.environ.setdefault(_var, "1")

from .api import (  # noqa: E402
    scan_region,
    scan_hud_onnx,
    scan_refinery,
)

__all__ = ["scan_region", "scan_hud_onnx", "scan_refinery"]
