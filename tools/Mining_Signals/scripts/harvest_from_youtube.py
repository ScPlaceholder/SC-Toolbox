"""Harvest labeled digit glyphs from 1440p+ Star Citizen mining streams.

Pipeline per time range:
  1. yt-dlp downloads a 1440p clip (~25-60 MB per minute)
  2. ffmpeg extracts frames at a configurable fps (default 0.5 = 1 per 2s)
  3. Each frame is scanned for the SCAN RESULTS panel via a two-stage
     heuristic — high-contrast text density in the right-third of
     the frame that matches the panel's aspect ratio
  4. The panel crop is UPSCALED 3x (legacy OCR was tuned for native
     HUD capture size; stream panels are ~2x smaller so need upscale)
     then passed through ``scan_hud_onnx``
  5. If OCR produces a full result (mass + resistance + instability,
     all non-None, with panel_visible=True), we trust it as ground
     truth and harvest the individual digit crops into
     ``training_data_clean/<digit>/yt_<videoid>_<frame>.png``

Resume-friendly: frame hashes are tracked in ``.harvest_seen.txt`` —
re-running is idempotent.

Usage:
  python harvest_from_youtube.py \\
      --url 'https://www.youtube.com/watch?v=VIDEO_ID' \\
      --start 0:54:00 --end 0:55:30 \\
      --fps 0.5

Multiple time ranges per run:
  python harvest_from_youtube.py --batch harvest_plan.txt
  # harvest_plan.txt: one line per run, "URL<TAB>start<TAB>end<TAB>fps"

Dependencies: yt-dlp (pip), ffmpeg (on PATH), pillow, numpy,
onnxruntime (legacy OCR needs these).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

THIS = Path(__file__).resolve()
TOOL = THIS.parent.parent
sys.path.insert(0, str(TOOL))

# yt-dlp needs a JS runtime to solve YouTube's n-challenge (otherwise
# only image/storyboard formats are returned). Deno is installed via
# winget but isn't auto-added to PATH; locate it and prepend.
_DENO_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "Microsoft", "WinGet", "Packages",
    "DenoLand.Deno_Microsoft.Winget.Source_8wekyb3d8bbwe",
)
if _DENO_DIR and os.path.isdir(_DENO_DIR) and _DENO_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _DENO_DIR + os.pathsep + os.environ.get("PATH", "")

from ocr.onnx_hud_reader import (  # noqa: E402
    _ensure_model, _find_label_rows, _find_value_crop, _otsu,
    scan_hud_onnx,
)
from ocr import onnx_hud_reader as hud  # noqa: E402
from ocr import screen_reader as sr  # noqa: E402

OUT_DIR = TOOL / "training_data_clean"
PROGRESS_FILE = OUT_DIR / ".harvest_seen.txt"

# Parallel dataset for the CRNN: whole value crops + a JSON manifest
# mapping each file to the label decoded by the teacher pipeline.
CRNN_OUT_DIR = TOOL / "training_data_crnn"
CRNN_MANIFEST = CRNN_OUT_DIR / "manifest.json"

# Panels-only mode (--mode panels): saves the full SCAN RESULTS panel
# (header + values + IMPOSSIBLE/EASY/etc + composition rows) as a
# single PNG per detected frame. No OCR, no segmentation — raw crops
# for later post-processing into training samples by a separate splitter.
PANEL_OUT_DIR = TOOL / "training_data_panels"
PANEL_PROGRESS_FILE = PANEL_OUT_DIR / ".panel_harvest_seen.txt"

# After upscale, the panel matches what the legacy OCR expects.
UPSCALE_FACTOR = 3.0

# ─── Single-instance lock ────────────────────────────────────────────
# Prevents accidentally launching multiple harvesters on the same plan,
# which races on the seen-hashes file and produces 4× duplicate crops.
LOCK_FILE = TOOL / ".harvest.lock"


def _acquire_lock() -> bool:
    """Try to acquire an exclusive run lock. Returns False if another
    harvester is already running."""
    if LOCK_FILE.is_file():
        try:
            other_pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            other_pid = -1
        # Check if that PID is alive
        if other_pid > 0 and _pid_alive(other_pid):
            print(
                f"ERROR: another harvester is running (PID {other_pid}). "
                f"Wait for it to finish or kill it. Lock: {LOCK_FILE}",
                file=sys.stderr,
            )
            return False
        # Stale lock — overwrite
    try:
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        print(f"WARNING: could not write lock: {exc}", file=sys.stderr)
    return True


def _release_lock() -> None:
    try:
        if LOCK_FILE.is_file():
            LOCK_FILE.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """Cross-platform liveness probe."""
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _is_live_url(url: str) -> bool:
    """Return True if the URL is a currently-live broadcast.

    Skips livestreams that haven't been processed into a VOD yet —
    those have no fixed end-of-stream marker and yt-dlp/ffmpeg will
    stream until the broadcast actually ends or the connection drops.
    Once a livestream finishes and YouTube re-publishes it as a VOD
    (usually within hours), the same URL becomes harvestable.
    """
    cookies_file = os.environ.get("HARVEST_COOKIES_FILE")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--print", "is_live",
        "--no-warnings", "--quiet",
        "--skip-download",
    ]
    if cookies_file and os.path.isfile(cookies_file):
        cmd.extend(["--cookies", cookies_file])
    cmd.append(url)
    try:
        out = subprocess.check_output(
            cmd, text=True, timeout=30, stderr=subprocess.DEVNULL,
        ).strip()
        return out.lower() == "true"
    except Exception:
        return False  # If we can't tell, attempt the download


def _seen_hashes() -> set[str]:
    if not PROGRESS_FILE.is_file():
        return set()
    try:
        return set(PROGRESS_FILE.read_text(encoding="utf-8").split())
    except OSError:
        return set()


def _mark_seen(h: str) -> None:
    try:
        with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
            f.write(h + "\n")
    except OSError:
        pass


def _download_clip(url: str, start: str, end: str, out_path: Path) -> bool:
    """Download a time range from a YouTube video.

    If ``start == '0:00:00'`` and ``end in ('', 'FULL', None)`` the
    entire video is downloaded (no --download-sections slicing).

    Retries with ``--cookies-from-browser`` fallbacks when YouTube's
    anti-bot gate blocks anonymous requests. Browser order tries
    Firefox first (preferred by yt-dlp), then Edge/Chrome on
    Windows. Set HARVEST_COOKIES_BROWSER=<name> to override.
    """
    base_cmd = [
        sys.executable, "-m", "yt_dlp",
        # Prefer 1440p (codec 400 AV1, or 308 VP9), fall back to
        # 1080p, then any best video+audio combo, then any single best.
        # The trailing /best ensures we always grab SOMETHING — many
        # older mining VODs are 720p-only with no separate streams.
        "-f", "400+bestaudio/308+bestaudio/399+bestaudio/"
              "bestvideo[height<=1440]+bestaudio/"
              "bestvideo+bestaudio/best",
        "-o", str(out_path),
        "--merge-output-format", "mp4",
        "--no-warnings", "--quiet",
    ]
    if end and end.upper() != "FULL":
        base_cmd.extend(["--download-sections", f"*{start}-{end}"])

    # Preferred: --cookies <cookies.txt> via HARVEST_COOKIES_FILE env var.
    # Side-steps Chrome 127+ App-Bound Encryption entirely; user exports
    # cookies with a browser extension like "Get cookies.txt LOCALLY".
    cookies_file = os.environ.get("HARVEST_COOKIES_FILE")
    override = os.environ.get("HARVEST_COOKIES_BROWSER")
    attempts: list[list[str]] = []
    if cookies_file and os.path.isfile(cookies_file):
        attempts.append(base_cmd + ["--cookies", cookies_file, url])
    if not override and not cookies_file:
        attempts.append(base_cmd + [url])  # try anonymous first
    if not cookies_file:
        browsers = ([override] if override else ["firefox", "edge", "chrome"])
        for br in browsers:
            attempts.append(base_cmd + ["--cookies-from-browser", br, url])

    last_err: Optional[str] = None
    for i, cmd in enumerate(attempts):
        try:
            subprocess.check_call(cmd)
            if out_path.exists():
                return True
        except subprocess.CalledProcessError as exc:
            last_err = str(exc)
            # Try next fallback
            continue
        except Exception as exc:
            last_err = str(exc)
            continue
    print(f"  yt-dlp failed after {len(attempts)} attempts: {last_err}", file=sys.stderr)
    return False


def _extract_frames(video_path: Path, frames_dir: Path, fps: float) -> list[Path]:
    frames_dir.mkdir(exist_ok=True)
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-q:v", "2",
        str(frames_dir / "f_%04d.jpg"),
        "-loglevel", "error",
    ]
    subprocess.check_call(cmd)
    return sorted(frames_dir.glob("*.jpg"))


def _find_scan_panel(frame: Image.Image) -> Optional[Image.Image]:
    """Locate the SCAN RESULTS panel in a 1440p/1080p streamer frame.

    The panel has a very specific color signature: ORANGE text on a
    dark-navy translucent background. We mask for orange-ish pixels
    (R high, G moderate, B low) and find the densest rectangular
    region of such pixels in the right half of the frame.

    This rejects QR codes, white backgrounds, cyan icons, and most
    streamer overlays.
    """
    W, H = frame.size
    rgb = np.asarray(frame.convert("RGB"), dtype=np.int16)

    # The SCAN RESULTS panel always sits in the right portion of the HUD.
    # Scan the right 40% of the frame.
    right_x0 = int(W * 0.60)
    right = rgb[:, right_x0:]
    right_W = right.shape[1]

    # Orange text signature:
    #   R > 150  (bright red-orange)
    #   G 60-180 (warm yellow component, but not white)
    #   B < 120  (darker blue — rejects cyan and white)
    #   R - B > 80  (strongly biased toward warm)
    R, G, B = right[..., 0], right[..., 1], right[..., 2]
    text_mask = (
        (R > 150) & (R < 256)
        & (G > 60) & (G < 200)
        & (B < 140)
        & ((R - B) > 60)
    )

    # Find rows with ANY orange pixels (≥3)
    row_density = text_mask.sum(axis=1)
    dense_rows = row_density >= 3

    # Allow small gaps between rows (up to 3 rows) — numbers have
    # vertical whitespace between label and value lines
    MAX_GAP = 6

    # Find vertical bands of mostly-dense rows (with small gap tolerance)
    bands: list[tuple[int, int]] = []
    y = 0
    while y < H:
        if not dense_rows[y]:
            y += 1
            continue
        start = y
        last_hit = y
        while y < H and (y - last_hit) <= MAX_GAP:
            if dense_rows[y]:
                last_hit = y
            y += 1
        end = last_hit + 1
        if end - start >= 60:  # panel is at least ~60 px tall
            bands.append((start, end))
        while y < H and not dense_rows[y]:
            y += 1

    if not bands:
        return None

    # Prefer the band in the bottom half of the frame (SCAN RESULTS
    # usually sits below the cockpit chrome), but take any tall band
    # otherwise.
    bands.sort(key=lambda b: (b[1] - b[0]), reverse=True)

    for y1, y2 in bands:
        # Determine column bounds within this band
        band_mask = text_mask[y1:y2]
        col_density = band_mask.sum(axis=0)
        cols_hot = col_density >= (y2 - y1) * 0.05
        if not cols_hot.any():
            continue
        xs = np.where(cols_hot)[0]
        x1_rel, x2_rel = int(xs[0]), int(xs[-1]) + 1
        bw = x2_rel - x1_rel
        bh = y2 - y1
        # Panel aspect ratio is ~1:1 to ~2:1 (taller than wide) at native
        # scale; on 1440p streamer frames the panel is narrower.
        if bw < 80 or bw > 500:
            continue
        if bh < 80 or bh > 500:
            continue
        # Add margins
        x1 = max(0, right_x0 + x1_rel - 8)
        x2 = min(W, right_x0 + x2_rel + 8)
        y1 = max(0, y1 - 8)
        y2 = min(H, y2 + 8)
        return frame.crop((x1, y1, x2, y2))

    return None


def _find_scan_panel_full(frame: Image.Image) -> Optional[Image.Image]:
    """Locate the SCAN RESULTS panel including composition rows.

    Differs from ``_find_scan_panel`` in two ways:
      1. After finding the initial dense band (header + values +
         IMPOSSIBLE bar), it keeps extending the bottom edge downward
         while *any* orange row appears within MAX_GAP, capturing the
         composition section that often has visual gaps between rows.
      2. Requires a "header-shaped" thick dense row near the top of
         the band — rejects false positives from chat overlays, ship
         status text, and other HUD elements that share the orange
         color but lack the SCAN RESULTS title bar.

    Returns the panel crop (with margins) or None.
    """
    W, H = frame.size
    rgb = np.asarray(frame.convert("RGB"), dtype=np.int16)
    # SCAN RESULTS panel is anchored to the right edge of the HUD.
    right_x0 = int(W * 0.55)
    right = rgb[:, right_x0:]

    R, G, B = right[..., 0], right[..., 1], right[..., 2]
    # SC HUD orange: warm red-orange. Tighter hue band rejects pink
    # streamer alerts (high B), magenta sub banners (high B + low G),
    # and yellow callouts (high G).
    text_mask = (
        (R > 160) & (R < 256)
        & (G > 60) & (G < 170)
        & (B < 110)
        & ((R - B) > 80)        # strongly warm
        & ((R - G) > 30)        # not yellow
        & (G > B)               # G dominates B (rejects magenta/pink)
    )
    row_density = text_mask.sum(axis=1)
    dense_rows = row_density >= 3

    MAX_GAP = 6
    EXTEND_GAP = 14  # composition rows can have larger gaps

    # Find initial bands (header + values + IMPOSSIBLE region)
    bands: list[tuple[int, int]] = []
    y = 0
    while y < H:
        if not dense_rows[y]:
            y += 1
            continue
        start = y
        last_hit = y
        while y < H and (y - last_hit) <= MAX_GAP:
            if dense_rows[y]:
                last_hit = y
            y += 1
        end = last_hit + 1
        if end - start >= 60:
            bands.append((start, end))
        while y < H and not dense_rows[y]:
            y += 1

    if not bands:
        return None

    bands.sort(key=lambda b: (b[1] - b[0]), reverse=True)

    for y1, y2 in bands:
        # Extend bottom downward through the composition section
        ext_end = y2
        last_hit = y2 - 1
        y_probe = y2
        while y_probe < H and (y_probe - last_hit) <= EXTEND_GAP:
            if dense_rows[y_probe]:
                last_hit = y_probe
                ext_end = y_probe + 1
            y_probe += 1
        y2 = ext_end

        # Column bounds across the (now extended) full band — but
        # take only the RIGHTMOST contiguous cluster of hot columns,
        # so we don't merge in cockpit chrome that's left of the panel.
        band_mask = text_mask[y1:y2]
        col_density = band_mask.sum(axis=0)
        cols_hot = col_density >= max(3, (y2 - y1) * 0.03)
        if not cols_hot.any():
            continue
        # Cluster hot columns; allow gaps up to ~50 px so labels and
        # their values (e.g. "MASS:" and "8261") merge into one cluster.
        hot_idx = np.where(cols_hot)[0]
        clusters: list[tuple[int, int]] = []
        cs = ce = int(hot_idx[0])
        for idx in hot_idx[1:]:
            i = int(idx)
            if i - ce <= 50:
                ce = i
            else:
                clusters.append((cs, ce))
                cs = ce = i
        clusters.append((cs, ce))
        # Pick the widest cluster (the panel is the densest+widest
        # contiguous group of orange in the right half of the frame)
        clusters.sort(key=lambda c: c[1] - c[0], reverse=True)
        x1_rel, x2_rel = clusters[0]
        x2_rel += 1
        bw = x2_rel - x1_rel
        bh = y2 - y1
        if os.environ.get("HARVEST_DEBUG"):
            print(f"    band y={y1}-{y2} (h={bh}) widest cluster x={x1_rel}-{x2_rel} (w={bw}) clusters={len(clusters)}", file=sys.stderr)
        if bw < 60 or bw > 700:
            continue
        if bh < 100 or bh > 900:
            continue

        # Multi-row stack check — real SCAN RESULTS panel has at
        # least 3 vertically separated text rows (MASS / RESISTANCE /
        # INSTABILITY at minimum). Streamer alert banners are usually
        # 1-2 rows high. Count distinct row groups inside this cluster.
        cluster_mask = text_mask[y1:y2, x1_rel:x2_rel]
        row_dens = cluster_mask.sum(axis=1)
        row_thr = max(2, bw * 0.03)
        active = row_dens >= row_thr
        # Count contiguous active row groups separated by ≥3-px gaps
        groups = 0
        in_g = False
        gap = 0
        for v in active:
            if v:
                if not in_g:
                    in_g = True
                    groups += 1
                gap = 0
            else:
                gap += 1
                if gap > 3:
                    in_g = False
        if groups < 3:
            if os.environ.get("HARVEST_DEBUG"):
                print(f"      reject: only {groups} text rows", file=sys.stderr)
            continue

        # Header sanity check — look for WHITE/cream pixels in the
        # top portion of the band (the "SCAN RESULTS" title and its
        # underline are rendered in white, not orange). The orange
        # mask above caught the values+labels region; this check
        # confirms it's actually a SCAN RESULTS panel and not a
        # spurious orange band like ship-status text.
        head_x0 = right_x0 + max(0, x1_rel - 4)
        head_x1 = right_x0 + min(right.shape[1], x2_rel + 4)
        head_y0 = max(0, y1 - 4)
        head_y1 = min(H, y1 + max(40, (y2 - y1) // 4))
        head_rgb = rgb[head_y0:head_y1, head_x0:head_x1]
        if head_rgb.size == 0:
            continue
        Rh, Gh, Bh = head_rgb[..., 0], head_rgb[..., 1], head_rgb[..., 2]
        white_mask = (Rh > 180) & (Gh > 180) & (Bh > 180) & (np.abs(Rh - Gh) < 40)
        white_count = int(white_mask.sum())
        if white_count < 30:  # need at least some header text
            continue

        # Add margins. Top margin is generous so we include the white
        # "SCAN RESULTS" header text that sits ABOVE the orange band.
        x1 = max(0, right_x0 + x1_rel - 10)
        x2 = min(W, right_x0 + x2_rel + 10)
        y1m = max(0, y1 - 70)
        y2m = min(H, y2 + 10)
        return frame.crop((x1, y1m, x2, y2m))

    return None


def _harvest_panels(
    url: str, start: str, end: str, fps: float,
) -> int:
    """Panels-only harvest: save full SCAN RESULTS panels for later splitting.

    No Paddle, no OCR, no segmentation. Just locate the panel and
    dump the crop. Much faster than the legacy / CRNN paths.
    """
    PANEL_OUT_DIR.mkdir(exist_ok=True)
    seen = _seen_hashes_panel()
    video_id = url.rsplit("=", 1)[-1].rsplit("/", 1)[-1].split("&")[0]
    out_dir = PANEL_OUT_DIR / video_id
    out_dir.mkdir(exist_ok=True)

    if _is_live_url(url):
        print(f"[{video_id}] SKIP: currently live (try again after broadcast ends)")
        return 0

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        video_path = tmp / "clip.mp4"
        frames_dir = tmp / "frames"

        print(f"[{video_id} {start}-{end}] downloading...")
        if not _download_clip(url, start, end, video_path):
            return 0
        frames = _extract_frames(video_path, frames_dir, fps)
        print(f"[{video_id}] {len(frames)} frames")

        saved = 0
        skipped_no_panel = 0
        for fp in frames:
            try:
                img = Image.open(fp).convert("RGB")
            except Exception:
                continue
            h = hashlib.md5(open(fp, "rb").read()).hexdigest()
            if h in seen:
                continue
            seen.add(h)

            panel = _find_scan_panel_full(img)
            if panel is None:
                skipped_no_panel += 1
                _mark_seen_panel(h)
                continue

            out_path = out_dir / f"f_{fp.stem}.png"
            try:
                panel.save(out_path)
                saved += 1
            except OSError:
                pass
            _mark_seen_panel(h)

        print(
            f"[{video_id}] saved {saved} panels "
            f"(skipped {skipped_no_panel} no-panel, "
            f"{len(frames) - saved - skipped_no_panel} dup)"
        )
        return saved


def _seen_hashes_panel() -> set[str]:
    if not PANEL_PROGRESS_FILE.is_file():
        return set()
    try:
        return set(PANEL_PROGRESS_FILE.read_text(encoding="utf-8").split())
    except OSError:
        return set()


def _mark_seen_panel(h: str) -> None:
    try:
        PANEL_OUT_DIR.mkdir(exist_ok=True)
        with open(PANEL_PROGRESS_FILE, "a", encoding="utf-8") as f:
            f.write(h + "\n")
    except OSError:
        pass


def _try_ocr(panel: Image.Image) -> Optional[dict]:
    """Quick panel-visibility check (Paddle-based labeling happens later).

    We just confirm the panel has a detectable mineral/label layout
    by calling ``_find_label_rows``. If labels are found we return a
    stub ``{"panel_visible": True}``, and Paddle does the actual
    labeling inside ``_extract_value_crops``. This avoids routing
    through the buggy sc_ocr pipeline just to gate harvesting.
    """
    W, H = panel.size
    REF_H = 541
    scale = max(UPSCALE_FACTOR, REF_H / max(1, H))
    scaled = panel.resize(
        (int(W * scale), int(H * scale)), Image.LANCZOS,
    )
    hud._label_cache.clear()
    try:
        rows = _find_label_rows(scaled)
    except Exception as exc:
        print(f"    label detect error: {exc}", file=sys.stderr)
        return None
    if not rows:
        return None
    return {"panel_visible": True, "mass": None, "resistance": None, "instability": None}


def _extract_glyphs(panel: Image.Image, result: dict) -> list[tuple[str, np.ndarray]]:
    """Extract 28x28 labeled digit crops from an already-OCR'd panel.

    Uses the legacy segmentation on the upscaled panel and pairs each
    glyph with the corresponding character in the OCR result string.
    """
    W, H = panel.size
    scaled = panel.resize(
        (int(W * UPSCALE_FACTOR), int(H * UPSCALE_FACTOR)), Image.LANCZOS,
    )
    gray_img = np.array(scaled.convert("L"), dtype=np.uint8)

    hud._label_cache.clear()
    rows = _find_label_rows(scaled)
    if not rows:
        return []

    out: list[tuple[str, np.ndarray]] = []
    for field, expected_value in (
        ("mass", result.get("mass")),
        ("resistance", result.get("resistance")),
        ("instability", result.get("instability")),
    ):
        if expected_value is None:
            continue
        entry = rows.get(field)
        if entry is None:
            continue
        y1, y2, lbl_right = entry
        x_min = max(0, lbl_right + 6)
        value_crop = _find_value_crop(scaled, gray_img, y1, y2, x_min=x_min)
        if value_crop is None:
            continue

        # Format the expected value as the OCR would have seen it
        if field == "mass":
            expected_str = f"{int(expected_value)}"
        elif field == "resistance":
            expected_str = f"{int(expected_value)}"
        else:  # instability — may have decimals
            if expected_value == int(expected_value):
                expected_str = f"{int(expected_value)}"
            else:
                expected_str = f"{expected_value:.2f}".rstrip("0").rstrip(".")

        # Segment the value crop
        gray_crop = np.array(value_crop.convert("L"), dtype=np.uint8)
        if np.median(gray_crop) > 140:
            gray_crop = 255 - gray_crop
        thr = _otsu(gray_crop)
        binary = (gray_crop > thr).astype(np.uint8) * 255
        proj = (binary > 0).sum(axis=0)
        w = binary.shape[1]
        spans: list[tuple[int, int]] = []
        in_c = False
        start = 0
        for x in range(w + 1):
            v = proj[x] if x < w else 0
            if v > 0 and not in_c:
                in_c = True; start = x
            elif v == 0 and in_c:
                in_c = False
                if x - start >= 2:
                    spans.append((start, x))

        # Keep only digit chars from expected_str, must match span count
        digit_chars = [c for c in expected_str if c.isdigit()]
        if len(digit_chars) != len(spans):
            continue

        for (x1, x2), ch in zip(spans, digit_chars):
            ys = np.where(np.any(binary[:, x1:x2] > 0, axis=1))[0]
            if len(ys) < 2:
                continue
            ya, yb = int(ys[0]), int(ys[-1]) + 1
            glyph = gray_crop[ya:yb, x1:x2].astype(np.float32)
            pad = 2
            padded = np.full(
                (glyph.shape[0] + pad * 2, glyph.shape[1] + pad * 2),
                255.0, dtype=np.float32,
            )
            padded[pad:pad + glyph.shape[0], pad:pad + glyph.shape[1]] = glyph
            pil = Image.fromarray(padded.astype(np.uint8)).resize(
                (28, 28), Image.BILINEAR,
            )
            out.append((ch, np.asarray(pil, dtype=np.uint8)))

    return out


def _paddle_label_panel(scaled_panel: Image.Image) -> list[dict]:
    """Ask Paddle to OCR the WHOLE upscaled panel.

    Returns Paddle's raw region list: ``[{"text":..,"conf":..,"y_mid":..,"x_mid":..}, ...]``
    or an empty list on any failure. Paddle's detector expects
    enough context to find text regions, so we feed the whole panel
    rather than tiny value crops.
    """
    try:
        from ocr import paddle_client
    except Exception as exc:
        print(f"    paddle_client import failed: {exc}", file=sys.stderr)
        return []
    if not paddle_client.is_available():
        print("    paddle_client: not available (py313 embed missing)", file=sys.stderr)
        return []
    try:
        regions = paddle_client.recognize(scaled_panel)
    except Exception as exc:
        print(f"    paddle recognize failed: {exc}", file=sys.stderr)
        return []
    return regions or []


def _band_for_region(
    region: dict, label_rows: dict, y_tol: int = 12,
) -> Optional[str]:
    """Which mass/resistance/instability band does this Paddle region land in?"""
    y = int(region.get("y_mid", -1))
    if y < 0:
        return None
    for field, (y1, y2, _lx) in label_rows.items():
        # Allow small vertical slop — Paddle's y_mid is the center of
        # the bounding box, and value rows can be ±a few px off the
        # label row center.
        if (y1 - y_tol) <= y <= (y2 + y_tol):
            return field
    return None


def _extract_value_crops(
    panel: Image.Image, _result_ignored: dict,
) -> list[tuple[str, Image.Image]]:
    """Extract (label, value_crop) pairs for CRNN training.

    **Uses Paddle as the teacher labeler** — the legacy pipeline's
    "extremely accurate but resource-intensive" engine. We upscale
    the whole panel, ask Paddle to OCR it, then match each detected
    text region to a label row (mass/resistance/instability) by
    y-coordinate. For each match we save the tight pre-upscale value
    crop with Paddle's decoded text as the label.

    This beats the sc_ocr/Tesseract path because Paddle was
    pretrained on ~10M labeled text images and handles the SC font
    robustly without domain-specific training.
    """
    # Match sc_ocr's upscaling convention (reference height 541 px)
    # so ``_find_label_rows`` sees glyphs at the size it was tuned for.
    REF_H = 541
    W, H = panel.size
    scale = max(UPSCALE_FACTOR, REF_H / max(1, H))
    scaled = panel.resize(
        (int(W * scale), int(H * scale)), Image.LANCZOS,
    )
    gray_img = np.array(scaled.convert("L"), dtype=np.uint8)

    hud._label_cache.clear()
    rows = _find_label_rows(scaled)
    if not rows:
        if os.environ.get("HARVEST_DEBUG"):
            print(f"    no label rows (panel={W}x{H}, scaled={scaled.size})", file=sys.stderr)
        return []

    # Ask Paddle to OCR the whole upscaled panel.
    paddle_regions = _paddle_label_panel(scaled)
    if not paddle_regions:
        if os.environ.get("HARVEST_DEBUG"):
            print(f"    paddle returned no regions on {scaled.size} panel", file=sys.stderr)
        return []

    # For each Paddle region, identify which field it belongs to,
    # filter to numeric-looking text, and build (label, value_crop).
    out: list[tuple[str, Image.Image]] = []
    seen_fields: set[str] = set()
    for reg in paddle_regions:
        raw = str(reg.get("text", "")).strip()
        conf = float(reg.get("conf", 0.0))
        if conf < 0.85:
            continue
        # Keep only digit/./% chars — Paddle sometimes adds ' ' or trailing letters
        cleaned = "".join(c for c in raw if c in "0123456789.-% ()ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
        if not cleaned or not any(c.isdigit() for c in cleaned):
            continue
        # Collapse double dots
        import re
        cleaned = re.sub(r"\.+", ".", cleaned).strip(".")
        if not cleaned:
            continue

        field = _band_for_region(reg, rows)
        if field is None or field in seen_fields:
            continue

        y1, y2, lbl_right = rows[field]
        x_min = max(0, lbl_right + 6)
        crop = _find_value_crop(scaled, gray_img, y1, y2, x_min=x_min)
        if crop is None:
            continue

        out.append((cleaned, crop))
        seen_fields.add(field)

    if os.environ.get("HARVEST_DEBUG") and out:
        print(f"    paddle-labeled crops: {[p[0] for p in out]}", file=sys.stderr)
    return out


def _save_crnn_samples(
    pairs: list[tuple[str, Image.Image]],
    video_id: str,
    frame_name: str,
) -> int:
    """Save (label, crop) pairs to training_data_crnn/ and append to manifest."""
    if not pairs:
        return 0
    import json
    CRNN_OUT_DIR.mkdir(exist_ok=True)
    manifest: dict = {"files": []}
    if CRNN_MANIFEST.is_file():
        try:
            with open(CRNN_MANIFEST, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {"files": []}
    files = manifest.setdefault("files", [])

    # Only keep labels that look like mining values — digits, dots,
    # percent signs, and commas (as thousands separators). Everything
    # else (chat overlays, stream labels, viewer names, in-game rock
    # names, etc) is discarded at write time. Without this filter the
    # manifest fills with hundreds of unique non-numeric labels that
    # pollute training — see the label-quality audit where 32,068 of
    # 32,790 crops had non-numeric labels.
    import re
    _NUMERIC_LABEL_RE = re.compile(r"^[0-9][0-9.,]*%?$")

    n = 0
    for i, (label, crop) in enumerate(pairs):
        if not _NUMERIC_LABEL_RE.match(label or ""):
            continue
        safe = label.replace(".", "dot").replace("%", "pct").replace(",", "c")
        fname = f"yt_{video_id}_{frame_name}_{i}_{safe}.png"
        try:
            crop.save(CRNN_OUT_DIR / fname)
            files.append({
                "path": fname,
                "label": label,
                "source": f"youtube_{video_id}",
            })
            n += 1
        except OSError:
            pass

    if n:
        try:
            with open(CRNN_MANIFEST, "w", encoding="utf-8") as f:
                json.dump(manifest, f)
        except OSError:
            pass
    return n


def _parse_bbox(s: str) -> Optional[tuple[int, int, int, int]]:
    """Parse a 'x,y,w,h' string into a bbox tuple."""
    try:
        parts = [int(p.strip()) for p in s.split(",")]
        if len(parts) == 4:
            return tuple(parts)  # type: ignore
    except (ValueError, AttributeError):
        pass
    return None


_LABEL_TRIGGERS = {
    "mass": ("mass",),
    "resistance": ("resistance", "resist"),
    "instability": ("instability", "instab"),
}


def _harvest_frame_with_paddle(frame: Image.Image) -> list[tuple[str, str, Image.Image]]:
    """Use Paddle on the (downscaled, right-portion-cropped) frame.

    Paddle's detection network chokes on full 1080p+ frames (sidecar
    OOMs and crashes). We crop the right 60 % of the frame — where
    the SC mining HUD lives — and cap the longest side at 960 px.
    Label-trigger scanning on Paddle's output then pairs ``MASS:`` /
    ``RESISTANCE:`` / ``INSTABILITY:`` tokens with nearby numeric
    regions by y-alignment and rightward proximity.

    Returns list of (field, label, value_crop) tuples. Value crops
    are taken in the DOWNSCALED coordinate space (consistent with
    what Paddle saw) so label/crop alignment is preserved.
    """
    try:
        from ocr import paddle_client
    except Exception:
        return []
    if not paddle_client.is_available():
        return []

    # Right-portion crop + downscale
    W, H = frame.size
    rx = int(W * 0.55)
    right = frame.crop((rx, 0, W, H))
    max_side = 960
    rw, rh = right.size
    if max(rw, rh) > max_side:
        scale = max_side / max(rw, rh)
        right = right.resize(
            (max(1, int(rw * scale)), max(1, int(rh * scale))),
            Image.LANCZOS,
        )

    try:
        regions = paddle_client.recognize(right)
    except Exception as exc:
        if os.environ.get("HARVEST_DEBUG"):
            print(f"    paddle call failed: {exc}", file=sys.stderr)
        return []
    if not regions:
        return []
    frame = right  # everything below operates in this coordinate space

    # Separate label triggers from numeric-looking regions.
    triggers: dict[str, list[dict]] = {"mass": [], "resistance": [], "instability": []}
    numeric: list[dict] = []
    for r in regions:
        text = str(r.get("text", "")).strip().lower()
        if not text:
            continue
        matched_field = None
        for field, keys in _LABEL_TRIGGERS.items():
            if any(k in text for k in keys):
                matched_field = field
                break
        if matched_field:
            triggers[matched_field].append(r)
            continue
        # Numeric candidate: clean to digits/./%, non-empty, has a digit
        cleaned = "".join(c for c in str(r.get("text", "")) if c in "0123456789.-% ()ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
        if cleaned and any(c.isdigit() for c in cleaned):
            import re
            cleaned = re.sub(r"\.+", ".", cleaned).strip(".")
            if cleaned:
                r = dict(r)
                r["_clean"] = cleaned
                numeric.append(r)

    out: list[tuple[str, str, Image.Image]] = []
    for field, labels in triggers.items():
        if not labels:
            continue
        # Prefer the highest-confidence label for this field
        lbl = max(labels, key=lambda r: float(r.get("conf", 0)))
        ly = int(lbl.get("y_mid", -1))
        lx = int(lbl.get("x_mid", -1))
        if ly < 0 or lx < 0:
            continue
        # Find the best numeric region on the same y row, to the RIGHT of the label
        best = None
        best_score = 1e9
        for n in numeric:
            ny = int(n.get("y_mid", -1))
            nx = int(n.get("x_mid", -1))
            if ny < 0 or nx < 0:
                continue
            if abs(ny - ly) > 20:
                continue
            if nx <= lx:
                continue
            score = abs(ny - ly) + (nx - lx) * 0.1
            if score < best_score:
                best = n
                best_score = score
        if best is None:
            continue
        conf = float(best.get("conf", 0))
        if conf < 0.85:
            continue

        # Build the value crop from the Paddle region's bbox.
        # Paddle gives x_mid/y_mid; we don't have a full bbox, so
        # crop a region around the midpoint sized generously.
        # Ideally Paddle would give us a full polygon — fallback to a
        # fixed-size rectangle centered on the midpoint.
        cx = int(best["x_mid"])
        cy = int(best["y_mid"])
        # Heuristic size — text height ~24px in native frame, so give
        # a generous crop for the CRNN to learn from.
        crop_w = 140
        crop_h = 40
        left = max(0, cx - crop_w // 2)
        top = max(0, cy - crop_h // 2)
        right = min(frame.width, cx + crop_w // 2)
        bottom = min(frame.height, cy + crop_h // 2)
        value_crop = frame.crop((left, top, right, bottom))
        out.append((field, best["_clean"], value_crop))

    # ALSO harvest letter-containing regions (mineral names, HUD
    # labels like "IRON (ORE)", "MASS:", "EASY", etc.) — these
    # provide the letter supervision the CRNN needs for 99% accuracy
    # across the alphabet.
    _letter_allowed = "0123456789.-% ()ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    for r in regions:
        raw = str(r.get("text", "")).strip()
        conf = float(r.get("conf", 0.0))
        if conf < 0.90:
            continue
        if not raw or len(raw) > 24:
            continue
        # Require at least one letter (otherwise numeric branch covers it).
        if not any(c.isalpha() for c in raw):
            continue
        # Filter to alphabet, strip leading/trailing spaces.
        cleaned = "".join(c for c in raw if c in _letter_allowed).strip()
        if not cleaned or len(cleaned) < 2:
            continue
        cy = int(r.get("y_mid", -1))
        cx = int(r.get("x_mid", -1))
        if cy < 0 or cx < 0:
            continue
        # Crop wider for text labels (they can be longer than numeric values)
        crop_w = max(160, len(cleaned) * 18)
        crop_h = 40
        left = max(0, cx - crop_w // 2)
        top = max(0, cy - crop_h // 2)
        right_bound = min(frame.width, cx + crop_w // 2)
        bottom = min(frame.height, cy + crop_h // 2)
        letter_crop = frame.crop((left, top, right_bound, bottom))
        out.append(("letters", cleaned, letter_crop))
    return out


def harvest(url: str, start: str, end: str, fps: float,
            bbox: Optional[tuple[int, int, int, int]] = None,
            collect_crnn: bool = True,
            collect_glyphs: bool = True,
            paddle_full_frame: bool = True) -> int:
    if not _ensure_model():
        print("ERROR: legacy ONNX model failed to load", file=sys.stderr)
        return 0

    OUT_DIR.mkdir(exist_ok=True)
    if collect_crnn:
        CRNN_OUT_DIR.mkdir(exist_ok=True)
    seen = _seen_hashes()
    video_id = url.rsplit("=", 1)[-1].rsplit("/", 1)[-1].split("&")[0]

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        video_path = tmp / "clip.mp4"
        frames_dir = tmp / "frames"

        print(f"[{video_id} {start}-{end}] downloading...")
        if not _download_clip(url, start, end, video_path):
            return 0
        frames = _extract_frames(video_path, frames_dir, fps)
        print(f"[{video_id}] {len(frames)} frames")

        per_class: dict[str, int] = {str(d): 0 for d in range(10)}
        total_harvested = 0
        crnn_saved = 0
        ocr_success = 0

        for fp in frames:
            try:
                img = Image.open(fp).convert("RGB")
            except Exception:
                continue
            h = hashlib.md5(open(fp, "rb").read()).hexdigest()
            if h in seen:
                continue
            seen.add(h)

            # NEW PATH: feed whole frame to Paddle, let it find labels + values.
            # This bypasses the brittle panel auto-detector and handles any
            # SCAN RESULTS panel position / rendering in the video.
            if paddle_full_frame and bbox is None:
                triples = _harvest_frame_with_paddle(img)
                if triples:
                    ocr_success += 1
                    if collect_crnn:
                        pairs = [(label, crop) for _field, label, crop in triples]
                        crnn_saved += _save_crnn_samples(pairs, video_id, fp.stem)
                _mark_seen(h)
                continue

            # LEGACY PATH: panel auto-detect + _extract_value_crops.
            if bbox is not None:
                bx, by, bw, bh = bbox
                panel = img.crop((bx, by, bx + bw, by + bh))
            else:
                panel = _find_scan_panel(img)
                if panel is None:
                    _mark_seen(h)
                    continue

            result = _try_ocr(panel)
            if result is None:
                _mark_seen(h)
                continue
            ocr_success += 1

            # Whole-value crops for the CRNN (labeled by the teacher OCR)
            if collect_crnn:
                value_pairs = _extract_value_crops(panel, result)
                crnn_saved += _save_crnn_samples(value_pairs, video_id, fp.stem)

            # Individual glyph crops for the 28×28 classifier
            if collect_glyphs:
                glyphs = _extract_glyphs(panel, result)
                for ch, crop in glyphs:
                    if ch not in per_class:
                        continue
                    d = OUT_DIR / ch
                    d.mkdir(exist_ok=True)
                    name = f"yt_{video_id}_{fp.stem}_{per_class[ch]}.png"
                    try:
                        Image.fromarray(crop, mode="L").save(d / name)
                        per_class[ch] += 1
                        total_harvested += 1
                    except OSError:
                        pass
            _mark_seen(h)

        print(
            f"[{video_id}] OCR succeeded on {ocr_success}/{len(frames)} frames, "
            f"harvested {total_harvested} glyphs + {crnn_saved} CRNN value crops"
        )
        for ch in "0123456789":
            if per_class[ch]:
                print(f"  '{ch}': +{per_class[ch]}")
        return total_harvested + crnn_saved


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url")
    p.add_argument("--start", default="0:00:00")
    p.add_argument("--end", default="0:05:00")
    p.add_argument("--fps", type=float, default=0.5)
    p.add_argument("--bbox", help="Known panel bbox 'x,y,w,h' in native frame coords. "
                                   "Skips auto-detection.")
    p.add_argument("--batch", help="Path to TSV: url<TAB>start<TAB>end<TAB>fps<TAB>bbox")
    p.add_argument(
        "--mode", choices=["legacy", "panels"], default="legacy",
        help="legacy = original per-glyph + CRNN harvest. "
             "panels = save full SCAN RESULTS panel crops only "
             "(no OCR, much faster, post-process with splitter script).",
    )
    args = p.parse_args()

    if not _acquire_lock():
        sys.exit(1)

    try:
        total = 0
        if args.batch:
            import random
            import time
            entries: list[tuple[str, str, str, float, Optional[tuple]]] = []
            with open(args.batch) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) < 3:
                        continue
                    url = parts[0]
                    s = parts[1]
                    e = parts[2]
                    fps = float(parts[3]) if len(parts) > 3 else 0.5
                    bbox = _parse_bbox(parts[4]) if len(parts) > 4 else None
                    entries.append((url, s, e, fps, bbox))
            # Shuffle to randomize per-channel access pattern
            random.shuffle(entries)
            for i, (url, s, e, fps, bbox) in enumerate(entries):
                if args.mode == "panels":
                    total += _harvest_panels(url, s, e, fps)
                else:
                    total += harvest(url, s, e, fps, bbox=bbox)
                # Randomized inter-video delay so YouTube doesn't see
                # a steady robotic pattern. Skip on the last entry.
                if i < len(entries) - 1:
                    delay = random.uniform(45, 180)
                    print(f"  [rate-limit] sleeping {delay:.0f}s before next video", flush=True)
                    time.sleep(delay)
        elif args.url:
            if args.mode == "panels":
                total = _harvest_panels(args.url, args.start, args.end, args.fps)
            else:
                total = harvest(args.url, args.start, args.end, args.fps,
                                bbox=_parse_bbox(args.bbox) if args.bbox else None)
        else:
            p.error("provide --url or --batch")

        print(f"\nTotal harvested: {total}")
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
