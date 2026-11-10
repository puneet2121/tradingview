from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import math
import re
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from . import alpaca


@dataclass(frozen=True)
class Symbol:
    value: str
    label: str
    provider: str
    exchange: str
    group: str


HYPERLIQUID_WS_URL = "wss://api.hyperliquid.xyz/ws"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
HISTORY_CACHE_TTL_SECONDS = 30
QUOTE_CACHE_TTL_SECONDS = 15

_history_cache: dict[tuple[str, str], tuple[float, list[dict[str, float | int]]]] = {}
_quote_cache: dict[str, tuple[float, dict[str, float | int | str | None]]] = {}

TIMEFRAMES = {
    "1m": {"yf_interval": "1m", "yf_period": "1d", "hl_interval": "1m", "seconds": 60},
    "3m": {
        "yf_interval": "1m",
        "yf_period": "5d",
        "yf_resample_rule": "3min",
        "hl_interval": "3m",
        "seconds": 180,
    },
    "5m": {"yf_interval": "5m", "yf_period": "5d", "hl_interval": "5m", "seconds": 300},
    "15m": {"yf_interval": "15m", "yf_period": "5d", "hl_interval": "15m", "seconds": 900},
    "30m": {"yf_interval": "30m", "yf_period": "1mo", "hl_interval": "30m", "seconds": 1800},
    "1h": {"yf_interval": "60m", "yf_period": "1mo", "hl_interval": "1h", "seconds": 3600},
    "4h": {
        "yf_interval": "60m",
        "yf_period": "3mo",
        "yf_resample_rule": "4h",
        "hl_interval": "4h",
        "seconds": 14400,
    },
    "1d": {"yf_interval": "1d", "yf_period": "1y", "hl_interval": "1d", "seconds": 86400},
}

SYMBOLS = [
    Symbol("BTC", "BTC-PERP", "hyperliquid", "Hyperliquid", "Crypto"),
    Symbol("ETH", "ETH-PERP", "hyperliquid", "Hyperliquid", "Crypto"),
    Symbol("SOL", "SOL-PERP", "hyperliquid", "Hyperliquid", "Crypto"),
    Symbol("HYPE", "HYPE-PERP", "hyperliquid", "Hyperliquid", "Crypto"),
    Symbol("AAPL", "Apple", "yfinance", "NASDAQ", "US Mega Cap"),
    Symbol("MSFT", "Microsoft", "yfinance", "NASDAQ", "US Mega Cap"),
    Symbol("NVDA", "Nvidia", "yfinance", "NASDAQ", "US Mega Cap"),
    Symbol("AMZN", "Amazon", "yfinance", "NASDAQ", "US Mega Cap"),
    Symbol("GOOGL", "Alphabet", "yfinance", "NASDAQ", "US Mega Cap"),
    Symbol("META", "Meta", "yfinance", "NASDAQ", "US Mega Cap"),
    Symbol("TSLA", "Tesla", "yfinance", "NASDAQ", "US Mega Cap"),
    Symbol("SPY", "SPDR S&P 500 ETF", "yfinance", "NYSE Arca", "US ETFs"),
    Symbol("QQQ", "Invesco QQQ ETF", "yfinance", "NASDAQ", "US ETFs"),
    Symbol("IWM", "iShares Russell 2000 ETF", "yfinance", "NYSE Arca", "US ETFs"),
    Symbol("RELIANCE.NS", "Reliance", "yfinance", "NSE", "India Large Cap"),
    Symbol("TCS.NS", "TCS", "yfinance", "NSE", "India Large Cap"),
    Symbol("INFY.NS", "Infosys", "yfinance", "NSE", "India Large Cap"),
    Symbol("HDFCBANK.NS", "HDFC Bank", "yfinance", "NSE", "India Banking"),
    Symbol("ICICIBANK.NS", "ICICI Bank", "yfinance", "NSE", "India Banking"),
    Symbol("SBIN.NS", "State Bank of India", "yfinance", "NSE", "India Banking"),
    Symbol("AXISBANK.NS", "Axis Bank", "yfinance", "NSE", "India Banking"),
    Symbol("KOTAKBANK.NS", "Kotak Mahindra Bank", "yfinance", "NSE", "India Banking"),
    Symbol("TATAMOTORS.NS", "Tata Motors", "yfinance", "NSE", "India Auto"),
    Symbol("MARUTI.NS", "Maruti Suzuki", "yfinance", "NSE", "India Auto"),
    Symbol("M&M.NS", "Mahindra & Mahindra", "yfinance", "NSE", "India Auto"),
    Symbol("BAJFINANCE.NS", "Bajaj Finance", "yfinance", "NSE", "India Financials"),
    Symbol("LT.NS", "Larsen & Toubro", "yfinance", "NSE", "India Industrials"),
    Symbol("TITAN.NS", "Titan", "yfinance", "NSE", "India Consumer"),
    Symbol("ITC.NS", "ITC", "yfinance", "NSE", "India Consumer"),
    Symbol("NIFTYBEES.NS", "Nippon Nifty 50 Bees", "yfinance", "NSE", "India ETFs"),
]


def get_symbol(symbol: str) -> Symbol:
    symbol = normalize_symbol(symbol)
    for item in SYMBOLS:
        if item.value == symbol:
            return item
    if "." in symbol or symbol.replace("-", "").isalnum():
        return Symbol(symbol, symbol, "yfinance", infer_exchange(symbol), "Custom")
    raise ValueError(f"Unsupported symbol: {symbol}")


def normalize_symbol(symbol: str, market: str | None = None) -> str:
    symbol = symbol.strip().upper()
    if market in {"india", "india_nse"} and "." not in symbol:
        return f"{symbol}.NS"
    if market == "india_bse" and "." not in symbol:
        return f"{symbol}.BO"
    return symbol


def infer_exchange(symbol: str) -> str:
    if symbol.endswith(".NS"):
        return "NSE"
    if symbol.endswith(".BO"):
        return "BSE"
    return "US"


def get_timeframe(timeframe: str) -> dict[str, str | int]:
    try:
        return TIMEFRAMES[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from exc


def available_config() -> dict[str, Any]:
    return {
        "symbols": [
            {
                "value": symbol.value,
                "label": symbol.label,
                "provider": symbol.provider,
                "exchange": symbol.exchange,
                "group": symbol.group,
            }
            for symbol in (get_chart_symbol(item.value) for item in SYMBOLS)
        ],
        "timeframes": list(TIMEFRAMES.keys()),
        "chartCounts": [1, 2, 4, 6, 8],
        "alpaca": alpaca.connection_info(),
    }


def get_chart_symbol(symbol: str) -> Symbol:
    item = get_symbol(symbol)
    if alpaca.data_enabled() and item.provider == "yfinance" and re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", item.value) and not item.value.endswith((".NS", ".BO")):
        return replace(item, provider="alpaca")
    return item


def get_chart_history(symbol: str, timeframe: str):
    item = get_chart_symbol(symbol)
    get_timeframe(timeframe)
    if item.provider == "alpaca":
        return fetch_alpaca_history(item.value, timeframe)
    return get_history(item.value, timeframe)


def get_chart_quote(symbol: str):
    item = get_chart_symbol(symbol)
    if item.provider == "alpaca":
        return fetch_alpaca_quote(item.value)
    return get_quote(item.value)


def validate_alpaca_symbol(symbol: str):
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol):
        raise ValueError("Enter one US stock or ETF symbol.")
    def fetch():
        asset = alpaca.read_get(f"/v2/assets/{symbol}", paper=True)
        if asset.get("class") != "us_equity" or asset.get("status") != "active":
            raise ValueError("Alpaca charts require an active US stock or ETF.")
        return asset
    return alpaca.cached_read(("asset", symbol), fetch, ttl=3600)


def alpaca_timestamp(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return int(stamp.timestamp())
    except (ValueError, TypeError, OverflowError):
        return None


def finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def normalize_alpaca_bar(row):
    result = {name: finite_number(row.get(key)) for name, key in (
        ("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v"),
    )}
    result["time"] = alpaca_timestamp(row.get("t"))
    if any(value is None for value in result.values()):
        raise alpaca.AlpacaDataError("Alpaca returned an invalid candle.")
    if result["low"] > min(result["open"], result["close"]) or result["high"] < max(result["open"], result["close"]) or result["volume"] < 0:
        raise alpaca.AlpacaDataError("Alpaca returned inconsistent candle prices.")
    return result


def fetch_alpaca_history(symbol: str, timeframe: str):
    validate_alpaca_symbol(symbol)
    get_timeframe(timeframe)
    interval = {"1m": "1Min", "3m": "3Min", "5m": "5Min", "15m": "15Min", "30m": "30Min", "1h": "1Hour", "4h": "4Hour", "1d": "1Day"}[timeframe]
    def fetch():
        # Descending pagination selects recent history, not the first page of old bars.
        now = datetime.now(timezone.utc)
        days = {"1m": 14, "3m": 21, "5m": 30, "15m": 60, "30m": 120, "1h": 200, "4h": 600, "1d": 1200}[timeframe]
        params = {"symbols": symbol, "timeframe": interval, "start": (now - timedelta(days=days)).isoformat(),
                  "end": now.isoformat(), "sort": "desc", "limit": 500, "adjustment": "raw", "feed": alpaca.data_config()["stock_feed"]}
        bars = {}
        tokens = set()
        for _ in range(10):
            result = alpaca.read_get("/v2/stocks/bars", params)
            for row in result.get("bars", {}).get(symbol, []):
                bar = normalize_alpaca_bar(row)
                bars[bar["time"]] = bar
            token = result.get("next_page_token")
            if len(bars) >= 500 or not token:
                return sorted(bars.values(), key=lambda row: row["time"])[-500:]
            if token in tokens:
                break
            tokens.add(token)
            params["page_token"] = token
        raise alpaca.AlpacaDataError("Alpaca history pagination did not complete.")
    return alpaca.cached_read(("history", symbol, timeframe), fetch, ttl=2)


def fetch_alpaca_quote(symbol: str):
    validate_alpaca_symbol(symbol)
    def fetch():
        row = alpaca.read_get(f"/v2/stocks/{symbol}/trades/latest", {"feed": alpaca.data_config()["stock_feed"]}).get("trade") or {}
        return {"symbol": symbol, "price": finite_number(row.get("p")), "time": alpaca_timestamp(row.get("t")),
                "previous": None, "source": "Alpaca", "feed": alpaca.data_config()["stock_feed"]}
    return alpaca.cached_read(("quote", symbol), fetch, ttl=5)


def alpaca_contracts(symbol: str):
    validate_alpaca_symbol(symbol)
    def fetch():
        today = datetime.now(ZoneInfo("America/New_York")).date()
        params = {"underlying_symbols": symbol, "status": "active", "limit": 10000,
                  "expiration_date_gte": today.isoformat(), "expiration_date_lte": (today + timedelta(days=1096)).isoformat()}
        rows, tokens = [], set()
        for _ in range(10):
            result = alpaca.read_get("/v2/options/contracts", params, paper=True)
            rows.extend(result.get("option_contracts") or [])
            token = result.get("next_page_token")
            if not token:
                return rows
            if token in tokens:
                break
            tokens.add(token)
            params["page_token"] = token
        raise alpaca.AlpacaDataError("Alpaca contract pagination did not complete. Try another underlying.")
    return alpaca.cached_read(("contracts", symbol), fetch, ttl=600)


def alpaca_expirations(symbol: str):
    return sorted({row["expiration_date"] for row in alpaca_contracts(symbol)})


def fetch_alpaca_option_chain(symbol: str, expiration: str | None = None):
    contracts = alpaca_contracts(symbol)
    expirations = sorted({row["expiration_date"] for row in contracts})
    if not expirations:
        raise ValueError(f"No Alpaca option contracts found for {symbol}.")
    expiration = expiration or expirations[0]
    if expiration not in expirations:
        raise ValueError("Select an available Alpaca expiration.")
    quote = fetch_alpaca_quote(symbol)
    def fetch():
        snapshots, tokens = {}, set()
        params = {"feed": alpaca.data_config()["option_feed"], "expiration_date": expiration, "limit": 1000}
        for _ in range(20):
            result = alpaca.read_get(f"/v1beta1/options/snapshots/{symbol}", params)
            snapshots.update(result.get("snapshots") or {})
            token = result.get("next_page_token")
            if not token:
                break
            if token in tokens:
                raise alpaca.AlpacaDataError("Alpaca option snapshot pagination repeated.")
            tokens.add(token)
            params["page_token"] = token
        else:
            raise alpaca.AlpacaDataError("Alpaca option snapshot pagination did not complete.")
        underlying = quote["price"]
        rows = []
        for contract in contracts:
            if contract["expiration_date"] != expiration:
                continue
            snap = snapshots.get(contract["symbol"]) or {}
            latest_quote, trade, greeks = snap.get("latestQuote") or {}, snap.get("latestTrade") or {}, snap.get("greeks") or {}
            bid, ask = finite_number(latest_quote.get("bp")), finite_number(latest_quote.get("ap"))
            strike = finite_number(contract.get("strike_price"))
            rows.append({
                "contract_symbol": contract["symbol"], "option_type": contract["type"], "expiration": expiration,
                "strike": strike, "bid": bid, "ask": ask, "last_price": finite_number(trade.get("p")),
                "mid": (bid + ask) / 2 if bid is not None and ask is not None and 0 <= bid <= ask and ask > 0 else None,
                "iv": finite_number(snap.get("impliedVolatility")),
                **{name: finite_number(greeks.get(name)) for name in ("delta", "gamma", "theta", "vega", "rho")},
                "quote_time": alpaca_timestamp(latest_quote.get("t")), "trade_time": alpaca_timestamp(trade.get("t")),
                "volume": None, "open_interest": finite_number(contract.get("open_interest")),
                "in_the_money": underlying is not None and strike is not None and (underlying > strike if contract["type"] == "call" else underlying < strike),
                "multiplier": finite_number(contract.get("size")),
            })
        rows.sort(key=lambda row: row["strike"] or 0)
        return {"symbol": symbol, "expiration": expiration, "expirations": expirations,
                "calls": [row for row in rows if row["option_type"] == "call"], "puts": [row for row in rows if row["option_type"] == "put"],
                "underlying_price": underlying, "underlying_time": quote["time"],
                "source": "Alpaca", "feed": alpaca.data_config()["option_feed"], "read_only": not alpaca.manual_enabled(),
                "feed_label": alpaca.connection_info()["option_label"], "updated_at": int(time.time())}
    return alpaca.cached_read(("chain", symbol, expiration), fetch, ttl=15)


def fetch_alpaca_bot_history(symbols, timeframe, now):
    """Batch native aggregates; follow every page because limits apply across symbols."""
    duration = {"5m": 300, "15m": 900, "1d": 86400}[timeframe]
    # Match completed_bars' 30-second grace so cached forming bars never become eligible.
    bucket = int(now.timestamp() - 30) // duration
    def fetch():
        params = {"symbols": ",".join(symbols), "timeframe": {"5m": "5Min", "15m": "15Min", "1d": "1Day"}[timeframe],
                  "start": (now - timedelta(days=180 if timeframe == "1d" else 10)).isoformat(), "end": now.isoformat(),
                  "limit": 10000, "sort": "asc", "adjustment": "split", "feed": alpaca.data_config()["stock_feed"]}
        bars, tokens = {symbol: {} for symbol in symbols}, set()
        for _ in range(20):
            result = alpaca.read_get("/v2/stocks/bars", params)
            for symbol, items in (result.get("bars") or {}).items():
                if symbol in bars:
                    for item in items:
                        bar = normalize_alpaca_bar(item)
                        bars[symbol][bar["time"]] = bar
            token = result.get("next_page_token")
            if not token:
                return {symbol: sorted(items.values(), key=lambda row: row["time"]) for symbol, items in bars.items()}
            if token in tokens:
                break
            tokens.add(token)
            params["page_token"] = token
        raise alpaca.AlpacaDataError("Bot history pagination did not complete; no signals evaluated.")
    return alpaca.cached_read(("bot-history", tuple(symbols), timeframe, bucket), fetch, ttl=duration)


def fetch_alpaca_bid_asks(symbols, *, options=False):
    if not symbols:
        return {}
    symbols = sorted(set(symbols))
    if len(symbols) > 100:
        raise ValueError("At most 100 symbols per quote request.")
    path = "/v1beta1/options/quotes/latest" if options else "/v2/stocks/quotes/latest"
    result = alpaca.read_get(path, {"symbols": ",".join(symbols), "feed": alpaca.data_config()["option_feed" if options else "stock_feed"]})
    return {symbol: {"bid": finite_number(row.get("bp")), "ask": finite_number(row.get("ap")), "time": alpaca_timestamp(row.get("t"))}
            for symbol, row in (result.get("quotes") or {}).items() if symbol in symbols}


def fetch_yfinance_history(symbol: str, timeframe: str, *, period=None, prepost=True, limit=500, ignore_tz=None) -> list[dict[str, float | int]]:
    import pandas as pd
    import yfinance as yf

    config = get_timeframe(timeframe)
    frame = yf.download(
        tickers=symbol,
        period=period or config["yf_period"],
        interval=config["yf_interval"],
        progress=False,
        auto_adjust=False,
        prepost=prepost,
        threads=False,
        timeout=15,
        ignore_tz=ignore_tz,
    )
    if frame.empty:
        return []

    if isinstance(frame.columns, pd.MultiIndex):
        if symbol in frame.columns.get_level_values(-1):
            frame = frame.xs(symbol, axis=1, level=-1)
        else:
            frame.columns = frame.columns.get_level_values(0)

    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    if "yf_resample_rule" in config:
        frame = resample_ohlcv(frame, str(config["yf_resample_rule"]))
    bars: list[dict[str, float | int]] = []
    for timestamp, row in frame.tail(limit).iterrows():
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(timezone.utc)
        else:
            timestamp = timestamp.tz_convert(timezone.utc)
        bars.append(
            {
                "time": int(timestamp.timestamp()),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume", 0) or 0),
            }
        )
    return bars


def get_alert_history(symbol: str, timeframe: str) -> list[dict[str, float | int]]:
    """Regular-session history with enough warm-up for a 200-period alert."""
    if get_symbol(symbol).provider != "yfinance":
        return get_history(symbol, timeframe)
    periods = {"1m": "5d", "3m": "5d", "5m": "1mo", "15m": "1mo", "30m": "3mo", "1h": "1y", "1d": "2y"}
    return fetch_yfinance_history(symbol, timeframe, period=periods[timeframe], prepost=False, limit=1000, ignore_tz=False)


def resample_ohlcv(frame, rule: str):
    aggregation = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
    }
    if "Volume" in frame.columns:
        aggregation["Volume"] = "sum"

    resampled = (
        frame.resample(rule, label="left", closed="left", origin="start_day")
        .agg(aggregation)
        .dropna(subset=["Open", "High", "Low", "Close"])
    )
    return resampled[resampled["High"] >= resampled["Low"]]


def fetch_yfinance_quote(symbol: str) -> dict[str, float | int | str | None]:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    history = ticker.history(period="2d", interval="1m", prepost=True)
    if history.empty:
        return {"symbol": symbol, "price": None, "previous": None, "time": None}

    closes = history["Close"].dropna()
    if closes.empty:
        return {"symbol": symbol, "price": None, "previous": None, "time": None}

    price = float(closes.iloc[-1])
    previous = float(closes.iloc[-2]) if len(closes) > 1 else price
    timestamp = closes.index[-1]
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(timezone.utc)
    else:
        timestamp = timestamp.tz_convert(timezone.utc)
    return {
        "symbol": symbol,
        "price": price,
        "previous": previous,
        "time": int(timestamp.timestamp()),
    }


def fetch_yfinance_fundamentals(symbol: str) -> dict[str, Any]:
    """On-demand company financials; normalization and caching belong to the caller."""
    import yfinance as yf

    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol):
        raise ValueError("Use a valid ticker symbol.")
    ticker = yf.Ticker(symbol)
    info = ticker.get_info()
    if not isinstance(info, dict) or not (info.get("longName") or info.get("shortName")):
        raise ValueError("Company information is unavailable for this ticker.")
    result = {"info": info, "statements": {}, "warnings": []}
    if info.get("quoteType") != "EQUITY":
        return result
    for frequency in ("yearly", "quarterly"):
        result["statements"][frequency] = {}
        for name, method in (("income", ticker.get_income_stmt), ("balance", ticker.get_balance_sheet), ("cash_flow", ticker.get_cash_flow)):
            try:
                frame = method(freq=frequency, pretty=False)
                result["statements"][frequency][name] = frame
                if frame is None or frame.empty:
                    result["warnings"].append(f"{frequency.title()} {name.replace('_', ' ')} statement unavailable.")
            except Exception:
                result["warnings"].append(f"{frequency.title()} {name.replace('_', ' ')} statement unavailable.")
    return result


def cached_yfinance_history(symbol: str, timeframe: str) -> list[dict[str, float | int]]:
    key = (symbol, timeframe)
    cached = _history_cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < HISTORY_CACHE_TTL_SECONDS:
        return cached[1]

    bars = fetch_yfinance_history(symbol, timeframe)
    _history_cache[key] = (now, bars)
    return bars


def cached_yfinance_quote(symbol: str) -> dict[str, float | int | str | None]:
    cached = _quote_cache.get(symbol)
    now = time.monotonic()
    if cached and now - cached[0] < QUOTE_CACHE_TTL_SECONDS:
        return cached[1]

    quote = fetch_yfinance_quote(symbol)
    _quote_cache[symbol] = (now, quote)
    return quote


def validate_symbol_has_data(symbol: str) -> None:
    bars = cached_yfinance_history(symbol, "1d")
    if not bars:
        raise ValueError(f"No yfinance data found for {symbol}")


def fetch_hyperliquid_history(symbol: str, timeframe: str) -> list[dict[str, float | int]]:
    import requests

    config = get_timeframe(timeframe)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(config["seconds"]) * 500 * 1000
    response = requests.post(
        HYPERLIQUID_INFO_URL,
        json={
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": config["hl_interval"],
                "startTime": start_ms,
                "endTime": end_ms,
            },
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return [normalize_hyperliquid_candle(candle) for candle in payload[-500:]]


def build_hyperliquid_candle_subscription(symbol: str, timeframe: str) -> dict[str, Any]:
    interval = get_timeframe(timeframe)["hl_interval"]
    return {"method": "subscribe", "subscription": {"type": "candle", "coin": symbol, "interval": interval}}


def normalize_hyperliquid_candle(payload: dict[str, Any]) -> dict[str, float | int | str]:
    return {
        "symbol": str(payload["s"]),
        "time": int(payload["t"] / 1000),
        "open": float(payload["o"]),
        "high": float(payload["h"]),
        "low": float(payload["l"]),
        "close": float(payload["c"]),
        "volume": float(payload.get("v", 0) or 0),
    }


HistoryProvider = Callable[[str, str], list[dict[str, float | int]]]
QuoteProvider = Callable[[str], dict[str, float | int | str | None]]

HISTORY_PROVIDERS: dict[str, HistoryProvider] = {
    "hyperliquid": fetch_hyperliquid_history,
    "yfinance": cached_yfinance_history,
}

QUOTE_PROVIDERS: dict[str, QuoteProvider] = {
    "yfinance": cached_yfinance_quote,
}


def get_history(symbol: str, timeframe: str) -> list[dict[str, float | int]]:
    item = get_symbol(symbol)
    provider = HISTORY_PROVIDERS.get(item.provider)
    if provider is None:
        return []
    return provider(symbol, timeframe)


def get_quote(symbol: str) -> dict[str, float | int | str | None]:
    item = get_symbol(symbol)
    provider = QUOTE_PROVIDERS.get(item.provider)
    if provider is None:
        return {"symbol": symbol, "price": None, "previous": None, "time": None}
    return provider(symbol)
