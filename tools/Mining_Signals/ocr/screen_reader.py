"""Screen capture and OCR for mining scanner digit extraction.

Uses ``mss`` for fast in-memory screen grabs and ``pytesseract`` for
digit-only OCR.  Tesseract binary is auto-downloaded on first use if
not already present.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from typing import Optional

log = logging.getLogger(__name__)

# ── Tesseract binary management ──
# Bundled/downloaded Tesseract lives here:
_TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESSERACT_DIR = os.path.join(_TOOL_DIR, "tesseract")
_TESSERACT_EXE = os.path.join(_TESSERACT_DIR, "tesseract.exe")

# UB-Mannheim portable build (Tesseract 5.5.0, ~33 MB zip)
_TESSERACT_URL = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/"
    "v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
)

# Lazy-loaded flags — set on first use
_MSS_AVAILABLE: Optional[bool] = None
_TESSERACT_AVAILABLE: Optional[bool] = None


def _check_mss() -> bool:
    global _MSS_AVAILABLE
    if _MSS_AVAILABLE is None:
        try:
            import mss  # noqa: F401
            _MSS_AVAILABLE = True
        except ImportError:
            _MSS_AVAILABLE = False
            log.warning("screen_reader: 'mss' not installed — screen capture disabled")
    return _MSS_AVAILABLE


def _find_tesseract() -> str | None:
    """Locate the Tesseract binary — bundled copy first, then system PATH."""
    # 1. Bundled copy (auto-downloaded or shipped with installer)
    if os.path.isfile(_TESSERACT_EXE):
        return _TESSERACT_EXE

    # 2. System PATH
    system_exe = shutil.which("tesseract")
    if system_exe:
        return system_exe

    # 3. Common Windows install locations
    for prog in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", "")):
        if prog:
            candidate = os.path.join(prog, "Tesseract-OCR", "tesseract.exe")
            if os.path.isfile(candidate):
                return candidate

    return None


def _download_tesseract() -> bool:
    """Download and extract Tesseract OCR binary to the tool directory.

    Downloads the UB-Mannheim NSIS installer and extracts it silently
    to a local directory.  No admin rights required.
    """
    log.info("screen_reader: downloading Tesseract OCR (~33 MB)...")

    os.makedirs(_TESSERACT_DIR, exist_ok=True)
    installer_path = os.path.join(_TESSERACT_DIR, "tesseract_setup.exe")

    try:
        # Download installer
        req = urllib.request.Request(
            _TESSERACT_URL,
            headers={"User-Agent": "SC-Toolbox/1.0"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(installer_path, "wb") as f:
                shutil.copyfileobj(resp, f)

        log.info("screen_reader: download complete, extracting...")

        # Run NSIS installer in silent mode to local directory.
        # The installer requires elevation — use PowerShell Start-Process
        # with -Verb RunAs to trigger the UAC prompt.
        tess_dir = _TESSERACT_DIR.replace("'", "''")
        installer = installer_path.replace("'", "''")
        result = subprocess.run(
            [
                "powershell", "-Command",
                f"Start-Process -FilePath '{installer}' "
                f"-ArgumentList '/S','/D={tess_dir}' "
                f"-Verb RunAs -Wait",
            ],
            timeout=120,
            capture_output=True,
        )

        # Clean up installer
        try:
            os.remove(installer_path)
        except OSError:
            pass

        if os.path.isfile(_TESSERACT_EXE):
            log.info("screen_reader: Tesseract installed to %s", _TESSERACT_DIR)
            return True
        else:
            log.error("screen_reader: Tesseract extraction failed (exe not found)")
            return False

    except Exception as exc:
        log.error("screen_reader: Tesseract download failed: %s", exc)
        # Clean up partial download
        try:
            os.remove(installer_path)
        except OSError:
            pass
        return False


def _check_tesseract() -> bool:
    global _TESSERACT_AVAILABLE
    if _TESSERACT_AVAILABLE is None:
        try:
            import pytesseract

            # Point pytesseract to local binary if available
            exe = _find_tesseract()
            if not exe:
                # Auto-download on first use
                log.info("screen_reader: Tesseract not found, attempting auto-download...")
                if _download_tesseract():
                    exe = _TESSERACT_EXE
                else:
                    _TESSERACT_AVAILABLE = False
                    return False

            pytesseract.pytesseract.tesseract_cmd = exe
            pytesseract.get_tesseract_version()
            _TESSERACT_AVAILABLE = True
            log.info("screen_reader: using Tesseract at %s", exe)

        except ImportError:
            _TESSERACT_AVAILABLE = False
            log.warning("screen_reader: 'pytesseract' not installed")
        except Exception as exc:
            _TESSERACT_AVAILABLE = False
            log.warning("screen_reader: Tesseract check failed: %s", exc)
    return _TESSERACT_AVAILABLE


def is_ocr_available() -> bool:
    """Return True if both mss and pytesseract/Tesseract are usable."""
    return _check_mss() and _check_tesseract()


def tesseract_status() -> str:
    """Return a human-readable status string for the OCR subsystem."""
    if not _check_mss():
        return "mss not installed (pip install mss)"
    if not _check_tesseract():
        return "Tesseract not available — will auto-download on first scan"
    return "Ready"


def capture_region(region: dict) -> Optional[object]:
    """Capture a screen region and return a PIL Image (or None).

    *region* must have keys: x, y, w, h (integers, pixels).
    The image lives entirely in memory — nothing is saved to disk.
    """
    if not _check_mss():
        return None

    import mss
    from PIL import Image

    monitor = {
        "left": region["x"],
        "top": region["y"],
        "width": region["w"],
        "height": region["h"],
    }

    try:
        with mss.mss() as sct:
            grab = sct.grab(monitor)
            img = Image.frombytes("RGB", grab.size, grab.bgra, "raw", "BGRX")

            # Remove the pin/marker icon on the left side.
            # Scan columns from the left: the icon is a solid colored blob.
            # Find the first gap (dark column) after the icon, then keep
            # everything to the right of it — that's where the digits start.
            import numpy as np
            arr = np.array(img.convert("L"))
            col_brightness = arr.mean(axis=0)  # average brightness per column
            # Find first dark column after any bright region (the icon)
            in_icon = False
            crop_x = 0
            for x, brightness in enumerate(col_brightness):
                if brightness > 40:
                    in_icon = True
                elif in_icon and brightness < 20:
                    # Passed through the icon, now in the gap before digits
                    crop_x = x
                    break
            if crop_x > 0:
                img = img.crop((crop_x, 0, img.width, img.height))

            return img
    except Exception as exc:
        log.error("screen_reader: capture failed: %s", exc)
        return None


def _try_ocr(image, config: str) -> str:
    """Run pytesseract with a given config and return the raw text."""
    import pytesseract
    return pytesseract.image_to_string(image, config=config).strip()


def _preprocess_variants(image) -> list:
    """Generate multiple preprocessed versions of the image for OCR.

    The SC mining scanner shows text in various colors (white, green,
    orange, red) on a dark game background.  We extract individual
    color channels and apply thresholding at multiple levels to handle
    all HUD text colors.
    """
    from PIL import Image, ImageEnhance, ImageOps

    variants = []
    scale = 4  # 4x upscale for better OCR accuracy

    # Extract color channels — scanner text can be white, green,
    # orange, or red depending on rarity/context
    r_ch, g_ch, b_ch = image.split()
    gray = image.convert("L")

    # Best performers from testing:
    # Green channel thresh 100-120 (works for orange/red/green text)
    # Blue channel thresh 100-120 (backup)
    # Gray inverted thresh 80 (works for white/bright text)
    # Gray thresh 140 (bright pixel isolation)

    for ch in (g_ch, b_ch, r_ch):
        for thresh in (100, 120):
            t = ch.point(lambda p, th=thresh: 255 if p > th else 0)
            t = t.resize((t.width * scale, t.height * scale), Image.LANCZOS)
            variants.append(t)

    # Inverted grayscale (white text on dark bg)
    inv = ImageOps.invert(gray)
    t_inv = inv.point(lambda p: 255 if p > 80 else 0)
    t_inv = t_inv.resize((t_inv.width * scale, t_inv.height * scale), Image.LANCZOS)
    variants.append(t_inv)

    # Bright pixel isolation
    t_bright = gray.point(lambda p: 255 if p > 140 else 0)
    t_bright = t_bright.resize((t_bright.width * scale, t_bright.height * scale), Image.LANCZOS)
    variants.append(t_bright)

    return variants


def extract_number(image) -> Optional[int]:
    """Run digit-only OCR on *image* and return the extracted integer.

    Tries multiple preprocessing variants and OCR configs to maximize
    detection of gaming HUD numbers.  Returns None if no digits found.
    """
    if not _check_tesseract():
        return None

    try:
        variants = _preprocess_variants(image)

        configs = [
            "--psm 6 -c tessedit_char_whitelist=0123456789",   # block of text, digits
            "--psm 7 -c tessedit_char_whitelist=0123456789",   # single line, digits
            "--psm 8 -c tessedit_char_whitelist=0123456789",   # single word, digits
        ]

        for i, img_variant in enumerate(variants):
            for config in configs:
                raw = _try_ocr(img_variant, config)
                if raw:
                    digits = re.findall(r"\d+", raw)
                    if digits:
                        best = max(digits, key=len)
                        if len(best) >= 3:  # signal values are 4-5 digits
                            result = int(best)
                            log.debug(
                                "screen_reader: extracted %d (variant=%d, config=%r, raw=%r)",
                                result, i, config, raw,
                            )
                            return result

        log.debug("screen_reader: no digits found across all variants")
        return None
    except Exception as exc:
        log.error("screen_reader: OCR failed: %s", exc)
        return None


def scan_region(region: dict) -> Optional[int]:
    """One-shot: capture the region and extract the number.

    Returns the extracted integer or None.  Entirely in-memory.
    """
    img = capture_region(region)
    if img is None:
        return None
    return extract_number(img)
