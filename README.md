<p align="center">
  <a href="https://robertsspaceindustries.com/community-hub/post/sc-toolbox-v2-is-released-42-testers-and-counting-aUYFfLH5ecHkh">
    <img src="assets/cig_staff_pick.png" alt="CIG Staff Pick" width="600">
  </a>
</p>

<p align="center">
  <strong>We got featured by CIG!!! Thank you everyone! We wouldn't be here if it wasn't for your testing, feedback and support!!!</strong>
</p>

<p align="center">
  <img src="assets/sc_toolbox_logo.png" alt="SC Toolbox" width="128">
</p>

<h1 align="center">SC Toolbox</h1>

<p align="center">
  A lightweight desktop overlay suite for <strong>Star Citizen</strong>.<br>
  Nine gameplay tools — always on top, one hotkey away, no alt-tab required.
</p>

<p align="center">
  <a href="https://github.com/ScPlaceholder/SC-Toolbox-Beta-V1.2/releases/latest">
    <img src="https://img.shields.io/github/v/release/ScPlaceholder/SC-Toolbox-Beta-V1.2?label=Download&style=for-the-badge" alt="Download">
  </a>
  <a href="https://discord.gg/D3hqGU5hNt">
    <img src="https://img.shields.io/badge/Discord-Join-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord">
  </a>
</p>

---

## Download & Install

**[Download the latest installer](https://github.com/ScPlaceholder/SC-Toolbox-Beta-V1.2/releases/latest)** — no Python required, everything is bundled.

1. Download `SC_Toolbox_Setup_X.Y.Z.exe`
2. Run the installer
3. Launch from the desktop shortcut or Start Menu

---

## Tools

| Hotkey | Tool | Description | Data Source |
|--------|------|-------------|------------|
| Shift+1 | **DPS Calculator** | Ship loadout viewer & DPS calculator with power allocator | erkul.games, fleetyards.net |
| Shift+2 | **Cargo Loader** | 3D isometric cargo grid viewer & container optimizer | sc-cargo.space |
| Shift+3 | **Mission Database** | Browse missions, crafting blueprints & mining resources | scmdb.net |
| Shift+4 | **Mining Loadout** | Mining laser, module & gadget optimizer | uexcorp.space |
| Shift+5 | **Market Finder** | Searchable catalog of all purchasable items with buy/sell locations | uexcorp.space |
| Shift+6 | **Trade Hub** | Trade route calculator for single-hop & multi-leg routes | uexcorp.space |
| Shift+7 | **Craft Database** | Crafting recipe browser with material requirements | scmdb.net |
| Shift+8 | **Battle Buddy** | Real-time HUD overlay — tracks kills, deaths, and inventory from game logs | Star Citizen game log |
| — | **Mining Signals** | OCR-powered scanner that reads mining signal percentages from the screen | Screen capture (Tesseract OCR) |

Press **Shift + `** to toggle the launcher window.

---

## Screenshots

<p align="center">
  <img src="assets/screenshots/launcher.png" alt="SC Toolbox Launcher" width="320"><br>
  <em>The main launcher — click any tile or use the hotkey to open a tool</em>
</p>

<p align="center">
  <img src="assets/screenshots/dps_calculator.png" alt="DPS Calculator" width="800"><br>
  <em>DPS Calculator — live ship loadout viewer with weapon DPS, sustained fire, shield, hull, and power data for the Perseus</em>
</p>

<p align="center">
  <img src="assets/screenshots/market_finder.png" alt="Market Finder" width="800"><br>
  <em>Market Finder — browse 272 ships and items with buy prices, crew, cargo, and rental locations</em>
</p>

<p align="center">
  <img src="assets/screenshots/battle_buddy.png" alt="Battle Buddy" width="800"><br>
  <em>Battle Buddy — real-time in-game HUD showing your equipped weapons, ammo counts, and consumables parsed live from game logs</em>
</p>

<p align="center">
  <img src="assets/screenshots/mining_signals.png" alt="Mining Signals" width="800"><br>
  <em>Mining Signals — OCR overlay that identifies ore types and signal strengths from your ship's scanner while you mine</em>
</p>

<p align="center">
  <img src="assets/screenshots/trade_hub.png" alt="Trade Hub" width="800"><br>
  <em>Trade Hub — multi-leg mixed freight route calculator showing 293 routes for a C2 Hercules Starlifter with estimated profits up to 12.6M aUEC</em>
</p>

---

## Features

- **Always-on-top overlay** — stays visible over Star Citizen
- **Global hotkeys** — toggle any tool without alt-tabbing
- **Customizable keybinds** — rebind all hotkeys in Settings
- **Live data** — prices, loadouts, and missions pulled from community APIs
- **Local caching** — fast startup with automatic background refresh
- **WingmanAI integration** — works as a voice-activated WingmanAI skill (optional)

---

## Requirements

- Windows 10 or 11 (64-bit)
- Internet connection (for live game data)

---

## Manual Install (Advanced)

If you prefer to run from source instead of the installer:

1. Install Python 3.10+
2. Run `INSTALL_AND_LAUNCH.bat` (installs dependencies and launches)
3. Or manually: `pip install -r requirements.txt` then `python skill_launcher.py`

---

## Data Sources & Credits

- [erkul.games](https://erkul.games) — DPS calculator data, weapon stats ([Patreon](https://patreon.com/erkul))
- [uexcorp.space](https://uexcorp.space) — Market prices, trade routes, ship data, mining equipment
- [scmdb.net](https://scmdb.net) — Mission database, crafting blueprints, mining resources
- [fleetyards.net](https://fleetyards.net) — Ship hardpoint data
- [sc-cargo.space](https://sc-cargo.space) — Cargo grid layouts

---

## Community

- [Discord](https://discord.gg/D3hqGU5hNt) — Bug reports, feedback, and discussion
