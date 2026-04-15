"""Rank Path Planner modal — plan faction reputation progression (PySide6)."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QScrollArea, QComboBox,
)

from shared.qt.theme import P
from ui.modals.base import ModalBase

log = logging.getLogger(__name__)


_COMBO_QSS = f"""
    QComboBox {{
        background-color: {P.bg_card};
        color: {P.fg};
        border: 1px solid {P.border};
        font-family: Consolas;
        font-size: 9pt;
        padding: 3px 8px;
    }}
    QComboBox:hover {{ border-color: {P.accent}; }}
    QComboBox::drop-down {{
        border: none;
        width: 20px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {P.bg_secondary};
        color: {P.fg};
        selection-background-color: {P.accent};
        selection-color: {P.bg_primary};
        border: 1px solid {P.border};
        font-family: Consolas;
        font-size: 9pt;
    }}
"""


def _clean_rank_name(name: str) -> str:
    if name.startswith("@"):
        return name.split("_")[-1] if "_" in name else name
    return name


def _make_combo() -> QComboBox:
    cb = QComboBox()
    cb.setStyleSheet(_COMBO_QSS)
    cb.setCursor(Qt.PointingHandCursor)
    return cb


class RankPathPlannerModal(ModalBase):
    """Modal for planning rank progression through a faction."""

    def __init__(self, parent, data_mgr):
        self._data = data_mgr
        self._faction_guid = ""
        self._scope_guid = ""
        self._ranks = []
        self._system_filter = None
        self._sys_btns: dict[str, QPushButton] = {}
        self._sys_container: QWidget | None = None
        super().__init__(parent, title="Rank Path Planner", width=700, height=620)
        self._build_ui()
        self.show()

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self):
        layout = self.body_layout

        # Experimental banner
        banner = QLabel("EXPERIMENTAL FEATURE")
        banner.setAlignment(Qt.AlignCenter)
        banner.setFixedHeight(28)
        banner.setStyleSheet(
            f"background: rgba(255, 170, 34, 0.15); color: #ffaa22; "
            f"font-family: Consolas; font-size: 8pt; font-weight: bold; "
            f"letter-spacing: 2px;"
        )
        layout.addWidget(banner)

        # Controls area
        ctrl = QWidget()
        ctrl.setStyleSheet(f"background-color: {P.bg_secondary};")
        cl = QVBoxLayout(ctrl)
        cl.setContentsMargins(16, 10, 16, 10)
        cl.setSpacing(6)

        # Row 1: Faction
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        lbl = QLabel("FACTION")
        lbl.setFixedWidth(60)
        lbl.setStyleSheet(self._label_qss())
        r1.addWidget(lbl)
        self._faction_combo = _make_combo()
        self._faction_combo.addItem("Select faction...")
        for name in self._data.all_faction_names:
            self._faction_combo.addItem(name)
        self._faction_combo.currentIndexChanged.connect(self._on_faction_changed)
        r1.addWidget(self._faction_combo, 1)
        cl.addLayout(r1)

        # Row 2: System
        r2 = QHBoxLayout()
        r2.setSpacing(8)
        lbl2 = QLabel("SYSTEM")
        lbl2.setFixedWidth(60)
        lbl2.setStyleSheet(self._label_qss())
        r2.addWidget(lbl2)
        self._sys_container = QWidget()
        self._sys_container.setStyleSheet("background: transparent;")
        self._sys_layout = QHBoxLayout(self._sys_container)
        self._sys_layout.setContentsMargins(0, 0, 0, 0)
        self._sys_layout.setSpacing(4)
        self._build_system_buttons([])
        r2.addWidget(self._sys_container, 1)
        cl.addLayout(r2)

        # Row 3: FROM / TO
        r3 = QHBoxLayout()
        r3.setSpacing(8)
        lbl3 = QLabel("FROM")
        lbl3.setFixedWidth(60)
        lbl3.setStyleSheet(self._label_qss())
        r3.addWidget(lbl3)
        self._from_combo = _make_combo()
        self._from_combo.currentIndexChanged.connect(lambda _: self._recalculate())
        r3.addWidget(self._from_combo, 1)

        lbl4 = QLabel("TO")
        lbl4.setFixedWidth(30)
        lbl4.setStyleSheet(self._label_qss())
        r3.addWidget(lbl4)
        self._to_combo = _make_combo()
        self._to_combo.currentIndexChanged.connect(lambda _: self._recalculate())
        r3.addWidget(self._to_combo, 1)
        cl.addLayout(r3)

        layout.addWidget(ctrl)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {P.border};")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # Scrollable results area
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._results_widget = QWidget()
        self._results_widget.setStyleSheet("background: transparent;")
        self._results_layout = QVBoxLayout(self._results_widget)
        self._results_layout.setContentsMargins(16, 8, 16, 16)
        self._results_layout.setSpacing(6)
        self._results_layout.addStretch(1)
        self._scroll.setWidget(self._results_widget)
        layout.addWidget(self._scroll, 1)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _label_qss() -> str:
        return (
            f"font-family: Consolas; font-size: 9pt; font-weight: bold; "
            f"color: {P.fg_dim}; background: transparent;"
        )

    def _build_system_buttons(self, systems: list[str]):
        """Rebuild system toggle buttons."""
        # Clear existing
        while self._sys_layout.count():
            item = self._sys_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._sys_btns.clear()

        all_btn = QPushButton("All")
        all_btn.setCursor(Qt.PointingHandCursor)
        all_btn.setStyleSheet(self._sys_btn_qss(True))
        all_btn.clicked.connect(lambda: self._select_system(None))
        self._sys_layout.addWidget(all_btn)
        self._sys_btns["All"] = all_btn

        for s in systems:
            btn = QPushButton(s)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._sys_btn_qss(False))
            btn.clicked.connect(lambda checked=False, sys=s: self._select_system(sys))
            self._sys_layout.addWidget(btn)
            self._sys_btns[s] = btn

        self._sys_layout.addStretch(1)

    def _sys_btn_qss(self, active: bool) -> str:
        return (
            f"QPushButton {{ background: {'#1a3030' if active else P.bg_card}; "
            f"color: {P.accent if active else P.fg_dim}; border: none; "
            f"font-family: Consolas; font-size: 8pt; padding: 2px 8px; }}"
            f"QPushButton:hover {{ color: {P.fg}; }}"
        )

    def _select_system(self, system: str | None):
        self._system_filter = system
        for key, btn in self._sys_btns.items():
            is_active = (system is None and key == "All") or (system == key)
            btn.setStyleSheet(self._sys_btn_qss(is_active))
        self._recalculate()

    # ── Faction selection ─────────────────────────────────────────────────

    def _on_faction_changed(self, index: int):
        if index <= 0:
            return
        faction_name = self._faction_combo.currentText()
        from services.rank_planner import get_faction_scope, get_faction_systems

        # Find faction GUID
        guid = ""
        for g, f in self._data.factions.items():
            if f.get("name") == faction_name:
                guid = g
                break
        if not guid:
            return

        self._faction_guid = guid
        self._scope_guid = get_faction_scope(
            guid, self._data.contracts, self._data.faction_rewards_pools
        )

        # Rebuild system buttons
        systems = get_faction_systems(guid, self._data.contracts)
        self._build_system_buttons(systems)
        self._system_filter = None

        # Populate rank dropdowns
        self._ranks = []
        if self._scope_guid and self._scope_guid in self._data.scopes:
            scope = self._data.scopes[self._scope_guid]
            self._ranks = sorted(
                scope.get("ranks", []), key=lambda r: r.get("rankIndex", 0)
            )

        rank_names = [_clean_rank_name(r.get("name", "?")) for r in self._ranks]

        self._from_combo.blockSignals(True)
        self._to_combo.blockSignals(True)
        self._from_combo.clear()
        self._to_combo.clear()
        for name in rank_names:
            self._from_combo.addItem(name)
            self._to_combo.addItem(name)

        # Default: first rank → last rank
        if rank_names:
            self._from_combo.setCurrentIndex(0)
            self._to_combo.setCurrentIndex(len(rank_names) - 1)
        self._from_combo.blockSignals(False)
        self._to_combo.blockSignals(False)

        self._recalculate()

    # ── Calculation & display ─────────────────────────────────────────────

    def _recalculate(self):
        from services.rank_planner import compute_rank_path

        if not self._faction_guid or not self._scope_guid or not self._ranks:
            self._clear_results()
            return

        # Resolve rank indices from combo text
        from_text = self._from_combo.currentText()
        to_text = self._to_combo.currentText()

        from_idx = self._rank_index_for_name(from_text)
        to_idx = self._rank_index_for_name(to_text)

        if from_idx is None or to_idx is None or from_idx >= to_idx:
            self._clear_results()
            return

        result = compute_rank_path(
            faction_guid=self._faction_guid,
            scope_guid=self._scope_guid,
            from_rank_index=from_idx,
            to_rank_index=to_idx,
            system_filter=self._system_filter,
            contracts=self._data.contracts,
            faction_rewards_pools=self._data.faction_rewards_pools,
            availability_pools=self._data.availability_pools,
            scopes=self._data.scopes,
        )

        self._display_results(result)

    def _rank_index_for_name(self, name: str):
        for r in self._ranks:
            if _clean_rank_name(r.get("name", "")) == name:
                return r.get("rankIndex", 0)
        return None

    def _clear_results(self):
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._results_layout.addStretch(1)

    def _display_results(self, result):
        self._clear_results()

        if not result.steps:
            info = QLabel("No path to display. Select valid FROM and TO ranks.")
            info.setStyleSheet(
                f"font-family: Consolas; font-size: 9pt; color: {P.fg_dim}; background: transparent;"
            )
            info.setWordWrap(True)
            self._results_layout.insertWidget(0, info)
            return

        idx = 0
        for step in result.steps:
            card = self._build_tier_card(step, idx)
            self._results_layout.insertWidget(idx, card)
            idx += 1

        # Totals bar
        totals = self._build_totals(result)
        self._results_layout.insertWidget(idx, totals)
        idx += 1

        # Disclaimer
        disc = QLabel(
            "Estimates assume repeating the best available mission per rank tier. "
            "Planner does not optimize for location. Actual progression may "
            "vary based on mission availability."
        )
        disc.setWordWrap(True)
        disc.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; font-style: italic; "
            f"color: {P.fg_disabled}; background: transparent; padding-top: 8px;"
        )
        self._results_layout.insertWidget(idx, disc)

    def _build_tier_card(self, step, card_idx: int) -> QWidget:
        card = QWidget()
        card.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(card)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)

        # Left coloured bar
        bar = QWidget()
        bar.setFixedWidth(4)
        bar.setStyleSheet(f"background-color: {P.accent};")
        hl.addWidget(bar)

        # Content
        content = QWidget()
        content.setStyleSheet(
            f"background-color: {P.bg_card if card_idx % 2 == 0 else P.bg_primary};"
        )
        cl = QVBoxLayout(content)
        cl.setContentsMargins(12, 8, 12, 8)
        cl.setSpacing(4)

        # Header row: "Rank A → Rank B"  +  "800 rep needed"
        hdr = QHBoxLayout()
        hdr.setSpacing(0)
        title = QLabel(f"{step.from_rank_name}  \u2192  {step.to_rank_name}")
        title.setStyleSheet(
            f"font-family: Consolas; font-size: 10pt; font-weight: bold; "
            f"color: {P.fg}; background: transparent;"
        )
        hdr.addWidget(title)
        hdr.addStretch(1)
        rep_lbl = QLabel(f"{step.rep_needed:,} rep needed")
        rep_lbl.setStyleSheet(
            f"font-family: Consolas; font-size: 9pt; color: {P.fg_dim}; background: transparent;"
        )
        hdr.addWidget(rep_lbl)
        cl.addLayout(hdr)

        # Best repeatable mission
        if step.best_repeatable:
            mission_title = step.best_repeatable.get("title", "")
            if not mission_title or mission_title.startswith("@"):
                mission_title = step.best_repeatable.get("debugName", "Unknown Mission")

            link_btn = QPushButton(mission_title)
            link_btn.setCursor(Qt.PointingHandCursor)
            link_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {P.accent}; "
                f"border: none; font-family: Consolas; font-size: 9pt; "
                f"text-decoration: underline; text-align: left; padding: 0; }}"
                f"QPushButton:hover {{ color: {P.fg_bright}; }}"
            )
            contract = step.best_repeatable
            link_btn.clicked.connect(
                lambda checked=False, c=contract: self._open_mission(c)
            )
            cl.addWidget(link_btn)

            # Stats: rep/run and repeats
            stats = QHBoxLayout()
            stats.setSpacing(12)
            rep_run = QLabel(f"\u2014 {step.best_rep_per_run} rep/run")
            rep_run.setStyleSheet(
                f"font-family: Consolas; font-size: 9pt; color: {P.fg_dim}; background: transparent;"
            )
            stats.addWidget(rep_run)

            repeats = QLabel(f"{step.repeats_needed}\u00d7 repeats")
            repeats.setStyleSheet(
                f"font-family: Consolas; font-size: 9pt; font-weight: bold; "
                f"color: {P.accent}; background: transparent;"
            )
            stats.addWidget(repeats)
            stats.addStretch(1)
            cl.addLayout(stats)

            # Show alternatives count
            alt_count = len(step.all_repeatables) - 1
            if alt_count > 0:
                alt_lbl = QLabel(f"({alt_count} alternative{'s' if alt_count != 1 else ''})")
                alt_lbl.setStyleSheet(
                    f"font-family: Consolas; font-size: 8pt; color: {P.fg_disabled}; background: transparent;"
                )
                cl.addWidget(alt_lbl)
        else:
            no_mission = QLabel("No repeatable missions available at this rank")
            no_mission.setStyleSheet(
                f"font-family: Consolas; font-size: 9pt; color: {P.fg_disabled}; "
                f"font-style: italic; background: transparent;"
            )
            cl.addWidget(no_mission)

        # One-time missions
        if step.one_time_missions:
            once_count = len(step.one_time_missions)
            # Show the best one-time mission
            best_once = max(step.one_time_missions, key=lambda x: x[1])
            once_title = best_once[0].get("title", "")
            if not once_title or once_title.startswith("@"):
                once_title = best_once[0].get("debugName", "Unknown")

            once_btn = QPushButton(once_title)
            once_btn.setCursor(Qt.PointingHandCursor)
            once_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {P.yellow}; "
                f"border: none; font-family: Consolas; font-size: 8pt; "
                f"text-decoration: underline; text-align: left; padding: 0; }}"
                f"QPushButton:hover {{ color: {P.fg_bright}; }}"
            )
            once_contract = best_once[0]
            once_btn.clicked.connect(
                lambda checked=False, c=once_contract: self._open_mission(c)
            )
            cl.addWidget(once_btn)

            suffix = f", {once_count - 1} alternative{'s' if once_count - 1 != 1 else ''}" if once_count > 1 else ""
            once_info = QLabel(f"\u2014 {best_once[1]} rep (one-time{suffix})")
            once_info.setStyleSheet(
                f"font-family: Consolas; font-size: 8pt; color: {P.fg_disabled}; background: transparent;"
            )
            cl.addWidget(once_info)

        hl.addWidget(content, 1)
        return card

    def _build_totals(self, result) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background-color: {P.bg_header};")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(16, 10, 16, 10)
        hl.setSpacing(24)

        runs_hdr = QLabel("REPEATABLE RUNS")
        runs_hdr.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; font-weight: bold; "
            f"color: {P.fg_dim}; background: transparent; letter-spacing: 1px;"
        )
        hl.addWidget(runs_hdr)
        runs_val = QLabel(str(result.total_repeatable_runs))
        runs_val.setStyleSheet(
            f"font-family: Consolas; font-size: 14pt; font-weight: bold; "
            f"color: {P.accent}; background: transparent;"
        )
        hl.addWidget(runs_val)

        hl.addSpacing(24)

        once_hdr = QLabel("ONE-TIME MISSIONS")
        once_hdr.setStyleSheet(
            f"font-family: Consolas; font-size: 8pt; font-weight: bold; "
            f"color: {P.fg_dim}; background: transparent; letter-spacing: 1px;"
        )
        hl.addWidget(once_hdr)
        once_val = QLabel(str(result.total_one_time_missions))
        once_val.setStyleSheet(
            f"font-family: Consolas; font-size: 14pt; font-weight: bold; "
            f"color: {P.accent}; background: transparent;"
        )
        hl.addWidget(once_val)

        hl.addStretch(1)
        return w

    def _open_mission(self, contract: dict):
        from ui.modals.mission_detail import MissionDetailModal
        MissionDetailModal(self.window(), contract, self._data)
