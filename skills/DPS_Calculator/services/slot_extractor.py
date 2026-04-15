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
      "turret_"             – classic turret arm (turret_left, turret_right, …)
      "hardpoint_class"     – direct gun hardpoint (hardpoint_class_2, …)
      "hardpoint_turret_"   – Paladin-style named arm (hardpoint_turret_weapon_left_a, …)
      "joint_turret_"       – Paladin left/right turret joint arms (joint_turret_weapon_left, …)
      "hardpoint_weapon_"   – generic weapon arm children in compound turrets
                              (e.g. Corsair tail remote turret: hardpoint_weapon_left/right,
                               F7C-M nose turret: hardpoint_weapon_s1_left/right)
      "hardpoint_gimbal_"   – gimbal arm positions inside remote turrets
                              (e.g. Perseus remote turrets: hardpoint_gimbal_left/right)
      "hardpoint_gun_"      – gun arm positions inside remote turrets
                              (e.g. Zeus Mk II remote turret: hardpoint_gun_left/right)
    Exact names:
      "hardpoint_left", "hardpoint_right", "hardpoint_upper", "hardpoint_lower"
      – named gun arms used on some turrets (e.g. Asgard pilot turret)
    """
    _GUN_POS_PREFIXES = ("turret_", "hardpoint_class", "hardpoint_turret_",
                         "joint_turret_", "hardpoint_weapon_",
                         "hardpoint_gimbal_", "hardpoint_gun_")
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
                            "salvage", "mining", "scan", "torpedo_storage",
                            "vehicle_screen")

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
                or pname.startswith("hardpoint_gimbal_")
                or pname.startswith("hardpoint_gun_")
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
                        # Rack hardware slot (the rack itself, e.g. MSD-423)
                        rack_ref = port.get("localReference", "") or port.get("localName", "")
                        slots.append({
                            "id":        f"rack:{parent_label}:{pname}",
                            "label":     label,
                            "max_size":  max_sz,
                            "editable":  editable,
                            "local_ref": rack_ref,
                            "is_rack":   True,
                        })
                        # Individual missile sub-slots
                        for child in children:
                            child_ipn = child.get("itemPortName", "")
                            child_ref = child.get("localReference", "") or child.get("localName", "")
                            if "missile" in child_ipn.lower():
                                slots.append({
                                    "id":         f"{parent_label}:{pname}:{child_ipn}",
                                    "label":      label,
                                    "max_size":   max_sz,
                                    "editable":   True,
                                    "local_ref":  child_ref,
                                    "is_missile": True,
                                })
                    else:
                        weapon_ref = _resolve_weapon_ref(port)
                        # outer_ref: what's directly equipped in this hardpoint port.
                        # May be a gimbal UUID (≠ weapon_ref) or a weapon UUID (== weapon_ref).
                        outer_ref = port.get("localReference", "") or port.get("localName", "")
                        slots.append({
                            "id":        f"{parent_label}:{pname}",
                            "label":     label,
                            "max_size":  max_sz,
                            "editable":  editable,
                            "local_ref": weapon_ref,
                            "outer_ref": outer_ref,
                        })
                elif is_housing and not missile_only:
                    # Manned/ball/remote turret housing: collect all identical
                    # inner gun positions and emit ONE grouped slot with gun_count=N
                    # instead of N separate slots (Erkul shows VariPuck S4 ×4 style).
                    _HP_PFXS = ("turret_", "hardpoint_class", "hardpoint_turret_",
                                "joint_turret_", "hardpoint_weapon_",
                                "hardpoint_gimbal_", "hardpoint_gun_")
                    _HP_EXACT = {"hardpoint_left", "hardpoint_right",
                                 "hardpoint_upper", "hardpoint_lower"}
                    inner_guns = [
                        cp for cp in children
                        if not cp.get("itemTypes")
                        and (cp.get("itemPortName", "") in _HP_EXACT
                             or any(cp.get("itemPortName", "").startswith(pfx)
                                    for pfx in _HP_PFXS))
                    ]
                    n = len(inner_guns)
                    if n >= 1:
                        first      = inner_guns[0]
                        weapon_ref = _resolve_weapon_ref(first)
                        outer_ref  = (first.get("localReference", "")
                                      or first.get("localName", ""))
                        # Use the first inner gun's declared size if > 0;
                        # otherwise inherit the housing size (app.py gimbal
                        # resolution will correct it if a gimbal is equipped).
                        inner_sz   = first.get("maxSize") or max_sz
                        slots.append({
                            "id":        f"{parent_label}:{pname}",
                            "label":     label,
                            "max_size":  inner_sz,
                            "editable":  True,
                            "local_ref": weapon_ref,
                            "outer_ref": outer_ref,
                            "gun_count": n,
                        })
                    else:
                        # No recognised inner gun ports — fall back to recursion
                        walk(children, label, max_sz)
                elif is_inner_gun and not missile_only:
                    # Only extract inner gun ports for gun extraction
                    weapon_ref = _resolve_weapon_ref(port)
                    outer_ref  = port.get("localReference", "") or port.get("localName", "")
                    slots.append({
                        "id":        f"{parent_label}:{pname}_{len(slots)}",
                        "label":     label,
                        "max_size":  inherited_size,
                        "editable":  True,
                        "local_ref": weapon_ref,
                        "outer_ref": outer_ref,
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


def extract_mining_laser_slots(loadout: list) -> list:
    """Extract mining laser slots from ship loadout.

    Mining laser ports use 'weapon_mining' or 'mining_laser' in their name and
    are normally blocked by the ``"mining"`` entry in _SKIP_PORT_PATTERNS.
    This dedicated extractor bypasses that skip so mining ships show their
    swappable laser slots.

    The port's ``localReference`` is the mining laser UUID (indexed in the
    repository's mining_lasers_by_ref dict).

    Slot dict: {id, label, max_size, editable, local_ref}
    """
    # Ports whose localReference IS a mining laser (extract directly)
    _ML_DIRECT_KW = ("weapon_mining", "mining_laser")
    # Ports that are containers whose CHILD holds the mining laser
    _ML_CONTAINER_KW = ("mining_arm",)

    slots: list[dict] = []

    def _walk(ports, parent_label=""):
        for port in (ports or []):
            pname    = port.get("itemPortName", "")
            pname_lo = pname.lower()
            children = port.get("loadout", [])

            is_direct    = any(kw in pname_lo for kw in _ML_DIRECT_KW)
            is_container = any(kw in pname_lo for kw in _ML_CONTAINER_KW)

            if is_direct:
                # This port holds the mining laser directly via localReference.
                lr    = port.get("localReference", "") or port.get("localName", "")
                label = _port_label(pname)
                if parent_label:
                    label = f"{parent_label} / {label}"
                slots.append({
                    "id":        f"ml:{parent_label}:{pname}",
                    "label":     label,
                    "max_size":  port.get("maxSize") or 1,
                    "editable":  True,
                    "local_ref": lr,
                })
            elif is_container:
                # e.g. hardpoint_mining_arm (ToolArm) — recurse to find the
                # hardpoint_mining_laser child port.
                label = _port_label(pname)
                if parent_label:
                    label = f"{parent_label} / {label}"
                _walk(children, label)
            elif children:
                # Generic recursion (e.g. UtilityTurret/MannedTurret housings)
                label = _port_label(pname)
                if parent_label and not re.match(r'^Class \d+$', label, re.I):
                    label = f"{parent_label} / {label}"
                _walk(children, label)

    _walk(loadout)
    return slots


def extract_utility_slots(loadout: list, accept_types: set) -> list:
    """Extract slots for utility component types (Container/Cargo ore pods, Module,
    ToolArm, ExternalFuelTank, etc.).

    Unlike extract_slots_by_type, does NOT apply weapon-specific skip patterns,
    so it can find mining pods, fuel pods, salvage arms, and ship modules.

    Returns list of {id, label, max_size, editable, local_ref}.
    """
    slots: list[dict] = []

    def _walk(ports, parent_label=""):
        for port in (ports or []):
            pname    = port.get("itemPortName", "")
            types    = port.get("itemTypes", [])
            children = port.get("loadout", [])
            max_sz   = port.get("maxSize") or 1
            editable = port.get("editable", False)
            lr       = port.get("localReference", "") or port.get("localName", "")

            type_names = {t.get("type", "") for t in types}
            label = _port_label(pname)
            if parent_label:
                label = f"{parent_label} / {label}"

            if accept_types & type_names:
                slots.append({
                    "id":        f"util:{pname}:{parent_label}",
                    "label":     label,
                    "max_size":  max_sz,
                    "editable":  editable,
                    "local_ref": lr,
                })
            elif children:
                _walk(children, label)

    _walk(loadout)
    return slots


def extract_salvage_head_slots(loadout: list) -> list:
    """Extract SalvageHead sub-slots from ToolArm containers.

    The Vulture's structure:
      hardpoint_salvage_arm_left  [ToolArm, no types on head]
        hardpoint_salvage_laser   [no itemTypes, localName=salvage_head_standard]
          hardpoint_salvage_subitem01  [no types, localName=salvage_modifier_*]

    This extractor finds 'hardpoint_salvage_laser' ports nested inside
    ToolArm containers and returns them as SalvageHead slots.
    """
    slots: list[dict] = []
    _SALVAGE_HEAD_KW = ("salvage_laser", "salvage_head")

    def _walk(ports, parent_label="", inside_toolarm=False):
        for port in (ports or []):
            pname    = port.get("itemPortName", "").lower()
            children = port.get("loadout", [])
            types    = {t.get("type", "") for t in port.get("itemTypes", [])}
            max_sz   = port.get("maxSize") or 1
            lr       = port.get("localReference", "") or port.get("localName", "")

            label = _port_label(port.get("itemPortName", ""))
            if parent_label:
                label = f"{parent_label} / {label}"

            if inside_toolarm and any(kw in pname for kw in _SALVAGE_HEAD_KW):
                slots.append({
                    "id":        f"svhd:{parent_label}:{port.get('itemPortName','')}",
                    "label":     label,
                    "max_size":  max_sz,
                    "editable":  port.get("editable", False),
                    "local_ref": lr,
                })
                # Don't recurse deeper — sub-slots are separate
            elif "ToolArm" in types:
                _walk(children, label, inside_toolarm=True)
            elif children:
                _walk(children, label, inside_toolarm)

    _walk(loadout)
    return slots


def extract_fuel_pod_slots(loadout: list) -> list:
    """Extract ExternalFuelTank slots from Starfarer/Gemini loadout.

    Starfarer fuel pod ports (hardpoint_fuel_pod_*) have no itemTypes in
    Erkul's loadout JSON but carry localName pointing to the fuel pod component.
    """
    slots: list[dict] = []
    _FUEL_POD_KW = ("fuel_pod",)

    def _walk(ports, parent_label=""):
        for port in (ports or []):
            pname    = port.get("itemPortName", "").lower()
            children = port.get("loadout", [])
            lr       = port.get("localReference", "") or port.get("localName", "")
            max_sz   = port.get("maxSize") or 1

            label = _port_label(port.get("itemPortName", ""))
            if parent_label:
                label = f"{parent_label} / {label}"

            if any(kw in pname for kw in _FUEL_POD_KW) and lr:
                slots.append({
                    "id":        f"fpod:{port.get('itemPortName','')}:{parent_label}",
                    "label":     label,
                    "max_size":  max_sz,
                    "editable":  port.get("editable", False),
                    "local_ref": lr,
                })
            elif children:
                _walk(children, label)

    _walk(loadout)
    return slots


# Prefixes / substrings that identify gimbal/mount local names
_GIMBAL_PREFIXES  = ("mount_gimbal_", "mrai_pulse_mount_gimbal_")
_MOUNT_SUBSTRINGS = ("_mount_gimbal_",)


def _is_gimbal_local_name(ln: str) -> bool:
    lo = ln.lower()
    return any(lo.startswith(p) for p in _GIMBAL_PREFIXES) or any(s in lo for s in _MOUNT_SUBSTRINGS)


def extract_mount_slots(loadout: list) -> list:
    """Extract gimbal/mount slots from weapon hardpoints.

    Returns one slot per weapon hardpoint that accepts a gimbal.  The slot's
    ``local_ref`` is the gimbal's localName if one is already equipped
    (from the ship's default loadout), otherwise ``""``.

    Slot dict: {id, label, max_size, editable, local_ref}
    """
    slots: list[dict] = []

    # Port names that are never user-accessible weapon hardpoints
    _SKIP_PORT_PATTERNS = ("camera", "tractor", "self_destruct", "landing",
                            "fuel_port", "fuel_intake", "docking", "air_traffic",
                            "relay", "salvage", "mining", "scan", "torpedo_storage")

    def _walk(ports, parent_label=""):
        for port in (ports or []):
            pname    = port.get("itemPortName", "")
            pname_lo = pname.lower()
            if any(pat in pname_lo for pat in _SKIP_PORT_PATTERNS):
                continue

            types    = port.get("itemTypes", [])
            max_sz   = port.get("maxSize") or 1
            children = port.get("loadout", [])
            # localReference holds the UUID of the equipped gimbal/mount (or weapon
            # on fixed mounts).  localName is always "" on weapon ports in Erkul's API.
            ln       = port.get("localReference", "") or port.get("localName", "")

            type_names = {t.get("type", "") for t in types}
            label = _port_label(pname)
            if parent_label and not re.match(r'^Class \d+$', label, re.I):
                label = f"{parent_label} / {label}"

            is_weapon_hp = "WeaponGun" in type_names

            if is_weapon_hp:
                # Pass the UUID/localRef as-is; app.py's find_mount() will resolve it
                # and determine required_tags.  Empty means no gimbal equipped.
                current_gimbal = ln
                slots.append({
                    "id":        f"mount:{parent_label}:{pname}",
                    "label":     label,
                    "max_size":  max_sz,
                    "editable":  True,
                    "local_ref": current_gimbal,
                })
                # Don't recurse — this port is accounted for
            elif children:
                _walk(children, label)

    _walk(loadout)
    return slots
