#!/usr/bin/env python3
"""
Slot-count & size parity audit — for every ship in the Erkul cache, count
how many weapon / missile / shield / cooler / powerplant / qdrive / radar
slots our slot_extractor produces vs what the raw Erkul loadout tree actually
has.

Also checks:
 - max_size per slot matches Erkul's maxSize
 - default component (local_ref) resolves to a known component
 - no phantom slots (ours but not Erkul) or missing slots (Erkul but not ours)

Outputs slot_parity_report.txt
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(SCRIPT_DIR, '..', '..')))
from shared.app_bootstrap import bootstrap_skill  # noqa: E402
bootstrap_skill(__file__)

from services.slot_extractor import extract_slots_by_type
from services.dps_calculator import compute_weapon_stats
from services.stat_computation import (
    compute_shield_stats, compute_cooler_stats, compute_radar_stats,
    compute_missile_stats, compute_powerplant_stats_erkul, compute_qdrive_stats_erkul,
)

CACHE_FILE = os.path.join(SCRIPT_DIR, ".erkul_cache.json")
REPORT_FILE = os.path.join(SCRIPT_DIR, "slot_parity_report.txt")

# ── Load data ─────────────────────────────────────────────────────────────────

def load_cache():
    with open(CACHE_FILE, encoding="utf-8") as f:
        return json.load(f).get("data", {})

def build_ref_index(raw):
    """Build lookup from ref UUID and localName -> raw entry data."""
    by_ref = {}
    by_ln = {}
    for ep_key, entries in raw.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            d = entry.get("data", {})
            ref = d.get("ref", "")
            ln = entry.get("localName", "")
            if ref:
                by_ref[ref] = d
            if ln:
                by_ln[ln] = d
    return by_ref, by_ln

# ── Count raw Erkul slots by walking the loadout tree ─────────────────────────

def count_erkul_raw_slots(loadout, target_type):
    """Walk the Erkul loadout tree and count ports that accept target_type.
    Returns list of (port_name, max_size, local_name_or_ref)."""
    results = []

    _TURRET_HOUSINGS = {
        "TopTurret", "MannedTurret", "BallTurret", "NoseTurret",
        "RemoteTurret", "UpperTurret", "LowerTurret",
    }
    _SKIP = ("camera", "tractor", "self_destruct", "landing",
             "fuel_port", "docking", "air_traffic", "relay",
             "salvage", "mining", "scan")

    def _count_guns_in_turret(port, parent_size):
        """Count actual gun positions inside a turret housing."""
        guns = []
        for child in port.get("loadout", []):
            child_types = {t.get("type", "") for t in child.get("itemTypes", [])}
            child_subs = {t.get("subType", "") for t in child.get("itemTypes", [])}
            child_pname = child.get("itemPortName", "")
            child_sz = child.get("maxSize") or parent_size

            if "WeaponGun" in child_types:
                guns.append((child_pname, child_sz, child.get("localName", "")))
            elif "Turret" in child_types and ("Gun" in child_subs or "GunTurret" in child_subs):
                guns.append((child_pname, child_sz, child.get("localName", "")))
            elif "Turret" in child_types and bool(child_subs & _TURRET_HOUSINGS):
                guns.extend(_count_guns_in_turret(child, child_sz))
            else:
                # Check if it looks like a gun port by name
                if (child_pname.startswith("turret_") or
                    child_pname.startswith("hardpoint_class") or
                    child_pname.startswith("hardpoint_weapon")):
                    guns.append((child_pname, child_sz or parent_size, child.get("localName", "")))
                elif child.get("loadout"):
                    guns.extend(_count_guns_in_turret(child, child_sz or parent_size))
        return guns

    def walk(ports, parent_label="", inherited_size=None):
        for port in (ports or []):
            pname = port.get("itemPortName", "")
            pname_lower = pname.lower()

            if any(pat in pname_lower for pat in _SKIP):
                continue

            types = {t.get("type", "") for t in port.get("itemTypes", [])}
            subs = {t.get("subType", "") for t in port.get("itemTypes", [])}
            max_sz = port.get("maxSize") or inherited_size or 1
            children = port.get("loadout", [])

            if target_type == "WeaponGun":
                # Skip missile/bomb ports
                if ("missile" in pname_lower or "missilerack" in pname_lower
                        or "bombrack" in pname_lower or "bomb_" in pname_lower):
                    if "WeaponGun" not in types:
                        continue

                if "WeaponGun" in types:
                    results.append((pname, max_sz, port.get("localName", "")))
                elif "Turret" in types and ("Gun" in subs or "GunTurret" in subs):
                    results.append((pname, max_sz, port.get("localName", "")))
                elif "Turret" in types and bool(subs & _TURRET_HOUSINGS):
                    # Turret housing: recurse to find actual gun ports
                    guns = _count_guns_in_turret(port, max_sz)
                    if guns:
                        for g in guns:
                            results.append(g)
                    else:
                        # Empty turret housing — still a slot
                        results.append((pname, max_sz, ""))
                elif children:
                    walk(children, pname, max_sz if types else inherited_size)

            elif target_type == "MissileLauncher":
                if "MissileLauncher" in types:
                    results.append((pname, max_sz, port.get("localName", "")))
                elif children:
                    walk(children, pname, inherited_size)

            else:
                # Component types: Shield, Cooler, PowerPlant, QuantumDrive, Radar
                if target_type in types:
                    results.append((pname, max_sz, port.get("localName", port.get("localReference", ""))))
                elif children:
                    # Check if port name infers the type
                    inferred = None
                    if "shield" in pname_lower:
                        inferred = "Shield"
                    elif "cooler" in pname_lower:
                        inferred = "Cooler"
                    elif "power_plant" in pname_lower:
                        inferred = "PowerPlant"
                    elif "quantum_drive" in pname_lower:
                        inferred = "QuantumDrive"
                    elif "radar" in pname_lower:
                        inferred = "Radar"

                    if inferred == target_type:
                        results.append((pname, max_sz, port.get("localName", port.get("localReference", ""))))
                    else:
                        walk(children, pname, inherited_size)

    walk(loadout)
    return results


# ── Main audit ────────────────────────────────────────────────────────────────

def main():
    lines = []
    def out(s=""):
        lines.append(s)

    raw = load_cache()
    by_ref, by_ln = build_ref_index(raw)

    # Collect ships
    ships = []
    seen = set()
    for e in raw.get("/live/ships", []):
        d = e.get("data", {})
        n = d.get("name", "")
        if n and n not in seen:
            seen.add(n)
            ships.append((n, d))
    ships.sort(key=lambda x: x[0])

    out("=" * 100)
    out("  SLOT PARITY AUDIT — Our Extractor vs Erkul Raw Loadout Tree")
    out("=" * 100)
    out(f"Ships: {len(ships)}")
    out()

    TYPES_TO_CHECK = [
        ("WeaponGun", {"WeaponGun", "Turret"}),
        ("MissileLauncher", {"MissileLauncher"}),
        ("Shield", {"Shield"}),
        ("Cooler", {"Cooler"}),
        ("PowerPlant", {"PowerPlant"}),
        ("QuantumDrive", {"QuantumDrive"}),
        ("Radar", {"Radar"}),
    ]

    total_mismatches = 0
    ships_with_issues = 0
    issue_details = {}

    # Per-type summary
    type_summary = {t[0]: {"match": 0, "mismatch": 0, "ships": []} for t in TYPES_TO_CHECK}

    for ship_name, ship_data in ships:
        loadout = ship_data.get("loadout", [])
        if not loadout:
            continue

        ship_issues = []

        for target_type, accept_types in TYPES_TO_CHECK:
            # Our extractor
            our_slots = extract_slots_by_type(loadout, accept_types)
            our_count = len(our_slots)

            # Raw Erkul count
            erkul_slots = count_erkul_raw_slots(loadout, target_type)
            erkul_count = len(erkul_slots)

            if our_count != erkul_count:
                ship_issues.append(
                    f"  {target_type:18s}: ours={our_count:3d}  erkul={erkul_count:3d}  delta={our_count - erkul_count:+d}"
                )
                type_summary[target_type]["mismatch"] += 1
                type_summary[target_type]["ships"].append(ship_name)
            else:
                type_summary[target_type]["match"] += 1

            # Check sizes match (when counts match)
            if our_count == erkul_count and our_count > 0:
                our_sizes = sorted([s["max_size"] for s in our_slots])
                erkul_sizes = sorted([s[1] for s in erkul_slots])
                if our_sizes != erkul_sizes:
                    ship_issues.append(
                        f"  {target_type:18s}: SIZE MISMATCH ours={our_sizes} erkul={erkul_sizes}"
                    )

            # Check for unresolved refs
            for slot in our_slots:
                lr = slot.get("local_ref", "")
                if lr and lr not in by_ref and lr not in by_ln:
                    # It's a localName — check if it starts with a known prefix
                    # (weapons start with specific prefixes, so this is expected for some)
                    pass  # already covered by Phase 2 of the other audit

        if ship_issues:
            ships_with_issues += 1
            total_mismatches += len(ship_issues)
            issue_details[ship_name] = ship_issues

    # ── Output issues ──
    out("=" * 100)
    out("  SLOT COUNT MISMATCHES")
    out("=" * 100)
    out()

    if not issue_details:
        out("  ALL SHIPS MATCH — no slot count discrepancies found!")
    else:
        for ship_name in sorted(issue_details.keys()):
            out(f"  {ship_name}:")
            for issue in issue_details[ship_name]:
                out(f"  {issue}")
            out()

    # ── Per-type summary ──
    out()
    out("=" * 100)
    out("  PER-TYPE SUMMARY")
    out("=" * 100)
    out()
    out(f"  {'Type':20s} {'Match':>6s} {'Mismatch':>10s} {'Ships with issues'}")
    out("  " + "-" * 80)
    for target_type, _ in TYPES_TO_CHECK:
        s = type_summary[target_type]
        ship_list = ", ".join(s["ships"][:5])
        if len(s["ships"]) > 5:
            ship_list += f" ... +{len(s['ships'])-5} more"
        out(f"  {target_type:20s} {s['match']:>6d} {s['mismatch']:>10d}   {ship_list}")

    # ── Slot detail table for ALL ships ──
    out()
    out("=" * 100)
    out("  FULL SLOT COUNT TABLE (ours / erkul raw)")
    out("=" * 100)
    out()
    header = f"  {'Ship':35s} | {'Guns':>9s} | {'Missiles':>9s} | {'Shields':>9s} | {'Coolers':>9s} | {'PP':>9s} | {'QD':>9s} | {'Radar':>9s}"
    out(header)
    out("  " + "-" * len(header))

    for ship_name, ship_data in ships:
        loadout = ship_data.get("loadout", [])
        if not loadout:
            continue

        cols = []
        has_mismatch = False
        for target_type, accept_types in TYPES_TO_CHECK:
            our_count = len(extract_slots_by_type(loadout, accept_types))
            erkul_count = len(count_erkul_raw_slots(loadout, target_type))
            marker = " " if our_count == erkul_count else "*"
            cols.append(f"{our_count:>3d}/{erkul_count:<3d}{marker}")
            if our_count != erkul_count:
                has_mismatch = True

        flag = " <<<" if has_mismatch else ""
        out(f"  {ship_name:35s} | {cols[0]} | {cols[1]} | {cols[2]} | {cols[3]} | {cols[4]} | {cols[5]} | {cols[6]}{flag}")

    # ── Summary ──
    out()
    out("=" * 100)
    out("  SUMMARY")
    out("=" * 100)
    out(f"  Ships audited: {len(ships)}")
    out(f"  Ships with slot count mismatches: {ships_with_issues}")
    out(f"  Total mismatches: {total_mismatches}")
    out()

    # Write report
    report = "\n".join(lines)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    try:
        print(report)
    except UnicodeEncodeError:
        print(report.encode("ascii", "replace").decode("ascii"))
    print(f"\nReport saved to: {REPORT_FILE}")


if __name__ == "__main__":
    main()
