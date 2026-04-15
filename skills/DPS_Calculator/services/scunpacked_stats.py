"""Stat extraction functions for scunpacked-data item JSON files.

Each function receives the parsed JSON dict for a single item file
(the full document with ``Raw`` and ``Item`` top-level keys) and
returns a normalized stats dict suitable for storing in the repository.

The returned dicts always include at minimum:
    name, local_name, ref, type, sub_type, size, grade, manufacturer

scunpacked JSON layout (relevant paths)::

    {
      "Raw": {
        "Entity": {
          "Components": {
            "SAttachableComponentParams": {
              "AttachDef": { "Type", "SubType", "Size", "Grade", "Manufacturer" }
            },
            "SCItemThrusterParams": {
              "thrustCapacity", "fuelBurnRatePer10KNewton", "thrusterType", ...
            },
            "ItemResourceComponentParams": {
              "states": { "ItemResourceState": { "signatureParams": {
                "EMSignature": { "nominalSignature" },
                "IRSignature": { "nominalSignature" }
              }}}
            },
            "SHealthComponentParams": { "Health" },
            "SCItemWeaponComponentParams": {
              "weaponAIData": { "idealCombatRange", "maxFiringRange" },
              "connectionParams": { ... power state stats ... }
            },
            "SAmmoContainerComponentParams": {
              "initialAmmoCount", "maxAmmoCount", "maxRestockCount"
            },
          }
        }
      },
      "Item": {
        "className", "reference", "itemName", "type", "subType",
        "size", "grade", "name", "manufacturer",
        "stdItem": {
          "Name", "UUID", "Size", "Grade",
          "Manufacturer": { "Code", "Name" },
          "Thruster": {
            "ThrustCapacity", "BurnRatePerMN", "ThrusterType", ...
          },
          "ResourceNetwork": { ... },
          "Emission": { "Temperature", "IR", "EM" },
        }
      }
    }
"""

from __future__ import annotations

import re


# ── Helpers ───────────────────────────────────────────────────────────────────

def _item(doc: dict) -> dict:
    return doc.get("Item") or {}

def _std(doc: dict) -> dict:
    return _item(doc).get("stdItem") or {}

def _comps(doc: dict) -> dict:
    return (doc.get("Raw") or {}).get("Entity", {}).get("Components") or {}

def _attach_def(doc: dict) -> dict:
    return _comps(doc).get("SAttachableComponentParams", {}).get("AttachDef") or {}

def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _name_from_doc(doc: dict) -> str:
    """Best available display name for an item.

    Priority: stdItem.Name (if not a placeholder) → Item.name → className
    formatted from snake_case.
    """
    item = _item(doc)
    std  = _std(doc)

    candidate = std.get("Name") or item.get("name") or ""
    if candidate and "<=" not in candidate:
        return candidate

    # Fall back: format className (AEGS_Avenger_Thruster_Main → Aegs Avenger Thruster Main)
    cls = item.get("className") or item.get("itemName") or ""
    if cls:
        return re.sub(r"_+", " ", cls).title()

    return "Unknown"


def _local_name_from_doc(doc: dict) -> str:
    item = _item(doc)
    return (item.get("itemName") or item.get("className") or "").lower()


def _ref_from_doc(doc: dict) -> str:
    item = _item(doc)
    std  = _std(doc)
    return std.get("UUID") or item.get("reference") or ""


def _size_from_doc(doc: dict) -> int:
    item = _item(doc)
    std  = _std(doc)
    raw  = std.get("Size") or item.get("size") or 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _grade_from_doc(doc: dict) -> int:
    item = _item(doc)
    raw  = item.get("grade") or 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _manufacturer_from_doc(doc: dict) -> str:
    item = _item(doc)
    std  = _std(doc)
    mfr  = std.get("Manufacturer") or {}
    if isinstance(mfr, dict):
        return mfr.get("Name") or mfr.get("Code") or item.get("manufacturer") or ""
    return item.get("manufacturer") or ""


def _sig_from_resource(doc: dict) -> tuple[float, float]:
    """Return (em_nominal, ir_nominal) from ItemResourceComponentParams."""
    res_comp = _comps(doc).get("ItemResourceComponentParams") or {}
    states   = res_comp.get("states") or {}
    state    = states.get("ItemResourceState") or {}
    sig      = state.get("signatureParams") or {}
    em       = sig.get("EMSignature") or {}
    ir_d     = sig.get("IRSignature") or {}
    return (_safe_float(em.get("nominalSignature")),
            _safe_float(ir_d.get("nominalSignature")))


def _hp_from_doc(doc: dict) -> float:
    hp_comp = _comps(doc).get("SHealthComponentParams") or {}
    return _safe_float(hp_comp.get("Health"))


# ── Thruster ──────────────────────────────────────────────────────────────────

def parse_thruster_stats(doc: dict) -> dict | None:
    """Extract stats from a thruster item document.

    Returns None if the document does not look like a thruster.
    """
    item     = _item(doc)
    item_type = item.get("type") or ""
    if "thruster" not in item_type.lower():
        return None

    std      = _std(doc)
    thr_std  = std.get("Thruster") or {}          # CamelCase path in stdItem
    thr_raw  = _comps(doc).get("SCItemThrusterParams") or {}  # lowercase path

    # Thrust capacity: prefer stdItem (already computed), fall back to raw component
    thrust   = (_safe_float(thr_std.get("ThrustCapacity"))
                or _safe_float(thr_raw.get("thrustCapacity")))

    # Burn rate per MegaNewton (standardized unit from stdItem)
    burn_mn  = (_safe_float(thr_std.get("BurnRatePerMN"))
                or _safe_float(thr_raw.get("fuelBurnRatePer10KNewton")) * 100)

    thruster_type = (thr_std.get("ThrusterType")
                     or thr_raw.get("thrusterType")
                     or item_type)

    em_sig, ir_sig = _sig_from_resource(doc)

    return {
        "name":          _name_from_doc(doc),
        "local_name":    _local_name_from_doc(doc),
        "ref":           _ref_from_doc(doc),
        "type":          item_type,            # MainThruster / ManneuverThruster
        "sub_type":      item.get("subType") or "",
        "thruster_type": thruster_type,        # "Main" / "Maneuver" / "Retro"
        "size":          _size_from_doc(doc),
        "grade":         _grade_from_doc(doc),
        "manufacturer":  _manufacturer_from_doc(doc),
        "thrust":        thrust,               # Newtons
        "burn_rate_mn":  burn_mn,              # fuel per MN thrust
        "hp":            _hp_from_doc(doc),
        "em_sig":        em_sig,
        "ir_sig":        ir_sig,
        # Required-tags lock thrusters to a specific ship
        "required_tags": item.get("required_tags") or "",
    }


# ── Countermeasure Launcher ───────────────────────────────────────────────────

def parse_cml_stats(doc: dict) -> dict | None:
    """Extract stats from a countermeasure launcher item document.

    Returns None if the document does not look like a CML.
    """
    item      = _item(doc)
    item_type = item.get("type") or ""
    sub_type  = item.get("subType") or ""

    if "CountermeasureLauncher" not in sub_type and "WeaponDefensive" not in item_type:
        return None

    comps    = _comps(doc)
    weapon   = comps.get("SCItemWeaponComponentParams") or {}
    ai_data  = weapon.get("weaponAIData") or {}
    ammo_c   = comps.get("SAmmoContainerComponentParams") or {}

    # Fire rate: for CMLs the stat lives under normalStats or connectionParams
    # In practice CMLs don't expose a meaningful fire rate in the XML —
    # they fire once per input event.  Default to 1 shot / press.
    cp             = weapon.get("connectionParams") or {}
    normal_stats   = cp.get("normalStats") or {}
    fire_rate      = _safe_float(normal_stats.get("fireRate"), default=0.0)

    em_sig, ir_sig = _sig_from_resource(doc)

    return {
        "name":           _name_from_doc(doc),
        "local_name":     _local_name_from_doc(doc),
        "ref":            _ref_from_doc(doc),
        "type":           item_type,
        "sub_type":       sub_type,
        "size":           _size_from_doc(doc),
        "grade":          _grade_from_doc(doc),
        "manufacturer":   _manufacturer_from_doc(doc),
        "ammo_count":     int(ammo_c.get("initialAmmoCount") or 0),
        "ammo_max":       int(ammo_c.get("maxAmmoCount") or 0),
        "restock_count":  int(ammo_c.get("maxRestockCount") or 0),
        "fire_rate":      fire_rate,
        "range":          _safe_float(ai_data.get("maxFiringRange")),
        "hp":             _hp_from_doc(doc),
        "em_sig":         em_sig,
        "ir_sig":         ir_sig,
    }


# ── Module ────────────────────────────────────────────────────────────────────

def parse_module_stats(doc: dict) -> dict | None:
    """Extract stats from a ship module item document.

    Handles player-swappable ship modules (Retaliator cargo/torpedo/living
    modules, etc.).  Excludes NPC AIModule entries.

    Returns None if the document is not a Module type.
    """
    item      = _item(doc)
    item_type = item.get("type") or ""

    # Only player-swappable modules; skip AIModule, Room, etc.
    if item_type != "Module":
        return None

    std         = _std(doc)
    description = std.get("DescriptionText") or std.get("Description") or ""

    # Collect ports provided by this module (from stdItem.Ports)
    raw_ports = std.get("Ports") or []
    ports = []
    for p in raw_ports:
        port_name = p.get("PortName") or p.get("Name") or ""
        ports.append({
            "name":      port_name,
            "min_size":  int(p.get("MinSize") or p.get("Size") or 1),
            "max_size":  int(p.get("MaxSize") or p.get("Size") or 1),
            "editable":  not bool(p.get("Uneditable")),
        })

    return {
        "name":          _name_from_doc(doc),
        "local_name":    _local_name_from_doc(doc),
        "ref":           _ref_from_doc(doc),
        "type":          item_type,
        "sub_type":      item.get("subType") or "",
        "size":          _size_from_doc(doc),
        "grade":         _grade_from_doc(doc),
        "manufacturer":  _manufacturer_from_doc(doc),
        "description":   description,
        "ports":         ports,          # slots provided by this module
        "port_count":    len(ports),
        "hp":            _hp_from_doc(doc),
        "required_tags": item.get("required_tags") or "",
    }


# ── Dispatch ──────────────────────────────────────────────────────────────────

# Filename-pattern → parse function.  Ordered: more-specific patterns first.
# "_module" (with leading underscore) matches ship modules but NOT "aimodule_*".
_PARSERS: list[tuple[str, callable]] = [
    ("_cml_",    parse_cml_stats),
    ("thruster", parse_thruster_stats),
    ("_module",  parse_module_stats),
]

# Filename patterns to fetch from scunpacked
FETCH_PATTERNS: list[str] = [p for p, _ in _PARSERS]


def parse_item(doc: dict, filename: str = "") -> dict | None:
    """Auto-dispatch to the correct parser based on filename patterns.

    Returns a stats dict or None if no parser matches / parse fails.
    """
    fn_lower = filename.lower()
    for pattern, fn in _PARSERS:
        if pattern in fn_lower:
            return fn(doc)
    # Fallback: try each parser in order
    for _, fn in _PARSERS:
        result = fn(doc)
        if result is not None:
            return result
    return None
