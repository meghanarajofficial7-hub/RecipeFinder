"""
logger.py — Centralised logging for the Recipe Finder app.
Writes to both the terminal and logs/recipe_app.log.
Log file rotates at 1 MB; up to 3 old copies are kept before deletion.
"""

import logging, os
from logging.handlers import RotatingFileHandler

def setup_logger(name="RecipeApp"):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    log_dir  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "recipe_app.log")

    # Rotate at 1 MB; keep 3 backups (recipe_app.log.1, .2, .3) then delete oldest
    fh = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger
