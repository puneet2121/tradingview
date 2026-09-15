from __future__ import annotations

import logging
import os
import sys
import threading
import time

from django.conf import settings
from django.db import OperationalError, ProgrammingError

from .paper_trading import scan_enabled_watchlist


logger = logging.getLogger(__name__)
_scanner_started = False


def should_start_scanner() -> bool:
    if not getattr(settings, "PAPER_SCANNER_ENABLED", True):
        return False
    blocked_commands = {"check", "makemigrations", "migrate", "shell", "test", "collectstatic"}
    if any(command in sys.argv for command in blocked_commands):
        return False
    return "uvicorn" in sys.argv[0] or "runserver" in sys.argv


def scanner_loop() -> None:
    interval = int(getattr(settings, "PAPER_SCANNER_INTERVAL_SECONDS", 60))
    while True:
        try:
            scan_enabled_watchlist()
        except (OperationalError, ProgrammingError):
            logger.debug("Paper scanner skipped because database is not ready.", exc_info=True)
        except Exception:
            logger.exception("Paper scanner iteration failed.")
        time.sleep(interval)


def start_scanner_once() -> None:
    global _scanner_started
    if _scanner_started or os.environ.get("RUN_MAIN") == "false" or not should_start_scanner():
        return

    _scanner_started = True
    thread = threading.Thread(target=scanner_loop, name="paper-trade-scanner", daemon=True)
    thread.start()
