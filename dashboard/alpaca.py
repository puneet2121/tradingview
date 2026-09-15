from __future__ import annotations

from decimal import Decimal
from typing import Any

import requests
from django.conf import settings


CRYPTO_SYMBOL_MAP = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
}


class AlpacaOrderError(Exception):
    pass


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
