from datetime import date
import re

from .config import PREMIUMS
from .providers import decimal


def symbols(value):
    if not isinstance(value, list) or len(value) > 25 or any(not isinstance(s, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]{0,14}", s.strip()) for s in value):
        raise ValueError("Use up to 25 US ticker symbols.")
    return list(dict.fromkeys(s.strip().upper() for s in value))


def integer(value, low, high):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)) or not low <= int(value) <= high:
        raise ValueError(f"Expected a whole number from {low} to {high}.")
    return int(value)


def boolean(value):
    if not isinstance(value, bool):
        raise ValueError("Expected true or false.")
    return value


def filters(payload):
    allowed = {"symbols", "min_premium", "option_type", "min_quantity", "min_score", "classification", "structure", "expiry_from", "expiry_to", "strike", "min_repeats", "confirmation", "min_vol_oi"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Unsupported flow filter.")
    result = {"symbols": symbols(payload.get("symbols", [])), "min_premium": integer(payload.get("min_premium", 50000), 0, 1000000000),
              "min_quantity": integer(payload.get("min_quantity", 0), 0, 1000000000), "min_score": integer(payload.get("min_score", 0), 0, 100),
              "min_repeats": integer(payload.get("min_repeats", 0), 0, 10000)}
    for key, allowed_values in {"option_type": ("", "CALL", "PUT"), "classification": ("", "ASK_SIDE", "BID_SIDE", "UNKNOWN"),
                               "structure": ("", "SWEEP", "BLOCK", "MULTI_LEG", "HEDGE_CANDIDATE", "UNKNOWN"),
                               "confirmation": ("", "BULLISH", "BEARISH")}.items():
        value = payload.get(key, "")
        if value not in allowed_values:
            raise ValueError(f"Unsupported {key}.")
        result[key] = value
    for key in ("expiry_from", "expiry_to"):
        if payload.get(key):
            result[key] = date.fromisoformat(payload[key]).isoformat()
    if result.get("expiry_from") and result.get("expiry_to") and result["expiry_from"] > result["expiry_to"]:
        raise ValueError("Expiry start must precede expiry end.")
    for key in ("strike", "min_vol_oi"):
        if payload.get(key) not in (None, ""):
            result[key] = str(decimal(payload[key], positive=True))
    return result


def collection(payload):
    if not isinstance(payload, dict) or set(payload) != {"enabled", "symbols", "min_premium", "revision", "sec_enabled", "sec_ciks"}:
        raise ValueError("Provide the complete collection settings and revision.")
    tickers = symbols(payload["symbols"])
    minimum = integer(payload["min_premium"], 50000, 5000000)
    if minimum not in PREMIUMS:
        raise ValueError("Choose a listed minimum premium.")
    if payload["enabled"] and not tickers:
        raise ValueError("Add at least one ticker before enabling collection.")
    ciks = payload["sec_ciks"]
    if not isinstance(ciks, list) or len(ciks) > 5 or any(not isinstance(c, str) or not re.fullmatch(r"\d{1,10}", c) for c in ciks):
        raise ValueError("Use up to five numeric SEC CIKs.")
    return {"enabled": boolean(payload["enabled"]), "symbols": tickers, "min_premium": minimum,
            "revision": integer(payload["revision"], 1, 1000000000), "sec_enabled": boolean(payload["sec_enabled"]),
            "sec_ciks": list(dict.fromkeys(c.zfill(10) for c in ciks))}
