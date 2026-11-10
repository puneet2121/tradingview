"""Transparent research heuristics. Never consumed by the order or signal engines."""
from datetime import date, datetime, timedelta, timezone as utc
from statistics import mean

from dashboard.market_alerts import EASTERN, session_bounds, calendar
from dashboard.paper_trading import calculate_ema
from .analytics import wilder_rsi
from .fundamentals import number

VERSION = "research-v1"


def latest_session(now):
    # Allow Yahoo's end-of-day bar to settle; unfinished daily candles never vote.
    for offset in range(15):
        day = now.astimezone(EASTERN).date() - timedelta(days=offset)
        bounds = session_bounds(day)
        if bounds and bounds[1] + 900 <= now.timestamp():
            return day.isoformat()
    return None


def daily_snapshot(raw, now):
    rows = {}
    for bar in raw:
        stamp = number(bar.get("time"))
        if stamp is None or not 0 < stamp <= now.timestamp():
            continue
        day = datetime.fromtimestamp(stamp, EASTERN).date()
        bounds = session_bounds(day)
        if not bounds or bounds[1] + 900 > now.timestamp():
            continue
        values = {key: number(bar.get(key)) for key in ("open", "high", "low", "close", "volume")}
        if any(values[key] is None or values[key] <= 0 for key in ("open", "high", "low", "close")):
            continue
        if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
            continue
        if values["volume"] is not None and values["volume"] < 0:
            values["volume"] = None
        rows[day.isoformat()] = {**values, "time": stamp, "closed_at": bounds[1]}
    dates = sorted(rows)
    bars = [rows[key] for key in dates]
    if not bars:
        raise ValueError("No completed daily sessions")
    closes = [bar["close"] for bar in bars]
    volumes = [bar["volume"] for bar in bars[-21:-1]]
    baseline = mean(volumes) if len(volumes) == 20 and all(v is not None for v in volumes) else None
    relative_volume = number(bars[-1]["volume"] / baseline) if baseline and bars[-1]["volume"] is not None else None
    history_start = dates[-200] if len(dates) >= 200 else dates[0]
    expected = len(calendar().sessions_in_range(history_start, dates[-1]))
    return {"source": "Yahoo Finance via yfinance", "timeframe": "1d", "session": dates[-1],
            "bar_closed_at": datetime.fromtimestamp(bars[-1]["closed_at"], utc.utc).isoformat(),
            "bar_count": len(bars), "missing_sessions": expected - min(len(bars), 200),
            "close": closes[-1], "previous_close": closes[-2] if len(closes) >= 2 else None,
            "sma50": mean(closes[-50:]) if len(closes) >= 50 else None,
            "sma200": mean(closes[-200:]) if len(closes) >= 200 else None,
            "ema9": calculate_ema(bars, 9)[-1], "ema21": calculate_ema(bars, 21)[-1],
            "rsi14": wilder_rsi(closes), "relative_volume20": relative_volume,
            "method": "Completed daily US regular sessions; 15-minute close grace; no dividend adjustment."}


def check(key, label, weight, grade, reason, group=""):
    return {"key": key, "label": label, "weight": weight, "grade": grade,
            "points": round(weight * grade / 100, 2) if grade is not None else None,
            "state": "missing" if grade is None else "support" if grade > 50 else "caution" if grade == 50 else "against",
            "reason": reason, "group": group}


def finish(checks, blockers, cautions, positive_label, as_of):
    available = sum(c["weight"] for c in checks if c["grade"] is not None)
    points = sum(c["points"] for c in checks if c["points"] is not None)
    score = round(points / available * 100) if available and not blockers else None
    return {"score": score, "label": "Not rated" if score is None else positive_label if score >= 70 else "Mixed" if score >= 40 else "Weak",
            "coverage": available, "checks": checks, "blockers": blockers, "cautions": cautions,
            "as_of": as_of, "version": VERSION}


def fundamental_rating(data, frequency, now, stale=False):
    checks, blockers = [], []
    cautions = ["Valuation is not scored: no peer or fair-value benchmark. Strong business results do not establish that the shares are cheap."]
    if not data or not data.get("supported"):
        return finish([], ["Company financials unavailable or not applicable to this asset."], cautions, "Strong", None)
    period = data.get("periods", {}).get(frequency, {})
    a = period.get("analysis", {})
    statements = period.get("statements", {})
    balance = next(iter(statements.get("balance", [])), {})
    max_age = 540 if frequency == "yearly" else 180
    def old(value):
        try:
            return not 0 <= (now.date() - date.fromisoformat(value)).days <= max_age
        except (ValueError, TypeError):
            return True
    if stale:
        blockers.append("Financial snapshot is stale; refresh before using the combined assessment.")
    if old(a.get("period_end")):
        blockers.append(f"Income statement period is missing or older than the {max_age}-day research limit.")
    if data.get("sector") in ("Financial Services", "Real Estate"):
        blockers.append("This sector needs a sector-specific financial model; generic leverage and cash-flow scores are not applied.")
    for key, label, weight in (("revenue_yoy", "Revenue growth YoY", 20), ("net_income_yoy", "Net income growth YoY", 15)):
        value = number(a.get(key))
        grade = None if value is None else 100 if value >= 10 else 70 if value > 0 else 50 if value == 0 else 0
        reason = "No comparable positive prior-year base." if value is None else f"{label}: {value:+.1f}% versus {a.get('prior_period_end')}."
        checks.append(check(key, label, weight, grade, reason, "growth"))
    for key, label in (("operating_margin", "Operating profit margin"), ("net_margin", "Net profit margin")):
        value = number(a.get(key))
        grade = None if value is None else 100 if value > 0 else 50 if value == 0 else 0
        reason = "Margin unavailable." if value is None else f"{label}: {value*100:.1f}% for {a.get('period_end')}."
        checks.append(check(key, label, 15, grade, reason, "profit"))
    fcf = number(a.get("free_cash_flow"))
    grade = None if fcf is None else 100 if fcf > 0 else 50 if fcf == 0 else 0
    checks.append(check("fcf", "Free cash flow", 15, grade,
        "No cash-flow statement matching the income period." if fcf is None else f"{'Positive' if fcf > 0 else 'Negative' if fcf < 0 else 'Zero'} free cash flow: {fcf:,.0f} {data.get('financial_currency', '')} for {a.get('period_end')}.", "cash"))
    debt, equity = number(a.get("debt_equity")), number(balance.get("equity"))
    grade = None if debt is None else 100 if debt <= 1 else 50 if debt <= 2 else 0
    reason = "Debt/equity unavailable." if debt is None else f"Debt/equity {debt:.2f}x; this model favors <=1x, is neutral through 2x, and penalizes >2x."
    if equity is not None and equity <= 0:
        grade, reason = 0, "Nonpositive shareholder equity; leverage cannot be assessed with a normal debt/equity ratio."
    if old(a.get("balance_period_end")):
        grade, reason = None, "Balance sheet missing or outside the reporting-age limit."
    checks.append(check("debt_equity", "Leverage", 10, grade, reason, "balance"))
    liquidity = number(a.get("current_ratio"))
    grade = None if liquidity is None or old(a.get("balance_period_end")) else 100 if liquidity >= 1.5 else 50 if liquidity >= 1 else 0
    reason = "Current ratio unavailable or balance sheet too old." if grade is None else f"Current ratio {liquidity:.2f}x; this model favors >=1.5x, is neutral at 1-1.5x, and penalizes <1x."
    checks.append(check("current_ratio", "Liquidity", 10, grade, reason, "balance"))
    available = [c for c in checks if c["grade"] is not None]
    if sum(c["weight"] for c in available) < 80 or {c["group"] for c in available} != {"growth", "profit", "cash", "balance"}:
        blockers.append("Need at least 80% evidence coverage, including growth, profit, cash flow and balance sheet data.")
    if len(available) < len(checks):
        cautions.append("Some inputs are missing; the score is normalized over available weights, not filled with zeroes.")
    cautions.append("Generic thresholds are screening choices, not sector-adjusted valuation or a probability of profit.")
    return finish(checks, blockers, cautions, "Strong", a.get("period_end"))


def technical_rating(data, now, stale=False):
    if not data:
        return finish([], ["Daily price history unavailable."], [], "Bullish", None)
    checks, blockers, cautions = [], [], []
    if stale or data.get("session") != latest_session(now):
        blockers.append("Prices do not cover the latest completed session, or the price cache needs refreshing.")
    if data.get("bar_count", 0) < 200 or data.get("missing_sessions", 0):
        blockers.append("Need 200 consecutive completed daily sessions for the long-term trend score.")
    for key, label, weight, left, right in (
        ("long_trend", "Close vs SMA 200", 25, "close", "sma200"),
        ("trend_alignment", "SMA 50 vs SMA 200", 25, "sma50", "sma200"),
        ("short_trend", "EMA 9 vs EMA 21", 20, "ema9", "ema21"),
    ):
        x, y = number(data.get(left)), number(data.get(right))
        grade = None if x is None or y is None else 100 if x > y else 0 if x < y else 50
        reason = f"{label}: insufficient history." if grade is None else f"{left.replace('_', ' ').upper()} {x:.2f} is {'above' if x > y else 'below' if x < y else 'equal to'} {right.upper()} {y:.2f}."
        checks.append(check(key, label, weight, grade, reason))
    rsi = number(data.get("rsi14"))
    grade = None if rsi is None else 100 if 50 <= rsi < 70 else 50 if rsi >= 40 else 0
    reason = "RSI unavailable." if rsi is None else f"Wilder RSI 14: {rsi:.1f}; model favors 50-70, treats 40-50 or >=70 cautiously, and penalizes <40."
    checks.append(check("rsi", "Momentum", 15, grade, reason))
    if rsi is not None and (rsi >= 70 or rsi < 30):
        cautions.append("RSI is overbought (>=70); avoid assuming the trend will continue." if rsi >= 70 else "RSI is oversold (<30); this alone is not a buy signal.")
    volume, close, previous = (number(data.get(key)) for key in ("relative_volume20", "close", "previous_close"))
    grade = None if any(x is None for x in (volume, close, previous)) else 50
    if grade is not None and volume >= 1.2:
        grade = 100 if close > previous else 0 if close < previous else 50
    reason = "Volume or comparison price unavailable." if grade is None else f"Session volume {volume:.2f}x the previous 20-session average; close {'rose' if close > previous else 'fell' if close < previous else 'was unchanged'}. Direction only scores at >=1.2x volume."
    checks.append(check("volume", "Volume confirmation", 15, grade, reason))
    if sum(c["weight"] for c in checks if c["grade"] is not None) < 90:
        blockers.append("Need at least 90% technical evidence coverage.")
    cautions.append("Daily trend assessment, not a new crossover alert or an intraday entry signal. Yahoo prices are not guaranteed live.")
    return finish(checks, blockers, cautions, "Bullish", data.get("bar_closed_at"))


def combined_rating(technical, fundamental):
    t, f = technical["score"], fundamental["score"]
    reasons = [c["reason"] for group in (technical, fundamental) for c in group["checks"] if c["state"] == "support"]
    against = [c["reason"] for group in (technical, fundamental) for c in group["checks"] if c["state"] in ("against", "caution", "missing")]
    blockers = technical["blockers"] + fundamental["blockers"]
    cautions = technical["cautions"] + fundamental["cautions"]
    score = round((t + f) / 2) if t is not None and f is not None else None
    if score is None:
        label, summary = "Not rated", "No combined buy assessment until both evidence sets are sufficiently complete and current."
    elif min(t, f) < 40:
        label, summary = "Weak buy case", "At least one assessment is weak. Strength in the other does not cancel that risk."
    elif score >= 70 and min(t, f) >= 60:
        label, summary = "Buy candidate", "Daily technicals and reported business performance both support further buy research. Review valuation and the cautions before any entry."
    else:
        label, summary = "Watch / wait", "The evidence is mixed or technicals and fundamentals disagree. There is not yet a strong combined buy case."
    return {"score": score, "label": label, "summary": summary, "supporting": reasons, "against": against,
            "blockers": blockers, "cautions": cautions, "weights": {"technical": 50, "fundamental": 50}, "version": VERSION}
