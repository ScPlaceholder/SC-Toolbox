"""
Audit: DPS Calculator slot_extractor vs Erkul raw weapon port counts.

Compares, for every ship in the Erkul cache, the number of weapon slots
returned by extract_slots_by_type (the function app.py actually uses) against
an independent walk of the raw Erkul loadout tree that counts "weapon ports"
according to Erkul's own type metadata.

Run from the DPS_Calculator directory:
    python audit_slot_extractor.py
"""

import json
import os
import sys
import re

# ── Ensure DPS Calculator modules are importable ────────────────────────────
DPS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DPS_DIR)

from services.slot_extractor import extract_slots_by_type


# ── Load Erkul cache ─────────────────────────────────────────────────────────
CACHE_PATH = os.path.join(DPS_DIR, ".erkul_cache.json")
with open(CACHE_PATH, encoding="utf-8") as f:
    cache = json.load(f)

ships_raw = cache["data"]["/live/ships"]

# ── Helper: DPS Calculator weapon slot count ─────────────────────────────────
# app.py line 918: extract_slots_by_type(loadout, {"WeaponGun", "Turret"})
DPS_ACCEPT = {"WeaponGun", "Turret"}

def dps_slots(loadout):
    """Return slot list the DPS Calculator would compute."""
    return extract_slots_by_type(loadout, DPS_ACCEPT)


# ── Helper: Erkul "truth" raw weapon port counter ────────────────────────────
# Walk the loadout tree and count ports that Erkul explicitly marks as weapon
# ports via itemTypes, subject to the criteria in the task spec.

_TURRET_HOUSING_SUBTYPES = {
    "TopTurret", "MannedTurret", "BallTurret", "NoseTurret",
    "RemoteTurret", "UpperTurret", "LowerTurret",
}
_WEAPON_GUN_SUBTYPES = {"Gun", "GunTurret"}

# Port names that are definitely not weapon slots (mirrors slot_extractor skip list)
_SKIP_PATTERNS = (
    "camera", "tractor", "self_destruct", "landing",
    "fuel_port", "fuel_intake", "docking", "air_traffic", "relay",
    "salvage", "mining", "scan", "torpedo_storage", "vehicle_screen",
    "missile", "missilerack", "bomb_", "bombrack",
    "paint",
)


def erkul_weapon_ports(loadout):
    """
    Independently walk the Erkul loadout and collect weapon ports.

    Inclusion criteria:
      - itemTypes contains WeaponGun  → direct gun port
      - itemTypes contains Turret + subType Gun or GunTurret → gun turret port
      - maxSize >= 1
      - editable=True OR has a non-empty localReference/localName
      - Pure turret housings (MannedTurret, RemoteTurret, etc.) are NOT counted
        as slots themselves — they are recursed into so their child gun ports
        are found (mirrors what the DPS extractor does).

    Returns list of dicts with keys: pname, type_info, max_size, editable,
    local_ref, path.
    """
    results = []

    def _walk(ports, path=""):
        for port in (ports or []):
            pname = port.get("itemPortName", "")
            pname_lower = pname.lower()

            # Skip obviously non-weapon ports
            if any(pat in pname_lower for pat in _SKIP_PATTERNS):
                _walk(port.get("loadout", []), f"{path}/{pname}")
                continue

            types = port.get("itemTypes", [])
            max_sz = port.get("maxSize") or 0
            editable = port.get("editable", False)
            local_ref = port.get("localReference", "") or port.get("localName", "")
            children = port.get("loadout", [])

            type_names = {t.get("type", "") for t in types}
            sub_names  = {t.get("subType", "") for t in types}

            is_weapon_gun  = "WeaponGun" in type_names
            is_gun_turret  = "Turret" in type_names and bool(sub_names & _WEAPON_GUN_SUBTYPES)
            is_housing     = ("Turret" in type_names or "TurretBase" in type_names) and bool(
                sub_names & (_TURRET_HOUSING_SUBTYPES - _WEAPON_GUN_SUBTYPES)
            )
            # Pure missile turrets: skip from gun counts
            is_missile_turret = (
                "Turret" in type_names
                and "MissileTurret" in sub_names
                and "GunTurret" not in sub_names
            )

            port_path = f"{path}/{pname}"

            if (is_weapon_gun or is_gun_turret) and not is_missile_turret:
                if max_sz >= 1 and (editable or local_ref):
                    results.append({
                        "pname":     pname,
                        "type_info": f"types={sorted(type_names)} subs={sorted(sub_names)}",
                        "max_size":  max_sz,
                        "editable":  editable,
                        "local_ref": local_ref,
                        "path":      port_path,
                    })
                # Even if this port itself qualified, it may also have children
                # (compound turrets) — recurse so we don't miss sub-slots.
                if children:
                    _walk(children, port_path)
            elif is_housing and not is_missile_turret:
                # Recurse into housing to find child gun ports
                _walk(children, port_path)
            else:
                # Not a weapon port type-wise — still recurse
                _walk(children, port_path)

    _walk(loadout)
    return results


# ── Main audit loop ───────────────────────────────────────────────────────────

class Diff:
    __slots__ = ("name", "local_name", "dps_count", "erkul_count",
                 "dps_slots", "erkul_ports")

results = []

for ship_entry in ships_raw:
    if ship_entry.get("calculatorType") != "ship":
        continue

    data = ship_entry.get("data")
    if not isinstance(data, dict):
        continue

    loadout = data.get("loadout")
    if not loadout:
        continue

    local_name = ship_entry.get("localName", "")
    ship_name  = data.get("name") or data.get("shortName") or local_name

    # DPS Calculator count
    dps = dps_slots(loadout)
    dps_count = len(dps)

    # Erkul raw count
    erkul = erkul_weapon_ports(loadout)
    erkul_count = len(erkul)

    if dps_count != erkul_count:
        results.append({
            "name":        ship_name,
            "local_name":  local_name,
            "dps_count":   dps_count,
            "erkul_count": erkul_count,
            "dps_slots":   dps,
            "erkul_ports": erkul,
        })

# Sort: biggest DPS-vs-Erkul over-count first
results.sort(key=lambda r: r["dps_count"] - r["erkul_count"], reverse=True)

# ── Report ────────────────────────────────────────────────────────────────────

BOLD  = "\033[1m"
RED   = "\033[31m"
GREEN = "\033[32m"
YEL   = "\033[33m"
RESET = "\033[0m"

def _color(diff):
    if diff > 0:  return RED    # DPS shows MORE than Erkul
    if diff < 0:  return GREEN  # DPS shows FEWER than Erkul
    return ""

print(f"\n{'='*80}")
print(f"  WEAPON SLOT AUDIT: DPS Calculator vs Erkul Raw Loadout")
print(f"  Ships checked: {sum(1 for s in ships_raw if s.get('calculatorType')=='ship' and isinstance(s.get('data'),dict) and s['data'].get('loadout'))}")
print(f"  Ships with DIFFERENCES: {len(results)}")
print(f"{'='*80}\n")

if not results:
    print("  No discrepancies found — DPS Calculator and Erkul raw counts match on all ships.")
else:
    for r in results:
        diff = r["dps_count"] - r["erkul_count"]
        col  = _color(diff)
        sign = "+" if diff > 0 else ""
        print(f"{BOLD}{r['name']}{RESET}  ({r['local_name']})")
        print(f"  DPS Calculator : {r['dps_count']} slot(s)")
        print(f"  Erkul raw      : {r['erkul_count']} port(s)")
        print(f"  Delta          : {col}{sign}{diff}{RESET}")

        # Show which DPS slots have no matching Erkul counterpart (by port name)
        erkul_pnames = {p["pname"] for p in r["erkul_ports"]}
        dps_extra = [s for s in r["dps_slots"]
                     if s.get("id","").split(":")[-1] not in erkul_pnames
                     and not any(p["pname"] in s.get("id","") for p in r["erkul_ports"])]

        erkul_pnames_in_dps_ids = set()
        for s in r["dps_slots"]:
            slot_id = s.get("id", "")
            for ep in r["erkul_ports"]:
                if ep["pname"] in slot_id:
                    erkul_pnames_in_dps_ids.add(ep["pname"])

        erkul_missing = [p for p in r["erkul_ports"]
                         if p["pname"] not in erkul_pnames_in_dps_ids]

        if diff > 0:
            print(f"  {RED}-- DPS EXTRA slots (not matched in Erkul raw):{RESET}")
            for s in r["dps_slots"]:
                slot_id    = s.get("id", "")
                slot_label = s.get("label", "")
                slot_sz    = s.get("max_size", "?")
                slot_ref   = s.get("local_ref", "")
                matched = any(p["pname"] in slot_id for p in r["erkul_ports"])
                marker = "  " if matched else f"{RED}* {RESET}"
                print(f"    {marker}id={slot_id!r:60s}  label={slot_label!r}  sz={slot_sz}  ref={slot_ref!r}")
            print(f"  {GREEN}-- Erkul raw ports found:{RESET}")
            for p in r["erkul_ports"]:
                print(f"    path={p['path']!r}")
                print(f"      {p['type_info']}  sz={p['max_size']}  editable={p['editable']}  ref={p['local_ref']!r}")
        elif diff < 0:
            print(f"  {GREEN}-- Erkul raw ports NOT captured by DPS extractor:{RESET}")
            for p in erkul_missing:
                print(f"    path={p['path']!r}")
                print(f"      {p['type_info']}  sz={p['max_size']}  editable={p['editable']}  ref={p['local_ref']!r}")
            print(f"  {YEL}-- DPS slots found:{RESET}")
            for s in r["dps_slots"]:
                print(f"    id={s.get('id','')!r}  label={s.get('label','')!r}  sz={s.get('max_size','?')}")

        print()

# ── Summary table ─────────────────────────────────────────────────────────────
print(f"\n{'='*80}")
print(f"  SUMMARY TABLE  (only ships where DPS > Erkul raw)")
print(f"{'='*80}")
over_count = [r for r in results if r["dps_count"] > r["erkul_count"]]
if over_count:
    print(f"  {'Ship':<40} {'DPS':>5} {'Erkul':>6} {'Delta':>6}")
    print(f"  {'-'*60}")
    for r in over_count:
        diff = r["dps_count"] - r["erkul_count"]
        print(f"  {r['name']:<40} {r['dps_count']:>5} {r['erkul_count']:>6} {'+'+str(diff):>6}")
else:
    print("  None — DPS Calculator never shows MORE slots than Erkul raw.")

under_count = [r for r in results if r["dps_count"] < r["erkul_count"]]
if under_count:
    print(f"\n  {'Ship':<40} {'DPS':>5} {'Erkul':>6} {'Delta':>6}  (DPS UNDER-counts)")
    print(f"  {'-'*60}")
    for r in under_count:
        diff = r["dps_count"] - r["erkul_count"]
        print(f"  {r['name']:<40} {r['dps_count']:>5} {r['erkul_count']:>6} {str(diff):>6}")

print()
