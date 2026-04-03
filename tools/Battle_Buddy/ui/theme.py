"""
MobiGlas-inspired colour palette for Battle Buddy.
Single source of truth — import these constants everywhere.
"""

# Backgrounds
BG        = "#0d1117"   # main window
BG2       = "#141b24"   # panels / cards
BG3       = "#1c2533"   # hover state
BG4       = "#243040"   # selected / active
BG_INPUT  = "#0a0f16"   # text inputs

# Borders & separators
BORDER    = "#2a3a4a"
BORDER2   = "#1e2d3d"

# Text
FG        = "#c8d8e8"   # primary text
FG_DIM    = "#6a8aaa"   # secondary / hint text
FG_DIMMER = "#3a5570"   # very muted

# Accent (cyan — MobiGlas signature)
ACCENT    = "#00c8e8"
ACCENT2   = "#008aaa"   # darker accent for inactive tabs

# Ammo-type colours
COLOR_ENERGY     = "#00c8e8"   # cyan
COLOR_BALLISTIC  = "#e8a000"   # amber
COLOR_DISTORTION = "#a060e8"   # purple
COLOR_UNKNOWN    = "#6a8aaa"   # dim

# Consumable colours
COLOR_MEDPEN     = "#00e870"   # green  (med category)
COLOR_OXYPEN     = "#00aaff"   # blue   (oxy category)
COLOR_STIM       = "#e8c800"   # yellow (stim category — adrena/boost/cortico)
COLOR_DETOX      = "#c060e8"   # purple (detox category — detox/decon)
COLOR_PEN_OTHER  = "#6a8aaa"   # dim    (unknown pens)
COLOR_GRENADE    = "#e84000"   # red-orange

# Status colours
GREEN   = "#00e870"
YELLOW  = "#e8c800"
ORANGE  = "#e87000"
RED     = "#e83000"

# Header bar
HEADER_BG = "#0a1520"

# Typography
FONT_TITLE = "Electrolize, Consolas, monospace"
FONT_BODY  = "Consolas, monospace"


def ammo_color(ammo_type: str) -> str:
    return {
        "energy":     COLOR_ENERGY,
        "ballistic":  COLOR_BALLISTIC,
        "distortion": COLOR_DISTORTION,
    }.get(ammo_type, COLOR_UNKNOWN)
