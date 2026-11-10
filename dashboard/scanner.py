from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid

from django.conf import settings
from django.db import OperationalError, ProgrammingError

from .paper_trading import scan_enabled_watchlist


logger = logging.getLogger(__name__)
_scanner_started = False
_alert_scanner_started = False
_alpaca_scanner_started = False


def should_start_scanner(setting_name="PAPER_SCANNER_ENABLED") -> bool:
    if not getattr(settings, setting_name, True):
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


def alert_scanner_loop() -> None:
    from django.db import close_old_connections
    from .market_alerts import scan_market_alerts

    token = uuid.uuid4().hex
    while True:
        close_old_connections()
        try:
            scan_market_alerts(token)
        except (OperationalError, ProgrammingError):
            logger.debug("Alert scanner waiting for the database.")
        except Exception:
            logger.exception("Market alert scanner iteration failed.")
        finally:
            close_old_connections()
        time.sleep(60)


def start_alert_scanner_once() -> None:
    global _alert_scanner_started
    if _alert_scanner_started or os.environ.get("RUN_MAIN") == "false" or not should_start_scanner("ALERT_SCANNER_ENABLED"):
        return
    _alert_scanner_started = True
    threading.Thread(target=alert_scanner_loop, name="market-alert-scanner", daemon=True).start()


def alpaca_scanner_loop():
    from django.db import close_old_connections
    from .alpaca_bot import scan_once
    from .models import AlpacaBotConfig

    token = uuid.uuid4().hex
    while True:
        close_old_connections()
        try:
            scan_once(token)
        except (OperationalError, ProgrammingError):
            logger.debug("Alpaca bot waiting for database migration.")
        except Exception:
            # Keep upstream failures out of logs; API credentials never belong there.
            logger.warning("Alpaca bot scan interrupted; see the dashboard for status.")
            try:
                AlpacaBotConfig.objects.filter(pk=1, lease_owner=token).update(status="Scan interrupted: check connection, quotes, permissions, and pending orders")
            except (OperationalError, ProgrammingError):
                pass
        finally:
            close_old_connections()
        time.sleep(30)


def start_alpaca_scanner_once():
    from .alpaca_bot import available

    global _alpaca_scanner_started
    if _alpaca_scanner_started or not should_start_scanner("ALPACA_AUTO_PAPER_ENABLED") or not available():
        return
    if "runserver" in sys.argv and "--noreload" not in sys.argv and os.environ.get("RUN_MAIN") != "true":
        return
    _alpaca_scanner_started = True
    threading.Thread(target=alpaca_scanner_loop, name="alpaca-paper-bot", daemon=True).start()
