"""Component stat computation functions — pure logic, no UI dependencies."""


# ── Fleetyards helpers (inlined to avoid services → ui dependency) ────────────

_FY_SIZE_MAP = {
    "small": 1, "s": 1, "one": 1, "1": 1,
    "medium": 2, "m": 2, "two": 2, "2": 2,
    "large": 3, "l": 3, "three": 3, "3": 3,
    "capital": 4, "xl": 4, "four": 4, "4": 4,
}


def _fy_size(raw) -> int:
    if isinstance(raw, int):
        return raw
    s = str(raw).lower().strip()
    return _FY_SIZE_MAP.get(s, 1)


def _fy_comp_name(hp: dict) -> str:
    comp = hp.get("component") or {}
    return comp.get("name") or hp.get("loadoutIdentifier") or "\u2014"


def _fy_comp_mfr(hp: dict) -> str:
    comp = hp.get("component") or {}
    mfr = comp.get("manufacturer") or {}
    return mfr.get("name") or mfr.get("code") or ""


def compute_shield_stats(raw: dict) -> dict:
    d  = raw.get("data", {})
    sh = d.get("shield", {})
    res = sh.get("resistance", {})
    ab  = sh.get("absorption", {})
    return {
        "name":             d.get("name", "?"),
        "local_name":       raw.get("localName", ""),
        "ref":              d.get("ref", ""),
        "size":             d.get("size", 1),
        "hp":               sh.get("maxShieldHealth", 0),
        "regen":            sh.get("maxShieldRegen", 0),
        "dmg_delay":        sh.get("damagedRegenDelay", 0),
        "down_delay":       sh.get("downedRegenDelay", 0),
        "res_phys_min":     res.get("physicalMin", 0),
        "res_phys_max":     res.get("physicalMax", 0),
        "res_energy_min":   res.get("energyMin", 0),
        "res_energy_max":   res.get("energyMax", 0),
        "res_dist_min":     res.get("distortionMin", 0),
        "res_dist_max":     res.get("distortionMax", 0),
        "abs_phys_min":     ab.get("physicalMin", 0),
        "abs_phys_max":     ab.get("physicalMax", 0),
        "abs_energy_min":   ab.get("energyMin", 0),
        "abs_energy_max":   ab.get("energyMax", 0),
        "abs_dist_min":     ab.get("distortionMin", 0),
        "abs_dist_max":     ab.get("distortionMax", 0),
        "class":            d.get("class", ""),
    }


def compute_cooler_stats(raw: dict) -> dict:
    d  = raw.get("data", {})
    co = d.get("cooler", {})
    res = d.get("resource", {}) or {}
    onl = res.get("online", {}) or {}
    gen = onl.get("generation", {}) or {}
    cooling_rate = co.get("coolingRate") or gen.get("cooling", 0)
    return {
        "name":          d.get("name", "?"),
        "local_name":    raw.get("localName", ""),
        "ref":           d.get("ref", ""),
        "size":          d.get("size", 1),
        "cooling_rate":  cooling_rate,
        "suppression_heat": co.get("suppressionHeatFactor", 0),
        "suppression_ir":   co.get("suppressionIRFactor", 0),
    }


def compute_radar_stats(raw: dict) -> dict:
    d  = raw.get("data", {})
    rd = d.get("radar", {}) or {}
    return {
        "name":         d.get("name", "?"),
        "local_name":   raw.get("localName", ""),
        "ref":          d.get("ref", ""),
        "size":         d.get("size", 1),
        "detection_min": rd.get("detectionLifetimeMin", 0),
        "detection_max": rd.get("detectionLifetimeMax", 0),
        "cross_section": rd.get("crossSectionOcclusionFactor", 0),
        "scan_speed":    rd.get("azimuthScanSpeed", 0) or d.get("radar", {}).get("scanSpeed", 0) if rd else 0,
    }


def compute_missile_stats(raw: dict) -> dict:
    d  = raw.get("data", {})
    ms = d.get("missile", {}) or {}
    dmg = ms.get("damage", {}) or {}
    total_dmg = sum(v for v in dmg.values() if isinstance(v, (int, float)))
    return {
        "name":       d.get("name", "?"),
        "local_name": raw.get("localName", ""),
        "ref":        d.get("ref", ""),
        "size":       d.get("size", 1),
        "total_dmg":  total_dmg,
        "dmg_phys":   float(dmg.get("damagePhysical", 0) or 0),
        "dmg_energy": float(dmg.get("damageEnergy", 0) or 0),
        "dmg_dist":   float(dmg.get("damageDistortion", 0) or 0),
        "tracking":   ms.get("trackingSignalType", "?"),
        "lock_range": ms.get("lockRangeMax", 0),
        "lock_time":  ms.get("lockTime", 0),
        "speed":      ms.get("linearSpeed", 0),
        "lifetime":   ms.get("maxLifetime", 0),
        "lock_angle": ms.get("lockingAngle", 0),
    }


# ── erkul power-plant / quantum-drive stat helpers ────────────────────────────

def compute_powerplant_stats_erkul(raw: dict) -> dict:
    d    = raw.get("data", {})
    # Power output lives at resource.online.generation.powerSegment (erkul 4.x)
    res  = d.get("resource", {}) or {}
    onl  = res.get("online", {}) or {}
    gen  = onl.get("generation", {}) or {}
    sig  = onl.get("signatureParams", {}) or {}
    em_d = sig.get("em", {}) or {}
    ir_d = sig.get("ir", {}) or {}
    # health is a dict {"hp":N, ...} in erkul data
    hlth = d.get("health", {})
    hp_val = hlth.get("hp", 0) if isinstance(hlth, dict) else (hlth or 0)
    return {
        "name":          d.get("name", "?"),
        "local_name":    raw.get("localName", ""),
        "ref":           d.get("ref", ""),
        "size":          d.get("size", 1),
        "class":         d.get("class", ""),
        "grade":         d.get("grade", "?"),
        "output":        float(gen.get("powerSegment", 0) or 0),
        "power_draw":    0.0,   # PPs generate, not consume
        "power_max":     0.0,
        "overclocked":   0.0,
        "em_idle":       float(em_d.get("nominalSignature", 0) or 0),
        "em_max":        float(em_d.get("nominalSignature", 0) or 0),
        "ir_max":        float(ir_d.get("nominalSignature", 0) or 0),
        "hp":            float(hp_val or 0),
    }


def compute_qdrive_stats_erkul(raw: dict) -> dict:
    d  = raw.get("data", {})
    # erkul uses "qdrive" key (not "quantumDrive")
    qd = d.get("qdrive", d.get("quantumDrive", d.get("quantumdrive", {}))) or {}
    # Speed/spool are inside qdrive.params (erkul 4.x)
    params = qd.get("params", qd.get("standardJump", {})) or {}
    # Resource for EM/power
    res  = d.get("resource", {}) or {}
    onl  = res.get("online", {}) or {}
    sig  = onl.get("signatureParams", {}) or {}
    em_d = sig.get("em", {}) or {}
    # health is a dict {"hp":N, ...}
    hlth = d.get("health", {})
    hp_val = hlth.get("hp", 0) if isinstance(hlth, dict) else (hlth or 0)
    return {
        "name":       d.get("name", "?"),
        "local_name": raw.get("localName", ""),
        "ref":        d.get("ref", ""),
        "size":       d.get("size", 1),
        "class":      d.get("class", ""),
        "grade":      d.get("grade", "?"),
        "speed":      float(params.get("driveSpeed", qd.get("speed", 0)) or 0),
        "spool":      float(params.get("spoolUpTime", qd.get("spoolUpTime", 0)) or 0),
        "cooldown":   float(params.get("cooldownTime", qd.get("cooldown", 0)) or 0),
        "fuel_rate":  float(qd.get("quantumFuelRequirement", qd.get("fuelRate", 0)) or 0),
        "jump_range": float(qd.get("jumpRange", qd.get("maxRange", 0)) or 0),
        "efficiency": float(qd.get("quantumFuelRequirement", 0) or 0),
        "power_draw": 0.0,
        "power_max":  0.0,
        "em_idle":    float(em_d.get("nominalSignature", 0) or 0),
        "em_max":     float(em_d.get("nominalSignature", 0) or 0),
        "ir_max":     0.0,
        "hp":         float(hp_val or 0),
    }


# ── Fleetyards component helpers ──────────────────────────────────────────────

def compute_powerplant_stats(hp: dict) -> dict:
    """Extract power plant info from a Fleetyards hardpoint entry."""
    comp = hp.get("component") or {}
    td   = comp.get("typeData") or {}
    return {
        "name":       _fy_comp_name(hp),
        "size":       _fy_size(comp.get("size", hp.get("size", 1))),
        "grade":      comp.get("grade", "?"),
        "class":      comp.get("class", ""),
        "mfr":        _fy_comp_mfr(hp),
        "power_output": float(td.get("output", td.get("powerOutput", 0)) or 0),
    }


def compute_qdrive_stats(hp: dict) -> dict:
    """Extract quantum drive info from a Fleetyards hardpoint entry."""
    comp = hp.get("component") or {}
    td   = comp.get("typeData") or {}
    sj   = td.get("standardJump") or {}
    return {
        "name":        _fy_comp_name(hp),
        "size":        _fy_size(comp.get("size", hp.get("size", 1))),
        "grade":       comp.get("grade", "?"),
        "mfr":         _fy_comp_mfr(hp),
        "speed":       float(sj.get("speed", 0) or 0),          # m/s
        "spool":       float(sj.get("spoolUpTime", 0) or 0),    # s
        "cooldown":    float(sj.get("cooldown", 0) or 0),       # s
        "fuel_rate":   float(td.get("fuelRate", 0) or 0),
        "jump_range":  float(td.get("jumpRange", 0) or 0),
    }


def compute_thruster_stats(hp: dict) -> dict:
    """Extract thruster info from a Fleetyards hardpoint entry."""
    comp     = hp.get("component") or {}
    td       = comp.get("typeData") or {}
    category = hp.get("category") or hp.get("categoryLabel") or hp.get("type", "")
    return {
        "name":     _fy_comp_name(hp),
        "size":     _fy_size(comp.get("size", hp.get("size", 1))),
        "category": category,
        "mfr":      _fy_comp_mfr(hp),
        "thrust":   float(td.get("thrustCapacity", td.get("thrust", 0)) or 0),
    }


# ── Erkul new-endpoint stat extractors ────────────────────────────────────────
# All receive the full entry dict:  {"calculatorType": ..., "data": {...}, "localName": ...}


def _erkul_base(raw: dict) -> dict:
    """Common fields shared by every Erkul component entry."""
    d = raw.get("data", {})
    mfr = (d.get("manufacturerData") or {}).get("data", {})
    hlth = d.get("health") or {}
    res  = d.get("resource") or {}
    onl  = res.get("online") or {}
    sig  = onl.get("signatureParams") or {}
    em_d = sig.get("em") or {}
    ir_d = sig.get("ir") or {}
    pwr  = onl.get("consumption") or {}
    return {
        "name":       d.get("name", "?"),
        "local_name": raw.get("localName", ""),
        "ref":        d.get("ref", ""),
        "size":       d.get("size", 1),
        "grade":      d.get("grade", "?"),
        "sub_type":   d.get("subType", ""),
        "mfr":        mfr.get("name", d.get("manufacturer", "")),
        "hp":         float((hlth.get("hp") or 0)),
        "power_draw": float(pwr.get("powerSegment", 0) or 0),
        "em_max":     float(em_d.get("nominalSignature", 0) or 0),
        "ir_max":     float(ir_d.get("nominalSignature", 0) or 0),
        "required_tags": d.get("requiredTags", ""),
    }


def compute_missile_rack_stats(raw: dict) -> dict:
    """Missile rack (MissileLauncher/MissileRack) — holds 1-N missiles.

    ports[] array tells us missile count and size class.
    Name encoding: MSD-322 = S3 housing, 2× S2 missiles.
    """
    b = _erkul_base(raw)
    d = raw.get("data", {})
    ports = d.get("ports") or []
    # Each port in the list is one missile slot
    missile_ports = [p for p in ports
                     if any(it.get("type") == "Missile"
                            for it in (p.get("itemTypes") or []))]
    capacity   = len(missile_ports)
    missile_sz = missile_ports[0].get("maxSize", 1) if missile_ports else 1
    rack       = d.get("missileRack") or {}
    return {**b,
        "type":           "MissileLauncher",
        "missile_count":  capacity,       # number of missiles it holds
        "missile_size":   missile_sz,     # size of each missile slot
        "launch_delay":   float(rack.get("launchDelay", 0) or 0),
    }


def compute_mount_stats(raw: dict) -> dict:
    """Gimbal / utility mount (Turret/GunTurret).

    Mounts are the hardware that lets a gun track — they have a size
    (the port they occupy) and hold a gun one size smaller.
    """
    b = _erkul_base(raw)
    d = raw.get("data", {})
    ports = d.get("ports") or []
    # port_max_size: largest weapon this mount can hold
    port_max = max(
        (p.get("maxSize", 1) for p in ports if isinstance(p, dict)),
        default=d.get("size", 1),
    )
    return {**b,
        "type":          d.get("type", "Turret"),
        "required_tags": d.get("requiredTags", ""),
        "port_count":    len(ports),
        "port_max_size": port_max,
    }


def compute_emp_stats(raw: dict) -> dict:
    """EMP burst generator stats."""
    b    = _erkul_base(raw)
    d    = raw.get("data", {})
    emp  = d.get("emp") or {}
    dist = d.get("distortion") or {}
    return {**b,
        "type":            "EMP",
        "charge_time":     float(emp.get("chargeTime", 0) or 0),
        "cooldown_time":   float(emp.get("cooldownTime", 0) or 0),
        "unleash_time":    float(emp.get("unleashTime", 0) or 0),
        "emp_radius":      float(emp.get("empRadius", 0) or 0),
        "min_emp_radius":  float(emp.get("minEmpRadius", 0) or 0),
        "phys_radius":     float(emp.get("physRadius", 0) or 0),
        "distortion_dmg":  float(emp.get("distortionDamage", 0) or 0),
        "dist_decay_rate": float(dist.get("decayRate", 0) or 0),
    }


def compute_qed_stats(raw: dict) -> dict:
    """Quantum Enforcement Device (quantum interdiction generator)."""
    b   = _erkul_base(raw)
    d   = raw.get("data", {})
    res = d.get("resource") or {}
    onl = res.get("online") or {}
    pwr = onl.get("consumption") or {}
    return {**b,
        "type":       "QuantumInterdictionGenerator",
        "power_draw": float(pwr.get("powerSegment", 0) or 0),
    }


def compute_bomb_stats(raw: dict) -> dict:
    """Bomb stats — damage comes from the health.damageResistanceMultiplier proxy
    or the bomb sub-object (explosion radii, arm time etc.).
    Direct damage values are not present in Erkul; we store blast geometry instead.
    """
    b    = _erkul_base(raw)
    d    = raw.get("data", {})
    bomb = d.get("bomb") or {}
    dist = d.get("distortion") or {}
    return {**b,
        "type":         d.get("type", "Bomb"),
        "max_lifetime": float(bomb.get("maxLifetime",            0) or 0),
        "arm_time":     float(bomb.get("armTime",                0) or 0),
        "min_radius":   float(bomb.get("minRadius",              0) or 0),
        "max_radius":   float(bomb.get("maxRadius",              0) or 0),
        "dist_max":     float(dist.get("maximum",                0) or 0),
    }


def compute_turret_stats(raw: dict) -> dict:
    """Turret housing (manned / remote / ball / nose turrets).

    These are the physical turret structures, not the guns inside them.
    HP, size, sub_type (GunTurret, MannedTurret, etc.) are the key stats.
    """
    b = _erkul_base(raw)
    d = raw.get("data", {})
    return {**b,
        "type": d.get("type", "Turret"),
    }


def compute_mining_laser_stats(raw: dict) -> dict:
    """Mining laser stats.

    Erkul stores mining params under 'miningLaser' (instability, modifiers)
    and weapon connection power under weapon.connection.  Module slot count
    is at top-level 'moduleSlots'.
    """
    b   = _erkul_base(raw)
    d   = raw.get("data", {})
    ml  = d.get("miningLaser") or {}
    wpn = d.get("weapon") or {}
    conn = wpn.get("connection") or {}
    norm = conn.get("normalStats") or {}
    return {**b,
        "type":            d.get("type", "WeaponMining"),
        "module_slots":    int(d.get("moduleSlots", 0) or 0),
        "instability":     float(ml.get("laserInstability",         0) or 0),
        "resistance_mod":  float(ml.get("resistanceModifier",       0) or 0),
        "filter_mod":      float(ml.get("filterModifier",           0) or 0),
        "throttle_min":    float(ml.get("throttleMinimum",          0) or 0),
        "power_draw":      float(norm.get("powerMod",
                                 conn.get("heatRateOnline",         0)) or 0),
    }


def compute_tool_arm_stats(raw: dict) -> dict:
    """ToolArm — mining arm / salvage arm housing (ship-specific, usually non-swappable)."""
    b = _erkul_base(raw)
    d = raw.get("data", {})
    return {**b, "type": "ToolArm"}


def compute_salvage_head_stats(raw: dict) -> dict:
    """SalvageHead — salvage beam head (e.g. Baler Salvage Head on Vulture)."""
    b   = _erkul_base(raw)
    d   = raw.get("data", {})
    wpn = d.get("weapon", {}) or {}
    tb  = wpn.get("tractorBeam", {}) or {}
    return {**b,
        "type":         "SalvageHead",
        "max_force":    float(tb.get("maxForce",    0) or 0),
        "max_distance": float(tb.get("maxDistance", 0) or 0),
    }


def compute_mining_modifier_stats(raw: dict) -> dict:
    """MiningModifier — consumable module for mining laser sub-slots."""
    b    = _erkul_base(raw)
    d    = raw.get("data", {})
    mod  = d.get("modifier", {}) or {}
    ml   = mod.get("miningModifier", {}) or {}
    wmod = mod.get("weaponModifier", {}) or {}
    return {**b,
        "type":              "MiningModifier",
        "charges":           int(mod.get("charges", 0) or 0),
        "resistance_mod":    float(ml.get("resistanceModifier",              0) or 0),
        "instability_mod":   float(ml.get("laserInstability",                0) or 0),
        "charge_window_mod": float(ml.get("optimalChargeWindowSizeModifier", 0) or 0),
        "shatter_mod":       float(ml.get("shatterdamageModifier",           0) or 0),
        "dmg_mult":          float(wmod.get("laserDamageMultiplier",         1) or 1),
        "lifetime":          float(wmod.get("lifetimeLaser",                 0) or 0),
    }


def compute_salvage_modifier_stats(raw: dict) -> dict:
    """SalvageModifier — consumable module for salvage head sub-slots."""
    b   = _erkul_base(raw)
    d   = raw.get("data", {})
    mod = d.get("modifier", {}) or {}
    return {**b,
        "type":    "SalvageModifier",
        "charges": int(mod.get("charges", 0) or 0),
    }


def compute_ore_pod_stats(raw: dict) -> dict:
    """Container/Cargo — ore pod / mining cargo pod (Argo Ore Pod etc.)."""
    b = _erkul_base(raw)
    d = raw.get("data", {})
    return {**b,
        "type":     "Container",
        "capacity": float(d.get("resourceContainerCapacity", 0) or 0),
    }


def compute_fuel_tank_stats(raw: dict) -> dict:
    """ExternalFuelTank — external fuel pod (Starfarer, Gemini)."""
    b = _erkul_base(raw)
    d = raw.get("data", {})
    return {**b,
        "type":     "ExternalFuelTank",
        "capacity": float(d.get("resourceContainerCapacity", 0) or 0),
    }


def compute_erkul_module_stats(raw: dict) -> dict:
    """Erkul /live/modules entry — ship modules (Retaliator, Apollo, Aurora Mk II)."""
    b = _erkul_base(raw)
    d = raw.get("data", {})
    return {**b,
        "type": d.get("type", "Module"),
    }
