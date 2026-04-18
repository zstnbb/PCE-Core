# SPDX-License-Identifier: Apache-2.0
"""PCE – One-click launcher.

Double-click this file (or run `python pce.py`) to start PCE as a
desktop application with system tray icon and auto-opened dashboard.
"""

import multiprocessing
import os
import sys

# PyInstaller console=False sets stdout/stderr to None – patch early.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

if __name__ == "__main__":
    multiprocessing.freeze_support()

    from pce_app.__main__ import main
    main()
