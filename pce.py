"""PCE – One-click launcher.

Double-click this file (or run `python pce.py`) to start PCE as a
desktop application with system tray icon and auto-opened dashboard.
"""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()

    from pce_app.__main__ import main
    main()
