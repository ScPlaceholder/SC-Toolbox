=====================================================================
  SC_Toolbox — Installer Build System
=====================================================================

OVERVIEW
--------
This directory contains everything needed to build a standalone Windows
installer for SC Toolbox. The installer bundles a portable Python
interpreter with all dependencies pre-installed, so end users do NOT
need Python on their system.

The output is a single SC_Toolbox_Setup_X.Y.Z.exe file that can be
uploaded as a GitHub Release asset.


PREREQUISITES
-------------
1. Internet connection (to download Python embeddable + pip packages)
2. Inno Setup 6 installed
   Download: https://jrsoftware.org/isinfo.php
   Make sure iscc.exe is either:
   - In the default location (C:\Program Files (x86)\Inno Setup 6\)
   - Or on your system PATH


HOW TO BUILD
------------
1. Open a terminal in this build/ directory
2. Run:  build_installer.bat
3. Wait for it to complete (downloads ~100 MB on first run)
4. Output:  build\Output\SC_Toolbox_Setup_1.2.0.exe


BUILD PROCESS
-------------
The build script performs these steps:

1. Downloads Python 3.12.9 embeddable package (python.org)
2. Extracts it and enables site-packages
3. Bootstraps pip via get-pip.py
4. Installs PySide6, requests, pynput into the embedded Python
5. Stages only runtime files into build\staging\
6. Runs Inno Setup to produce the installer .exe


FILES EXCLUDED FROM INSTALLER
-----------------------------
The following are stripped from the installer to reduce size and remove
development-only content:

Source Control & IDE:
  .git/                         Git repository history
  .claude/                      Claude Code settings

Development Tools:
  DEBUG_LAUNCH.bat              Debug launcher (verbose console)
  INSTALL_AND_LAUNCH.bat        Full installer (downloads Python)
  LAUNCH.bat                    Dev launcher (finds system Python)
  SC_Toolbox.vbs                Dev VBS launcher (finds system Python)
  pyproject.toml                Kept (version info read at runtime)
  requirements.txt              Not needed (deps pre-installed)
  generate_brand_guide.py       Brand guide generator
  SC_Toolbox_Brand_Guide.docx   Design documentation
  main.py                       WingmanAI integration module
  tools/                        Localization build tools

Tests:
  **/tests/                     All unit test directories
  **/.pytest_cache/             Pytest cache directories
  **/conftest.py                Pytest configuration

Python Bytecode:
  **/__pycache__/               Compiled .pyc files (auto-regenerated)

Runtime Caches (regenerated on first launch):
  .cargo_cache.json             Cargo Loader API cache
  .erkul_cache.json             DPS Calculator erkul cache
  .fy_hardpoints_cache.json     DPS Calculator fleetyards cache
  .uex_cache.json               Market Finder UEX cache
  .scmdb_cache*.json            Mission Database SCMDB caches
  .craft_cache/                 Craft Database cache directory
  .api_cache/                   Mining Loadout API cache directory

Logs & Lock Files:
  **/*.log                      Runtime log files
  **/*.log.*                    Rotated log files
  nul.lock                      Process lock file
  _debug.log                    Debug output

DPS Calculator Dev Files:
  erkul_audit.py                Data validation audit
  erkul_parity_audit.py         Erkul parity checker
  erkul_power_formulas.js       Reference formulas
  ERKUL_PARITY_FIX_PROMPT.md    Debug notes
  dps_loadout_audit.py          Loadout audit script
  dps_power_audit.py            Power audit script
  audit_report.txt              Audit results
  dps_loadout_audit_report.txt  Audit results
  dps_power_audit_report.txt    Audit results

Other Dev Files:
  INSTALL.md                    Per-skill install instructions
  validate_calc.py              Mining Loadout validation
  generate_layout.py            Cargo layout generator
  cargo_grid_editor.html        Cargo grid editor (browser tool)
  Per-skill requirements.txt    Not needed (deps pre-installed)


FILES INCLUDED IN INSTALLER
----------------------------
  python/                       Bundled Python 3.12 + PySide6 + deps
  skill_launcher.py             Main entry point
  skill_launcher_settings.json  Default settings
  pyproject.toml                Version metadata
  README.txt                    User manual
  SC_Toolbox.vbs                Installed-version launcher
  core/                         Process manager, skill registry
  shared/                       Infrastructure (minus tests)
  ui/                           Launcher UI
  skills/                       All 7 tools (minus tests/caches/dev)
  locales/                      Translation template


UPDATING THE VERSION
--------------------
1. Edit pyproject.toml:  version = "X.Y.Z"
2. Edit SC_Toolbox_Installer.iss:  #define MyAppVersion "X.Y.Z"
3. Run build_installer.bat
4. Upload Output\SC_Toolbox_Setup_X.Y.Z.exe to GitHub Releases


GITHUB RELEASE WORKFLOW
-----------------------
1. Build the installer (see above)
2. Tag the release:  git tag vX.Y.Z && git push --tags
3. Create a GitHub Release from the tag
4. Attach SC_Toolbox_Setup_X.Y.Z.exe as a release asset
5. Update the repo README with a download link:
   https://github.com/YOUR_USER/SC-Toolbox/releases/latest
