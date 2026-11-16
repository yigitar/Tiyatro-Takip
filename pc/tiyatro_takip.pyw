"""Double-click launcher: starts the Tiyatro Takip PC app without a console window.
Logic lives in pc/launcher.py (see there)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pc.launcher import main  # noqa: E402

sys.exit(main())
