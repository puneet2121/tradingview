from __future__ import annotations

from decimal import Decimal
from collections import OrderedDict, deque
import threading
import time
from typing import Any

import requests
from django.conf import settings


CRYPTO_SYMBOL_MAP = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
}


class AlpacaOrderError(Exception):
    pass


class AlpacaDataError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


PAPER_API = "https://paper-api.alpaca.markets"
DATA_API = "https://data.alpaca.markets"
_read_lock = threading.RLock()
_read_cache = OrderedDict()
_fetch_locks = [threading.RLock() for _ in range(64)]
_read_times = deque()
_retry_at = 0.0


def data_config():
    return settings.ALPACA_DATA


def data_enabled():
    return bool(data_config()["enabled"])


def data_headers():
    config = data_config()
    if not data_enabled() or not config["key"] or not config["secret"]:
        raise AlpacaDataError("Alpaca data is not configured. Run manage.py configure_alpaca locally.")
    return {"APCA-API-KEY-ID": config["key"], "APCA-API-SECRET-KEY": config["secret"]}


def read_get(path, params=None, *, paper=False):
    """GET only, fixed hosts, no redirects or raw upstream error/credential output."""
    global _retry_at
    headers = data_headers()
    if not path.startswith("/v2/") and not path.startswith("/v1beta1/"):
        raise AlpacaDataError("Unsupported Alpaca endpoint.")
    with _read_lock:
        now = time.monotonic()
        while _read_times and _read_times[0] <= now - 60:
            _read_times.popleft()
        if now < _retry_at or len(_read_times) >= 180:
            raise AlpacaDataError("Alpaca request limit reached. Wait a minute before retrying.")
        _read_times.append(now)
    try:
        response = requests.get(
            (PAPER_API if paper else DATA_API) + path, params=params,
            headers=headers, timeout=(5, 15), allow_redirects=False,
        )
    except requests.RequestException:
        raise AlpacaDataError("Cannot reach Alpaca. Check the connection and retry.") from None
    if response.status_code == 429:
        with _read_lock:
            _retry_at = time.monotonic() + 60
        raise AlpacaDataError("Alpaca rate limit reached. Wait a minute before retrying.")
    if response.status_code == 401:
        raise AlpacaDataError("Alpaca authentication failed. Check your paper API keys.")
    if response.status_code == 403:
        raise AlpacaDataError("Alpaca denied access. Check your paper account and feed subscription.")
    if response.status_code != 200:
        raise AlpacaDataError(f"Alpaca request failed (HTTP {response.status_code}).", status=response.status_code)
    try:
        return response.json()
    except ValueError:
        raise AlpacaDataError("Alpaca returned an invalid response.") from None


def cached_read(key, fetch, ttl=10):
    data_headers()
    config = data_config()
    cache_key = (config["key"], config["secret"], config["stock_feed"], config["option_feed"], key)
    # Per-key coalescing keeps slow chain requests from blocking all chart reads.
    with _fetch_locks[hash(cache_key) % len(_fetch_locks)]:
        with _read_lock:
            cached = _read_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < ttl:
                return cached[1]
        result = fetch()
        with _read_lock:
            _read_cache[cache_key] = (time.monotonic(), result)
            _read_cache.move_to_end(cache_key)
            while len(_read_cache) > 256:
                _read_cache.popitem(last=False)
        return result


def connection_info():
    config = data_config()
    return {
        "enabled": data_enabled(), "configured": bool(config["key"] and config["secret"]),
        "mode": "paper", "read_only": not manual_enabled(), "manual_trading_enabled": manual_enabled(), "stock_feed": config["stock_feed"],
        "option_feed": config["option_feed"],
        "stock_label": "IEX only" if config["stock_feed"] == "iex" else "SIP",
        "option_label": "Indicative: delayed trades, modified quotes" if config["option_feed"] == "indicative" else "OPRA",
        "scanner_source": "Existing Yahoo / Hyperliquid feeds",
    }


def read_account():
    def fetch():
        account = read_get("/v2/account", paper=True)
        positions = read_get("/v2/positions", paper=True)
        orders = read_get("/v2/orders", {"status": "all", "limit": 50, "direction": "desc"}, paper=True)
        clock = read_get("/v2/clock", paper=True)
        return {
            "account_id": account.get("id"),
            "connected": True, "checked_at": int(time.time()),
            "account": {key: account.get(key) for key in (
                "status", "currency", "equity", "cash", "buying_power", "last_equity",
                "trading_blocked", "options_trading_level", "options_buying_power",
            )},
            "positions": [{key: row.get(key) for key in (
                "symbol", "asset_class", "qty", "side", "avg_entry_price", "current_price",
                "market_value", "unrealized_pl", "unrealized_plpc",
            )} for row in positions],
            "orders": [{key: row.get(key) for key in (
                "id", "symbol", "side", "qty", "filled_qty", "filled_avg_price", "status",
                "type", "limit_price", "submitted_at", "filled_at",
                "client_order_id", "updated_at", "position_intent",
            )} for row in orders],
            "clock": {key: clock.get(key) for key in ("is_open", "timestamp", "next_open", "next_close")},
        }
    return {**connection_info(), **cached_read("account", fetch)}


def manual_enabled():
    return bool(getattr(settings, "ALPACA_MANUAL_PAPER_ENABLED", False) and data_enabled())


def invalidate_account_cache():
    with _read_lock:
        for key in list(_read_cache):
            if key[-1] == "account":
                del _read_cache[key]


def enabled() -> bool:
    return bool(
        getattr(settings, "ALPACA_PAPER_ENABLED", False)
        and getattr(settings, "ALPACA_API_KEY", "")
        and getattr(settings, "ALPACA_SECRET_KEY", "")
    )


def broker_symbol(symbol: str) -> str:
    return CRYPTO_SYMBOL_MAP.get(symbol.upper(), symbol.upper())


def time_in_force(symbol: str) -> str:
    if symbol.upper() in CRYPTO_SYMBOL_MAP:
        return "gtc"
    return getattr(settings, "ALPACA_ORDER_TIME_IN_FORCE", "day")


def order_side(position_side: str, closing: bool = False) -> str:
    if position_side == "LONG":
        return "sell" if closing else "buy"
    return "buy" if closing else "sell"


def submit_market_order(symbol: str, side: str, quantity: Decimal, closing: bool = False) -> dict[str, Any]:
    if not enabled():
        raise AlpacaOrderError("Alpaca paper trading is not enabled.")

    qty = quantity.normalize()
    payload = {
        "symbol": broker_symbol(symbol),
        "qty": format(qty, "f"),
        "side": order_side(side, closing=closing),
        "type": "market",
        "time_in_force": time_in_force(symbol),
    }
    url = f"{getattr(settings, 'ALPACA_PAPER_TRADING_BASE_URL').rstrip('/')}/v2/orders"
    response = requests.post(
        url,
        json=payload,
        headers={
            "APCA-API-KEY-ID": getattr(settings, "ALPACA_API_KEY"),
            "APCA-API-SECRET-KEY": getattr(settings, "ALPACA_SECRET_KEY"),
        },
        timeout=10,
    )
    try:
        data = response.json()
    except ValueError:
        data = {"message": response.text}
    if response.status_code >= 400:
        message = data.get("message") if isinstance(data, dict) else None
        raise AlpacaOrderError(message or f"Alpaca rejected order with HTTP {response.status_code}.")
    return data
