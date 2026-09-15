from __future__ import annotations

import json
import logging
import ssl
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib import error, request
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from .models import TradeActivity


logger = logging.getLogger(__name__)
PACIFIC_ZONE = ZoneInfo("America/Los_Angeles")


def dispatch_trade_activity_alert(event_id: int) -> None:
    try:
        event = TradeActivity.objects.get(pk=event_id)
    except TradeActivity.DoesNotExist:
        return

    if not should_alert(event):
        return

    content = format_trade_activity_alert(event)
    if not content:
        return

    try:
        post_discord_message(content)
    except Exception:
        logger.exception("Discord alert failed for trade activity %s.", event_id)


def should_alert(event: TradeActivity) -> bool:
    if not getattr(settings, "DISCORD_ALERTS_ENABLED", False):
        return False
    if not getattr(settings, "DISCORD_WEBHOOK_URL", ""):
        return False
    if event.action not in {"OPEN", "CLOSE"}:
        return False
    if getattr(settings, "DISCORD_ALERT_STRATEGY_ONLY", True) and event.actor != "STRATEGY":
        return False
    return True


def post_discord_message(content: str) -> None:
    webhook_url = getattr(settings, "DISCORD_WEBHOOK_URL", "")
    body = json.dumps(
        {
            "username": getattr(settings, "DISCORD_ALERT_USERNAME", "Trading Dashboard"),
            "content": content[:2000],
        }
    ).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "local-trading-dashboard"},
        method="POST",
    )
    timeout = float(getattr(settings, "DISCORD_ALERT_TIMEOUT_SECONDS", 5))
    context = verified_ssl_context()
    try:
        with request.urlopen(req, timeout=timeout, context=context) as response:
            if response.status >= 400:
                raise RuntimeError(f"Discord webhook returned HTTP {response.status}.")
    except error.HTTPError as exc:
        raise RuntimeError(f"Discord webhook returned HTTP {exc.code}.") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Discord webhook request failed: {exc.reason}") from exc


def verified_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def send_test_alert() -> None:
    if not getattr(settings, "DISCORD_ALERTS_ENABLED", False):
        raise ValueError("Discord alerts are disabled. Set DISCORD_ALERTS_ENABLED=true and restart the server.")
    if not getattr(settings, "DISCORD_WEBHOOK_URL", ""):
        raise ValueError("Discord webhook URL is missing. Set DISCORD_WEBHOOK_URL and restart the server.")
    post_discord_message(
        "**TEST ALERT**\n"
        "Strategy: local dashboard\n"
        "Reason: Discord connection test\n"
        f"Alert time: {format_time(timezone.now())}"
    )


def format_trade_activity_alert(event: TradeActivity) -> str:
    trade = event.snapshot or {}
    if event.asset == "option":
        return format_option_alert(event, trade)
    return format_underlying_alert(event, trade)


def format_option_alert(event: TradeActivity, trade: dict[str, Any]) -> str:
    action = option_action(event, trade)
    header = f"**{action} {trade.get('underlying_symbol', '--')} {trade.get('timeframe') or ''}**".strip()
    lines = [
        header,
        f"Strategy: {trade.get('strategy') or event.strategy or '--'}",
        f"Reason: {human_reason(event.reason)}",
        (
            f"Contract: {trade.get('contract_symbol', '--')} "
            f"{str(trade.get('option_type') or '').upper()} "
            f"{format_price(trade.get('strike'))} exp {trade.get('expiration_date', '--')}"
        ),
        (
            f"Entry: {format_price(trade.get('entry_price'))} x {trade.get('quantity', '--')} "
            f"| premium {format_premium(trade.get('entry_price'), trade.get('quantity'))} "
            f"| underlying {format_price(trade.get('entry_underlying_price'))}"
        ),
        f"Entry quote: bid {format_price(trade.get('entry_bid'))} / ask {format_price(trade.get('entry_ask'))}",
        (
            f"Greeks: delta {format_metric(trade.get('entry_delta'))} "
            f"gamma {format_metric(trade.get('entry_gamma'))} "
            f"theta {format_metric(trade.get('entry_theta'))} "
            f"vega {format_metric(trade.get('entry_vega'))} "
            f"rho {format_metric(trade.get('entry_rho'))} "
            f"IV {format_percent(trade.get('entry_iv'))}"
        ),
        strategy_risk_line(trade),
    ]
    if event.action == "CLOSE":
        lines.extend(
            [
                (
                    f"Exit: {format_price(trade.get('exit_price'))} "
                    f"| premium {format_premium(trade.get('exit_price'), trade.get('quantity'))}"
                ),
                f"P/L: {format_money(trade.get('pnl'))}",
            ]
        )
    if trade.get("signal_time") is not None:
        lines.append(f"Signal candle: {format_time(trade.get('signal_time'))}")
    lines.append(f"Alert time: {format_time(event.recorded_at)}")
    return "\n".join(line for line in lines if line)


def format_underlying_alert(event: TradeActivity, trade: dict[str, Any]) -> str:
    side = str(trade.get("side") or "").upper()
    action = "BUY" if side == "LONG" else "SELL"
    if event.action == "CLOSE":
        action = "SELL TO CLOSE" if side == "LONG" else "BUY TO CLOSE"
    header = f"**{action} {trade.get('symbol', '--')} {trade.get('timeframe') or ''}**".strip()
    lines = [
        header,
        f"Strategy: {trade.get('strategy') or event.strategy or '--'}",
        f"Reason: {human_reason(event.reason)}",
        (
            f"Entry: {format_price(trade.get('entry_price'))} x {format_quantity(trade.get('quantity'))} "
            f"| risk {format_money(trade.get('risk_amount'))}"
        ),
        f"Stop: {format_price(trade.get('stop_price'))} | risk/share {format_price(trade.get('risk_per_share'))}",
    ]
    if event.action == "CLOSE":
        lines.extend(
            [
                f"Exit: {format_price(trade.get('exit_price'))}",
                f"P/L: {format_money(trade.get('pnl'))}",
            ]
        )
    if trade.get("signal_time") is not None:
        lines.append(f"Signal candle: {format_time(trade.get('signal_time'))}")
    lines.append(f"Alert time: {format_time(event.recorded_at)}")
    return "\n".join(line for line in lines if line)


def option_action(event: TradeActivity, trade: dict[str, Any]) -> str:
    side = str(trade.get("side") or "").upper()
    option_type = str(trade.get("option_type") or "").upper()
    if event.action == "CLOSE":
        return "SELL TO CLOSE" if side == "LONG" else "BUY TO CLOSE"
    if side == "SHORT":
        return f"SELL {option_type}".strip()
    return f"BUY {option_type}".strip()


def strategy_risk_line(trade: dict[str, Any]) -> str:
    stop = trade.get("strategy_stop_loss_percent")
    target = trade.get("strategy_take_profit_percent")
    parts = []
    if stop is not None:
        parts.append(f"stop loss {format_percent(stop)}")
    if target is not None:
        parts.append(f"take profit {format_percent(target)}")
    return "Strategy risk: " + " | ".join(parts) if parts else ""


def human_reason(reason: str) -> str:
    return str(reason or "--").replace("_", " ").title()


def decimal_value(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def format_price(value: Any) -> str:
    number = decimal_value(value)
    if number is None:
        return "--"
    return f"{number:.2f}"


def format_metric(value: Any) -> str:
    number = decimal_value(value)
    if number is None:
        return "--"
    return f"{number:.4f}"


def format_percent(value: Any) -> str:
    number = decimal_value(value)
    if number is None:
        return "--"
    return f"{number * Decimal('100'):.1f}%"


def format_money(value: Any) -> str:
    number = decimal_value(value)
    if number is None:
        return "--"
    return f"${number:.2f}"


def format_quantity(value: Any) -> str:
    number = decimal_value(value)
    if number is None:
        return "--"
    return f"{number:.4f}".rstrip("0").rstrip(".")


def format_premium(price_value: Any, quantity_value: Any) -> str:
    contract_price = decimal_value(price_value)
    quantity = decimal_value(quantity_value)
    if contract_price is None or quantity is None:
        return "--"
    return format_money(contract_price * quantity * Decimal("100"))


def format_time(value: Any) -> str:
    if value is None:
        return "--"
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=PACIFIC_ZONE)
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone=datetime_timezone.utc)
        dt = dt.astimezone(PACIFIC_ZONE)
    else:
        dt = value
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        dt = dt.astimezone(PACIFIC_ZONE)
    return dt.strftime("%b %d, %Y %I:%M:%S %p %Z")
