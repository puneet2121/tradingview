"""Read-only provider adapters. Chain snapshots are never converted to trade prints."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import os

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from dashboard import alpaca
from dashboard.data_source import normalize_alpaca_bar
from dashboard.market_alerts import EASTERN
from .config import options, permission


class ProviderError(ValueError):
    pass


class BudgetExhausted(Exception):
    pass


def decimal(value, *, positive=False, nullable=False):
    if value is None and nullable:
        return None
    try:
        result = Decimal(str(value))
        if isinstance(value, bool) or not result.is_finite() or abs(result) > Decimal("1e15") or (positive and result <= 0):
            raise ValueError
        return result
    except (ValueError, InvalidOperation, TypeError):
        raise ProviderError("Invalid provider numeric value.") from None


def timestamp(value):
    try:
        result = parse_datetime(value)
        if result is None or timezone.is_naive(result):
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ProviderError("Invalid provider timestamp.") from None


def stored_decimal(value, integer_digits, *, nullable=False):
    result = decimal(value, positive=True, nullable=nullable)
    if result is not None and (result >= Decimal(10)**integer_digits or result != result.quantize(Decimal("0.000001"))):
        raise ProviderError("Provider value exceeds supported numeric precision.")
    return result


class AlpacaOpra:
    code = "alpaca_opra"
    name = "Alpaca OPRA"
    capabilities = {"transactions": True, "execution_quotes": False, "sweeps": False, "blocks": False,
                    "historical_greeks": False, "historical_oi": False, "dark_pool": False,
                    "stock_bars": True, "coverage": "Watchlist only; up to 100 contracts per ticker, nearest strikes, next 60 days"}

    def __init__(self, guard=lambda: None, budget=12):
        self.guard, self.remaining = guard, budget
        self.opra_verified = False

    @staticmethod
    def readiness():
        if not permission("allow_storage"):
            return "Storage permission not confirmed"
        if not os.environ.get("ALPACA_DATA_API_KEY") or not os.environ.get("ALPACA_DATA_SECRET_KEY"):
            return "Environment credentials unavailable"
        cfg = alpaca.data_config()
        if not cfg["enabled"]:
            return "Alpaca data connection disabled"
        if options().get("option_feed", "opra") != "opra":
            return "Indicative feed is not a verified transaction feed; OPRA is required"
        if options().get("delay_seconds", 900) not in (0, 900):
            return "Feed delay must be 0 or 900 seconds"
        return ""

    def get(self, path, params=None, *, paper=False):
        self.guard()
        if self.remaining <= 0:
            raise BudgetExhausted
        self.remaining -= 1
        try:
            response = alpaca.read_get(path, params, paper=paper)
            if not isinstance(response, dict):
                raise ProviderError("Provider returned an invalid response object.")
            return response
        except alpaca.AlpacaDataError as exc:
            raise ProviderError(str(exc)) from None

    def catalog(self, symbol, now):
        today = now.astimezone(EASTERN).date()
        params = {"underlying_symbols": symbol, "status": "active", "limit": 10000,
                  "expiration_date_gte": today.isoformat(), "expiration_date_lte": (today + timedelta(days=60)).isoformat()}
        response = self.get("/v2/options/contracts", params, paper=True)
        # No silent truncation when the contract universe itself exceeds our budget.
        if response.get("next_page_token"):
            raise ProviderError("Contract catalog exceeds the bounded scanner; narrow the watchlist.")
        latest = self.get(f"/v2/stocks/{symbol}/trades/latest", {"feed": alpaca.data_config()["stock_feed"]})
        spot = decimal((latest.get("trade") or {}).get("p"), positive=True)
        asset = self.get(f"/v2/assets/{symbol}", paper=True)
        company = str(asset.get("name") or "")[:160]
        rows = []
        for item in response.get("option_contracts") or []:
            if item.get("underlying_symbol") != symbol or item.get("type") not in ("call", "put"):
                continue
            strike = stored_decimal(item.get("strike_price"), 14)
            multiplier = stored_decimal(item.get("size"), 10, nullable=True)
            try:
                expiration = date.fromisoformat(item["expiration_date"]).isoformat()
                if not isinstance(item["symbol"], str) or not 1 <= len(item["symbol"]) <= 64:
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                raise ProviderError("Provider returned invalid contract metadata.") from None
            rows.append({"symbol": item["symbol"], "underlying": symbol, "company_name": company,
                         "strike": str(strike), "expiration": expiration, "option_type": item["type"].upper(),
                         "multiplier": str(multiplier) if multiplier is not None else None})
        rows.sort(key=lambda row: (abs(Decimal(row["strike"]) - spot), row["expiration"], row["symbol"]))
        return rows[:100], len(rows)

    def trades(self, contracts, start, end, token=None):
        # The historical endpoint has no documented feed selector. Verify explicit
        # OPRA entitlement each pass before using the subscriber historical tape.
        if not self.opra_verified:
            self.get("/v1beta1/options/trades/latest", {"symbols": contracts[0], "feed": "opra"})
            self.opra_verified = True
        params = {"symbols": ",".join(contracts), "start": start, "end": end, "sort": "asc", "limit": 1000}
        if token:
            params["page_token"] = token
        return self.get("/v1beta1/options/trades", params)

    def stock_bars(self, symbol, start, end, token=None):
        params = {"symbols": symbol, "start": start.isoformat(), "end": end.isoformat(), "timeframe": "5Min",
                  "sort": "asc", "limit": 10000, "feed": alpaca.data_config()["stock_feed"], "adjustment": "raw"}
        if token:
            params["page_token"] = token
        response = self.get("/v2/stocks/bars", params)
        try:
            return [normalize_alpaca_bar(row) for row in response.get("bars", {}).get(symbol, [])], response.get("next_page_token")
        except (alpaca.AlpacaDataError, TypeError, AttributeError):
            raise ProviderError("Provider returned invalid historical candles.") from None


PROVIDERS = {"alpaca": AlpacaOpra}


def selected():
    return PROVIDERS.get(options().get("provider", "none"))


def provider_info():
    provider = selected()
    reason = provider.readiness() if provider else "Verified flow provider not connected"
    return {"code": provider.code if provider else "none", "name": provider.name if provider else "No flow provider",
            "ready": bool(provider and not reason), "reason": reason,
            "mode": "Delayed / polled" if options().get("delay_seconds", 900) else "Real-time entitlement / polled",
            "delay_seconds": options().get("delay_seconds", 900), "capabilities": provider.capabilities if provider else {},
            "storage_allowed": permission("allow_storage"), "sharing_allowed": permission("allow_sharing"),
            "discord_allowed": permission("allow_discord")}
