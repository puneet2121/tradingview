"""Saved notification rules. This module never opens or closes trades."""

import logging
import math
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .data_source import get_alert_history, get_symbol, get_timeframe
from .discord_alerts import DiscordDeliveryError, format_time, post_discord_message
from .models import MarketAlert, MarketAlertEvent, MarketAlertState, MarketAlertWorker
from .paper_trading import calculate_ema


logger = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "1d")
OPERATORS = ("touches", "crosses_above", "crosses_below", "above", "below")
MAX_AGE = 20 * 60


def integer(value, low, high, label):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
        raise ValueError(f"{label} must be an integer.")
    result = int(value)
    if not low <= result <= high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    return result


def number(value, low, high, label):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label} must be a number.")
    if isinstance(value, bool) or not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    return result


def operand(raw, allowed):
    if not isinstance(raw, dict) or raw.get("type") not in allowed:
        raise ValueError("Unsupported indicator. Choose price, SMA, or EMA.")
    kind = raw["type"]
    expected = {"type", "period"} if kind in ("ema", "sma") else ({"type", "value"} if kind == "level" else {"type"})
    if set(raw) != expected:
        raise ValueError("Unsupported indicator settings.")
    result = {"type": kind}
    if kind in ("ema", "sma"):
        result["period"] = integer(raw["period"], 1, 200, "MA period")
    if kind == "level":
        result["value"] = number(raw["value"], 0.000001, 1000000000, "Price level")
    return result


def validate_rule(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected an alert object.")
    if set(payload) - {"name", "symbols", "timeframes", "condition", "enabled", "cooldown_minutes", "revision"}:
        raise ValueError("Unsupported alert settings.")
    name = payload.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(char) < 32 for char in name):
        raise ValueError("Name must contain 1-80 characters on one line.")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 10:
        raise ValueError("Choose 1-10 US stock or crypto symbols.")
    if not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,14}", item.strip()) for item in symbols):
        raise ValueError("Use US ticker symbols such as AAPL, SOFI, BRK-B, or BTC/ETH without a slash.")
    symbols = list(dict.fromkeys(item.strip().upper() for item in symbols))
    timeframes = payload.get("timeframes")
    if not isinstance(timeframes, list) or not 1 <= len(timeframes) <= 4 or any(item not in TIMEFRAMES for item in timeframes):
        raise ValueError("Choose 1-4 supported timeframes.")
    condition = payload.get("condition")
    if not isinstance(condition, dict) or set(condition) != {"left", "operator", "right", "tolerance_percent"}:
        raise ValueError("Choose a complete supported condition.")
    left = operand(condition["left"], ("price", "ema", "sma"))
    right = operand(condition["right"], ("ema", "sma", "level"))
    operator = condition["operator"]
    if operator not in OPERATORS or (operator == "touches" and left["type"] != "price"):
        raise ValueError("Touches compares a price candle with an MA or price level.")
    if left == right:
        raise ValueError("Choose two different indicators or periods.")
    tolerance = number(condition["tolerance_percent"], 0, 5, "Touch tolerance (%)")
    if operator != "touches" and tolerance != 0:
        raise ValueError("Tolerance applies only to touches.")
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("Enabled must be true or false.")
    return {
        "name": name.strip(), "symbols": symbols, "timeframes": list(dict.fromkeys(timeframes)),
        "condition": {"left": left, "operator": operator, "right": right, "tolerance_percent": tolerance},
        "enabled": enabled, "cooldown_minutes": integer(payload.get("cooldown_minutes", 0), 0, 1440, "Cooldown minutes"),
    }


def operand_label(value):
    return {"price": "Price", "level": f"{value.get('value', 0):g}"}.get(value["type"], f"{value['type'].upper()}({value.get('period', '')})")


def rule_label(condition):
    label = f"{operand_label(condition['left'])} {condition['operator'].replace('_', ' ')} {operand_label(condition['right'])}"
    if condition["operator"] == "touches" and condition["tolerance_percent"]:
        label += f" (+/- {condition['tolerance_percent']:g}%)"
    return label


def values(bars, specification):
    kind = specification["type"]
    if kind == "price":
        return [bar["close"] for bar in bars]
    if kind == "level":
        return [specification["value"]] * len(bars)
    period = specification["period"]
    if kind == "ema":
        return calculate_ema(bars, period)
    result, total = [], 0.0
    for index, bar in enumerate(bars):
        total += bar["close"]
        if index >= period:
            total -= bars[index - period]["close"]
        result.append(total / period if index >= period - 1 else None)
    return result


def evaluate(bars, condition):
    left, right = values(bars, condition["left"]), values(bars, condition["right"])
    if len(bars) < 2 or any(value is None for value in [*left[-2:], *right[-2:]]):
        raise ValueError("Insufficient history for this MA period.")
    operator = condition["operator"]
    def matches(index):
        if operator == "touches":
            tolerance = abs(right[index]) * condition["tolerance_percent"] / 100
            return bars[index]["low"] <= right[index] + tolerance and bars[index]["high"] >= right[index] - tolerance
        return left[index] > right[index] if operator == "above" else left[index] < right[index]
    if operator == "crosses_above":
        matched = left[-2] <= right[-2] and left[-1] > right[-1]
    elif operator == "crosses_below":
        matched = left[-2] >= right[-2] and left[-1] < right[-1]
    else:
        matched = matches(-1) and not matches(-2)
    return matched, {"left": left[-1], "right": right[-1], "close": bars[-1]["close"], "condition": condition}


@lru_cache(maxsize=1)
def calendar():
    import exchange_calendars
    return exchange_calendars.get_calendar("XNYS")


@lru_cache(maxsize=4096)
def session_bounds(day):
    cal = calendar()
    if not cal.is_session(str(day)):
        return None
    return cal.session_open(str(day)).timestamp(), cal.session_close(str(day)).timestamp()


def bar_close_time(bar, symbol, timeframe):
    stamp = int(bar["time"])
    duration = int(get_timeframe(timeframe)["seconds"])
    if get_symbol(symbol).provider == "hyperliquid":
        return stamp + duration
    bounds = session_bounds(datetime.fromtimestamp(stamp, EASTERN).date())
    if bounds is None:
        return None
    if timeframe == "1d":
        return bounds[1]
    if bounds[0] <= stamp < bounds[1]:
        return min(stamp + duration, bounds[1])
    return None


def active_window(symbol, timeframe, now):
    if get_symbol(symbol).provider == "hyperliquid":
        return True
    bounds = session_bounds(now.astimezone(EASTERN).date())
    if not bounds:
        return False
    stamp = now.timestamp()
    if timeframe == "1d":
        return bounds[1] + 30 <= stamp <= bounds[1] + MAX_AGE
    return bounds[0] <= stamp <= bounds[1] + MAX_AGE


def completed_bars(raw, symbol, timeframe, now):
    result = []
    seen = set()
    for item in sorted(raw, key=lambda bar: bar["time"]):
        if not all(math.isfinite(float(item[key])) for key in ("time", "open", "high", "low", "close")):
            raise ValueError("The data provider returned invalid candles.")
        if item["low"] > min(item["open"], item["close"]) or item["high"] < max(item["open"], item["close"]):
            raise ValueError("The data provider returned invalid candle prices.")
        stamp = int(item["time"])
        if stamp in seen:
            raise ValueError("The data provider returned duplicate candles.")
        seen.add(stamp)
        end = bar_close_time(item, symbol, timeframe)
        if end is not None and end + 30 <= now.timestamp():
            result.append({**item, "closed_at": end})
    return result


def discord_ready():
    return bool(getattr(settings, "DISCORD_ALERTS_ENABLED", False) and getattr(settings, "DISCORD_WEBHOOK_URL", ""))


def process_rule(alert, symbol, timeframe, bars, now):
    state, _ = MarketAlertState.objects.get_or_create(alert=alert, symbol=symbol, timeframe=timeframe)
    state.checked_at = now
    if not bars:
        state.status = "No completed candles returned"
        state.save(update_fields=["checked_at", "status"])
        return
    latest = bars[-1]
    stamp = int(latest["time"])
    if now.timestamp() - latest["closed_at"] > MAX_AGE:
        status = "Waiting for fresh candles (feed is stale)"
    elif latest["closed_at"] <= alert.armed_at.timestamp():
        status = "Armed; waiting for a new completed candle"
    elif state.last_bar_time is not None and stamp <= state.last_bar_time:
        status = state.status
    else:
        matched, snapshot = evaluate(bars, alert.condition)
        status = "Watching; no new match"
        with transaction.atomic():
            current = MarketAlert.objects.filter(pk=alert.pk, revision=alert.revision, enabled=True).first()
            if current is None:
                return
            previous = MarketAlertEvent.objects.filter(alert=alert, revision=alert.revision, symbol=symbol, timeframe=timeframe).order_by("-bar_time").first()
            cooling = previous and latest["closed_at"] - previous.bar_closed_at.timestamp() < alert.cooldown_minutes * 60
            if matched and not cooling:
                end = datetime.fromtimestamp(latest["closed_at"], dt_timezone.utc)
                owner = alert.owner.username if alert.owner_id else "Local owner"
                message = "\n".join([
                    f"**MARKET ALERT | {symbol} | {timeframe}**", f"Alert: {alert.name}", f"Created by: {owner}",
                    f"Condition: {rule_label(alert.condition)}", f"Close: {snapshot['close']:.4f}",
                    f"{operand_label(alert.condition['left'])}: {snapshot['left']:.4f} | {operand_label(alert.condition['right'])}: {snapshot['right']:.4f}",
                    f"Candle close: {format_time(end)}", f"Detected: {format_time(now)}",
                    f"Feed age at detection: {max(0, int(now.timestamp() - end.timestamp()))}s",
                    "Notification only; no trade was placed.",
                ])
                MarketAlertEvent.objects.get_or_create(
                    alert=alert, revision=alert.revision, symbol=symbol, timeframe=timeframe, bar_time=stamp,
                    defaults={"owner_id": alert.owner_id, "name": alert.name, "bar_closed_at": end, "snapshot": snapshot, "message": message, "retry_at": now},
                )
                status = "Matched; see delivery history"
            elif matched:
                status = "Matched during cooldown"
            state.last_bar_time = stamp
    state.status = status
    state.save(update_fields=["checked_at", "status", "last_bar_time"])


def claim_worker(token, now):
    MarketAlertWorker.objects.get_or_create(key="alerts", defaults={"lease_until": now})
    return bool(MarketAlertWorker.objects.filter(key="alerts").filter(
        Q(lease_owner=token) | Q(lease_until__lte=now)
    ).update(lease_owner=token, lease_until=now + timedelta(minutes=5), heartbeat_at=now))


def deliver_pending(now):
    MarketAlertEvent.objects.filter(status="pending", bar_closed_at__lt=now - timedelta(seconds=MAX_AGE)).update(
        status="cancelled", error="Signal expired before delivery."
    )
    # An interrupted send may already have reached Discord. Never blindly replay it.
    MarketAlertEvent.objects.filter(status="sending", retry_at__lt=now - timedelta(minutes=5)).update(
        status="failed", error="Delivery uncertain after worker interruption; not resent."
    )
    if not discord_ready():
        return
    worker = MarketAlertWorker.objects.filter(key="alerts").first()
    if worker and worker.discord_retry_at and worker.discord_retry_at > now:
        return
    events = MarketAlertEvent.objects.filter(status="pending", retry_at__lte=now).select_related("alert", "owner").order_by("id")[:20]
    for event in events:
        if (
            not event.alert or not event.alert.enabled or event.alert.revision != event.revision
            or (event.owner_id and not event.owner.is_active)
            or (now - event.bar_closed_at).total_seconds() > MAX_AGE
            or not active_window(event.symbol, event.timeframe, now)
        ):
            MarketAlertEvent.objects.filter(pk=event.pk, status="pending").update(status="cancelled", error="Alert paused, changed, or signal expired.")
            continue
        if not MarketAlertEvent.objects.filter(pk=event.pk, status="pending").update(status="sending", attempts=event.attempts + 1, retry_at=now):
            continue
        try:
            post_discord_message(event.message)
        except DiscordDeliveryError as exc:
            if exc.retry_after and event.attempts < 4:
                retry = now + timedelta(seconds=exc.retry_after)
                MarketAlertWorker.objects.filter(key="alerts").update(discord_retry_at=retry)
                MarketAlertEvent.objects.filter(pk=event.pk).update(status="pending", error=str(exc), retry_at=retry)
                # The destination is shared: respect its rate limit for every queued alert.
                MarketAlertEvent.objects.filter(status="pending", retry_at__lt=retry).update(retry_at=retry)
                break
            MarketAlertEvent.objects.filter(pk=event.pk).update(status="failed", error="Discord rejected delivery.")
        except Exception:
            MarketAlertEvent.objects.filter(pk=event.pk).update(status="failed", error="Discord delivery failed or is uncertain; not automatically resent.")
        else:
            MarketAlertEvent.objects.filter(pk=event.pk).update(status="sent", error="", sent_at=timezone.now())


def scan_market_alerts(token):
    now = timezone.now()
    if not claim_worker(token, now):
        return
    rules = list(MarketAlert.objects.filter(enabled=True).filter(Q(owner__is_active=True) | Q(owner=None)).select_related("owner"))
    jobs = {}
    for rule in rules:
        for symbol in rule.symbols:
            for timeframe in rule.timeframes:
                jobs.setdefault((symbol, timeframe), []).append(rule)
    for (symbol, timeframe), alerts in jobs.items():
        now = timezone.now()
        if not claim_worker(token, now):
            return
        if not active_window(symbol, timeframe, now):
            continue
        try:
            bars = completed_bars(get_alert_history(symbol, timeframe), symbol, timeframe, timezone.now())
        except Exception:
            for alert in alerts:
                MarketAlertState.objects.update_or_create(alert=alert, symbol=symbol, timeframe=timeframe,
                    defaults={"checked_at": now, "status": "History unavailable; check symbol or data provider"})
            continue
        for alert in alerts:
            try:
                process_rule(alert, symbol, timeframe, bars, timezone.now())
            except ValueError as exc:
                MarketAlertState.objects.update_or_create(alert=alert, symbol=symbol, timeframe=timeframe,
                    defaults={"checked_at": now, "status": str(exc)[:160]})
        deliver_pending(timezone.now())
    deliver_pending(timezone.now())
