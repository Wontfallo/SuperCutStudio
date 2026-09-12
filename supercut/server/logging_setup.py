"""Rotating file logging for the desktop/web studio."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

from ..paths import logs_dir

_configured = False


def setup_logging(level: int = logging.INFO, filename: str = "supercut.log") -> Path:
    """Configure root logging to a rotating file (and stderr when a console exists)."""
    global _configured
    log_path = logs_dir() / filename
    if _configured:
        return log_path

    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    fh = logging.handlers.RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    if sys.stderr is not None and not getattr(sys, "frozen", False):
        try:
            sh = logging.StreamHandler(sys.stderr)
            sh.setFormatter(fmt)
            root.addHandler(sh)
        except Exception:
            pass

    # Keep uvicorn's access log quiet in the file
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _configured = True
    return log_path


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or "supercut")


__all__ = ["get_logger", "setup_logging"]
