"""
main.py — Entry point for Recipe Finder with Nutrition Analysis.

HOW THIS FILE WORKS:
  1. check()   — makes sure all required Python libraries are installed.
                 If anything is missing, it prints a helpful message and stops.
  2. After the check passes, it sets up the logger (the diary that records
     everything the app does), then opens the main GUI window.

RUN THIS FILE to start the app:
  python main.py
  OR just double-click  run.bat  (Windows)  /  run.sh  (Mac/Linux)
"""

import sys
import os
import platform
import importlib.metadata


def check():
    """
    Check that every required library is installed before we try to open
    the GUI window.  If anything is missing, we print a clear error message
    and exit — much friendlier than a confusing ImportError crash later.
    """
    missing = []

    # Each tuple is:  (module_to_import,  package_name_for_pip)
    # We try importing each module; if it fails, we add it to the missing list.
    required = [
        ("PIL",        "Pillow"),        # for showing recipe images in the window
        ("pandas",     "pandas"),        # for organising nutrition data into tables
        ("matplotlib", "matplotlib"),    # for drawing bar/pie charts
        ("requests",   "requests"),      # for making web requests to Spoonacular API
        ("dotenv",     "python-dotenv"), # for loading API key from the .env file
    ]

    for module_name, package_name in required:
        try:
            __import__(module_name)
        except ImportError:
            missing.append(package_name)

    if missing:
        print("\n❌  Missing packages:", ", ".join(missing))
        print("   Fix this by running:")
        print("   pip install " + " ".join(missing))
        print("\n   Or run:  pip install -r requirements.txt")
        sys.exit(1)  # stop the app here — nothing will work without these

def _log_startup_info(log):
    """Log Python version, OS, and key package versions at startup."""
    log.info("Python %s | %s %s | %s",
             sys.version.split()[0],
             platform.system(), platform.release(),
             platform.machine())
    packages = ("requests", "Pillow", "pandas", "matplotlib", "python-dotenv", "reportlab")
    for pkg in packages:
        try:
            ver = importlib.metadata.version(pkg)
            log.info("  %-20s %s", pkg, ver)
        except importlib.metadata.PackageNotFoundError:
            log.info("  %-20s (not installed)", pkg)


def _install_exception_hook(log):
    """Write full traceback to log file on any unhandled exception."""
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical("Unhandled exception — app crashed",
                     exc_info=(exc_type, exc_value, exc_tb))
    sys.excepthook = _hook


if __name__ == "__main__":
    # ── Step 1: verify all libraries are installed ─────────────────────────────
    check()

    # ── Step 2: set up the logger ──────────────────────────────────────────────
    # The logger writes a timestamped diary to  recipe_app.log  in this folder.
    # If something goes wrong, open that file to see exactly what happened.
    from logger import setup_logger
    log = setup_logger("Main")
    log.info("=" * 60)
    log.info("Starting Recipe Finder")
    _install_exception_hook(log)
    _log_startup_info(log)

    # ── Step 3: open the GUI window ────────────────────────────────────────────
    # RecipeApp is a subclass of tk.Tk — it IS the main window.
    # app.mainloop() hands control to Tkinter; it stays here until the user
    # closes the window.
    from gui import RecipeApp
    app = RecipeApp()
    app.mainloop()           # blocks here until the window is closed

    log.info("App closed.")
