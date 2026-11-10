"""Explicit evidence, not an institutional identity or a trading recommendation."""
from datetime import datetime, timedelta, timezone as utc
from decimal import Decimal
from statistics import mean

from dashboard.market_alerts import EASTERN, calendar, session_bounds
from dashboard.paper_trading import calculate_ema, calculate_vwap


def classify(price, bid, ask, quote_at, trade_at, structure="UNKNOWN"):
    if structure in ("MULTI_LEG", "HEDGE_CANDIDATE"):
        return "UNKNOWN", "Execution belongs to a complex structure; intent is not inferred"
    if bid is None or ask is None or quote_at is None or not Decimal(0) < bid < ask:
        return "UNKNOWN", "No valid execution-time bid/ask"
    if not 0 <= (trade_at - quote_at).total_seconds() <= 2:
        return "UNKNOWN", "Quote is later than the trade or too old"
    spread = ask - bid
    if price > ask + spread or price < bid - spread or spread / ((ask + bid) / 2) > Decimal("0.30"):
        return "UNKNOWN", "Wide spread or execution outside a plausible quote range"
    if price >= ask - spread * Decimal("0.10"):
        return "ASK_SIDE", "Aggressive buying candidate; opening/closing and hedging are unknown"
    if price <= bid + spread * Decimal("0.10"):
        return "BID_SIDE", "Possible option selling; opening/closing and hedging are unknown"
    return "UNKNOWN", "Execution inside spread; aggressor unknown"


def directional(option_type, execution):
    if execution == "UNKNOWN":
        return "UNKNOWN"
    return "BULLISH" if (option_type == "CALL") == (execution == "ASK_SIDE") else "BEARISH"


def unusual_score(premium, repeat_count, classification, volume=None, oi=None):
    points, reasons = 0, []
    if premium is not None:
        for threshold, weight in [(50000, 10), (100000, 20), (250000, 30), (500000, 40), (1000000, 50), (5000000, 60)]:
            if premium >= threshold:
                points = weight
        reasons.append(f"Observed premium: {points} points")
    if repeat_count >= 2:
        repeat_points = min(20, (repeat_count - 1) * 5)
        points += repeat_points
        reasons.append(f"{repeat_count} observed same-contract prints in 10 minutes: {repeat_points} points")
    if classification != "UNKNOWN":
        points += 5
        reasons.append("Execution near a usable bid/ask: 5 points (not a direction guarantee)")
    if volume is not None and oi is not None and oi > 0:
        ratio = volume / oi
        if ratio >= 1:
            points += min(15, int(ratio * 5))
        reasons.append(f"As-of contract volume/OI: {ratio:.2f}")
    return min(100, points), {"reasons": reasons, "repeat_count": repeat_count,
        "limitations": ["Observed watchlist sample, not the full tape", "No historical-normal-volume baseline yet",
                        "Large premium alone does not establish bullishness, bearishness or institutional identity"]}


def regular_bars(bars, end):
    output = []
    for bar in sorted(bars, key=lambda row: row["time"]):
        bounds = session_bounds(datetime.fromtimestamp(bar["time"], EASTERN).date())
        if bounds and bounds[0] <= bar["time"] < bounds[1] and bar["time"] + 300 <= end.timestamp():
            output.append(bar)
    return output


def wilder_rsi(closes, period=14):
    if len(closes) <= period:
        return None
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gains, losses = mean(max(x, 0) for x in changes[:period]), mean(max(-x, 0) for x in changes[:period])
    for change in changes[period:]:
        gains, losses = (gains * (period - 1) + max(change, 0)) / period, (losses * (period - 1) + max(-change, 0)) / period
    return 50 if gains == losses == 0 else 100 if losses == 0 else 100 - 100 / (1 + gains / losses)


def technical_snapshot(bars, event_at, direction):
    bars = regular_bars(bars, event_at)
    if not bars or event_at.timestamp() - (bars[-1]["time"] + 300) > 900:
        return None
    closes = [row["close"] for row in bars]
    ema9, ema21 = calculate_ema(bars, 9)[-1], calculate_ema(bars, 21)[-1]
    vwap = calculate_vwap(bars)[-1]
    sma = lambda n: mean(closes[-n:]) if len(closes) >= n else None
    rsi = wilder_rsi(closes)
    fast, slow = calculate_ema(bars, 12), calculate_ema(bars, 26)
    macd_values = [a - b for a, b in zip(fast, slow) if a is not None and b is not None]
    macd = macd_values[-1] if macd_values else None
    signal_values = calculate_ema([{"close": x} for x in macd_values], 9)
    signal = signal_values[-1] if signal_values else None
    pivots_low, pivots_high = [], []
    for i in range(3, len(bars) - 3):
        window = bars[i - 3:i + 4]
        if bars[i]["low"] == min(b["low"] for b in window):
            pivots_low.append(bars[i]["low"])
        if bars[i]["high"] == max(b["high"] for b in window):
            pivots_high.append(bars[i]["high"])
    average_volume = mean(b["volume"] for b in bars[-21:-1]) if len(bars) >= 21 else 0
    trend = "INCONCLUSIVE"
    if ema9 is not None and ema21 is not None and vwap is not None:
        if ema9 > ema21 and closes[-1] > vwap:
            trend = "BULLISH"
        elif ema9 < ema21 and closes[-1] < vwap:
            trend = "BEARISH"
    confirmation = "Mixed or inconclusive"
    if direction != "UNKNOWN" and trend != "INCONCLUSIVE":
        confirmation = f"{direction.title()} flow + {trend.lower()} technical {'confirmation' if direction == trend else 'divergence'}"
    return {"timeframe": "5m", "bar_closed_at": bars[-1]["time"] + 300, "price": closes[-1],
            "ema9": ema9, "ema21": ema21, "vwap": vwap, "sma50": sma(50), "sma200": sma(200),
            "rsi14": rsi, "macd": macd, "macd_signal": signal,
            "relative_volume20": bars[-1]["volume"] / average_volume if average_volume else None,
            "support": max((v for v in pivots_low if v < closes[-1]), default=None),
            "resistance": min((v for v in pivots_high if v > closes[-1]), default=None),
            "trend": trend, "confirmation": confirmation, "method": "Completed 5m RTH bars only; confirmed 3-bar pivots; historical reconstruction"}


def horizons(event_at):
    day = event_at.astimezone(EASTERN).date()
    cal = calendar()
    if not cal.is_session(str(day)):
        return {}
    session = cal.date_to_session(str(day))
    targets = {"15m": event_at + timedelta(minutes=15), "1h": event_at + timedelta(hours=1),
               "close": datetime.fromtimestamp(session_bounds(day)[1], utc.utc)}
    for count in (1, 5, 20):
        target = cal.session_offset(session, count).date()
        targets[f"{count}d"] = datetime.fromtimestamp(session_bounds(target)[1], utc.utc)
    return targets
