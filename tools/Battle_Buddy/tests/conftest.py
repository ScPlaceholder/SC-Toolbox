import os
import sys

# Bootstrap Battle Buddy's own package onto sys.path
_BB_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _BB_ROOT not in sys.path:
    sys.path.insert(0, _BB_ROOT)
