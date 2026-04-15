"""Resources/Mining page -- location browser with resource data (PySide6)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QScrollArea, QRadioButton, QButtonGroup, QStackedWidget,
)

from shared.qt.theme import P
from shared.qt.fuzzy_multi_check import SCFuzzyMultiCheck
from shared.qt.search_bar import SCSearchBar
from config import HIDDEN_LOCATIONS, MINING_GROUP_TYPES
from ui.theme import tag_colors
from ui.components.virtual_grid import VirtualScrollGrid, MissionCard

# Group keys used as table columns (in display order)
_TABLE_GROUPS = [
    ("SpaceShip_Mineables", "Ship", P.accent),
    ("FPS_Mineables", "FPS", P.purple),
    ("GroundVehicle_Mineables", "ROC", P.orange),
    ("Harvestables", "Harv", P.green),
]
_SALVAGE_GROUPS = [
    "Salvage_FreshDerelicts",
    "Salvage_BrokenShips_Poor",
    "Salvage_BrokenShips_Normal",
    "Salvage_BrokenShips_Elite",
]


class ResourcesPage(QWidget):
    """Resources/Mining page with sidebar filters and location card grid."""

    def __init__(self, parent, data_mgr):
        super().__init__(parent)
        self._data = data_mgr
        self._res_all_results = []
        self._resource_multi = None
        self._view_mode = "cards"
        self._table_sort_key = "location"
        self._table_sort_asc = True
        self._col_btns: dict[str, QPushButton] = {}
        self._open_bubbles: dict[str, QWidget] = {}  # loc_name -> bubble widget
        self._build()

    def _build(self):
        main = QHBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # ── Sidebar ──
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setFixedWidth(220)
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sidebar_scroll.setStyleSheet(f"QScrollArea {{ background-color: {P.bg_secondary}; border: none; }}")
        sb = QWidget()
        sb.setStyleSheet(f"background-color: {P.bg_secondary};")
        sb_lay = QVBoxLayout(sb)
        sb_lay.setContentsMargins(0, 0, 0, 0)
        sb_lay.setSpacing(0)
        sidebar_scroll.setWidget(sb)
        main.addWidget(sidebar_scroll)

        def section(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"font-family: Consolas; font-size: 8pt; font-weight: bold; color: {P.accent}; background: transparent; padding: 8px 8px 2px 8px;")
            sb_lay.addWidget(lbl)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet(f"background: {P.bg_secondary};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 8, 8, 4)
        fl = QLabel("FILTERS")
        fl.setStyleSheet(f"font-family: Consolas; font-size: 10pt; font-weight: bold; color: {P.fg}; background: transparent;")
        hl.addWidget(fl)
        hl.addStretch(1)
        clear_btn = QPushButton("Clear")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {P.fg_dim}; border: none; font-family: Consolas; font-size: 7pt; }}
            QPushButton:hover {{ color: {P.fg}; }}
        """)
        clear_btn.clicked.connect(self.clear_filters)
        hl.addWidget(clear_btn)
        sb_lay.addWidget(hdr)

        # Search
        section("SEARCH")
        self._search = SCSearchBar(placeholder="Search locations...", debounce_ms=300)
        self._search.search_changed.connect(lambda _: self.on_filter_change())
        sb_lay.addWidget(self._search)

        # System
        section("SYSTEM")
        self._sys_btns = {}
        sys_row = QHBoxLayout()
        sys_row.setContentsMargins(8, 0, 8, 2)
        for s in ["Stanton", "Pyro", "Nyx"]:
            btn = QPushButton(s)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{ background: {P.bg_card}; color: {P.fg_dim}; border: none;
                              font-family: Consolas; font-size: 8pt; padding: 2px 6px; }}
                QPushButton:checked {{ background: #1a3030; color: {P.accent}; }}
                QPushButton:hover {{ color: {P.fg}; }}
            """)
            btn.toggled.connect(lambda _: self.on_filter_change())
            sys_row.addWidget(btn)
            self._sys_btns[s] = btn
        sys_row.addStretch(1)
        sw = QWidget()
        sw.setStyleSheet("background: transparent;")
        sw.setLayout(sys_row)
        sb_lay.addWidget(sw)

        # Location type
        section("LOCATION TYPE")
        self._lt_btns = {}
        lt_row = QHBoxLayout()
        lt_row.setContentsMargins(8, 0, 8, 2)
        for lt in ["Planet", "Moon", "Belt", "Lagrange", "Cluster", "Event", "Special"]:
            btn = QPushButton(lt)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{ background: {P.bg_card}; color: {P.fg_dim}; border: none;
                              font-family: Consolas; font-size: 7pt; padding: 1px 4px; }}
                QPushButton:checked {{ background: #1a3030; color: {P.accent}; }}
                QPushButton:hover {{ color: {P.fg}; }}
            """)
            btn.toggled.connect(lambda _: self.on_filter_change())
            lt_row.addWidget(btn)
            self._lt_btns[lt.lower()] = btn
        lt_row.addStretch(1)
        ltw = QWidget()
        ltw.setStyleSheet("background: transparent;")
        ltw.setLayout(lt_row)
        sb_lay.addWidget(ltw)

        # Deposit type
        section("DEPOSIT TYPE")
        self._dt_btns = {}
        dt_row = QHBoxLayout()
        dt_row.setContentsMargins(8, 0, 8, 2)
        _dt_colors = {
            "SpaceShip_Mineables": P.accent,
            "FPS_Mineables": P.purple,
            "GroundVehicle_Mineables": P.orange,
            "Harvestables": P.green,
        }
        for key, label in [("SpaceShip_Mineables", "Ship"), ("FPS_Mineables", "FPS"),
                           ("GroundVehicle_Mineables", "ROC"), ("Harvestables", "Harvest")]:
            active_fg = _dt_colors.get(key, P.accent)
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{ background: {P.bg_card}; color: {P.fg_dim}; border: none;
                              font-family: Consolas; font-size: 8pt; padding: 2px 6px; }}
                QPushButton:checked {{ background: #1a3030; color: {active_fg}; }}
                QPushButton:hover {{ color: {P.fg}; }}
            """)
            btn.toggled.connect(lambda _: self.on_filter_change())
            dt_row.addWidget(btn)
            self._dt_btns[key] = btn
        dt_row.addStretch(1)
        dtw = QWidget()
        dtw.setStyleSheet("background: transparent;")
        dtw.setLayout(dt_row)
        sb_lay.addWidget(dtw)

        # Resources filter
        section("RESOURCES")
        self._resource_multi = SCFuzzyMultiCheck(label="All", items=[])
        self._resource_multi.selection_changed.connect(lambda _: self.on_filter_change())
        sb_lay.addWidget(self._resource_multi)

        # Match mode
        match_w = QWidget()
        match_w.setStyleSheet("background: transparent;")
        ml = QHBoxLayout(match_w)
        ml.setContentsMargins(8, 0, 8, 2)
        self._match_group = QButtonGroup(self)
        self._any_radio = QRadioButton("Any")
        self._any_radio.setChecked(True)
        self._any_radio.setStyleSheet(f"font-family: Consolas; font-size: 8pt; color: {P.fg_dim}; background: transparent;")
        self._any_radio.toggled.connect(lambda _: self.on_filter_change())
        ml.addWidget(self._any_radio)
        self._all_radio = QRadioButton("All")
        self._all_radio.setStyleSheet(f"font-family: Consolas; font-size: 8pt; color: {P.fg_dim}; background: transparent;")
        ml.addWidget(self._all_radio)
        self._match_group.addButton(self._any_radio)
        self._match_group.addButton(self._all_radio)
        ml.addStretch(1)
        sb_lay.addWidget(match_w)

        sb_lay.addStretch(1)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color: {P.border};")
        sep.setFixedWidth(1)
        main.addWidget(sep)

        # ── Main area ──
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        # Top bar with count + view toggle
        top_bar = QWidget()
        top_bar.setFixedHeight(28)
        top_bar.setStyleSheet(f"background: {P.bg_primary};")
        tb_lay = QHBoxLayout(top_bar)
        tb_lay.setContentsMargins(10, 0, 10, 0)
        tb_lay.setSpacing(4)

        self._count_label = QLabel("Loading resource data...")
        self._count_label.setStyleSheet(f"font-family: Consolas; font-size: 9pt; color: {P.fg_dim}; background: transparent;")
        tb_lay.addWidget(self._count_label)
        tb_lay.addStretch(1)

        self._view_btns = {}
        for key, label in [("cards", "Cards"), ("table", "Table")]:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, k=key: self._set_view(k))
            tb_lay.addWidget(btn)
            self._view_btns[key] = btn
        self._update_view_btn_style()

        right.addWidget(top_bar)

        # Stacked views
        self._view_stack = QStackedWidget()

        # Index 0: Cards
        self._vgrid = VirtualScrollGrid(
            card_width=320, row_height=280,
            fill_fn=self._fill_card,
            on_click_fn=self._on_click,
            card_class=MissionCard,
        )
        self._view_stack.addWidget(self._vgrid)

        # Index 1: Table
        self._table_scroll = QScrollArea()
        self._table_scroll.setWidgetResizable(True)
        self._table_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._table_scroll.setStyleSheet(f"QScrollArea {{ background: {P.bg_primary}; border: none; }}")

        table_inner = QWidget()
        table_inner.setStyleSheet(f"background: {P.bg_primary};")
        self._table_layout = QVBoxLayout(table_inner)
        self._table_layout.setContentsMargins(0, 0, 0, 0)
        self._table_layout.setSpacing(0)

        # Table header (persistent)
        self._table_header = self._build_table_header()
        self._table_layout.addWidget(self._table_header)

        # Table rows container
        self._table_rows_widget = QWidget()
        self._table_rows_widget.setStyleSheet("background: transparent;")
        self._table_rows_layout = QVBoxLayout(self._table_rows_widget)
        self._table_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._table_rows_layout.setSpacing(0)
        self._table_layout.addWidget(self._table_rows_widget)
        self._table_layout.addStretch(1)

        self._table_scroll.setWidget(table_inner)
        self._view_stack.addWidget(self._table_scroll)

        right.addWidget(self._view_stack, 1)
        main.addLayout(right, 1)

    # ── View toggle ──

    def _set_view(self, mode: str):
        self._view_mode = mode
        self._view_stack.setCurrentIndex(0 if mode == "cards" else 1)
        self._update_view_btn_style()
        self._update_active_view()

    def _update_view_btn_style(self):
        for key, btn in self._view_btns.items():
            active = key == self._view_mode
            btn.setStyleSheet(
                f"QPushButton {{ background: {'#1a3030' if active else P.bg_card}; "
                f"color: {P.accent if active else P.fg_dim}; border: none; "
                f"font-family: Consolas; font-size: 8pt; font-weight: bold; padding: 2px 8px; }}"
                f"QPushButton:hover {{ color: {P.fg}; }}"
            )

    def _update_active_view(self):
        if self._view_mode == "cards":
            self._vgrid.set_data(self._res_all_results)
        else:
            self._rebuild_table(self._res_all_results)

    # ── Table view ──

    def _build_table_header(self) -> QWidget:
        hdr = QWidget()
        hdr.setStyleSheet(f"background-color: {P.bg_header};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.setSpacing(0)

        # (sort_key, label, width)  — width 0 = stretch
        cols = [("location", "LOCATION", 180), ("system", "SYSTEM", 70), ("type", "TYPE", 70)]
        for grp_key, short, _ in _TABLE_GROUPS:
            cols.append((grp_key, short.upper(), 55))
        cols.append(("salv", "SALV", 55))
        cols.append(("resources", "TOP RESOURCES", 0))

        self._col_btns.clear()
        for sort_key, text, w in cols:
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, k=sort_key: self._set_table_sort(k))
            if w:
                btn.setFixedWidth(w)
            else:
                btn.setMinimumWidth(120)
            hl.addWidget(btn)
            self._col_btns[sort_key] = btn
            if not w:
                hl.addStretch(1)

        self._update_col_btn_style()
        return hdr

    def _set_table_sort(self, key: str):
        if self._table_sort_key == key:
            self._table_sort_asc = not self._table_sort_asc
        else:
            self._table_sort_key = key
            # Default: ascending for text columns, descending for numeric
            self._table_sort_asc = key in ("location", "system", "type", "resources")
        self._update_col_btn_style()
        if self._view_mode == "table":
            self._rebuild_table(self._res_all_results)

    def _update_col_btn_style(self):
        for key, btn in self._col_btns.items():
            active = key == self._table_sort_key
            arrow = " \u25b2" if self._table_sort_asc else " \u25bc" if active else ""
            base = btn.text().split(" \u25b2")[0].split(" \u25bc")[0]
            btn.setText(f"{base}{arrow}")
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; "
                f"font-family: Consolas; font-size: 8pt; font-weight: bold; "
                f"color: {P.accent if active else P.fg_dim}; text-align: left; padding: 0; }}"
                f"QPushButton:hover {{ color: {P.fg}; }}"
            )

    def _table_sort_value(self, loc: dict) -> object:
        """Return a sort key for the current column."""
        k = self._table_sort_key
        if k == "location":
            return loc.get("locationName", "").lower()
        if k == "system":
            return loc.get("system", "").lower()
        if k == "type":
            return loc.get("locationType", "").lower()
        if k == "resources":
            return len(self._data.get_location_resources(loc.get("locationName", "")))
        if k == "salv":
            resources = self._data.get_location_resources(loc.get("locationName", ""))
            return 1 if any(r.get("group", "") in _SALVAGE_GROUPS for r in resources) else 0
        # Group percentage column
        resources = self._data.get_location_resources(loc.get("locationName", ""))
        best = 0.0
        for r in resources:
            if r.get("group") == k:
                best = max(best, r.get("max_pct", 0))
        return best

    def _rebuild_table(self, results: list):
        # Sort
        sorted_results = sorted(
            results,
            key=self._table_sort_value,
            reverse=not self._table_sort_asc,
        )

        # Clear existing rows
        while self._table_rows_layout.count():
            item = self._table_rows_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        sys_colors = {
            "Stanton": P.accent,
            "Pyro": P.orange,
            "Nyx": P.purple,
        }

        for i, loc in enumerate(sorted_results):
            loc_name = loc.get("locationName", "?")
            system = loc.get("system", "")
            loc_type = loc.get("locationType", "")
            resources = self._data.get_location_resources(loc_name)

            # Compute max% per group
            group_pcts: dict[str, float] = {}
            for r in resources:
                grp = r.get("group", "")
                pct = r.get("max_pct", 0)
                if grp in group_pcts:
                    group_pcts[grp] = max(group_pcts[grp], pct)
                else:
                    group_pcts[grp] = pct

            # Check salvage (any salvage group)
            has_salvage = any(sg in group_pcts for sg in _SALVAGE_GROUPS)

            # Top resources
            top_names = []
            for r in resources[:3]:
                name = r["resource"]
                for suffix in [" (Ore)", " (Raw)", " (Gem)"]:
                    name = name.replace(suffix, "")
                top_names.append(name)
            extra = len(resources) - 3
            top_str = ", ".join(top_names)
            if extra > 0:
                top_str += f", +{extra}"

            # Build row
            row_bg = P.bg_card if i % 2 == 0 else P.bg_primary
            row = _TableRow(loc, self._on_table_row_click)
            row.setStyleSheet(f"background-color: {row_bg};")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 3, 8, 3)
            rl.setSpacing(0)

            # Location
            loc_lbl = QLabel(loc_name)
            loc_lbl.setFixedWidth(180)
            loc_lbl.setStyleSheet(
                f"font-family: Consolas; font-size: 9pt; color: {P.fg}; background: transparent;"
            )
            rl.addWidget(loc_lbl)

            # System
            sys_fg = sys_colors.get(system, P.fg_dim)
            sys_lbl = QLabel(system)
            sys_lbl.setFixedWidth(70)
            sys_lbl.setStyleSheet(
                f"font-family: Consolas; font-size: 9pt; font-weight: bold; "
                f"color: {sys_fg}; background: transparent;"
            )
            rl.addWidget(sys_lbl)

            # Type
            type_lbl = QLabel(loc_type.title())
            type_lbl.setFixedWidth(70)
            type_lbl.setStyleSheet(
                f"font-family: Consolas; font-size: 9pt; color: {P.fg_dim}; background: transparent;"
            )
            rl.addWidget(type_lbl)

            # Group percentage columns
            for grp_key, _, grp_color in _TABLE_GROUPS:
                pct = group_pcts.get(grp_key)
                if pct is not None and pct > 0:
                    txt = f"{pct:.1f}%" if pct < 1 else f"{pct:.0f}%"
                    color = grp_color
                else:
                    txt = "\u2014"
                    color = P.fg_disabled
                pl = QLabel(txt)
                pl.setFixedWidth(55)
                pl.setStyleSheet(
                    f"font-family: Consolas; font-size: 9pt; color: {color}; background: transparent;"
                )
                rl.addWidget(pl)

            # Salvage column
            if has_salvage:
                salv_lbl = QLabel("\u2713")
                salv_color = P.fg
            else:
                salv_lbl = QLabel("\u2014")
                salv_color = P.fg_disabled
            salv_lbl.setFixedWidth(55)
            salv_lbl.setStyleSheet(
                f"font-family: Consolas; font-size: 9pt; color: {salv_color}; background: transparent;"
            )
            rl.addWidget(salv_lbl)

            # Top resources
            top_lbl = QLabel(top_str)
            top_lbl.setMinimumWidth(120)
            top_lbl.setStyleSheet(
                f"font-family: Consolas; font-size: 8pt; color: {P.fg_dim}; background: transparent;"
            )
            rl.addWidget(top_lbl, 1)

            self._table_rows_layout.addWidget(row)

    # ── Public API ──

    def set_count_message(self, msg):
        self._count_label.setText(msg)

    def populate_resource_values(self):
        if self._resource_multi:
            self._resource_multi.set_items(sorted(self._data.all_resource_names))

    def rebuild_grid(self):
        self._update_active_view()

    # ── Filter logic ──

    def on_filter_change(self):
        if not self._data.mining_loaded:
            return

        search = (self._search.text() or "").lower()
        active_systems = {s for s, btn in self._sys_btns.items() if btn.isChecked()}
        active_loctypes = {t for t, btn in self._lt_btns.items() if btn.isChecked()}
        active_deptypes = {t for t, btn in self._dt_btns.items() if btn.isChecked()}
        selected_res = set(self._resource_multi.get_selected()) if self._resource_multi else set()
        match_mode = "all" if self._all_radio.isChecked() else "any"

        hidden = HIDDEN_LOCATIONS
        results = []

        for loc in self._data.mining_locations:
            loc_name = loc.get("locationName", "")
            if loc_name in hidden:
                continue

            system = loc.get("system", "")
            loc_type = loc.get("locationType", "")

            if active_systems and system not in active_systems:
                continue
            if active_loctypes and loc_type not in active_loctypes:
                continue
            if active_deptypes:
                group_names = {g.get("groupName", "") for g in loc.get("groups", [])}
                if not group_names.intersection(active_deptypes):
                    continue

            loc_resources = self._data.get_location_resources(loc_name)
            resource_names = {r["resource"] for r in loc_resources}

            if selected_res:
                if match_mode == "all":
                    if not selected_res.issubset(resource_names):
                        continue
                else:
                    if not selected_res.intersection(resource_names):
                        continue

            if search:
                all_text = loc_name.lower() + " " + " ".join(r.lower() for r in resource_names)
                if search not in all_text:
                    continue

            results.append(loc)

        self._res_all_results = results
        total = len([loc for loc in self._data.mining_locations
                     if loc.get("locationName", "") not in hidden])
        shown = len(results)
        suffix = f" of {total}" if shown != total else ""
        self._count_label.setText(
            f"{shown}{suffix} Locations  \u00b7  {len(self._data.all_resource_names)} Resources")
        self._update_active_view()

    def clear_filters(self):
        self._search.clear()
        for btn in self._sys_btns.values():
            btn.setChecked(False)
        for btn in self._lt_btns.values():
            btn.setChecked(False)
        for btn in self._dt_btns.values():
            btn.setChecked(False)
        if self._resource_multi:
            self._resource_multi.set_selected([])
        self._any_radio.setChecked(True)
        self.on_filter_change()

    # ── Card fill + click ──

    def _fill_card(self, card, loc, idx):
        loc_name = loc.get("locationName", "?")
        system = loc.get("system", "")
        loc_type = loc.get("locationType", "")

        sys_colors = {
            "Stanton": (P.accent, "#0a2020"),
            "Pyro": (P.orange, "#2a1a0a"),
            "Nyx": (P.purple, "#1a0a2a"),
        }
        sys_fg, sys_bg = sys_colors.get(system, (P.fg_dim, P.bg_card))

        tags = [(system, sys_bg, sys_fg, True)]

        LOC_TYPE_COLORS = {
            "planet": ("#0a1a20", "#55bbaa"), "moon": ("#0a1520", "#7799bb"),
            "belt": ("#1a1a0a", "#bbaa55"), "lagrange": ("#0a0a1a", "#8888cc"),
            "cluster": ("#1a0a1a", "#aa77bb"), "event": ("#1a1a0a", P.yellow),
            "special": ("#1a0a0a", P.red),
        }
        type_labels = {"planet": "Planet", "moon": "Moon", "belt": "Belt",
                       "lagrange": "Lagrange", "cluster": "Cluster",
                       "event": "Event", "special": "Special"}
        type_label = type_labels.get(loc_type, loc_type.title())
        lt_bg, lt_fg = LOC_TYPE_COLORS.get(loc_type, (P.bg_card, P.fg_dim))
        tags.append((type_label, lt_bg, lt_fg, True))

        GROUP_COLORS = {
            "SpaceShip_Mineables": ("#0a1a2a", P.accent),
            "SpaceShip_Mineables_Rare": ("#1a1a0a", P.yellow),
            "FPS_Mineables": ("#1a0a1a", P.purple),
            "GroundVehicle_Mineables": ("#1a1a0a", P.orange),
            "Harvestables": ("#0a1a0a", P.green),
        }
        groups = loc.get("groups", [])
        for g in groups[:3]:
            gn = g.get("groupName", "")
            gt_info = MINING_GROUP_TYPES.get(gn, {})
            if gt_info:
                bg_c, fg_c = GROUP_COLORS.get(gn, (P.bg_card, P.fg_dim))
                tags.append((gt_info["short"], bg_c, fg_c, True))

        resources = self._data.get_location_resources(loc_name)
        lines = []
        for r in resources[:8]:
            pct = f"{r['max_pct']:.0f}%" if r["max_pct"] else ""
            name = r["resource"]
            for suffix in [" (Ore)", " (Raw)", " (Gem)"]:
                name = name.replace(suffix, "")
            lines.append(f"{name:<16s} {pct:>5s}")
        if len(resources) > 8:
            lines.append(f"+{len(resources) - 8} more")
        extra = "\n".join(lines)

        reward_text = f"{len(resources)} resources"
        reward_color = P.green if resources else P.fg_dim
        initials = loc_name[:2].upper()

        card.set_data(loc_name, initials, system, tags, reward_text, reward_color, extra=extra)

    def _on_click(self, loc, idx):
        from ui.modals.location_detail import LocationDetailModal
        LocationDetailModal(self.window(), loc, self._data)

    # ── Table row bubble ──

    def _on_table_row_click(self, loc, idx):
        loc_name = loc.get("locationName", "?")

        # If bubble already open for this location, just focus it
        if loc_name in self._open_bubbles:
            bubble = self._open_bubbles[loc_name]
            if bubble.isVisible():
                return
            else:
                del self._open_bubbles[loc_name]

        bubble = _ResourceBubble(loc, self._data, self)
        bubble.closed.connect(lambda name=loc_name: self._open_bubbles.pop(name, None))
        self._open_bubbles[loc_name] = bubble

        # Position near cursor
        from PySide6.QtGui import QCursor
        pos = self.mapFromGlobal(QCursor.pos())
        # Clamp within the view area
        bw, bh = 420, 320
        x = max(0, min(pos.x() - bw // 2, self.width() - bw))
        y = max(0, min(pos.y() + 10, self.height() - bh))
        bubble.setGeometry(x, y, bw, bh)
        bubble.raise_()
        bubble.show()


class _ResourceBubble(QFrame):
    """Inline popup bubble showing location resource details."""

    from PySide6.QtCore import Signal as _Signal
    closed = _Signal()

    _GRP_FG = {
        "SpaceShip_Mineables": P.accent,
        "SpaceShip_Mineables_Rare": P.yellow,
        "FPS_Mineables": P.purple,
        "GroundVehicle_Mineables": P.orange,
        "Harvestables": P.green,
        "Salvage_FreshDerelicts": P.red,
        "Salvage_BrokenShips_Poor": "#cc6644",
        "Salvage_BrokenShips_Normal": "#cc6644",
        "Salvage_BrokenShips_Elite": "#cc6644",
    }

    def __init__(self, loc: dict, data_mgr, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Box)
        self.setStyleSheet(
            f"QFrame {{ background-color: rgba(11, 14, 20, 240); "
            f"border: 1px solid {P.accent}; border-radius: 4px; }}"
        )

        loc_name = loc.get("locationName", "?")
        system = loc.get("system", "")
        loc_type = loc.get("locationType", "")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 10)
        lay.setSpacing(4)

        # Header with title + close button
        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        title = QLabel(loc_name)
        title.setStyleSheet(
            f"font-family: Consolas; font-size: 11pt; font-weight: bold; "
            f"color: {P.fg}; background: transparent; border: none;"
        )
        hdr.addWidget(title)
        hdr.addStretch(1)

        sys_colors = {"Stanton": P.accent, "Pyro": P.orange, "Nyx": P.purple}
        sys_lbl = QLabel(f" {system} ")
        sys_lbl.setStyleSheet(
            f"background: {P.bg_card}; color: {sys_colors.get(system, P.fg_dim)}; "
            f"font-family: Consolas; font-size: 8pt; font-weight: bold; border: none;"
        )
        hdr.addWidget(sys_lbl)

        type_lbl = QLabel(f" {loc_type.title()} ")
        type_lbl.setStyleSheet(
            f"background: {P.bg_card}; color: {P.fg_dim}; "
            f"font-family: Consolas; font-size: 8pt; border: none;"
        )
        hdr.addWidget(type_lbl)

        close_btn = QPushButton("x")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 60, 60, 0.15); color: #cc6666; "
            f"border: none; border-radius: 3px; font-family: Consolas; "
            f"font-size: 11pt; font-weight: bold; padding: 0; }}"
            f"QPushButton:hover {{ background: rgba(220, 50, 50, 0.85); color: #fff; }}"
        )
        close_btn.clicked.connect(self._close)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {P.border}; border: none;")
        lay.addWidget(sep)

        # Resource list (scrollable)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 2, 0, 2)
        il.setSpacing(0)

        resources = data_mgr.get_location_resources(loc_name)
        for i, r in enumerate(resources):
            max_pct = r.get("max_pct", 0)
            group = r.get("group", "")

            if max_pct >= 40:
                pct_color = P.green
            elif max_pct >= 15:
                pct_color = P.yellow
            elif max_pct >= 5:
                pct_color = P.orange
            else:
                pct_color = P.fg_dim

            display_name = r["resource"]
            for suffix in [" (Ore)", " (Raw)", " (Gem)"]:
                display_name = display_name.replace(suffix, "")

            gt_info = MINING_GROUP_TYPES.get(group, {})
            group_short = gt_info.get("short", group[:8] if group else "\u2014")
            grp_fg = self._GRP_FG.get(group, P.fg_dim)

            row_bg = P.bg_card if i % 2 == 0 else "transparent"
            rw = QWidget()
            rw.setStyleSheet(f"background-color: {row_bg}; border: none;")
            rl = QHBoxLayout(rw)
            rl.setContentsMargins(4, 2, 4, 2)

            nl = QLabel(display_name)
            nl.setFixedWidth(160)
            nl.setStyleSheet(f"font-family: Consolas; font-size: 9pt; color: {P.fg}; background: transparent; border: none;")
            rl.addWidget(nl)

            gl = QLabel(group_short)
            gl.setFixedWidth(70)
            gl.setStyleSheet(f"font-family: Consolas; font-size: 8pt; font-weight: bold; color: {grp_fg}; background: transparent; border: none;")
            rl.addWidget(gl)

            pl = QLabel(f"{max_pct:.0f}%")
            pl.setFixedWidth(50)
            pl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            pl.setStyleSheet(f"font-family: Consolas; font-size: 9pt; font-weight: bold; color: {pct_color}; background: transparent; border: none;")
            rl.addWidget(pl)

            il.addWidget(rw)

        il.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

    def _close(self):
        self.closed.emit()
        self.hide()
        self.deleteLater()


class _TableRow(QWidget):
    """Clickable table row widget."""

    def __init__(self, loc: dict, click_fn, parent=None):
        super().__init__(parent)
        self._loc = loc
        self._click_fn = click_fn
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._click_fn(self._loc, 0)
        super().mousePressEvent(event)
