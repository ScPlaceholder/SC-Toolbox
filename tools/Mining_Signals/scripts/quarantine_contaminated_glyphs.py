"""Detect + quarantine glyph training samples that visually contain
MORE than one digit (e.g. an "8" crop that shows "08").

Background: ``augment_from_source.py`` was generating training crops
by jittering Tesseract bbox positions ±4 px without checking that
the shifts didn't cross into adjacent digits. With SC HUD's tight
4-6 px inter-digit kerning, those shifts pulled neighbor digit
pixels into the crop. Result: thousands of training samples labeled
e.g. ``8`` whose actual visual content is ``08``, ``13`` for ``3``,
etc. The classifier learned the wrong concept.

This script scans the per-class folders under
``training_data_user_sig/`` (and optionally
``training_data_user_panel/``), detects multi-digit content via
column-cluster counting, and MOVES contaminated samples to a
``_quarantine/`` subfolder so they're preserved for inspection
but excluded from future training.

Detection heuristic:
  1. Resize each crop to a canonical 28×28 (already the standard
     trainer input size).
  2. Adaptive-binarize via the same routine the OCR pipeline uses
     so we see what the classifier sees.
  3. Project bright pixels onto the x-axis.
  4. Count distinct vertical column clusters separated by ≥ 2 px
     of zero-projection gap.
  5. If ≥ 2 distinct column clusters of width ≥ 3 px → multi-digit
     → quarantine.

SAFETY:
  * NEVER touches ``user_*.png`` (your hand-curated samples).
  * NEVER touches ``aug_*.png`` (the synthetic-augment outputs).
  * Only ``src_*.png`` (the buggy bbox-jitter augmentation output)
    is examined / quarantined.
  * Files are MOVED, never deleted. To restore, drag back from
    ``_quarantine/``.

Run:
    python scripts/quarantine_contaminated_glyphs.py
    python scripts/quarantine_contaminated_glyphs.py --dry-run
    python scripts/quarantine_contaminated_glyphs.py --kind both
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

THIS = Path(__file__).resolve()
TOOL = THIS.parent.parent

KIND_DIRS = {
    "signal": TOOL / "training_data_user_sig",
    "hud":    TOOL / "training_data_user_panel",
}

# Only touch files that match this prefix. ``user_*.png`` are the
# user's hand-labels — never touched. ``aug_*.png`` are synthetic
# variants from a different augmenter — also untouched (different
# script, different bug surface).
TARGET_PREFIX = "src_"


def _binarize_for_detection(gray: np.ndarray) -> np.ndarray:
    """Polarity-aware binarization tuned for 28×28 training glyphs,
    where the output mask always has TEXT pixels = 1 and BG = 0.

    Originally used a median-based polarity flip (``if median > 128
    invert``) but that fails on glyphs rendered as bright text on a
    DARK-GRAY (not pure black) background — a typical SC HUD case.
    There the median sits ~150-180 even though text is already the
    bright pixels, so the heuristic flipped the wrong way and made
    the BG end up as the "1" class. Connected-component analysis
    then saw one giant BG blob and missed the (now dark) digit
    components.

    Minority-class rule (background-agnostic):
      1. Pick an Otsu threshold from the histogram.
      2. Binarize with that threshold.
      3. Whichever class has FEWER pixels IS the text (text is
         always a small fraction of the crop).
      4. If the minority is the "0" side of the Otsu split, invert
         so text ends up as "1".

    This is the same convention ``_canonicalize_polarity`` in
    api.py uses, just inlined here to avoid pulling in the OCR
    module chain.
    """
    if gray.size == 0:
        return np.zeros_like(gray, dtype=np.uint8)
    # Otsu on the raw image (no pre-inversion).
    hist, _ = np.histogram(gray.flatten(), bins=256, range=(0, 256))
    total = gray.size
    sum_total = float(np.sum(np.arange(256) * hist))
    sum_bg, w_bg = 0.0, 0
    max_var, thr = 0.0, 127
    for t in range(256):
        w_bg += int(hist[t])
        if w_bg == 0:
            continue
        w_fg = total - w_bg
        if w_fg == 0:
            break
        sum_bg += t * int(hist[t])
        m_bg = sum_bg / w_bg
        m_fg = (sum_total - sum_bg) / w_fg
        var = w_bg * w_fg * (m_bg - m_fg) ** 2
        if var > max_var:
            max_var = var
            thr = t
    above = (gray > thr).astype(np.uint8)
    below = 1 - above
    # Polarity decision via connected-component size, not pixel-count
    # majority. The "text" polarity has the digit shapes as several
    # small-medium components. The "BG" polarity has the whole
    # background as ONE huge component. Pick whichever side produces
    # the SMALLER largest-component — that's the side where text is
    # the foreground.
    #
    # This is robust against anti-aliasing where bright/dark pixel
    # counts can be nearly 50/50 and pixel-majority guesses wrong.
    from scipy.ndimage import label
    _, n_above = label(above)
    _, n_below = label(below)

    def _max_comp_size(mask):
        if not mask.any():
            return 0
        from scipy.ndimage import label as _lbl
        labels, _ = _lbl(mask)
        sizes = np.bincount(labels.flatten())
        if len(sizes) <= 1:
            return 0
        return int(sizes[1:].max())

    max_above = _max_comp_size(above)
    max_below = _max_comp_size(below)
    # Pick the side whose largest component is smaller (= text side).
    # Tiebreaker: minority pixel count (the original heuristic).
    if max_above < max_below:
        return above
    elif max_below < max_above:
        return below
    else:
        return above if int(above.sum()) <= total // 2 else below


def _count_x_separated_blobs(
    binary: np.ndarray,
    min_comp_size: int = 12,
    min_x_gap: int = 1,
) -> int:
    """Count distinct HORIZONTALLY-SEPARATED blob groups in the binary
    mask. Robust to font-quirks where one digit has multiple strokes.

    Algorithm:
      1. Connected-components label the binary mask.
      2. Drop tiny components (< min_comp_size px) — anti-aliasing
         halos and salt-pepper noise.
      3. Compute each surviving component's (x_min, x_max).
      4. Sort components by x_min, then merge any pair whose x-ranges
         overlap (or are within min_x_gap px of each other) into a
         single horizontal blob group.
      5. Return the number of resulting groups.

    Single digit → 1 group regardless of stroke count:
      * ``0``: 1 closed-loop component → 1 group
      * ``8``: 1 figure-8 component → 1 group
      * ``9``: top-loop + tail at SAME x-range (stacked) → 1 group
      * ``4``: vertical stem + horizontal bar at SAME x-range → 1 group
      * ``i``: stem + dot at same x → 1 group

    Multi-digit contamination → 2+ groups:
      * ``08``: ``0`` blob at x≈4-12, ``8`` blob at x≈14-22 → 2 groups
      * ``20``: same pattern → 2 groups
    """
    from scipy.ndimage import label
    labels, n = label(binary)
    if n == 0:
        return 0
    # Per-component bounding box.
    blobs: list[tuple[int, int]] = []  # (x_min, x_max) for surviving comps
    for i in range(1, n + 1):
        ys, xs = np.where(labels == i)
        if xs.size < min_comp_size:
            continue
        blobs.append((int(xs.min()), int(xs.max())))
    if not blobs:
        return 0
    # Merge x-overlapping (or near-touching) components into groups.
    blobs.sort(key=lambda b: b[0])
    groups = 1
    cur_x_max = blobs[0][1]
    for x_min, x_max in blobs[1:]:
        if x_min - cur_x_max <= min_x_gap:
            # Same horizontal blob group — extend the right edge.
            cur_x_max = max(cur_x_max, x_max)
        else:
            # Gap large enough → new group.
            groups += 1
            cur_x_max = x_max
    return groups


def _expected_internal_holes(class_label: str) -> int:
    """How many natural interior holes the digit class has, in the
    SC HUD font. Used to set the per-class component-count threshold
    so a clean digit doesn't get false-flagged for having multiple
    natural connected pieces."""
    return {
        "0": 1, "1": 0, "2": 0, "3": 0, "4": 0,
        "5": 0, "6": 1, "7": 0, "8": 2, "9": 1,
    }.get(class_label, 0)


def _is_contaminated(path: Path) -> bool:
    """True if the crop shows ≥ 2 distinct digit-shaped column
    clusters (multi-digit contamination)."""
    try:
        img = Image.open(path).convert("L")
    except Exception:
        return False
    arr = np.asarray(img, dtype=np.uint8)
    if arr.shape != (28, 28):
        # Resize to canonical size for consistent detection.
        img = img.resize((28, 28), Image.LANCZOS)
        arr = np.asarray(img, dtype=np.uint8)
    # Class label is the parent directory name.
    class_label = path.parent.name
    # Polarity is genuinely ambiguous on some glyph crops (text
    # strokes touching the BG via anti-aliasing makes both sides of
    # the Otsu split look "text-like"). Check BOTH polarities and
    # flag if EITHER signals contamination. False-positive risk
    # stays low because clean single-digit crops produce 1-2
    # meaningful components in either polarity.
    from scipy.ndimage import label as _label
    binary = _binarize_for_detection(arr)
    MIN_COMP_SIZE = 8
    # Per-class meaningful-component threshold: 1 (digit outline) +
    # 1 (BG) + N (interior holes for this class) + 1 (extra blob =
    # contamination signal). So digit "0" allows up to 3 components
    # before flagging; "8" allows up to 4; etc.
    holes = _expected_internal_holes(class_label)
    component_threshold = 3 + holes

    for cand in (binary, 1 - binary):
        # Signal A: 2+ horizontally-separated blob groups.
        if _count_x_separated_blobs(cand) >= 2:
            return True
        # Signal B: meaningful-sized component count, against the
        # per-class threshold. A clean "8" naturally has 3
        # components in the BG polarity (BG + 2 interior holes),
        # so for class "8" we need ≥ 5 to flag. Class "0" has 1
        # natural hole → threshold 4. Class "1" has 0 holes →
        # threshold 3. This stops false positives on clean digits
        # whose font shape includes multiple internal voids.
        labels, n = _label(cand)
        if n == 0:
            continue
        sizes = np.bincount(labels.flatten())[1:]  # drop 0=background
        meaningful = int((sizes >= MIN_COMP_SIZE).sum())
        if meaningful >= component_threshold:
            return True
        # Signal C: outside-main-blob check (catches "0," / "I0"
        # patterns where the small extra blob sits outside the
        # main digit's bbox).
        if n < 2:
            continue
        biggest_idx = int(np.argmax(sizes)) + 1
        main_mask = (labels == biggest_idx)
        ys, xs = np.where(main_mask)
        if xs.size == 0:
            continue
        x1, x2 = int(xs.min()), int(xs.max())
        y1, y2 = int(ys.min()), int(ys.max())
        if int((cand == 1).sum()) < 30:
            continue
        other = (cand == 1) & (~main_mask)
        other_outside = other.copy()
        other_outside[y1:y2 + 1, x1:x2 + 1] = False
        if int(other_outside.sum()) >= 8:
            return True
    return False


def quarantine_kind(kind: str, dry_run: bool) -> dict[str, int]:
    """Scan one training folder, quarantine contaminated src_*.png.
    Returns per-class quarantine counts."""
    root = KIND_DIRS[kind]
    if not root.is_dir():
        print(f"[skip] {kind}: {root} doesn't exist")
        return {}
    print(f"\n=== {kind}: scanning {root} ===")
    counts: dict[str, int] = {}
    for cls_dir in sorted(root.iterdir()):
        if not cls_dir.is_dir() or cls_dir.name.startswith("_"):
            continue
        cls = cls_dir.name
        candidates = sorted(cls_dir.glob(f"{TARGET_PREFIX}*.png"))
        if not candidates:
            continue
        contaminated: list[Path] = []
        for p in candidates:
            if _is_contaminated(p):
                contaminated.append(p)
        counts[cls] = len(contaminated)
        if contaminated:
            print(
                f"  class {cls!r:>5}: {len(contaminated):5d} of "
                f"{len(candidates):5d} src_*.png files contaminated "
                f"({100.0 * len(contaminated) / len(candidates):.1f}%)"
            )
            if not dry_run:
                quarantine_dir = cls_dir / "_quarantine"
                quarantine_dir.mkdir(exist_ok=True)
                for p in contaminated:
                    target = quarantine_dir / p.name
                    # If a file with the same name already exists in
                    # quarantine (re-running), append a counter.
                    counter = 1
                    while target.exists():
                        target = quarantine_dir / (
                            f"{p.stem}__dup{counter}{p.suffix}"
                        )
                        counter += 1
                    shutil.move(str(p), str(target))
        else:
            print(
                f"  class {cls!r:>5}: 0 of {len(candidates)} clean (good)"
            )
    return counts


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--kind", choices=["signal", "hud", "both"], default="signal",
        help="Which training folder to scan. Default: signal "
             "(only place augment_from_source has written so far).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Report contamination counts without moving any files.",
    )
    args = p.parse_args()

    print(f"=== Contaminated-glyph quarantine ===")
    print(f"    kind:    {args.kind}")
    print(f"    mode:    {'DRY-RUN (no files moved)' if args.dry_run else 'live (files will be moved to _quarantine/)'}")
    print(f"    target:  {TARGET_PREFIX}*.png  (user_*.png and aug_*.png are NOT touched)")

    kinds = ["signal", "hud"] if args.kind == "both" else [args.kind]
    grand_total = 0
    for kind in kinds:
        counts = quarantine_kind(kind, args.dry_run)
        kind_total = sum(counts.values())
        print(f"  -> {kind} total: {kind_total} contaminated")
        grand_total += kind_total

    print()
    print(f"=== Grand total: {grand_total} files {'identified' if args.dry_run else 'moved to _quarantine/'} ===")
    if not args.dry_run and grand_total > 0:
        print()
        print("Next: re-augment with the FIXED script "
              "(neighbor-aware shifts, no more cross-boundary contamination):")
        print("  python scripts/augment_from_source.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
