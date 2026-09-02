from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
import json
import time
from typing import Any, Callable


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
            for symbol in SYMBOLS
        ],
        "timeframes": list(TIMEFRAMES.keys()),
        "chartCounts": [1, 2, 4, 6, 8],
    }


def fetch_yfinance_history(symbol: str, timeframe: str) -> list[dict[str, float | int]]:
    import pandas as pd
    import yfinance as yf

    config = get_timeframe(timeframe)
    frame = yf.download(
        tickers=symbol,
        period=config["yf_period"],
        interval=config["yf_interval"],
        progress=False,
        auto_adjust=False,
        threads=False,
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
    for timestamp, row in frame.tail(500).iterrows():
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
    history = ticker.history(period="2d", interval="1m")
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
