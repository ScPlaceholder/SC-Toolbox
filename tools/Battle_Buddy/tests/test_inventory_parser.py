"""Tests for core.inventory_parser — classification functions and log line parsing."""

from datetime import datetime

import pytest

from core.inventory_parser import (
    InventoryEvent,
    InventoryParser,
    base_class,
    classify_ammo,
    classify_grenade,
    classify_module,
    classify_pen,
    classify_weapon_type,
    pretty_weapon_name,
)


# ── base_class ───────────────────────────────────────────────────────────────


class TestBaseClass:
    def test_strips_store_suffix(self):
        assert base_class("volt_sniper_energy_01_store01") == "volt_sniper_energy_01"

    def test_strips_black_suffix(self):
        assert base_class("behr_rifle_ballistic_01_black") == "behr_rifle_ballistic_01"

    def test_strips_tint_suffix(self):
        assert base_class("ksar_pistol_ballistic_01_tint02") == "ksar_pistol_ballistic_01"

    def test_strips_iae_suffix(self):
        assert base_class("klwe_rifle_energy_01_iae2953") == "klwe_rifle_energy_01"

    def test_strips_color_suffix(self):
        for color in ("grey", "yellow", "blue", "red", "green"):
            result = base_class(f"some_weapon_01_{color}")
            assert result == "some_weapon_01", f"Failed for suffix _{color}"

    def test_no_op_on_clean_name(self):
        assert base_class("none_pistol_ballistic_01") == "none_pistol_ballistic_01"

    def test_no_op_on_empty(self):
        assert base_class("") == ""


# ── classify_ammo ────────────────────────────────────────────────────────────


class TestClassifyAmmo:
    def test_energy(self):
        assert classify_ammo("klwe_pistol_energy_01") == "energy"

    def test_ballistic(self):
        assert classify_ammo("behr_rifle_ballistic_01") == "ballistic"

    def test_distortion(self):
        assert classify_ammo("some_distortion_weapon") == "distortion"

    def test_unknown(self):
        assert classify_ammo("grin_multitool_01") == "unknown"

    def test_case_insensitive(self):
        assert classify_ammo("KLWE_PISTOL_ENERGY_01") == "energy"


# ── classify_weapon_type ─────────────────────────────────────────────────────


class TestClassifyWeaponType:
    @pytest.mark.parametrize("keyword,expected", [
        ("pistol", "Pistol"),
        ("smg", "SMG"),
        ("rifle", "Rifle"),
        ("shotgun", "Shotgun"),
        ("sniper", "Sniper"),
        ("lmg", "LMG"),
        ("carbine", "Carbine"),
        ("launcher", "Launcher"),
        ("multitool", "Multitool"),
        ("tractor", "Tractor"),
        ("medgun", "Medgun"),
        ("repair", "Repair Tool"),
    ])
    def test_known_types(self, keyword, expected):
        assert classify_weapon_type(f"mfr_{keyword}_energy_01") == expected

    def test_fallback(self):
        assert classify_weapon_type("unknown_thing_01") == "Weapon"


# ── classify_grenade ─────────────────────────────────────────────────────────


class TestClassifyGrenade:
    @pytest.mark.parametrize("keyword,expected", [
        ("frag", "Frag"),
        ("emp", "EMP"),
        ("concussion", "Concussion"),
        ("flashbang", "Flashbang"),
        ("shutter", "Flashbang"),
        ("smoke", "Smoke"),
        ("haze", "Smoke"),
        ("force", "Force"),
        ("mine", "Mine"),
        ("lidar", "LIDAR"),
        ("radar", "Radar Scatter"),
        ("glowstick", "Glowstick"),
        ("flare", "Flare"),
    ])
    def test_known_types(self, keyword, expected):
        assert classify_grenade(f"grenade_{keyword}_01") == expected

    def test_fallback(self):
        assert classify_grenade("unknown_throwable_01") == "Grenade"


# ── classify_pen ─────────────────────────────────────────────────────────────


class TestClassifyPen:
    def test_known_medpen(self):
        assert classify_pen("crlf_consumable_healing_01") == ("MedPen", "med")

    def test_known_oxypen(self):
        assert classify_pen("crlf_consumable_oxygen_01") == ("OxyPen", "oxy")

    def test_known_adrenapen(self):
        assert classify_pen("crlf_consumable_adrenaline_01") == ("AdrenaPen", "stim")

    def test_known_detoxpen(self):
        assert classify_pen("crlf_consumable_overdoserevival_01") == ("DetoxPen", "detox")

    def test_known_drema(self):
        assert classify_pen("rrs_consumable_sedative_01") == ("Drema", "other")

    def test_fallback_healing_keyword(self):
        assert classify_pen("new_healing_pen_99") == ("MedPen", "med")

    def test_fallback_oxygen_keyword(self):
        assert classify_pen("new_oxygen_pen_99") == ("OxyPen", "oxy")

    def test_fallback_adrenaline_keyword(self):
        assert classify_pen("new_adrenaline_pen_99") == ("AdrenaPen", "stim")

    def test_fallback_unknown(self):
        assert classify_pen("totally_unknown_consumable") == ("Pen", "other")


# ── classify_module ──────────────────────────────────────────────────────────


class TestClassifyModule:
    @pytest.mark.parametrize("keyword,expected", [
        ("tractorbeam", "Tractor Beam"),
        ("heal", "Healing"),
        ("mining", "Mining"),
        ("salvage", "Salvage"),
        ("repair", "Repair"),
        ("cutting", "Cutting"),
    ])
    def test_known_modules(self, keyword, expected):
        assert classify_module(f"module_{keyword}_01") == expected

    def test_fallback(self):
        assert classify_module("module_unknown_01") == "Module"


# ── pretty_weapon_name ───────────────────────────────────────────────────────


class TestPrettyWeaponName:
    def test_known_weapon_exact(self):
        assert pretty_weapon_name("klwe_pistol_energy_01") == "Arclight"

    def test_known_weapon_with_store_suffix(self):
        assert pretty_weapon_name("volt_sniper_energy_01_store01") == "Zenith"

    def test_fallback_strips_ammo_and_manufacturer(self):
        name = pretty_weapon_name("acme_pistol_ballistic_99")
        assert "ballistic" not in name.lower()
        assert "none" not in name.lower()
        assert "Acme" in name or "Pistol" in name

    def test_multitool(self):
        assert pretty_weapon_name("grin_multitool_energy_01") == "Multitool"


# ── InventoryParser — session events ─────────────────────────────────────────


class TestParserSessionEvents:
    def _collect(self, parser, line):
        events = []
        parser.subscribe(events.append)
        parser.on_line(line)
        return events

    def test_session_join(self):
        p = InventoryParser()
        events = self._collect(p, "<2025-01-15T12:00:00> {Join PU} Loading...")
        assert len(events) == 1
        assert events[0].event_type == "session_join"
        assert events[0].timestamp == datetime(2025, 1, 15, 12, 0, 0)

    def test_session_end_system_quit(self):
        p = InventoryParser()
        events = self._collect(p, "<2025-01-15T12:30:00> SystemQuit shutting down")
        assert len(events) == 1
        assert events[0].event_type == "session_end"

    def test_session_end_disconnect_stanton(self):
        p = InventoryParser()
        events = self._collect(p, "<2025-01-15T12:30:00> Disconnecting from Stanton")
        assert len(events) == 1
        assert events[0].event_type == "session_end"

    def test_session_end_disconnect_pyro(self):
        p = InventoryParser()
        events = self._collect(p, "<2025-01-15T12:30:00> Disconnecting from Pyro")
        assert len(events) == 1
        assert events[0].event_type == "session_end"

    def test_unrelated_line_ignored(self):
        p = InventoryParser()
        events = self._collect(p, "<2025-01-15T12:00:00> Loading terrain chunk 42")
        assert events == []


# ── InventoryParser — attach events ──────────────────────────────────────────

_ATTACH_LINE = (
    "<2025-01-15T12:05:00> <AttachmentReceived> Player[TestPlayer] "
    "Attachment[LN86_Pistol, klwe_pistol_energy_01, 12345] "
    "Status[persistent] Port[wep_sidearm]"
)

_ATTACH_LOCAL = (
    "<2025-01-15T12:05:00> <AttachmentReceived> Player[TestPlayer] "
    "Attachment[Default, Inventory_LocalAttach_Item, 99999] "
    "Status[local] Port[wep_sidearm]"
)


class TestParserAttachEvents:
    def _collect(self, parser, line):
        events = []
        parser.subscribe(events.append)
        parser.on_line(line)
        return events

    def test_attach_parsed(self):
        p = InventoryParser()
        events = self._collect(p, _ATTACH_LINE)
        assert len(events) == 1
        e = events[0]
        assert e.event_type == "attach"
        assert e.data["player"] == "TestPlayer"
        assert e.data["class_name"] == "klwe_pistol_energy_01"
        assert e.data["entity_id"] == "12345"
        assert e.data["port"] == "wep_sidearm"
        assert e.data["status"] == "persistent"

    def test_local_status_filtered(self):
        p = InventoryParser()
        events = self._collect(p, _ATTACH_LOCAL)
        assert events == []

    def test_player_filter_match(self):
        p = InventoryParser(player_name="TestPlayer")
        events = self._collect(p, _ATTACH_LINE)
        assert len(events) == 1

    def test_player_filter_mismatch(self):
        p = InventoryParser(player_name="OtherPlayer")
        events = self._collect(p, _ATTACH_LINE)
        assert events == []


# ── InventoryParser — remove events ──────────────────────────────────────────

_REMOVE_LINE = (
    "<2025-01-15T12:10:00> Player[TestPlayer] in port[Body_ItemPort:medPen_attach_1] "
    "has been removed before timer. Persistent Entity[MedPen, crlf_consumable_healing_01]"
)

_REMOVE_LOCAL_NULL = (
    "<2025-01-15T12:10:00> Player[TestPlayer] Local attached entity[null, null] "
    "in port[wep_sidearm] has been removed before timer. Persistent Entity[Gun, gun_class]"
)


class TestParserRemoveEvents:
    def _collect(self, parser, line):
        events = []
        parser.subscribe(events.append)
        parser.on_line(line)
        return events

    def test_remove_parsed(self):
        p = InventoryParser()
        events = self._collect(p, _REMOVE_LINE)
        assert len(events) == 1
        e = events[0]
        assert e.event_type == "remove"
        assert e.data["port"] == "medPen_attach_1"
        assert e.data["class_name"] == "crlf_consumable_healing_01"

    def test_local_null_entity_filtered(self):
        p = InventoryParser()
        events = self._collect(p, _REMOVE_LOCAL_NULL)
        assert events == []

    def test_player_filter_mismatch(self):
        p = InventoryParser(player_name="OtherPlayer")
        events = self._collect(p, _REMOVE_LINE)
        assert events == []


# ── InventoryParser — callback error handling ────────────────────────────────


class TestParserCallbackErrors:
    def test_callback_error_does_not_prevent_other_callbacks(self):
        p = InventoryParser()
        results = []

        def bad_cb(event):
            raise RuntimeError("boom")

        def good_cb(event):
            results.append(event)

        p.subscribe(bad_cb)
        p.subscribe(good_cb)
        p.on_line("<2025-01-15T12:00:00> {Join PU}")
        assert len(results) == 1
