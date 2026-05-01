"""Permanent break-calculator side panel for the Scanner tab.

Uses the same "Advanced Breakability Assistance" sectioned layout
as the floating BreakBubble, with collapsible sections and the
charge simulation display.

The panel is stateless: the main app computes a :class:`BreakResult`
via ``compute_with_gadgets`` (same call the bubble uses) and pushes
everything into :meth:`BreakPanel.update_state`.
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy,
)

from shared.qt.theme import P

from .theme import ACCENT

RED = "#ff4444"
YELLOW = "#ffc107"
DIM = P.fg_dim

_LABEL_W = 110


# ─────────────────────────────────────────────────────────────
# Row helpers (matching break_bubble.py style)
# ─────────────────────────────────────────────────────────────

def _build_rows(
    parent: QWidget,
    rows: list[tuple[str, str, str]],
    label_width: int = _LABEL_W,
) -> list[QWidget]:
    """Build row widgets from (label, value, color) tuples."""
    widgets: list[QWidget] = []
    for label_text, value_text, color in rows:
        row_widget = QWidget(parent)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        if label_text:
            lbl = QLabel(label_text, row_widget)
            lbl.setStyleSheet(
                f"font-family: Consolas, monospace; font-size: 8pt; "
                f"color: {DIM}; background: transparent;"
            )
            lbl.setFixedWidth(label_width)
            row_layout.addWidget(lbl)
        else:
            row_layout.addSpacing(label_width + 6)

        val = QLabel(value_text, row_widget)
        val.setStyleSheet(
            f"font-family: Consolas, monospace; font-size: 9pt; "
            f"font-weight: bold; color: {color}; background: transparent;"
        )
        val.setWordWrap(True)
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row_layout.addWidget(val, 1)

        widgets.append(row_widget)
    return widgets


def _result_signature(result) -> tuple:
    """Cheap, hashable fingerprint of the BreakResult fields that
    actually affect how the panel renders. Used by ``update_state`` to
    short-circuit rebuilds when nothing visible has changed.

    Keep in sync with every getattr(result, ...) call inside the
    rebuild path. Missing attrs become None so older/partial result
    shapes don't blow up."""
    if result is None:
        return ()
    cp = getattr(result, "charge_profile", None)
    cp_sig = (
        None if cp is None
        else (
            round(getattr(cp, "min_throttle_pct", 0.0) or 0.0, 1),
            None if getattr(cp, "est_total_time_sec", float("inf")) == float("inf")
            else round(cp.est_total_time_sec, 0),
        )
    )
    lasers = tuple(getattr(result, "used_lasers", ()) or ())
    return (
        bool(getattr(result, "insufficient", False)),
        bool(getattr(result, "unbreakable", False)),
        round(getattr(result, "missing_power", 0.0) or 0.0, 0),
        round(getattr(result, "percentage", 0.0) or 0.0, 0),
        getattr(result, "gadget_used", None),
        int(getattr(result, "active_modules_needed", 0) or 0),
        cp_sig,
        lasers,
    )


def _build_separator(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color: {DIM}; background: {DIM};")
    line.setFixedHeight(1)
    return line


# ─────────────────────────────────────────────────────────────
# Collapsible section (same pattern as break_bubble.py)
# ─────────────────────────────────────────────────────────────

class _CollapsibleSection(QWidget):
    """Section with a clickable header that toggles content visibility."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        icon: str = "",
        accent_color: str = ACCENT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._expanded = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header row
        header = QWidget(self)
        header.setCursor(Qt.PointingHandCursor)
        header.setStyleSheet("background: transparent;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(0, 4, 0, 4)
        h_layout.setSpacing(6)

        self._arrow = QLabel("\u25bc", header)
        self._arrow.setStyleSheet(
            f"font-size: 8pt; color: {accent_color}; background: transparent;"
        )
        self._arrow.setFixedWidth(12)
        h_layout.addWidget(self._arrow)

        if icon:
            icon_lbl = QLabel(icon, header)
            icon_lbl.setStyleSheet(
                f"font-size: 10pt; color: {accent_color}; background: transparent;"
            )
            icon_lbl.setFixedWidth(18)
            h_layout.addWidget(icon_lbl)

        title_lbl = QLabel(title, header)
        title_lbl.setStyleSheet(
            f"font-family: Electrolize, Consolas, monospace; font-size: 9pt; "
            f"font-weight: bold; color: {accent_color}; background: transparent;"
        )
        h_layout.addWidget(title_lbl)

        if subtitle:
            sub_lbl = QLabel(f"({subtitle})", header)
            sub_lbl.setStyleSheet(
                f"font-family: Consolas, monospace; font-size: 8pt; "
                f"color: {DIM}; background: transparent;"
            )
            h_layout.addWidget(sub_lbl)

        h_layout.addStretch(1)
        header.mousePressEvent = lambda e: self._toggle()
        layout.addWidget(header)

        # Content area
        self._content = QWidget(self)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(18, 2, 0, 6)
        self._content_layout.setSpacing(2)
        layout.addWidget(self._content)

    def add_rows(self, rows: list[tuple[str, str, str]], label_width: int = _LABEL_W) -> None:
        for w in _build_rows(self._content, rows, label_width):
            self._content_layout.addWidget(w)

    def add_widget(self, widget: QWidget) -> None:
        self._content_layout.addWidget(widget)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        self._arrow.setText("\u25bc" if self._expanded else "\u25b6")


# ─────────────────────────────────────────────────────────────
# Main panel widget
# ─────────────────────────────────────────────────────────────

class BreakPanel(QWidget):
    """Live break-calculator sidebar using the sectioned layout."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.setStyleSheet(f"background: {P.bg_primary};")

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(10, 6, 10, 8)
        self._root.setSpacing(2)

        self._children: list[QWidget] = []
        self._ship_label = ""
        # Persistent "full" skeleton — lazily built the first time we
        # enter the full-data state, then reused. All subsequent
        # updates mutate the rows in-place to eliminate rebuild flicker.
        self._full_title: QLabel | None = None
        self._full_section: _CollapsibleSection | None = None
        self._full_spacer: QWidget | None = None
        # Pool of pre-allocated row widgets inside the YOU section.
        # Each entry: (container, label_lbl, value_lbl).
        self._row_pool: list[tuple[QWidget, QLabel, QLabel]] = []
        self._state: str = ""  # "idle" | "idle_msg" | "full"
        self._build_idle()

    def _clear(self) -> None:
        for w in self._children:
            self._root.removeWidget(w)
            w.deleteLater()
        self._children.clear()

    def _build_idle(self) -> None:
        """Show the idle / waiting state."""
        self._clear()

        title = QLabel("ADVANCED BREAKABILITY ASSISTANCE", self)
        title.setStyleSheet(
            f"font-family: Electrolize, Consolas, monospace; font-size: 9pt; "
            f"font-weight: bold; color: {ACCENT}; background: transparent;"
        )
        self._root.addWidget(title)
        self._children.append(title)

        sep = _build_separator(self)
        self._root.addWidget(sep)
        self._children.append(sep)

        msg = QLabel("Waiting for rock data...", self)
        msg.setStyleSheet(
            f"font-family: Consolas, monospace; font-size: 9pt; "
            f"color: {DIM}; background: transparent; padding: 12px 4px;"
        )
        msg.setWordWrap(True)
        self._root.addWidget(msg)
        self._children.append(msg)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._root.addWidget(spacer)
        self._children.append(spacer)

    # ── public API ──

    def clear(self) -> None:
        self._tear_down_full()
        self._build_idle()
        self._state = "idle"
        self._last_sig = None

    def set_ship_label(self, text: str) -> None:
        self._ship_label = text or ""

    def update_state(
        self,
        *,
        mass: float | None,
        resistance: float | None,
        instability: float | None,
        ship_label: str = "",
        result=None,
        no_ship: bool = False,
        mineral: str | None = None,
    ) -> None:
        """Push a new snapshot of rock stats + break result into the panel."""
        if ship_label:
            self._ship_label = ship_label

        # ── Anti-flicker gate ──
        # `update_state` is called on every HUD scan (~1 Hz). Most scans
        # produce the SAME values as the previous scan (same rock, same
        # ship, same loadout) — rebuilding the whole widget tree then
        # causes a visible flash as Qt tears down and repaints. Build a
        # cheap signature of everything that affects rendering and skip
        # the rebuild if it matches the last one.
        sig = (
            no_ship,
            self._ship_label,
            mineral,
            None if mass is None else round(float(mass)),
            None if resistance is None else round(float(resistance)),
            None if instability is None else round(float(instability), 2),
            _result_signature(result),
        )
        if getattr(self, "_last_sig", None) == sig:
            return
        # Defer paints while we rebuild; single repaint at the end
        # prevents the mid-rebuild empty frame that looks like flicker.
        self.setUpdatesEnabled(False)
        try:
            self._rebuild_for_sig(
                mass=mass, resistance=resistance, instability=instability,
                result=result, no_ship=no_ship, mineral=mineral,
            )
            self._last_sig = sig
        finally:
            self.setUpdatesEnabled(True)

    def _rebuild_for_sig(
        self,
        *,
        mass: float | None,
        resistance: float | None,
        instability: float | None,
        result,
        no_ship: bool,
        mineral: str | None,
    ) -> None:
        if no_ship:
            if self._state != "no_ship":
                self._tear_down_full()
                self._clear()
                self._build_idle_msg(
                    "Select a mining ship",
                    "Open the Mining Ships tab to load a loadout.",
                )
                self._state = "no_ship"
            return

        if mass is None or resistance is None or result is None:
            if self._state != "idle":
                self._tear_down_full()
                self._clear()
                self._build_idle()
                self._state = "idle"
            return

        # ── Full data state: in-place update, no teardown ──
        self._ensure_full_skeleton()
        self._state = "full"
        self._apply_full(
            mass=mass, resistance=resistance, instability=instability,
            result=result, mineral=mineral,
        )

    def _tear_down_full(self) -> None:
        """Drop the persistent full-state skeleton so _clear can run."""
        self._full_title = None
        self._full_section = None
        self._full_spacer = None
        self._row_pool.clear()

    def _ensure_full_skeleton(self) -> None:
        """Lazily build the title / separator / YOU section / spacer
        the FIRST time we enter the full-data state. Subsequent calls
        are no-ops — the skeleton persists across scans and only row
        text/colors are mutated in :meth:`_apply_full`."""
        if self._full_section is not None:
            return
        # Coming from idle state → tear down the idle widgets first.
        self._clear()

        title = QLabel("ADVANCED BREAKABILITY ASSISTANCE", self)
        self._root.addWidget(title)
        self._children.append(title)
        self._full_title = title

        sep = _build_separator(self)
        self._root.addWidget(sep)
        self._children.append(sep)

        section = _CollapsibleSection(
            "YOU", "Active Miner", icon="\U0001f9d1",
            accent_color=ACCENT, parent=self,
        )
        self._root.addWidget(section)
        self._children.append(section)
        self._full_section = section

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._root.addWidget(spacer)
        self._children.append(spacer)
        self._full_spacer = spacer

    def _apply_full(
        self,
        *,
        mass: float | None,
        resistance: float | None,
        instability: float | None,
        result,
        mineral: str | None,
    ) -> None:
        """Mutate the persistent skeleton to reflect the current rock
        + result. Only label text and per-row colors change; no widgets
        are created or destroyed on the steady-state path, so there is
        no flicker between scans."""
        can_break = not getattr(result, "insufficient", False)
        unbreakable = getattr(result, "unbreakable", False)
        accent = ACCENT if (can_break and not unbreakable) else RED

        # Retint the title (red on unbreakable / insufficient).
        if self._full_title is not None:
            self._full_title.setStyleSheet(
                f"font-family: Electrolize, Consolas, monospace; "
                f"font-size: 9pt; font-weight: bold; color: {accent}; "
                f"background: transparent;"
            )

        # Build the desired row list — same logic as the old rebuild,
        # just collected into a list we then diff against the pool.
        rows: list[tuple[str, str, str]] = []
        if self._ship_label:
            rows.append(("Ship:", self._ship_label, P.fg))
        if mineral:
            rows.append(("Resource:", str(mineral), P.fg))
        if mass is not None:
            rows.append(("Mass:", f"{mass:,.0f} kg", P.fg))
        if resistance is not None:
            rows.append(("Resist:", f"{resistance:.0f}%", P.fg))
        if instability is not None:
            rows.append(("Instab:", f"{instability:.2f}", P.fg))

        if unbreakable:
            rows.append(("Status:", "UNBREAKABLE", RED))
        elif not can_break:
            missing = getattr(result, "missing_power", 0.0) or 0.0
            status = (
                f"CANNOT BREAK (+{missing:,.0f} MW)"
                if missing > 0 else "CANNOT BREAK"
            )
            rows.append(("Status:", status, RED))
            gadget = getattr(result, "gadget_used", None)
            if gadget:
                rows.append(("Gadget:", gadget, YELLOW))
        else:
            pct = getattr(result, "percentage", None)
            pct_str = f"{pct:.0f}%" if pct is not None else "?"
            rows.append(("Power:", pct_str, ACCENT))

        if getattr(result, "active_modules_needed", 0):
            rows.append(
                ("Active Mods:",
                 f"{result.active_modules_needed} activation(s)", YELLOW),
            )

        cp = getattr(result, "charge_profile", None)
        if cp is not None and can_break and not unbreakable:
            throttle_color = YELLOW if cp.min_throttle_pct > 50 else ACCENT
            rows.append(("Min Throttle:",
                         f"{cp.min_throttle_pct:.0f}%", throttle_color))
            if cp.est_total_time_sec < float("inf"):
                time_color = YELLOW if cp.est_total_time_sec > 60 else ACCENT
                rows.append(("Est. Time:",
                             f"~{cp.est_total_time_sec:.0f}s", time_color))

        if can_break and not unbreakable:
            rows.append(("Resistance:", "\u2713 Can Break", ACCENT))
        elif not unbreakable:
            rows.append(("Resistance:", "\u2717 Needs Help", RED))

        used_lasers = getattr(result, "used_lasers", []) or []
        for name in used_lasers:
            short = name.split(" > ", 1)[1] if " > " in name else name
            rows.append(("  Laser:", short, DIM))

        self._apply_row_pool(rows)

    def _apply_row_pool(self, rows: list[tuple[str, str, str]]) -> None:
        """Reconcile the row pool against ``rows`` — reuse existing
        label widgets via ``setText``/restyle, grow the pool if we need
        more slots, and hide any leftover rows. Never deletes widgets
        during a steady-state update, which is what keeps the panel
        flicker-free."""
        section = self._full_section
        if section is None:
            return
        content_layout = section._content_layout
        # Grow the pool if needed.
        while len(self._row_pool) < len(rows):
            container = QWidget(section._content)
            lay = QHBoxLayout(container)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            lbl = QLabel("", container)
            lbl.setFixedWidth(_LABEL_W)
            lay.addWidget(lbl)
            val = QLabel("", container)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(val, 1)
            content_layout.addWidget(container)
            self._row_pool.append((container, lbl, val))

        # Update existing rows in-place.
        for i, (label_text, value_text, color) in enumerate(rows):
            container, lbl, val = self._row_pool[i]
            if lbl.text() != label_text:
                lbl.setText(label_text)
            lbl.setStyleSheet(
                f"font-family: Consolas, monospace; font-size: 8pt; "
                f"color: {DIM}; background: transparent;"
            )
            if val.text() != value_text:
                val.setText(value_text)
            val.setStyleSheet(
                f"font-family: Consolas, monospace; font-size: 9pt; "
                f"font-weight: bold; color: {color}; background: transparent;"
            )
            if not container.isVisible():
                container.setVisible(True)

        # Hide any surplus rows from previous updates.
        for j in range(len(rows), len(self._row_pool)):
            container, _, _ = self._row_pool[j]
            if container.isVisible():
                container.setVisible(False)

    def _build_idle_msg(self, title: str, detail: str) -> None:
        """Show a simple message state."""
        self._clear()

        hdr = QLabel("ADVANCED BREAKABILITY ASSISTANCE", self)
        hdr.setStyleSheet(
            f"font-family: Electrolize, Consolas, monospace; font-size: 9pt; "
            f"font-weight: bold; color: {ACCENT}; background: transparent;"
        )
        self._root.addWidget(hdr)
        self._children.append(hdr)

        sep = _build_separator(self)
        self._root.addWidget(sep)
        self._children.append(sep)

        msg = QLabel(f"{title}\n{detail}", self)
        msg.setStyleSheet(
            f"font-family: Consolas, monospace; font-size: 9pt; "
            f"color: {DIM}; background: transparent; padding: 12px 4px;"
        )
        msg.setWordWrap(True)
        self._root.addWidget(msg)
        self._children.append(msg)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._root.addWidget(spacer)
        self._children.append(spacer)
