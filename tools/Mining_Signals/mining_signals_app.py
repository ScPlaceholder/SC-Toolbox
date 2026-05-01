# Mining Signals — powered by Star Citizen Mining Signals spreadsheet
#
# This is the sole entry point.  The sys.path adjustment here is the
# only place it exists — all internal modules use relative imports.

import os
import sys

# Force Qt Multimedia to use the Windows-native backend BEFORE any
# Qt modules load. Qt6's default FFmpeg backend fails on our bundled
# mp3 with "# channels not specified" (Qt reads this env var once at
# plugin load, so the assignment must precede every Qt import).
if os.name == "nt" and not os.environ.get("QT_MEDIA_BACKEND"):
    os.environ["QT_MEDIA_BACKEND"] = "windows"

# Bootstrap project root and skill directory
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')))
from shared.app_bootstrap import bootstrap_skill  # noqa: E402
bootstrap_skill(__file__)

from ui.app import main  # noqa: E402


if __name__ == "__main__":
    main()
