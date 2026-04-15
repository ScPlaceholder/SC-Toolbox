"""Unit tests for services.breakability — core mining math.

Covers the fundamental physics formulas, modifier stacking, subset
search, greedy fallback, charge-decay simulation, and edge cases
(zero mass, 100% resistance, infinite power, empty laser lists).
"""

from __future__ import annotations

import math
import sys
import os
import unittest

# Ensure the Mining_Signals package root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.breakability import (
    C_MASS,
    effective_resistance,
    mass_at_resistance,
    required_power,
    combine_resistance_modifiers,
    combine_power,
    power_percentage,
    breakability_curve,
    combined_curve,
    compute_charge_profile,
    compute_with_active_modules,
    LaserConfig,
    BreakResult,
    ChargeProfile,
    DECAY_COEFF,
    CAPACITY_COEFF,
)


class TestEffectiveResistance(unittest.TestCase):
    """effective_resistance(resistance_pct, modifier) -> 0..1"""

    def test_zero_resistance(self):
        self.assertAlmostEqual(effective_resistance(0, 1.0), 0.0)

    def test_full_resistance(self):
        self.assertAlmostEqual(effective_resistance(100, 1.0), 1.0)

    def test_half_resistance(self):
        self.assertAlmostEqual(effective_resistance(50, 1.0), 0.5)

    def test_modifier_reduces(self):
        # -25% modifier (0.75 multiplier) on 80% base = 0.6
        self.assertAlmostEqual(effective_resistance(80, 0.75), 0.6)

    def test_modifier_increases(self):
        # +50% modifier on 60% base = 0.9
        self.assertAlmostEqual(effective_resistance(60, 1.5), 0.9)

    def test_clamped_above_1(self):
        # A modifier that would push above 1.0 gets clamped
        result = effective_resistance(100, 1.5)
        self.assertAlmostEqual(result, 1.0)

    def test_clamped_below_0(self):
        # Negative modifier on zero resistance stays 0
        result = effective_resistance(0, -0.5)
        self.assertAlmostEqual(result, 0.0)


class TestMassAtResistance(unittest.TestCase):
    """mass_at_resistance(power, resistance_pct, resistance_modifier)"""

    def test_zero_resistance(self):
        # power * (1 - 0) / C_MASS = power / 0.2 = power * 5
        self.assertAlmostEqual(mass_at_resistance(10.0, 0.0), 50.0)

    def test_50pct_resistance(self):
        # 10 * (1 - 0.5) / 0.2 = 10 * 0.5 / 0.2 = 25
        self.assertAlmostEqual(mass_at_resistance(10.0, 50.0), 25.0)

    def test_100pct_resistance(self):
        # (1 - 1.0) = 0 -> mass = 0
        self.assertAlmostEqual(mass_at_resistance(10.0, 100.0), 0.0)

    def test_with_modifier(self):
        # 10 * (1 - 0.6) / 0.2 = 10 * 0.4 / 0.2 = 20
        result = mass_at_resistance(10.0, 80.0, 0.75)
        self.assertAlmostEqual(result, 20.0)


class TestRequiredPower(unittest.TestCase):
    """required_power(mass, resistance_pct, resistance_modifier)"""

    def test_zero_resistance(self):
        # (mass * C_MASS) / (1 - 0) = mass * 0.2
        self.assertAlmostEqual(required_power(50.0, 0.0), 10.0)

    def test_50pct_resistance(self):
        # (50 * 0.2) / (1 - 0.5) = 10 / 0.5 = 20
        self.assertAlmostEqual(required_power(50.0, 50.0), 20.0)

    def test_100pct_resistance_is_infinite(self):
        result = required_power(50.0, 100.0)
        self.assertTrue(math.isinf(result))

    def test_inverse_of_mass_at_resistance(self):
        # These should be inverses of each other
        power = 15.0
        res = 30.0
        mass = mass_at_resistance(power, res)
        recovered_power = required_power(mass, res)
        self.assertAlmostEqual(recovered_power, power, places=6)

    def test_zero_mass_zero_power(self):
        self.assertAlmostEqual(required_power(0.0, 50.0), 0.0)


class TestCombineResistanceModifiers(unittest.TestCase):
    """combine_resistance_modifiers(*pcts) -> multiplier"""

    def test_no_modifiers(self):
        self.assertAlmostEqual(combine_resistance_modifiers(), 1.0)

    def test_single_negative(self):
        # -25% -> 0.75
        self.assertAlmostEqual(combine_resistance_modifiers(-25), 0.75)

    def test_single_positive(self):
        # +10% -> 1.10
        self.assertAlmostEqual(combine_resistance_modifiers(10), 1.10)

    def test_stacking(self):
        # -25% and -10% -> 0.75 * 0.90 = 0.675
        self.assertAlmostEqual(combine_resistance_modifiers(-25, -10), 0.675)

    def test_triple_stack(self):
        result = combine_resistance_modifiers(-20, -15, -10)
        expected = 0.80 * 0.85 * 0.90
        self.assertAlmostEqual(result, expected, places=6)


class TestCombinePower(unittest.TestCase):
    """combine_power(*power_factors_pct) -> multiplier"""

    def test_single_100(self):
        # 100% = 1.0x (no change)
        self.assertAlmostEqual(combine_power(100), 1.0)

    def test_single_110(self):
        # 110% = 1.1x
        self.assertAlmostEqual(combine_power(110), 1.1)

    def test_stacking(self):
        # 110 * 120 = 1.1 * 1.2 = 1.32
        self.assertAlmostEqual(combine_power(110, 120), 1.32)


class TestPowerPercentage(unittest.TestCase):
    """power_percentage(mass, resistance_pct, lasers) -> BreakResult"""

    def _laser(self, name: str, power: float, rmod: float = 1.0) -> LaserConfig:
        return LaserConfig(name=name, max_power=power, resistance_modifier=rmod)

    def test_empty_lasers(self):
        result = power_percentage(1000, 0, [])
        self.assertTrue(result.insufficient)
        self.assertEqual(result.used_lasers, [])

    def test_single_laser_easy(self):
        # Need: 1000 * 0.2 / 1.0 = 200 power. Laser has 400 -> 50%
        laser = self._laser("A", 400.0)
        result = power_percentage(1000.0, 0.0, [laser])
        self.assertFalse(result.insufficient)
        self.assertAlmostEqual(result.percentage, 50.0)
        self.assertEqual(result.used_lasers, ["A"])

    def test_single_laser_insufficient(self):
        # Need 200 power, only have 100
        laser = self._laser("A", 100.0)
        result = power_percentage(1000.0, 0.0, [laser])
        self.assertTrue(result.insufficient)

    def test_two_lasers_picks_minimal_subset(self):
        # Need 200 power. A=250 (enough alone), B=100 (not alone)
        a = self._laser("A", 250.0)
        b = self._laser("B", 100.0)
        result = power_percentage(1000.0, 0.0, [a, b])
        self.assertFalse(result.insufficient)
        # Should pick A alone (fewer lasers)
        self.assertEqual(result.used_lasers, ["A"])

    def test_two_lasers_both_needed(self):
        # Need 200 power. A=150, B=150 — both needed
        a = self._laser("A", 150.0)
        b = self._laser("B", 150.0)
        result = power_percentage(1000.0, 0.0, [a, b])
        self.assertFalse(result.insufficient)
        self.assertEqual(len(result.used_lasers), 2)

    def test_resistance_modifier_helps(self):
        # Rock: mass=5000, resistance=80%
        # Without modifier: need 5000*0.2/(1-0.8) = 5000 power
        # With -50% rmod: eff_res = 0.8*0.5 = 0.4, need 5000*0.2/0.6 = 1666.7
        laser = self._laser("A", 2000.0, rmod=0.5)
        result = power_percentage(5000.0, 80.0, [laser])
        self.assertFalse(result.insufficient)

    def test_unbreakable_rock(self):
        # 100% resistance with modifier >= 1.0 -> unbreakable
        laser = self._laser("A", 9999.0, rmod=1.0)
        result = power_percentage(1000.0, 100.0, [laser])
        self.assertTrue(result.insufficient)
        self.assertTrue(result.unbreakable)

    def test_greedy_fallback_large_fleet(self):
        # 13 lasers triggers greedy path (n > 12)
        lasers = [self._laser(f"L{i}", 20.0) for i in range(13)]
        # Need: 500 * 0.2 = 100 power total
        result = power_percentage(500.0, 0.0, lasers)
        self.assertFalse(result.insufficient)
        # Greedy adds strongest first — should use <=5 lasers
        self.assertLessEqual(len(result.used_lasers), 6)


class TestBreakabilityCurve(unittest.TestCase):
    """breakability_curve and combined_curve"""

    def test_curve_starts_at_zero(self):
        pts = breakability_curve(10.0, 1.0, step=10.0)
        # First point: resistance=0%, mass = 10/0.2 = 50
        self.assertAlmostEqual(pts[0][0], 0.0)
        self.assertAlmostEqual(pts[0][1], 50.0)

    def test_curve_ends_at_zero_mass(self):
        pts = breakability_curve(10.0, 1.0, step=10.0)
        # Last point: resistance=100%, mass = 0
        last = pts[-1]
        self.assertAlmostEqual(last[1], 0.0, places=3)

    def test_curve_monotonically_decreasing(self):
        pts = breakability_curve(10.0, 1.0, step=5.0)
        masses = [m for _, m in pts]
        for i in range(1, len(masses)):
            self.assertLessEqual(masses[i], masses[i - 1] + 1e-9)

    def test_combined_curve_empty(self):
        result = combined_curve([])
        self.assertEqual(result, [])

    def test_combined_curve_single(self):
        laser = LaserConfig(name="A", max_power=10.0, visible=True)
        pts = combined_curve([laser], step=50.0)
        self.assertGreater(len(pts), 0)

    def test_combined_curve_ignores_invisible(self):
        vis = LaserConfig(name="A", max_power=10.0, visible=True)
        invis = LaserConfig(name="B", max_power=100.0, visible=False)
        pts_both = combined_curve([vis, invis], step=50.0)
        pts_one = combined_curve([vis], step=50.0)
        # Same result since B is invisible
        self.assertEqual(len(pts_both), len(pts_one))
        for a, b in zip(pts_both, pts_one):
            self.assertAlmostEqual(a[1], b[1])


class TestComputeChargeProfile(unittest.TestCase):
    """compute_charge_profile — charge-decay timing simulation"""

    def test_zero_mass_returns_none(self):
        self.assertIsNone(compute_charge_profile(0, 0, 100, 1.0))

    def test_zero_power_returns_none(self):
        self.assertIsNone(compute_charge_profile(1000, 0, 0, 1.0))

    def test_basic_profile(self):
        # mass=1000, resistance=0%, power=100, rmod=1.0
        # decay = 1000 * 0.02 = 20
        # max_input = 100 * (1-0) = 100
        # min_throttle = 20/100 = 0.20 = 20%
        # net_energy = 100 - 20 = 80
        profile = compute_charge_profile(1000, 0, 100, 1.0)
        self.assertIsNotNone(profile)
        self.assertAlmostEqual(profile.decay_rate, 20.0)
        self.assertAlmostEqual(profile.net_energy_max, 80.0)
        self.assertAlmostEqual(profile.min_throttle_pct, 20.0)
        self.assertGreater(profile.est_total_time_sec, 0)

    def test_decay_exceeds_power(self):
        # mass=10000 -> decay=200, power=50 -> max_input=50
        # net_energy = 50 - 200 = -150 (can't overcome decay)
        profile = compute_charge_profile(10000, 0, 50, 1.0)
        self.assertIsNotNone(profile)
        self.assertTrue(math.isinf(profile.time_to_window_sec))

    def test_high_resistance_reduces_input(self):
        # 80% resistance: max_input = 100 * (1-0.8) = 20
        profile = compute_charge_profile(500, 80, 100, 1.0)
        self.assertIsNotNone(profile)
        # decay = 500*0.02 = 10, max_input = 20, net = 10
        self.assertAlmostEqual(profile.net_energy_max, 10.0)


class TestComputeWithActiveModules(unittest.TestCase):
    """compute_with_active_modules — passive then active fallback"""

    def test_passive_sufficient(self):
        laser = LaserConfig(
            name="A", max_power=500.0,
            max_power_active=1000.0, active_module_uses=3,
            active_uses_remaining=3,
        )
        result = compute_with_active_modules(1000, 0, [laser])
        self.assertFalse(result.insufficient)
        self.assertEqual(result.active_modules_needed, 0)

    def test_active_needed(self):
        laser = LaserConfig(
            name="A", max_power=100.0,
            max_power_active=500.0, active_module_uses=3,
            active_uses_remaining=3,
            resistance_modifier_active=1.0,
        )
        # Need 200 power. Passive has 100 (insufficient), active has 500 (sufficient)
        result = compute_with_active_modules(1000, 0, [laser])
        self.assertFalse(result.insufficient)
        self.assertEqual(result.active_modules_needed, 1)

    def test_active_depleted(self):
        laser = LaserConfig(
            name="A", max_power=100.0,
            max_power_active=500.0, active_module_uses=3,
            active_uses_remaining=0,  # depleted
        )
        result = compute_with_active_modules(1000, 0, [laser])
        self.assertTrue(result.insufficient)


if __name__ == "__main__":
    unittest.main()
