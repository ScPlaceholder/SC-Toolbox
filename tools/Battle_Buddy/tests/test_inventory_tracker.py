"""Tests for core.inventory_tracker — loadout state machine."""

from datetime import datetime, timedelta

import pytest

from core.inventory_parser import InventoryEvent
from core.inventory_tracker import (
    InventoryTracker,
    LoadoutState,
    WeaponSlot,
    PenSlot,
    GrenadeSlot,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

_T0 = datetime(2025, 1, 15, 12, 0, 0)


def _attach(port: str, class_name: str, entity_id: str = "1",
            ts: datetime = _T0) -> InventoryEvent:
    return InventoryEvent("attach", ts, "", data={
        "player": "TestPlayer",
        "entity_name": class_name,
        "class_name": class_name,
        "entity_id": entity_id,
        "status": "persistent",
        "port": port,
    })


def _remove(port: str, class_name: str, entity_id: str = "1",
            ts: datetime = _T0) -> InventoryEvent:
    return InventoryEvent("remove", ts, "", data={
        "player": "TestPlayer",
        "port": port,
        "entity_name": class_name,
        "class_name": class_name,
        "entity_id": entity_id,
    })


def _session_join(ts: datetime = _T0) -> InventoryEvent:
    return InventoryEvent("session_join", ts, "")


# ── Basic state management ───────────────────────────────────────────────────


class TestBasicState:
    def test_session_join_clears_state(self):
        t = InventoryTracker()
        t.on_event(_attach("wep_sidearm", "klwe_pistol_energy_01"))
        assert t.get_state().weapons
        t.on_event(_session_join())
        assert t.get_state().weapons == {}

    def test_weapon_attach_primary_1(self):
        t = InventoryTracker()
        t.on_event(_attach("wep_stocked_1", "behr_rifle_ballistic_01"))
        state = t.get_state()
        assert "primary_1" in state.weapons
        assert state.weapons["primary_1"].display_name == "P4-AR"

    def test_weapon_attach_primary_2(self):
        t = InventoryTracker()
        t.on_event(_attach("wep_stocked_3", "volt_sniper_energy_01"))
        state = t.get_state()
        assert "primary_2" in state.weapons
        assert state.weapons["primary_2"].display_name == "Zenith"

    def test_weapon_attach_sidearm(self):
        t = InventoryTracker()
        t.on_event(_attach("wep_sidearm", "klwe_pistol_energy_01"))
        assert "sidearm" in t.get_state().weapons

    def test_weapon_attach_utility(self):
        t = InventoryTracker()
        t.on_event(_attach("utility_attach_1", "grin_multitool_energy_01"))
        assert "utility" in t.get_state().weapons

    def test_weapon_remove(self):
        t = InventoryTracker()
        t.on_event(_attach("wep_sidearm", "klwe_pistol_energy_01"))
        t.on_event(_remove("wep_sidearm", "klwe_pistol_energy_01"))
        assert "sidearm" not in t.get_state().weapons

    def test_port_stocked_2_maps_to_primary_1(self):
        t = InventoryTracker()
        t.on_event(_attach("wep_stocked_2", "behr_rifle_ballistic_01"))
        assert "primary_1" in t.get_state().weapons

    def test_port_stocked_4_maps_to_primary_2(self):
        t = InventoryTracker()
        t.on_event(_attach("wep_stocked_4", "volt_sniper_energy_01"))
        assert "primary_2" in t.get_state().weapons

    def test_unknown_port_ignored(self):
        t = InventoryTracker()
        t.on_event(_attach("some_random_port", "weapon_class"))
        assert t.get_state().weapons == {}


# ── Pen tracking ─────────────────────────────────────────────────────────────


class TestPenTracking:
    def test_pen_attach(self):
        t = InventoryTracker()
        t.on_event(_attach("medPen_attach_1", "crlf_consumable_healing_01"))
        state = t.get_state()
        assert len(state.pens) == 1
        assert state.medpens == 1

    def test_pen_remove_single(self):
        t = InventoryTracker()
        t.on_event(_attach("medPen_attach_1", "crlf_consumable_healing_01"))
        t.on_event(_remove("medPen_attach_1", "crlf_consumable_healing_01"))
        assert t.get_state().medpens == 0

    def test_oxypen(self):
        t = InventoryTracker()
        t.on_event(_attach("oxyPen_attach_1", "crlf_consumable_oxygen_01"))
        assert t.get_state().oxypens == 1

    def test_pen_categorized_by_class_not_port(self):
        """An oxypen in a medPen slot should still count as oxy."""
        t = InventoryTracker()
        t.on_event(_attach("medPen_attach_1", "crlf_consumable_oxygen_01"))
        state = t.get_state()
        assert state.oxypens == 1
        assert state.medpens == 0


# ── Grenade tracking ─────────────────────────────────────────────────────────


class TestGrenadeTracking:
    def test_grenade_attach(self):
        t = InventoryTracker()
        t.on_event(_attach("grenade_attach_1", "grenade_frag_01", "g1"))
        assert t.get_state().grenade_count == 1

    def test_grenade_remove(self):
        t = InventoryTracker()
        t.on_event(_attach("grenade_attach_1", "grenade_frag_01", "g1"))
        t.on_event(_remove("grenade_attach_1", "grenade_frag_01", "g1"))
        assert t.get_state().grenade_count == 0

    def test_grenade_bare_port(self):
        """grenade_attach (no number) is also valid."""
        t = InventoryTracker()
        t.on_event(_attach("grenade_attach", "grenade_emp_01", "g1"))
        assert t.get_state().grenade_count == 1

    def test_grenades_by_type(self):
        t = InventoryTracker()
        t.on_event(_attach("grenade_attach_1", "grenade_frag_01", "g1"))
        t.on_event(_attach("grenade_attach_2", "grenade_frag_01", "g2"))
        t.on_event(_attach("grenade_attach_3", "grenade_emp_01", "g3"))
        types = t.get_state().grenades_by_type()
        assert types["Frag"] == 2
        assert types["EMP"] == 1


# ── Consumable use detection ─────────────────────────────────────────────────


class TestConsumableUse:
    def test_pen_drawn_to_hand_removes_from_tracking(self):
        t = InventoryTracker()
        t.on_event(_attach("medPen_attach_1", "crlf_consumable_healing_01", "pen1"))
        assert t.get_state().medpens == 1
        # Player draws pen into hand
        t.on_event(_attach("weapon_attach_hand_right", "crlf_consumable_healing_01", "pen1"))
        assert t.get_state().medpens == 0

    def test_grenade_drawn_to_hand_removes_from_tracking(self):
        t = InventoryTracker()
        t.on_event(_attach("grenade_attach_1", "grenade_frag_01", "g1"))
        assert t.get_state().grenade_count == 1
        t.on_event(_attach("weapon_attach_hand_right", "grenade_frag_01", "g1"))
        assert t.get_state().grenade_count == 0

    def test_unrelated_hand_attach_no_effect(self):
        t = InventoryTracker()
        t.on_event(_attach("medPen_attach_1", "crlf_consumable_healing_01", "pen1"))
        # Some other entity in hand
        t.on_event(_attach("weapon_attach_hand_right", "other_item", "other_id"))
        assert t.get_state().medpens == 1


# ── Magazine tracking ────────────────────────────────────────────────────────


class TestMagazineTracking:
    def test_spare_mag_counted(self):
        t = InventoryTracker()
        t.on_event(_attach("wep_stocked_1", "behr_rifle_ballistic_01", "w1"))
        t.on_event(_attach("magazine_attach_1", "behr_rifle_ballistic_01_mag", "m1"))
        t.on_event(_attach("magazine_attach_2", "behr_rifle_ballistic_01_mag", "m2"))
        state = t.get_state()
        assert state.weapons["primary_1"].spare_mags == 2

    def test_reload_consumes_spare(self):
        t = InventoryTracker()
        t.on_event(_attach("wep_stocked_1", "behr_rifle_ballistic_01", "w1"))
        t.on_event(_attach("magazine_attach_1", "behr_rifle_ballistic_01_mag", "m1"))
        assert t.get_state().weapons["primary_1"].spare_mags == 1
        # Reload: same entity loaded into weapon
        t.on_event(_attach("magazine_attach", "behr_rifle_ballistic_01_mag", "m1"))
        assert t.get_state().weapons["primary_1"].spare_mags == 0

    def test_mag_remove(self):
        t = InventoryTracker()
        t.on_event(_attach("wep_stocked_1", "behr_rifle_ballistic_01", "w1"))
        t.on_event(_attach("magazine_attach_1", "behr_rifle_ballistic_01_mag", "m1"))
        t.on_event(_remove("magazine_attach_1", "behr_rifle_ballistic_01_mag", "m1"))
        assert t.get_state().weapons["primary_1"].spare_mags == 0

    def test_mag_matched_with_store_suffix(self):
        """Weapon with _store01 suffix should still match mags."""
        t = InventoryTracker()
        t.on_event(_attach("wep_stocked_1", "volt_sniper_energy_01_store01", "w1"))
        t.on_event(_attach("magazine_attach_1", "volt_sniper_energy_01_mag", "m1"))
        assert t.get_state().weapons["primary_1"].spare_mags == 1


# ── Multitool module ─────────────────────────────────────────────────────────


class TestMultitoolModule:
    def test_module_attaches_to_utility(self):
        t = InventoryTracker()
        t.on_event(_attach("utility_attach_1", "grin_multitool_energy_01", "mt1"))
        t.on_event(_attach("module_attach", "module_tractorbeam_01", "mod1"))
        assert t.get_state().weapons["utility"].module == "Tractor Beam"

    def test_module_ignored_without_utility(self):
        t = InventoryTracker()
        # No utility weapon equipped
        t.on_event(_attach("module_attach", "module_tractorbeam_01", "mod1"))
        assert t.get_state().weapons == {}


# ── Ghost-avatar suppression ─────────────────────────────────────────────────


class TestGhostSuppression:
    def test_sidearm_suppressed_after_body_itemport(self):
        t = InventoryTracker()
        t0 = _T0
        # Equip initial loadout
        t.on_event(_attach("wep_stocked_1", "behr_rifle_ballistic_01", "w1", t0))
        t.on_event(_attach("wep_sidearm", "klwe_pistol_energy_01", "s1", t0))
        # Ghost avatar event
        t1 = t0 + timedelta(seconds=5)
        t.on_event(_attach("Body_ItemPort", "body_class", "b1", t1))
        # Sidearm re-attach within suppression window
        t2 = t1 + timedelta(seconds=1)
        t.on_event(_attach("wep_sidearm", "none_pistol_ballistic_01", "ghost_s", t2))
        # Should still have original sidearm
        assert t.get_state().weapons["sidearm"].class_name == "klwe_pistol_energy_01"

    def test_stocked_weapon_cancels_suppression(self):
        t = InventoryTracker()
        t0 = _T0
        t.on_event(_attach("wep_stocked_1", "behr_rifle_ballistic_01", "w1", t0))
        t.on_event(_attach("wep_sidearm", "klwe_pistol_energy_01", "s1", t0))
        # Ghost event
        t1 = t0 + timedelta(seconds=5)
        t.on_event(_attach("Body_ItemPort", "body_class", "b1", t1))
        # Real loadout — stocked weapon proves real
        t2 = t1 + timedelta(seconds=0.5)
        t.on_event(_attach("wep_stocked_1", "volt_sniper_energy_01", "w2", t2))
        # Now sidearm attach should go through
        t3 = t2 + timedelta(seconds=0.5)
        t.on_event(_attach("wep_sidearm", "lbco_pistol_ballistic_01", "s2", t3))
        assert t.get_state().weapons["sidearm"].class_name == "lbco_pistol_ballistic_01"

    def test_pens_not_suppressed(self):
        t = InventoryTracker()
        t0 = _T0
        t.on_event(_attach("wep_stocked_1", "behr_rifle_ballistic_01", "w1", t0))
        # Ghost event
        t1 = t0 + timedelta(seconds=5)
        t.on_event(_attach("Body_ItemPort", "body_class", "b1", t1))
        # Pen attach within suppression window — should NOT be suppressed
        t2 = t1 + timedelta(seconds=1)
        t.on_event(_attach("medPen_attach_1", "crlf_consumable_healing_01", "p1", t2))
        assert t.get_state().medpens == 1


# ── Armor-swap detection ─────────────────────────────────────────────────────


class TestArmorSwap:
    def test_three_pen_removals_within_window_clears_all(self):
        t = InventoryTracker()
        t0 = _T0
        # Equip 4 pens
        for i, port in enumerate(["medPen_attach_1", "medPen_attach_2",
                                    "oxyPen_attach_1", "oxyPen_attach_2"]):
            t.on_event(_attach(port, "crlf_consumable_healing_01", f"p{i}", t0))
        assert len(t.get_state().pens) == 4
        # Rapid removal (armor swap) — 3 within 6 seconds
        for i, port in enumerate(["medPen_attach_1", "medPen_attach_2", "oxyPen_attach_1"]):
            ts = t0 + timedelta(seconds=1 + i)
            t.on_event(_remove(port, "crlf_consumable_healing_01", f"p{i}", ts))
        # All pens should be cleared (armor swap detected)
        assert len(t.get_state().pens) == 0

    def test_two_removals_is_individual(self):
        t = InventoryTracker()
        t0 = _T0
        for i, port in enumerate(["medPen_attach_1", "medPen_attach_2",
                                    "oxyPen_attach_1", "oxyPen_attach_2"]):
            t.on_event(_attach(port, "crlf_consumable_healing_01", f"p{i}", t0))
        # Only 2 removals — individual, not armor swap
        t.on_event(_remove("medPen_attach_1", "crlf_consumable_healing_01", "p0",
                           t0 + timedelta(seconds=1)))
        t.on_event(_remove("medPen_attach_2", "crlf_consumable_healing_01", "p1",
                           t0 + timedelta(seconds=2)))
        # Should still have 2 pens (the oxy ones)
        assert len(t.get_state().pens) == 2


# ── State callbacks ──────────────────────────────────────────────────────────


class TestStateCallbacks:
    def test_on_changed_fires(self):
        t = InventoryTracker()
        states = []
        t.on_changed(states.append)
        t.on_event(_attach("wep_sidearm", "klwe_pistol_energy_01"))
        assert len(states) == 1
        assert "sidearm" in states[0].weapons

    def test_snapshot_is_deep_copy(self):
        t = InventoryTracker()
        states = []
        t.on_changed(states.append)
        t.on_event(_attach("wep_sidearm", "klwe_pistol_energy_01"))
        snap = states[0]
        # Mutating snapshot should not affect tracker
        snap.weapons.clear()
        assert "sidearm" in t.get_state().weapons

    def test_session_join_fires_callback(self):
        t = InventoryTracker()
        states = []
        t.on_changed(states.append)
        t.on_event(_session_join())
        assert len(states) == 1
        assert states[0].weapons == {}


# ── LoadoutState convenience accessors ───────────────────────────────────────


class TestLoadoutStateAccessors:
    def test_pens_by_category(self):
        state = LoadoutState(pens={
            "medPen_attach_1": PenSlot("medPen_attach_1", "cls", "MedPen", "med", "1"),
            "oxyPen_attach_1": PenSlot("oxyPen_attach_1", "cls", "OxyPen", "oxy", "2"),
            "medPen_attach_2": PenSlot("medPen_attach_2", "cls", "MedPen", "med", "3"),
        })
        by_cat = state.pens_by_category()
        assert len(by_cat["med"]) == 2
        assert len(by_cat["oxy"]) == 1

    def test_grenades_by_type(self):
        state = LoadoutState(grenades={
            "g1": GrenadeSlot("g1", "cls", "Frag", "1"),
            "g2": GrenadeSlot("g2", "cls", "Frag", "2"),
            "g3": GrenadeSlot("g3", "cls", "EMP", "3"),
        })
        types = state.grenades_by_type()
        assert types == {"Frag": 2, "EMP": 1}
