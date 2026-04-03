# Erkul Mathematical Parity Audit — Full Value Verification & Refactor Plan

## Objective

Achieve **100% mathematical parity** with [erkul.games/live/calculator](https://www.erkul.games/live/calculator) for every computed value. This audit must:

1. **Identify every value** displayed on Erkul's calculator UI
2. **Trace each value** back to our computation code
3. **Compare our formula** against Erkul's actual behavior (via live site cross-referencing)
4. **Flag any delta** > 0 (zero tolerance — we must match Erkul exactly after rounding)
5. **Produce a refactor plan** for any formula that doesn't match

---

## Phase 1: Erkul Value Inventory

Open https://www.erkul.games/live/calculator in a browser. For a reference ship (start with **Gladius**, then **Hammerhead**, then **Constellation Andromeda**), record **every numerical value** displayed on the page. Organize by section:

### 1.1 Per-Weapon Row Values
For each equipped weapon, Erkul shows:
- **Size** (e.g., S3)
- **Alpha Damage** (single-shot peak damage)
- **Fire Rate** (shots per second or RPM — note which unit)
- **DPS** (raw = alpha x fire_rate)
- **Sustained DPS** (accounting for overheat/regen/ammo cycling)
- **Ammo Count** (magazine capacity, or "INF" for energy)
- **Damage Type Breakdown** (physical / energy / distortion / thermal — exact numbers)
- **Range** (effective range in meters, if shown)
- **Speed** (projectile velocity, if shown)

### 1.2 Per-Weapon Detail Panel
Click a weapon to expand details. Record:
- **Pellet Count**
- **Damage Multiplier**
- **Charge Damage Multiplier** (for charge weapons)
- **Heat Per Shot**
- **Overheat Temperature**
- **Overheat Fix Time**
- **Cooling Per Second**
- **Time Till Cooling Starts**
- **Max Ammo Load** (regen weapons)
- **Max Regen Per Sec** (regen weapons)
- **Regeneration Cooldown** (regen weapons)
- **Power Draw** (segment count)
- **EM Signature** (nominal)
- **IR Signature** (nominal)

### 1.3 Footer / Aggregate Values
- **Total DPS** (raw — sum of all weapon DPS)
- **Total Sustained DPS** (sum of all sustained)
- **Total Alpha** (sum of all alpha)
- **Total Missile Damage** (sum of all equipped missile warhead damage)

### 1.4 Shield Section
For each shield:
- **Shield HP**
- **Regen Rate** (HP/s)
- **Damaged Delay** (seconds before regen starts after hit)
- **Downed Delay** (seconds before regen starts after shield collapse)
- **Physical Resistance** (min% and max%, or interpolated value)
- **Energy Resistance** (min% and max%)
- **Distortion Resistance** (min% and max%)
- **Physical Absorption** (min% and max%)
- **Energy Absorption** (min% and max%)
- **Distortion Absorption** (min% and max%)

Aggregate:
- **Total Shield HP**
- **Total Shield Regen**
- **Effective Resistance** (after power ratio interpolation)

### 1.5 Power Allocation Section
- **Total Power Plant Output** (exact number)
- **Total Power Draw** (exact number)
- **Consumption %** (draw / output)
- **Per-category pip counts**: Weapons, Engine, Shield, Cooler, Radar, Life Support, QDrive, Utility
- **Per-category current allocation** (how many pips selected vs max)
- **Weapon Power Ratio** (shown as % or decimal)
- **Shield Power Ratio** (shown as % or decimal)

### 1.6 Signature Section
- **EM Signature** (exact value and unit)
- **IR Signature** (exact value and unit)
- **Cross-Section (CS)** (exact value and unit)
- Note: Are these shown as absolute values, percentages, or both?

### 1.7 Ship-Level Values
- **Hull HP**
- **Armor HP** (if shown separately)
- **Armor Type** (light/medium/heavy)
- **Armor Damage Multipliers** (physical, energy, distortion)
- **Cross-section dimensions** (x, y, z if shown)

### 1.8 Cooler Section
- **Cooling Rate** (per cooler)
- **Total Cooling Generation**
- **Total Cooling Consumption**
- **Suppression factors** (heat, IR)

### 1.9 Quantum Drive Section
- **Speed** (m/s)
- **Spool Time** (seconds)
- **Cooldown** (seconds)
- **Fuel Rate**
- **Jump Range**

### 1.10 Missile Section
For each missile type:
- **Total Damage**
- **Per-type breakdown** (physical, energy, distortion, thermal)
- **Tracking Type** (IR, EM, CS)
- **Lock Range** (meters)
- **Lock Time** (seconds)
- **Speed** (m/s)
- **Lifetime** (seconds)
- **Locking Angle** (degrees)

---

## Phase 2: Code-to-Erkul Formula Mapping

For each value identified in Phase 1, trace through our code and document:

| Erkul Value | Our Code Location | Our Formula | Erkul's Formula (observed) | Match? |
|---|---|---|---|---|
| Alpha Damage | `dps_calculator.py:alpha_max()` | `sum(dmg) * pelletCount * dmgMult * chargeMult + explosion` | ??? | ??? |
| Fire Rate (RPS) | `dps_calculator.py:fire_rate_rps()` | Looping: `fireRate/60`, Sequential: `N/sum(60/d)` | ??? | ??? |
| DPS Raw | `dps_calculator.py:compute_weapon_stats()` | `alpha * rps` | ??? | ??? |
| DPS Sustained (regen) | `dps_calculator.py:dps_sustained()` | `(ammos * alpha) / (chargeTime + fireTime)` | ??? | ??? |
| DPS Sustained (heat) | `dps_calculator.py:dps_sustained()` | `(shots * alpha) / (ohTime + fixTime)` | ??? | ??? |
| PP Output | `power_engine.py:load_ship()` | `rounded_seg_sum + (numPPs-1)*total_size` | ??? | ??? |
| EM Signature | `power_engine.py:recalculate()` | per-component sum with range modifiers * armor | ??? | ??? |
| IR Signature | `power_engine.py:recalculate()` | cooler IR * seg_ratio * cooling_ratio * range_mod * armor | ??? | ??? |
| Weapon Power Ratio | `power_engine.py:recalculate()` | `poolSize * prm / totalConsumption` | ??? | ??? |
| Shield Resistance (effective) | `loadout_aggregator.py` | `res_min + ratio * (res_max - res_min)` | ??? | ??? |

### Critical Files to Audit
```
services/dps_calculator.py     — fire_rate_rps(), alpha_max(), dps_sustained(), dmg_breakdown()
services/stat_computation.py   — compute_shield_stats(), compute_cooler_stats(), compute_missile_stats(), etc.
services/power_engine.py       — load_ship(), _init_segments_distribution(), recalculate()
services/loadout_aggregator.py — compute_footer_totals()
services/slot_extractor.py     — extract_slots_by_type()
```

---

## Phase 3: Ship-by-Ship Numerical Verification

### Method
For each ship (prioritize combat ships first):

1. Open the ship on Erkul with **stock loadout** (no changes)
2. Record every value from Phase 1's inventory
3. Run our `erkul_parity_audit.py` for the same ship
4. Compare every value field-by-field
5. For any mismatch, dig into the raw JSON data to understand the discrepancy

### Test Matrix (minimum coverage)

| Category | Ships | Why |
|---|---|---|
| Light fighters | Arrow, Gladius, Blade, Khartu-al | Simple loadouts, easy to verify |
| Medium fighters | Sabre, Hornet F7C-M, Vanguard Warden | Mixed weapon sizes, turrets |
| Heavy fighters | Eclipse, Retaliator, Ares Ion/Inferno | Special weapon types (torps, charge, gatling) |
| Multi-crew | Constellation Andromeda, Hammerhead, Redeemer | Many turrets, complex pip allocation |
| Capital | Polaris, Javelin (if available) | Maximum complexity |
| Edge cases | Avenger Renegade, Defender, Prowler | Unusual hardpoint configs |
| Regen weapons | Ships with energy repeaters | Test regen DPS formula specifically |
| Heat weapons | Ships with ballistic weapons | Test heat DPS formula specifically |
| Engineering buffs | Asgard, Idris (if buffed) | Test ammoLoadMultiplier, regenMultiplier, powerRatioMultiplier |
| Mixed power | Any ship where weapon ratio < 1.0 | Test sustained DPS under partial power |

### Per-Ship Checklist
For each ship, verify ALL of the following match Erkul exactly:

- [ ] Gun slot count and sizes
- [ ] Missile slot count and sizes
- [ ] Default weapon in each slot (localName matches)
- [ ] Per-weapon: alpha, RPS, DPS raw, DPS sustained, ammo
- [ ] Per-weapon: damage breakdown (all 4 types)
- [ ] Per-missile: total damage, tracking, lock range, speed
- [ ] Per-shield: HP, regen, resistances (min/max), absorption
- [ ] Shield count and sizes
- [ ] Cooler count, cooling rate
- [ ] Power plant count, output
- [ ] QDrive: speed, spool, cooldown, fuel rate
- [ ] Radar: detection range
- [ ] Total DPS raw (footer)
- [ ] Total DPS sustained (footer)
- [ ] Total alpha (footer)
- [ ] Total missile damage (footer)
- [ ] Total shield HP (footer)
- [ ] Total shield regen (footer)
- [ ] Effective shield resistance (after power ratio lerp)
- [ ] Hull HP
- [ ] Power pip allocation per category (SCM mode)
- [ ] Power pip allocation per category (NAV mode)
- [ ] Weapon power ratio
- [ ] Shield power ratio
- [ ] EM signature
- [ ] IR signature
- [ ] CS signature
- [ ] Consumption %
- [ ] Cooling generation / consumption
- [ ] Total power output
- [ ] Total power draw

---

## Phase 4: Specific Formula Deep-Dives

### 4.1 Sustained DPS — Regen Path
**Our code** (`dps_calculator.py:57-67`):
```python
effective_ratio = weapon_power_ratio * power_ratio_mult
ammos      = round(maxAmmoLoad * ammo_load_mult * effective_ratio)
max_regen  = maxRegenPerSec * regen_per_sec_mult * effective_ratio
fire_time  = ammos / rps
charge_time = cooldown + ammos / max_regen
sustained  = (ammos * alpha) / (charge_time + fire_time)
```

**Verify against Erkul:**
- Does Erkul use `round()` on ammos? Or `floor()`? Or `ceil()`?
- Does `effective_ratio` multiply into BOTH ammos AND regen? Or just one?
- Is `cooldown` (regenerationCooldown) applied per-cycle or only initially?
- Does Erkul cap `effective_ratio` at 1.0 before applying?
- Test with Asgard (ammoLoadMult=5) at various power ratios to isolate compounding behavior

### 4.2 Sustained DPS — Heat Path
**Our code** (`dps_calculator.py:68-98`):
```python
ot = overheatTemp - tempAfterFix
ft = overheatFixTime
cooling_between = max(0, (1/rps) - ttcs) * coolingPerSec
effective_hps = hps - cooling_between
oh_time = ot / (effective_hps * rps)
shots = ceil(oh_time * rps)
sustained = (shots * alpha) / (oh_time + ft)
```

**Verify against Erkul:**
- Is `temperatureAfterOverheatFix` subtracted from overheatTemp? Or is it the starting temp after fix?
- `heatPerShot` — averaged across fire actions, or per-action?
- Does Erkul use `ceil()` for shots_before_overheat?
- Is inter-shot cooling calculated the same way?
- Does the fix time include the cooling start delay?
- Test with a pure ballistic weapon (e.g., Sledge II mass driver) and verify step by step

### 4.3 Power Plant Output
**Our code** (`power_engine.py`):
```python
rounded_seg_sum = sum(round(powerSegment[i] / numPPs) for i in range(numPPs))
output = rounded_seg_sum + (numPPs - 1) * total_size
```

**Verify:**
- Does Erkul round each segment individually before summing?
- What is `total_size` — sum of all power plant sizes, or max?
- For ships with 2+ power plants of different sizes, does order matter?
- Compare output for: Aurora (1 PP), Constellation (2 PP), Hammerhead (2+ PP)

### 4.4 EM Signature Breakdown
**Our code** (`power_engine.py:recalculate()`):
```
pp_em = nom_em * range_modifier * pp_usage_ratio
wpn_em = nom_em (raw, NO range modifier)
component_em = nom_em * range_modifier * seg_ratio
final = (pp_em + wpn_em + component_em) * armor_em
```

**Verify:**
- Does Erkul truly skip range modifiers for weapons?
- What is `pp_usage_ratio` exactly — total draw / total output?
- Does Erkul include disabled components in the sum?
- Is the armor multiplier applied multiplicatively at the end?
- What happens to EM when a shield is toggled off?

### 4.5 IR Signature
**Our code** (`power_engine.py:recalculate()`):
```
ir = sum(cooler.ir * seg_ratio * cooling_ratio * range_mod) * armor_ir
cooling_ratio = min(cooling_consumption / cooling_generation, 1.0)
```

**Verify:**
- Is IR really only from coolers? Or do other components contribute?
- What exactly feeds into cooling_consumption?
- Is cooling_ratio capped at 1.0?
- Does toggling components on/off change IR immediately?

### 4.6 Shield Resistance Interpolation
**Our code** (`loadout_aggregator.py`):
```python
resistance = res_min + shield_power_ratio * (res_max - res_min)
```

**Verify:**
- Is this linear interpolation correct? Or does Erkul use a different curve?
- Is `shield_power_ratio` = selected_pips / total_pips?
- Does this apply to absorption too, or just resistance?
- When shield_power_ratio = 1.0, does Erkul show res_max?
- When shield_power_ratio = 0.5, verify the exact midpoint

### 4.7 Weapon Power Ratio
**Our code** (`power_engine.py`):
```python
ratio = weaponPoolSize * powerRatioMultiplier / totalWeaponConsumption
weapon_power_ratio = min(ratio, 1.0)
```

**Verify:**
- What exactly is `weaponPoolSize` — from `rnPowerPools.weaponGun.poolSize`?
- What is `totalWeaponConsumption` — sum of all weapon powerSegment values?
- Is `powerRatioMultiplier` from `ship.buff.regenModifier.powerRatioMultiplier`?
- When all pips are allocated, does ratio = poolSize / totalConsumption?
- When pips are reduced, how does it change?

### 4.8 Pip Allocation Algorithm
**Our code** (`power_engine.py:_init_segments_distribution()`):

Phase 1: Critical pips (shields all on in SCM, first pip for weapons/engine/cooler/etc.)
Phase 2: Greedy fill in priority order

**Verify:**
- SCM priority: `coolers > lifeSupport > miningLaser > weapon > shield > engine > radar > emp > qed > salvage`
- NAV priority: `coolers > lifeSupport > qdrive > miningLaser > engine > radar > salvage > weapon > emp > qed`
- Does Erkul use the same priority ordering?
- For ships with >2 shields, does Erkul only power 2 initially?
- Toggle between SCM/NAV on Erkul and record every pip change

---

## Phase 5: Discrepancy Classification & Refactor Plan

For every mismatch found, classify it:

### Category A: Formula Error (must fix)
Our formula produces a different result than Erkul for the same inputs. This means our math is wrong.

**Required output**: Exact corrected formula with derivation showing how to match Erkul.

### Category B: Data Extraction Error (must fix)
We're reading the wrong field from the JSON, or applying the wrong transformation (e.g., dividing by 60 when Erkul doesn't).

**Required output**: Correct field path and transformation.

### Category C: Rounding Difference (must fix to match)
We round at a different step than Erkul, producing small deltas that accumulate.

**Required output**: Where to move the round() call, and whether to use round/floor/ceil.

### Category D: Missing Feature (must implement)
Erkul displays a value we don't compute at all.

**Required output**: Full implementation spec with formula, data source, and target location in our code.

### Category E: Known Erkul Bug (replicate intentionally)
Erkul has a documented bug that we should replicate for parity (e.g., QDrive EM using lifeSupport count).

**Required output**: Document the bug, our intentional replication, and a comment in the code.

---

## Phase 6: Refactor Specification

For each fix needed, produce a detailed spec:

```
ISSUE ID:    ERKUL-001
CATEGORY:    A (Formula Error)
SEVERITY:    High / Medium / Low
VALUE:       Sustained DPS (regen path)
SHIP(S):     Asgard (and all regen-weapon ships)
OUR RESULT:  206.6
ERKUL SHOWS: 207.0
DELTA:       +0.4

ROOT CAUSE:
  Our code applies round() to ammos AFTER multiplying by effective_ratio.
  Erkul applies round() BEFORE the power_ratio_mult multiplication.

CURRENT CODE (dps_calculator.py:61):
  ammos = round(maxAmmoLoad * ammo_load_mult * effective_ratio)

CORRECTED CODE:
  ammos = round(round(maxAmmoLoad * ammo_load_mult) * effective_ratio)
  # Erkul rounds ammoLoad*buff first, then scales by power ratio

TEST:
  Asgard CF-337 Panther: expected sus=207.0, tolerance=0.0
```

---

## Phase 7: Regression Test Updates

For every fix in Phase 6, update the existing audit test suite:

1. **Update `_PHASE5_TESTS`** in `erkul_parity_audit.py` with corrected expected values
2. **Add new test cases** for any formulas that were missing coverage
3. **Tighten tolerances** — the goal is ZERO tolerance (exact match after Erkul's rounding)
4. **Add specific ship tests** for edge cases discovered during the audit

### New Tolerance Targets (post-refactor)
```
DPS (raw):        ±0.05  (was ±0.5)
DPS (sustained):  ±0.05  (was ±0.5)
Alpha:            ±0.01  (was implied)
Fire rate:        ±0.001
Shield HP:        ±0.1   (was ±1.0)
Shield regen:     ±0.01  (was ±0.1)
Resistance:       ±0.001 (was ±0.01)
EM signature:     ±0.1   (was ±5%)
IR signature:     ±0.1   (was ±5%)
CS signature:     ±0.01
Power pips:       0      (exact)
Power output:     ±0.1
Weapon ratio:     ±0.001
Shield ratio:     ±0.001
```

---

## Execution Order

1. **Phase 1** — Inventory all Erkul values (browser required)
2. **Phase 2** — Map each value to our code
3. **Phase 3** — Run ship-by-ship comparisons (automated + manual spot checks)
4. **Phase 4** — Deep-dive each formula where mismatches found
5. **Phase 5** — Classify every discrepancy
6. **Phase 6** — Write exact refactor specs
7. **Phase 7** — Update test suite

---

## Output Deliverables

### A. `erkul_math_audit_report.md`
Complete report with:
- Every value checked and its match status
- Every mismatch with root cause analysis
- Categorized discrepancy list

### B. `erkul_refactor_plan.md`
For each needed fix:
- Issue ID, category, severity
- Current code vs corrected code (exact diff)
- Test case with expected value
- Files affected

### C. Updated `erkul_parity_audit.py`
- New test cases covering all discovered issues
- Tightened tolerances
- New reference ship tests

---

## Rules

- **DO NOT make code changes during this audit** — produce the refactor plan only
- **DO NOT modify cache files**
- **Match Erkul's displayed values** — if Erkul rounds to 1 decimal, we match that rounding
- **If Erkul shows a bug, we replicate it** — document but replicate
- **Every claim must cite a specific ship + weapon + value** as evidence
- **Use the live site** as ground truth, not assumptions about what Erkul "should" do
- **Cross-reference at least 3 ships** before concluding a formula is correct/incorrect
- **Test both SCM and NAV modes** for power-dependent values
