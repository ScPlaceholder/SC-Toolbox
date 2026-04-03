"""Ship loadout slot extraction — pure logic, no UI."""
import re

# Port name keyword → component type inferred when itemTypes is absent.
# Some ships (e.g. Paladin) return ALL hardpoints without itemTypes from Erkul.
# Inference is only activated for a given type when there are ZERO explicitly-typed
# ports of that type in the entire loadout tree (two-phase approach).
_PORT_NAME_TYPES: list[tuple[tuple[str, ...], str]] = [
    (("shield",),        "Shield"),
    (("cooler",),        "Cooler"),
    (("power_plant",),   "PowerPlant"),
    (("quantum_drive",), "QuantumDrive"),
    (("radar",),         "Radar"),
]

# Keywords that disqualify a port from type inference regardless of name.
# "cockpit" → cockpit display radars are not user-replaceable slots.
# "screen"  → screen_radar inside manned turrets is a display, not a slot.
# "display" → same reasoning.
# "controller" → shield/cooler controllers are not component slots.
_INFERENCE_SKIP_KEYWORDS = ("cockpit", "screen", "display", "controller", "blastshield")


def _infer_type_from_port(pname: str) -> str | None:
    """Return a component type inferred from the port name, or None."""
    lower = pname.lower()
    # Skip ports whose names contain disqualifying keywords.
    if any(kw in lower for kw in _INFERENCE_SKIP_KEYWORDS):
        return None
    for keywords, type_name in _PORT_NAME_TYPES:
        if all(kw in lower for kw in keywords):
            return type_name
    return None


def _count_explicit_types(loadout: list) -> set:
    """Return the set of itemTypes that appear with explicit type info anywhere
    in the loadout tree.  Used to disable inference for types that already have
    proper ports."""
    found: set[str] = set()

    def _walk(ports):
        for port in (ports or []):
            for t in port.get("itemTypes", []):
                tp = t.get("type", "")
                if tp:
                    found.add(tp)
            _walk(port.get("loadout", []))

    _walk(loadout)
    return found


_TURRET_HOUSING_SUBTYPES = {
    "TopTurret", "MannedTurret", "BallTurret", "NoseTurret",
    "RemoteTurret", "UpperTurret", "LowerTurret",
}


def _port_label(name: str) -> str:
    s = re.sub(r"hardpoint_|_weapon$|weapon_", "", name, flags=re.I)
    s = re.sub(r"_+", " ", s).strip()
    return s.title() if s else name.replace("_", " ").title()


def _gun_position_count(port: dict) -> int:
    """Count direct children that look like independent gun attachment points.

    A child is a gun position if its name starts with one of the known gun-arm
    prefixes or exactly matches a known gun-arm name.  Returns > 1 when this
    port is a compound housing (multiple independent gun slots inside) rather
    than a single gun mount.

    Prefixes:
      "turret_"            – classic turret arm (turret_left, turret_right, …)
      "hardpoint_class"    – direct gun hardpoint (hardpoint_class_2, …)
      "hardpoint_turret_"  – Paladin-style named arm (hardpoint_turret_weapon_left_a, …)
      "joint_turret_"      – Paladin left/right turret joint arms (joint_turret_weapon_left, …)
    Exact names:
      "hardpoint_left", "hardpoint_right", "hardpoint_upper", "hardpoint_lower"
      – named gun arms used on some turrets (e.g. Asgard pilot turret)
    """
    _GUN_POS_PREFIXES = ("turret_", "hardpoint_class", "hardpoint_turret_", "joint_turret_")
    _GUN_POS_EXACT = {"hardpoint_left", "hardpoint_right", "hardpoint_upper", "hardpoint_lower"}
    count = 0
    for child in port.get("loadout", []):
        cpname = child.get("itemPortName", "")
        if cpname in _GUN_POS_EXACT or any(cpname.startswith(p) for p in _GUN_POS_PREFIXES):
            count += 1
    return count


def extract_slots_by_type(loadout: list, accept_types: set) -> list:
    """
    Walk the loadout tree and return slots whose itemTypes match accept_types.
    For turret housings that contain weapon/gun ports, recurse into them.
    Returns list of { id, label, max_size, editable, local_ref }.
    """
    # ── Pre-scan: find which types have at least one explicitly-typed port ──
    # Inference is only used for a component type when NO explicit port of that
    # type exists anywhere in the loadout (handles ships like the Paladin whose
    # entire loadout has no itemTypes, while avoiding phantom slots on ships
    # such as Caterpillar that have both explicit and un-typed shield ports).
    _explicit_types = _count_explicit_types(loadout)

    slots = []

    def _resolve_weapon_ref(port, depth=0):
        """Resolve the actual weapon/missile ref from a gun, turret, or missile port.
        Recursively searches up to 3 levels deep for the innermost weapon ref.

        Hierarchy examples:
          Gun port → hardpoint_class_2 → localReference = weapon UUID
          Turret → turret_left → hardpoint_class_2 → localReference = weapon UUID
          Missile rack → missile_01_attach → localName = missile localName
        """
        if depth > 4:
            return ""

        ln = port.get("localName", "")
        lr = port.get("localReference", "")
        children = port.get("loadout", [])

        # Missile racks: localName starts with 'mrck_', missile is in children
        if ln and ln.startswith("mrck_") and children:
            for child in children:
                child_ln = child.get("localName", "")
                if child_ln and child_ln.startswith("misl_"):
                    return child_ln
            return ln

        # If this port has localName that looks like a weapon/missile, use it
        # Skip names that are gimbal mounts, controllers, bomb racks, or other non-weapons
        _SKIP_PREFIXES = ("controller_", "bmbrck_", "mount_gimbal_", "mount_fixed_",
                          "turret_", "relay_", "vehicle_screen", "radar_display",
                          "grin_tractorbeam", "tmbl_emp", "umnt_", "gmisl_")
        _SKIP_SUBSTRINGS = ("_scoop_", "_camera_mount", "_sensor_mount",
                            "_cap", "blanking", "_blade", "missilerack_blade",
                            "missile_cap")
        if ln and not any(ln.startswith(pfx) for pfx in _SKIP_PREFIXES):
            if ln and any(s in ln for s in _SKIP_SUBSTRINGS):
                return ""
            # Also skip if it has children (it's a housing, not a weapon)
            if not children:
                return ln

        # Search children recursively for the deepest weapon ref
        for child in children:
            child_ipn = child.get("itemPortName", "")
            child_ln = child.get("localName", "")
            child_lr = child.get("localReference", "")
            child_children = child.get("loadout", [])

            # If child has its own children (deeper nesting), recurse
            if child_children:
                result = _resolve_weapon_ref(child, depth + 1)
                if result:
                    return result

            # Child has a localName (weapon/missile) — skip non-weapon names
            if child_ln and not any(child_ln.startswith(pfx) for pfx in _SKIP_PREFIXES):
                return child_ln

            # Child has a localReference (weapon UUID on hardpoint_class_*,
            # hardpoint_left/right, turret_weapon, etc.)
            is_weapon_port = ("class" in child_ipn or "weapon" in child_ipn
                              or "gun" in child_ipn or "turret" in child_ipn
                              or "missile" in child_ipn
                              or child_ipn in ("hardpoint_left", "hardpoint_right",
                                               "hardpoint_upper", "hardpoint_lower"))
            if is_weapon_port:
                if child_lr:
                    return child_lr
                else:
                    # Found the weapon port but it's empty — no stock weapon equipped.
                    # Return "" to prevent falling back to parent's mount UUID.
                    return ""

        # Fall back to this port's localReference
        return lr

    # Port names to skip entirely — not real weapon/missile slots
    _SKIP_PORT_PATTERNS = ("camera", "tractor", "self_destruct", "landing",
                            "fuel_port", "fuel_intake", "docking", "air_traffic", "relay",
                            "salvage", "mining", "scan", "torpedo_storage")

    def walk(ports, parent_label="", inherited_size=None):
        for port in (ports or []):
            pname     = port.get("itemPortName", "")
            pname_lower = pname.lower()

            # Skip non-weapon ports
            if any(pat in pname_lower for pat in _SKIP_PORT_PATTERNS):
                continue

            types     = port.get("itemTypes", [])
            editable  = port.get("editable", False)
            max_sz    = port.get("maxSize") or inherited_size or 1
            local_ref = port.get("localName", port.get("localReference", ""))
            children  = port.get("loadout", [])

            type_names = {t.get("type", "")  for t in types}
            sub_names  = {t.get("subType", "") for t in types}

            # Infer component type from port name when itemTypes is absent.
            # Only fires when NO explicitly-typed port of the inferred type exists
            # anywhere in the loadout (two-phase guard), and port name doesn't
            # contain any disqualifying keyword (cockpit, screen, display, controller).
            # The `not children` restriction is intentionally omitted so that ports
            # like Paladin's hardpoint_quantum_drive (which has a jump_drive child)
            # are correctly inferred.
            if not type_names:
                inferred = _infer_type_from_port(pname)
                if inferred and inferred not in _explicit_types:
                    type_names = {inferred}

            label = _port_label(pname)
            if re.match(r'^Class \d+$', label, re.I):
                # Generic "Class N" gun hardpoint names add no useful info —
                # inherit the parent's descriptive label unchanged.
                label = parent_label
            elif parent_label:
                label = f"{parent_label} / {label}"

            # Determine what this port actually is
            is_gun         = "WeaponGun" in type_names
            is_missile     = "MissileLauncher" in type_names
            is_bomb        = "BombLauncher" in type_names
            is_gun_turret  = "Turret" in type_names and bool(sub_names & {"Gun", "GunTurret"})
            is_housing     = ("Turret" in type_names or "TurretBase" in type_names) and bool(
                sub_names & (_TURRET_HOUSING_SUBTYPES - {"GunTurret"})
            )
            is_inner_gun   = (
                pname.startswith("turret_")
                or pname.startswith("hardpoint_class")
                or pname.startswith("hardpoint_weapon")
            ) and not types and inherited_size is not None

            # Skip bomb launchers from weapon extraction (they're not guns)
            if is_bomb and "WeaponGun" in accept_types and "BombLauncher" not in accept_types:
                continue

            # Skip PURE missile turrets (PDS/CIWS) from gun extraction.
            # Hybrid turrets that have BOTH GunTurret AND MissileTurret subtypes
            # (e.g. Scorpius remote turret) are NOT skipped — they can hold guns.
            is_missile_turret = ("Turret" in type_names
                                 and "MissileTurret" in sub_names
                                 and "GunTurret" not in sub_names)
            if is_missile_turret and "WeaponGun" in accept_types:
                continue

            is_match = bool(type_names & accept_types)

            if "WeaponGun" in accept_types or "MissileLauncher" in accept_types:
                want_guns = "WeaponGun" in accept_types
                want_missiles = "MissileLauncher" in accept_types

                # Skip missile-named ports when extracting guns
                if want_guns and not want_missiles:
                    if ("missile" in pname_lower or "missilerack" in pname_lower
                            or "bombrack" in pname_lower or "bomb_" in pname_lower):
                        if not is_gun or is_missile:
                            continue

                # For missile-only extraction: only extract direct MissileLauncher
                # ports. Don't recurse into turret housings or extract inner gun ports.
                missile_only = want_missiles and not want_guns

                if is_match or (is_gun_turret and not missile_only):
                    # Compound GunTurret / Turret detection:
                    # If the port contains multiple independent gun positions as
                    # children (e.g. 400i remote turrets, Scorpius remote turret,
                    # F7A canard nose, Asgard pilot turret with hardpoint_left/right),
                    # recurse into children instead of adding a single slot.
                    # Note: is_gun may be True on compound turrets that also declare
                    # WeaponGun in their itemTypes (e.g. Asgard pilot turret) — we
                    # must NOT skip the compound path for those.
                    if (children and _gun_position_count(port) > 1):
                        # Arm-style turrets (hardpoint_turret_*/joint_turret_* children)
                        # have guns one size class smaller than the housing port.
                        first_child = children[0].get("itemPortName", "") if children else ""
                        size_adjust = (first_child.startswith("hardpoint_turret_") or
                                       first_child.startswith("joint_turret_"))
                        walk(children, label, max(max_sz - 1, 1) if size_adjust else max_sz)
                    elif "MissileRack" in sub_names and children:
                        # Missile rack: create one slot per individual missile loaded.
                        # Each missile_XX_attach child holds one missile by localReference.
                        for child in children:
                            child_ipn = child.get("itemPortName", "")
                            child_ref = child.get("localReference", "") or child.get("localName", "")
                            if "missile" in child_ipn.lower():
                                slots.append({
                                    "id":        f"{parent_label}:{pname}:{child_ipn}",
                                    "label":     label,
                                    "max_size":  max_sz,
                                    "editable":  True,
                                    "local_ref": child_ref,
                                })
                    else:
                        weapon_ref = _resolve_weapon_ref(port)
                        slots.append({
                            "id":        f"{parent_label}:{pname}",
                            "label":     label,
                            "max_size":  max_sz,
                            "editable":  editable,
                            "local_ref": weapon_ref,
                        })
                elif is_housing and not missile_only:
                    # Only recurse into turret housings for gun extraction
                    walk(children, label, max_sz)
                elif is_inner_gun and not missile_only:
                    # Only extract inner gun ports for gun extraction
                    weapon_ref = _resolve_weapon_ref(port)
                    slots.append({
                        "id":        f"{parent_label}:{pname}_{len(slots)}",
                        "label":     label,
                        "max_size":  inherited_size,
                        "editable":  True,
                        "local_ref": weapon_ref,
                    })
                else:
                    if children:
                        # Pass max_sz (not inherited_size) so size is correctly
                        # inherited by child gun ports (e.g. canard nose children).
                        # For named gun arm ports, pass this arm's own label so the
                        # inner gun hardpoint inherits the direction name (Left A, Right B…)
                        # rather than the generic turret label.
                        is_gun_arm = (pname_lower.startswith("hardpoint_turret_weapon") or
                                      pname_lower.startswith("joint_turret_weapon"))
                        walk(children, label if is_gun_arm else parent_label, max_sz)
            else:
                # Component tab logic (Shield, Cooler, Radar, PowerPlant, QuantumDrive…)
                if is_match:
                    slots.append({
                        "id":        f"{pname}",
                        "label":     label,
                        "max_size":  max_sz,
                        "editable":  editable,
                        "local_ref": local_ref,
                    })
                elif children:
                    walk(children, parent_label, inherited_size)

    walk(loadout)
    return slots
