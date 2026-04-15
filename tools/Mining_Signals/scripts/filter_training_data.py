"""Filter the raw training_data/ to produce a clean per-class sample set.

Rules:
 1. Drop samples with bright-pixel count >= 700 (fully-white corrupt crops)
 2. Dedupe via md5 image hash
 3. Filter outliers: keep samples within the interquartile range of bright-pixel count
 4. Cap at K samples per class

Merges result with training_data_clean/ (from extract_templates_from_fixtures.py)
and writes the combined clean corpus to training_data_clean/.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

THIS = Path(__file__).resolve()
TOOL = THIS.parent.parent
RAW_DIR = TOOL / "training_data"
CLEAN_DIR = TOOL / "training_data_clean"

BRIGHT_CORRUPT = 700   # drop samples with >= this many bright pixels
MAX_PER_CLASS = 40     # keep at most this many clean samples per class
MIN_BRIGHT = 100       # drop too-dim samples (possibly mis-cropped)


def filter_class(cls_dir: Path) -> list[tuple[str, np.ndarray]]:
    files = sorted(cls_dir.glob("*.png"))
    hashes_seen: set[str] = set()
    candidates: list[tuple[int, str, np.ndarray]] = []  # (bright_count, name, arr)

    for p in files:
        arr = np.asarray(Image.open(p).convert("L"), dtype=np.uint8)
        h = hashlib.md5(arr.tobytes()).hexdigest()
        if h in hashes_seen:
            continue
        hashes_seen.add(h)
        bright = int((arr > 128).sum())
        if bright >= BRIGHT_CORRUPT or bright < MIN_BRIGHT:
            continue
        candidates.append((bright, p.stem, arr))

    if not candidates:
        return []

    # IQR filter
    bcs = np.array([b for b, _, _ in candidates])
    q1, q3 = np.percentile(bcs, [25, 75])
    iqr = q3 - q1
    lo, hi = q1 - 0.5 * iqr, q3 + 0.5 * iqr
    inrange = [(b, n, a) for (b, n, a) in candidates if lo <= b <= hi]
    if len(inrange) < 3:
        # If IQR filter is too aggressive, fall back to all
        inrange = candidates

    # Sort by "centerness" (closeness to median bright count) and cap
    median = float(np.median(bcs))
    inrange.sort(key=lambda t: abs(t[0] - median))
    kept = inrange[:MAX_PER_CLASS]
    return [(name, arr) for (_b, name, arr) in kept]


def merge_into_clean(per_class: dict[str, list[tuple[str, np.ndarray]]]):
    """Write filtered samples into training_data_clean/, alongside
    existing entries from the fixture extractor."""
    for ch, entries in per_class.items():
        d = CLEAN_DIR / ch
        d.mkdir(parents=True, exist_ok=True)
        # Count existing (from fixtures)
        existing = len(list(d.glob("*.png")))
        for i, (name, arr) in enumerate(entries):
            out = d / f"raw_{name}_{i}.png"
            Image.fromarray(arr, mode="L").save(out)
        print(f"  '{ch}': kept {len(entries)} (on top of {existing} from fixtures)")


def main() -> None:
    CLEAN_DIR.mkdir(exist_ok=True)
    per_class: dict[str, list[tuple[str, np.ndarray]]] = {}
    print(f"Filtering {RAW_DIR} -> {CLEAN_DIR}")
    for ch in "0123456789":
        cd = RAW_DIR / ch
        if not cd.is_dir():
            continue
        kept = filter_class(cd)
        per_class[ch] = kept
    merge_into_clean(per_class)
    print()
    # Final tally
    print("Final clean corpus:")
    for ch in "0123456789":
        d = CLEAN_DIR / ch
        n = len(list(d.glob("*.png"))) if d.is_dir() else 0
        print(f"  '{ch}': {n}")


if __name__ == "__main__":
    main()
