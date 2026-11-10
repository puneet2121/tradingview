"""Three fixed, paper-only strategies with durable intents and broker-owned fills."""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import math

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import alpaca, alpaca_orders as orders
from .data_source import alpaca_contracts, fetch_alpaca_bid_asks, fetch_alpaca_bot_history
from .discord_alerts import DiscordDeliveryError, format_time, post_discord_message
from .market_alerts import EASTERN, calendar, completed_bars, discord_ready, session_bounds, values
from .models import AlpacaBotAction, AlpacaBotConfig, AlpacaBotNotice, AlpacaBotPosition, AlpacaBotScan, AlpacaPaperOrder
from .paper_trading import calculate_vwap


SYMBOLS = ("AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "IWM", "SOFI", "SPY", "QQQ", "ABNB", "MCD", "WMT", "APP", "HOOD", "MU")
PRESETS = {
    "sma20_50": {"name": "SMA 20/50", "timeframes": ["1d"], "size": "10 shares", "rule": "SMA(20) crosses SMA(50); sell only the bot's held shares."},
    "ema9_vwap": {"name": "EMA 9 / VWAP", "timeframes": ["5m", "15m"], "size": "1 long contract", "rule": "EMA(9) crosses session VWAP, volume > previous volume, close confirms direction."},
    "ema9_21": {"name": "EMA 9 / EMA 21", "timeframes": ["5m", "15m"], "size": "1 long contract", "rule": "EMA(9) crosses EMA(21), volume > previous volume, close confirms direction."},
}
LIVE_PHASES = ("opening", "open", "closing", "blocked")
TP, SL = Decimal("0.30"), Decimal("0.20")
MAX_PREMIUM = Decimal("1500")
MAX_SIGNAL_AGE = 180


def available():
    return bool(getattr(settings, "ALPACA_AUTO_PAPER_ENABLED", False) and alpaca.manual_enabled() and not getattr(settings, "ALPACA_PAPER_ENABLED", False))


def config():
    return AlpacaBotConfig.objects.get_or_create(pk=1)[0]


def control(action, actor, min_dte=7):
    if action == "pause":
        cfg = config()
        AlpacaBotConfig.objects.filter(pk=1).update(enabled=False, actor=actor, status="New entries paused; bot exits remain managed")
        return state()
    if action != "start" or min_dte not in (7, 15, 60) or isinstance(min_dte, bool):
        raise ValueError("Use start/pause and an expiry target of 7, 15, or 60 days.")
    if not available():
        raise ValueError("Alpaca paper automation is disabled in server settings.")
    if getattr(settings, "ALPACA_PAPER_ENABLED", False):
        raise ValueError("Disable the legacy ALPACA_PAPER_ENABLED hook before starting this bot.")
    account = orders.current_account()
    if account.get("status") != "ACTIVE" or account.get("trading_blocked") or account.get("account_blocked"):
        raise ValueError("The connected paper account cannot trade.")
    if int(account.get("options_trading_level") or 0) < 2:
        raise ValueError("Options level 2 is required for the CALL/PUT strategies.")
    if not discord_ready():
        raise ValueError("Configure and enable the shared Discord webhook before starting the bot.")
    cfg = config()
    if cfg.account_id and cfg.account_id != str(account["id"]) and AlpacaBotPosition.objects.filter(phase__in=LIVE_PHASES).exists():
        raise ValueError("Existing bot positions belong to another paper account. Restore that connection first.")
    cfg.enabled = cfg.manage_exits = True
    cfg.account_id, cfg.actor, cfg.min_dte = str(account["id"]), actor, min_dte
    # Repeated Start requests must not move the activation baseline.
    if not AlpacaBotConfig.objects.filter(pk=1, enabled=True, account_id=cfg.account_id).exists():
        cfg.armed_at = timezone.now()
    cfg.status = "Armed; waiting for new completed candles"
    cfg.save(update_fields=["enabled", "manage_exits", "account_id", "actor", "min_dte", "armed_at", "status"])
    return state()


def claim(owner):
    now = timezone.now()
    return bool(AlpacaBotConfig.objects.filter(pk=1).filter(Q(lease_owner=owner) | Q(lease_owner="") | Q(lease_until__lte=now)).update(
        lease_owner=owner, lease_until=now + timedelta(seconds=300), heartbeat_at=now))


def guard(owner, account_id, *, entry=False):
    cfg = config()
    if not available() or cfg.account_id != account_id or not cfg.manage_exits:
        raise ValueError("Automation disabled or the bound paper account changed.")
    if cfg.lease_owner != owner or cfg.lease_until <= timezone.now() or (entry and not cfg.enabled):
        raise ValueError("Worker lease expired or new entries are paused.")
    if not claim(owner):
        raise ValueError("Another worker owns this scan.")
    return cfg


def regular_session(now):
    bounds = session_bounds(now.astimezone(EASTERN).date())
    return bounds if bounds and bounds[0] <= now.timestamp() < bounds[1] else None


def evaluate(strategy, bars):
    if strategy == "sma20_50":
        left, right = values(bars, {"type": "sma", "period": 20}), values(bars, {"type": "sma", "period": 50})
        names = ("SMA(20)", "SMA(50)")
    else:
        left = values(bars, {"type": "ema", "period": 9})
        right = calculate_vwap(bars) if strategy == "ema9_vwap" else values(bars, {"type": "ema", "period": 21})
        names = ("EMA(9)", "session VWAP" if strategy == "ema9_vwap" else "EMA(21)")
    if len(bars) < 2 or any(value is None or not math.isfinite(value) for value in [*left[-2:], *right[-2:]]):
        return None
    previous, current = bars[-2:]
    if strategy != "sma20_50" and datetime.fromtimestamp(previous["time"], EASTERN).date() != datetime.fromtimestamp(current["time"], EASTERN).date():
        return None
    cross = "BUY" if left[-2] <= right[-2] and left[-1] > right[-1] else "SELL" if left[-2] >= right[-2] and left[-1] < right[-1] else ""
    volume_up = float(current.get("volume") or 0) > float(previous.get("volume") or 0)
    confirmation = current["close"] > previous["close"] if cross == "BUY" else current["close"] < previous["close"]
    signal = cross if strategy == "sma20_50" or (volume_up and confirmation) else ""
    reason = f"{names[0]} {left[-2]:.4f} -> {left[-1]:.4f}; {names[1]} {right[-2]:.4f} -> {right[-1]:.4f}."
    if cross:
        reason = f"{names[0]} crossed {'above' if cross == 'BUY' else 'below'} {names[1]}. " + reason
    if strategy != "sma20_50":
        reason += f" Volume {previous.get('volume', 0):g} -> {current.get('volume', 0):g}; close {previous['close']:.4f} -> {current['close']:.4f}."
    return {"cross": cross, "signal": signal, "reason": reason, "bar_time": int(current["time"]), "closed_at": current["closed_at"],
            "left": left[-1], "right": right[-1], "volume_increasing": volume_up, "close_confirmation": confirmation}


def signal_deadline(timeframe, latest, now):
    bounds = regular_session(now)
    if not bounds:
        return None
    if timeframe != "1d":
        return latest["closed_at"] + MAX_SIGNAL_AGE
    previous_day = calendar().previous_session(str(now.astimezone(EASTERN).date())).date()
    expected = session_bounds(previous_day)
    if not expected or latest["closed_at"] != expected[1]:
        return None
    return bounds[0] + 15 * 60


def checked_quote(raw, now, *, options=False, entry=False):
    if not raw or any(raw.get(name) is None for name in ("bid", "ask", "time")):
        raise ValueError("No usable bid/ask quote; trade skipped.")
    bid, ask = orders.decimal(raw["bid"]), orders.decimal(raw["ask"])
    if not 0 < bid <= ask or not -5 <= now.timestamp() - raw["time"] <= 120:
        raise ValueError("Quote is stale, crossed, or has no bid; waiting for fresh prices.")
    if entry and (ask - bid) / ((ask + bid) / 2) > (Decimal("0.20") if options else Decimal("0.01")):
        raise ValueError("Bid/ask spread exceeds the entry limit (20% options, 1% stocks).")
    return bid, ask


def rounded_price(price, options=False):
    tick = Decimal("0.01") if options or price >= 1 else Decimal("0.0001")
    result = price.quantize(tick, rounding=ROUND_HALF_UP)
    if result <= 0:
        raise ValueError("No positive tradable limit price.")
    return str(result)


def select_contract(symbol, direction, cfg, now):
    stock_quote = fetch_alpaca_bid_asks([symbol]).get(symbol)
    bid, ask = checked_quote(stock_quote, now, entry=True)
    underlying = (bid + ask) / 2
    today = now.astimezone(EASTERN).date()
    earliest, latest = today + timedelta(days=cfg.min_dte), today + timedelta(days=max(62, cfg.min_dte + 14))
    kind = "call" if direction == "BUY" else "put"
    contracts = [row for row in alpaca_contracts(symbol) if row.get("type") == kind and row.get("tradable")
                 and row.get("root_symbol", symbol) == symbol and orders.decimal(row.get("size", 0)) == 100
                 and earliest.isoformat() <= row["expiration_date"] <= latest.isoformat()]
    if not contracts:
        raise ValueError("No standard tradable contracts in the selected expiry window.")
    expiration = min(row["expiration_date"] for row in contracts)
    candidates = [row for row in contracts if row["expiration_date"] == expiration]
    contract = min(candidates, key=lambda row: (abs(orders.decimal(row["strike_price"]) - underlying), orders.decimal(row["strike_price"])))
    quote = fetch_alpaca_bid_asks([contract["symbol"]], options=True).get(contract["symbol"])
    _, premium = checked_quote(quote, timezone.now(), options=True, entry=True)
    limit = rounded_price(premium, options=True)
    if Decimal(limit) * 100 > MAX_PREMIUM:
        raise ValueError("The nearest ATM contract costs more than the $1,500 premium cap.")
    return contract, limit, quote


def notice(key, message):
    AlpacaBotNotice.objects.get_or_create(key=key, defaults={"message": message[:1900]})


def action_message(action, label):
    snapshot = action.snapshot
    signal_time = snapshot.get("closed_at")
    text = f"**ALPACA PAPER | {label}**\nStrategy: {PRESETS[action.strategy]['name']} | {action.underlying} | {action.timeframe}\nReason: {action.reason}\n"
    if action.strategy != "sma20_50" and action.kind != "exit":
        text += f"Direction: {'CALL' if action.direction == 'BUY' else 'PUT'} (buy to open, no short options)\n"
    if signal_time:
        text += f"Signal candle closed: {format_time(datetime.fromtimestamp(signal_time, timezone.get_default_timezone()))}\n"
    if action.error:
        text += f"Status: {action.error}\n"
    if action.order_id:
        order = action.order
        text += f"{order.symbol} | {order.intent.replace('_', ' ')} | Quantity {order.payload['qty']}\nLimit ${order.payload['limit_price']} | Broker status: {order.broker_status or order.phase}\n"
        if order.filled_qty > 0 and order.filled_avg_price is not None:
            text += f"Actual fill: {order.filled_qty:g} at ${order.filled_avg_price:.4f}\n"
            if action.kind == "entry" and order.asset_class == "us_option":
                text += f"Premium ${order.filled_avg_price * order.filled_qty * 100:.2f} | TP trigger ${order.filled_avg_price * (1 + TP):.4f} | SL trigger ${order.filled_avg_price * (1 - SL):.4f}\n"
        else:
            text += "Not a confirmed fill.\n"
    text += f"Feeds: {alpaca.data_config()['stock_feed'].upper()} stocks / {alpaca.data_config()['option_feed']} options\nAlert: {format_time(timezone.now())}"
    return text


def skip(action, reason):
    if action.order_id:
        action.order = AlpacaPaperOrder.objects.get(pk=action.order_id)
        if action.order.phase in orders.ACTIVE_PHASES or action.order.filled_qty:
            action.outcome, action.error = "submitted", "Order saved; status verification delayed. No replacement submitted."
            action.save(update_fields=["outcome", "error"])
            notice(f"{action.pk}:uncertain", action_message(action, "STATUS CHECK NEEDED"))
            return
    action.outcome, action.error = "skipped", str(reason)[:240]
    action.save(update_fields=["outcome", "error"])
    notice(f"{action.pk}:skipped", action_message(action, "SKIPPED"))


def ledger(position):
    entry = position.entry_order
    quantity = entry.filled_qty if entry else Decimal(0)
    closed_qty, proceeds = Decimal(0), Decimal(0)
    for action in position.actions.filter(kind="exit", order__isnull=False).select_related("order"):
        closed_qty += action.order.filled_qty
        if action.order.filled_qty and action.order.filled_avg_price is None:
            return quantity, closed_qty, None
        proceeds += action.order.filled_qty * (action.order.filled_avg_price or 0)
    return quantity, closed_qty, proceeds


def before_submit(owner, action, position, *, entry):
    guard(owner, action.account_id, entry=entry)
    now = timezone.now()
    if not -5 <= now.timestamp() - action.snapshot.get("quote_time", 0) <= 120:
        raise ValueError("The order quote expired; no order submitted.")
    bounds = regular_session(now)
    if not bounds or not alpaca.read_get("/v2/clock", paper=True).get("is_open"):
        raise ValueError("Regular trading session is closed.")
    if entry and (now.timestamp() > action.snapshot["valid_until"] or now.timestamp() >= bounds[1] - 120):
        raise ValueError("Signal expired or the market is about to close.")
    current = orders.optional_get(f"/v2/positions/{position.symbol}")
    held = orders.decimal(current["qty"]) if current else Decimal(0)
    bought, sold, _ = ledger(position)
    if entry and held != 0:
        raise ValueError("An existing broker position owns this symbol; bot will not mix holdings.")
    if not entry and (held <= 0 or held != bought - sold):
        raise ValueError("Broker quantity no longer matches bot-owned shares/contracts. Manual review required.")


def send_order(owner, action, position, ticket):
    entry = action.kind == "entry"
    before_submit(owner, action, position, entry=entry)
    prepared = orders.prepare(ticket, orders.BOT_ACTOR, automation_key=action.pk)
    record = AlpacaPaperOrder.objects.get(pk=prepared["order"]["id"])
    with transaction.atomic():
        action.order = record
        action.save(update_fields=["order"])
        if entry:
            position.entry_order = record
            position.save(update_fields=["entry_order"])
    result = orders.submit(record.pk, prepared["confirmation_token"], orders.BOT_ACTOR,
                           before_send=lambda: before_submit(owner, action, position, entry=entry))
    action.outcome, action.order = "submitted", result
    action.save(update_fields=["outcome"])
    observe(action)


def observe(action):
    if not action.order_id:
        return
    record = action.order
    if record.phase in orders.ACTIVE_PHASES:
        record = orders.lookup(record)
    action.order = record
    fingerprint = f"{record.pk}:{record.phase}:{record.broker_status}:{record.filled_qty}:{record.filled_avg_price}"
    notice(fingerprint, action_message(action, "ORDER UPDATE"))


def process_signal(owner, cfg, strategy, symbol, timeframe, bars, now):
    scan, _ = AlpacaBotScan.objects.get_or_create(strategy=strategy, symbol=symbol, timeframe=timeframe)
    scan.checked_at = now
    def status(message):
        scan.status = message[:240]
        scan.save(update_fields=["checked_at", "status", "last_bar_time"])
    if not bars:
        status("Waiting for completed regular-session candles")
        return
    latest = bars[-1]
    deadline = signal_deadline(timeframe, latest, now)
    if deadline is None or now.timestamp() > deadline:
        status("No fresh completed signal candle")
        return
    if scan.last_bar_time == latest["time"]:
        return
    result = evaluate(strategy, bars)
    scan.last_bar_time = latest["time"]
    if not result:
        status("Warming up indicators / same-session comparison")
        return
    position = AlpacaBotPosition.objects.filter(account_id=cfg.account_id, strategy=strategy, underlying=symbol, timeframe=timeframe, phase__in=LIVE_PHASES).first()
    if position and result["cross"] and result["cross"] != position.direction and result["closed_at"] > position.created_at.timestamp():
        position.exit_reason = "Opposite crossover: " + ("bearish" if result["cross"] == "SELL" else "bullish")
        position.save(update_fields=["exit_reason"])
    if not result["signal"]:
        status("No confirmed crossover" if not result["cross"] else "Crossover did not pass volume/close entry filters")
        return
    if not cfg.enabled or not cfg.armed_at or result["closed_at"] <= cfg.armed_at.timestamp():
        status("Entries paused / waiting for a candle completed after activation")
        return
    result["valid_until"] = deadline
    key = f"{cfg.account_id}:{strategy}:{symbol}:{timeframe}:{result['bar_time']}:{result['signal']}"
    action, created = AlpacaBotAction.objects.get_or_create(key=key, defaults={"account_id": cfg.account_id, "strategy": strategy,
        "underlying": symbol, "timeframe": timeframe, "kind": "signal", "direction": result["signal"], "reason": result["reason"], "snapshot": result})
    if not created:
        status("Signal already recorded")
        return
    if position or (strategy == "sma20_50" and result["signal"] == "SELL"):
        action.position = position
        action.outcome = "exit_requested" if position and position.exit_reason else "skipped"
        action.error = "Closing the bot-owned position" if action.outcome == "exit_requested" else "No new entry: position already held, or no bot-owned shares to sell"
        if position and position.exit_reason and strategy != "sma20_50" and position.direction != result["signal"]:
            action.outcome, action.error = "waiting_exit", "Opposite signal: close first; reverse only after confirmed flat and while this signal is fresh"
        action.save(update_fields=["position", "outcome", "error"])
        notice(f"{action.pk}:signal", action_message(action, action.outcome.upper()))
        status(action.error)
        return
    enter_signal(owner, cfg, action, now)
    status(action.error or f"Entry {action.order.broker_status or action.order.phase}: {action.order.symbol}")


def enter_signal(owner, cfg, action, now):
    strategy, symbol, timeframe, result = action.strategy, action.underlying, action.timeframe, action.snapshot
    try:
        guard(owner, cfg.account_id, entry=True)
        if now.timestamp() > result["valid_until"] or result["closed_at"] <= cfg.armed_at.timestamp():
            raise ValueError("Signal expired or predates the latest activation; not replayed.")
        options = strategy != "sma20_50"
        if options:
            contract, limit, quote = select_contract(symbol, result["signal"], cfg, now)
            order_symbol, expiration = contract["symbol"], contract["expiration_date"]
        else:
            quote = fetch_alpaca_bid_asks([symbol]).get(symbol)
            _, ask = checked_quote(quote, timezone.now(), entry=True)
            order_symbol, expiration, limit = symbol, None, rounded_price(ask)
        current = orders.optional_get(f"/v2/positions/{order_symbol}")
        if current and orders.decimal(current["qty"]) != 0:
            raise ValueError("Existing Alpaca holdings for this symbol are not owned by this strategy.")
        position = AlpacaBotPosition.objects.create(account_id=cfg.account_id, strategy=strategy, underlying=symbol, timeframe=timeframe,
            symbol=order_symbol, asset_class="us_option" if options else "us_equity", direction=result["signal"], expiration=expiration)
        action.kind, action.position = "entry", position
        action.snapshot = {**result, "quote_time": quote["time"]}
        action.save(update_fields=["kind", "position", "snapshot"])
        send_order(owner, action, position, {"symbol": order_symbol, "asset_class": position.asset_class, "action": "buy", "quantity": "1" if options else "10", "limit_price": limit})
    except (ValueError, alpaca.AlpacaDataError, IntegrityError) as exc:
        skip(action, "Another bot strategy owns this contract/slot" if isinstance(exc, IntegrityError) else str(exc))
        if action.position_id and not action.order_id:
            AlpacaBotPosition.objects.filter(pk=action.position_id).update(phase="closed", status=action.error, closed_at=timezone.now())


def process_reversals(owner, cfg, now):
    pending = AlpacaBotAction.objects.filter(account_id=cfg.account_id, outcome="waiting_exit").select_related("position")
    for action in pending:
        if not cfg.enabled or now.timestamp() > action.snapshot["valid_until"] or action.snapshot["closed_at"] <= cfg.armed_at.timestamp():
            skip(action, "Reversal expired or entries paused; no historical order submitted")
            continue
        if action.position.phase in ("blocked", "external"):
            skip(action, "Position changed outside the bot; reversal cancelled")
        elif action.position.phase == "closed":
            action.position, action.error, action.outcome = None, "", "pending"
            action.save(update_fields=["position", "error", "outcome"])
            enter_signal(owner, cfg, action, now)


def cancel_working(owner, action):
    record = action.order
    if record.phase in orders.ACTIVE_PHASES and record.broker_order_id:
        action.order = orders.cancel(record.pk, orders.BOT_ACTOR, before_send=lambda: guard(owner, action.account_id))


def monitor_position(owner, cfg, position, broker_positions, quote, now):
    entry = position.entry_order
    if not entry:
        if (now - position.created_at).total_seconds() > 300:
            position.phase, position.status, position.closed_at = "closed", "Interrupted before order creation; no retry", now
            position.save()
        return
    options = position.asset_class == "us_option"
    bought, sold, proceeds = ledger(position)
    remaining = bought - sold
    position.remaining_qty = remaining
    multiplier = 100 if options else 1
    if remaining < 0:
        position.phase, position.status = "blocked", "Inconsistent fill ledger; manual review required"
        position.save()
        return
    active = list(position.actions.filter(order__phase__in=orders.ACTIVE_PHASES).select_related("order"))
    pending_tickets = list(position.actions.filter(order__phase="prepared").select_related("order"))
    if pending_tickets:
        for action in pending_tickets:
            if now > action.order.expires_at:
                AlpacaPaperOrder.objects.filter(pk=action.order_id, phase="prepared").update(phase="aborted", error="Interrupted bot ticket expired; not replayed.")
        position.status = "Waiting for an unsubmitted ticket to expire; no duplicate order"
        position.save()
        return
    if not active and remaining == 0 and entry.phase not in ("prepared",):
        position.phase, position.closed_at = "closed", now
        position.pnl = (proceeds - bought * entry.filled_avg_price) * multiplier if proceeds is not None and entry.filled_avg_price is not None else Decimal(0) if not bought else None
        position.status = "All bot-owned units closed" if bought else "Entry ended without a fill"
        position.save()
        if bought and sold and position.pnl is not None:
            message = f"**ALPACA PAPER | CLOSED**\n{PRESETS[position.strategy]['name']} | {position.underlying} {position.timeframe}\n{position.symbol}\nReason: {position.exit_reason or position.status}\nBought {bought:g} at ${entry.filled_avg_price:.4f} | Sold {sold:g} at ${proceeds / sold:.4f}\nRealized P/L: ${position.pnl:.2f} excluding fees\n{format_time(now)}"
        else:
            message = f"**ALPACA PAPER | {position.symbol}**\n{position.status}. {'No fill.' if not bought else 'Fill-price reconciliation pending.'}"
        notice(f"{position.pk}:closed", message)
        return
    if entry.phase == "prepared":
        if now > entry.expires_at:
            AlpacaPaperOrder.objects.filter(pk=entry.pk, phase="prepared").update(phase="aborted", error="Bot ticket expired before submission; not replayed.")
        return
    bid = None
    try:
        bid, _ = checked_quote(quote, now, options=options)
        position.mark, position.quote_time = bid, datetime.fromtimestamp(quote["time"], tz=now.tzinfo)
        if entry.filled_avg_price is not None and proceeds is not None:
            position.pnl = (proceeds + remaining * bid - bought * entry.filled_avg_price) * multiplier
        if options and bought and entry.filled_avg_price:
            if bid <= entry.filled_avg_price * (1 - SL):
                position.exit_reason = "Stop loss: bid at or below -20% of actual entry fill"
            elif bid >= entry.filled_avg_price * (1 + TP) and not position.exit_reason:
                position.exit_reason = "Take profit: bid at or above +30% of actual entry fill"
        bounds = regular_session(now)
        if options and position.expiration and position.expiration <= now.astimezone(EASTERN).date() and bounds and now.timestamp() >= bounds[1] - 45 * 60:
            position.exit_reason = "Expiration-day exit before broker exercise cutoff"
    except ValueError as exc:
        position.status = str(exc)
    for action in active:
        age = (now - (action.order.submitted_at or action.created_at)).total_seconds()
        if age >= 90 or (action.kind == "entry" and (not cfg.enabled or position.exit_reason)):
            cancel_working(owner, action)
        position.status = "Order unresolved; waiting for broker fill/cancellation confirmation"
    if active:
        position.save()
        return
    broker = broker_positions.get(position.symbol)
    held = orders.decimal(broker["qty"]) if broker else Decimal(0)
    if held != remaining:
        if (now - (entry.filled_at or entry.submitted_at or position.created_at)).total_seconds() < 60:
            position.status = "Waiting for broker position reconciliation"
        else:
            position.phase = "external" if held == 0 else "blocked"
            position.status = "Broker holdings changed outside this bot; automatic selling stopped"
            position.pnl = None
            notice(f"{position.pk}:external", f"**ALPACA PAPER | MANUAL REVIEW**\n{position.symbol}\n{position.status}. Expected {remaining:g}; broker reports {held:g}. No order sent.")
        position.save()
        return
    if position.phase == "blocked":
        return
    position.phase = "open"
    if bid is not None:
        position.status = "Managed / latest bid mark"
    if not position.exit_reason or bid is None:
        position.save()
        return
    attempt = position.actions.filter(kind="exit").count()
    if attempt >= 5:
        position.phase, position.status = "blocked", "Five exit attempts did not close the position; manage it in Alpaca"
        position.save()
        notice(f"{position.pk}:exit-failed", f"**ALPACA PAPER | MANUAL REVIEW**\n{position.symbol}\n{position.status}. Remaining {remaining:g}.")
        return
    action, created = AlpacaBotAction.objects.get_or_create(key=f"exit:{position.pk}:{attempt}", defaults={"account_id": cfg.account_id,
        "strategy": position.strategy, "underlying": position.underlying, "timeframe": position.timeframe, "kind": "exit", "direction": "SELL",
        "reason": position.exit_reason, "position": position, "snapshot": {"quote_time": quote["time"]}})
    if not created:
        return
    try:
        send_order(owner, action, position, {"symbol": position.symbol, "asset_class": position.asset_class, "action": "close", "quantity": str(remaining), "limit_price": rounded_price(bid, options)})
        position.phase, position.status = "closing", "Closing limit order submitted; awaiting broker confirmation"
    except (ValueError, alpaca.AlpacaDataError, IntegrityError) as exc:
        skip(action, str(exc) if not isinstance(exc, IntegrityError) else "Another order is active for this symbol")
        position.status = action.error
        if action.order_id:
            AlpacaPaperOrder.objects.filter(pk=action.order_id, phase="prepared").update(phase="aborted", error=action.error)
            if action.order.phase in orders.ACTIVE_PHASES:
                position.phase = "closing"
    position.save()


def deliver_notices(owner, cfg, now):
    if not discord_ready() or not regular_session(now):
        return
    if cfg.discord_retry_at and cfg.discord_retry_at > now:
        return
    for item in AlpacaBotNotice.objects.filter(status="pending", retry_at__lte=now).order_by("id")[:15]:
        if not regular_session(timezone.now()):
            break
        guard(owner, cfg.account_id)
        if (now - item.created_at).total_seconds() > 15 * 60:
            AlpacaBotNotice.objects.filter(pk=item.pk).update(status="expired", error="Old notification retained in history only")
            continue
        if not AlpacaBotNotice.objects.filter(pk=item.pk, status="pending").update(status="sending", attempts=item.attempts + 1):
            continue
        try:
            post_discord_message(item.message)
        except DiscordDeliveryError as exc:
            if exc.retry_after and item.attempts < 4:
                retry = now + timedelta(seconds=exc.retry_after)
                AlpacaBotConfig.objects.filter(pk=1).update(discord_retry_at=retry)
                AlpacaBotNotice.objects.filter(pk=item.pk).update(status="pending", retry_at=retry, error="Discord rate limited delivery")
                break
            AlpacaBotNotice.objects.filter(pk=item.pk).update(status="failed", error="Discord rejected delivery")
        except Exception:
            AlpacaBotNotice.objects.filter(pk=item.pk).update(status="failed", error="Delivery failed or uncertain; not automatically repeated")
        else:
            AlpacaBotNotice.objects.filter(pk=item.pk).update(status="sent", sent_at=timezone.now(), error="")


def scan_once(owner):
    cfg = config()
    if not available() or not cfg.manage_exits or not claim(owner):
        return
    account = orders.current_account()
    if str(account["id"]) != cfg.account_id:
        AlpacaBotConfig.objects.filter(pk=1).update(status="Account changed: automation blocked. Restore the bound paper account.")
        return
    now = timezone.now()
    bounds = regular_session(now)
    if not bounds or not alpaca.read_get("/v2/clock", paper=True).get("is_open"):
        AlpacaBotConfig.objects.filter(pk=1).update(status="Market closed: no orders or Discord messages")
        return
    actions = AlpacaBotAction.objects.filter(account_id=cfg.account_id, order__isnull=False).filter(
        Q(order__phase__in=orders.ACTIVE_PHASES) | Q(position__phase__in=LIVE_PHASES)
    ).select_related("order").order_by("created_at")
    for action in actions:
        guard(owner, cfg.account_id)
        observe(action)
    open_positions = list(AlpacaBotPosition.objects.filter(account_id=cfg.account_id, phase__in=LIVE_PHASES).select_related("entry_order"))
    broker_positions = {row["symbol"]: row for row in alpaca.read_get("/v2/positions", paper=True)}
    equity_quotes = fetch_alpaca_bid_asks([row.symbol for row in open_positions if row.asset_class == "us_equity"])
    option_quotes = fetch_alpaca_bid_asks([row.symbol for row in open_positions if row.asset_class == "us_option"], options=True)
    for position in open_positions:
        guard(owner, cfg.account_id)
        try:
            monitor_position(owner, cfg, position, broker_positions, (option_quotes if position.asset_class == "us_option" else equity_quotes).get(position.symbol), timezone.now())
        except (ValueError, alpaca.AlpacaDataError) as exc:
            AlpacaBotPosition.objects.filter(pk=position.pk).update(status=str(exc)[:240])
    process_reversals(owner, guard(owner, cfg.account_id), timezone.now())
    for timeframe in ("5m", "15m", "1d"):
        now = timezone.now()
        cfg = guard(owner, cfg.account_id)
        if not regular_session(now) or (timeframe == "1d" and now.timestamp() > bounds[0] + 15 * 60):
            continue
        jobs = [(key, symbol) for key, preset in PRESETS.items() if timeframe in preset["timeframes"] for symbol in SYMBOLS
                if cfg.enabled or AlpacaBotPosition.objects.filter(account_id=cfg.account_id, strategy=key, underlying=symbol, timeframe=timeframe, phase__in=LIVE_PHASES).exists()]
        if not jobs:
            continue
        try:
            history = fetch_alpaca_bot_history(SYMBOLS, timeframe, now)
        except (ValueError, alpaca.AlpacaDataError) as exc:
            for strategy, symbol in jobs:
                AlpacaBotScan.objects.update_or_create(strategy=strategy, symbol=symbol, timeframe=timeframe, defaults={"checked_at": now, "status": str(exc)[:240]})
            continue
        for strategy, symbol in jobs:
            cfg = guard(owner, cfg.account_id)
            try:
                bars = completed_bars(history.get(symbol, []), symbol, timeframe, timezone.now())
                process_signal(owner, cfg, strategy, symbol, timeframe, bars, timezone.now())
            except (ValueError, alpaca.AlpacaDataError, IntegrityError) as exc:
                AlpacaBotScan.objects.update_or_create(strategy=strategy, symbol=symbol, timeframe=timeframe, defaults={"checked_at": timezone.now(), "status": str(exc)[:240]})
    deliver_notices(owner, cfg, timezone.now())
    AlpacaBotConfig.objects.filter(pk=1, lease_owner=owner).update(status="Scanning completed Alpaca bars" if cfg.enabled else "Entries paused; existing bot exits managed", heartbeat_at=timezone.now())


def state():
    cfg = config()
    positions = []
    for pos in AlpacaBotPosition.objects.filter(account_id=cfg.account_id).select_related("entry_order")[:100]:
        entry = pos.entry_order
        _, sold, proceeds = ledger(pos)
        positions.append({"id": str(pos.pk), "strategy": PRESETS[pos.strategy]["name"], "underlying": pos.underlying, "timeframe": pos.timeframe,
            "symbol": pos.symbol, "direction": ("CALL" if pos.direction == "BUY" else "PUT") if pos.asset_class == "us_option" else "LONG", "phase": pos.phase,
            "quantity": str(pos.remaining_qty), "entry": str(entry.filled_avg_price) if entry and entry.filled_avg_price is not None else None,
            "exit": str(proceeds / sold) if sold and proceeds is not None else None,
            "mark": str(pos.mark) if pos.mark is not None else None, "pnl": str(pos.pnl) if pos.pnl is not None else None,
            "quote_time": pos.quote_time.isoformat() if pos.quote_time else None, "status": pos.status, "exit_reason": pos.exit_reason})
    return {"available": available(), "enabled": cfg.enabled, "manage_exits": cfg.manage_exits, "status": cfg.status, "min_dte": cfg.min_dte,
        "heartbeat": cfg.heartbeat_at.isoformat() if cfg.heartbeat_at else None, "armed_at": cfg.armed_at.isoformat() if cfg.armed_at else None,
        "worker_running": bool(cfg.heartbeat_at and (timezone.now() - cfg.heartbeat_at).total_seconds() < 90),
        "symbols": list(SYMBOLS), "presets": [{"key": key, **value} for key, value in PRESETS.items()], "positions": positions,
        "scans": list(AlpacaBotScan.objects.order_by("strategy", "symbol", "timeframe").values("strategy", "symbol", "timeframe", "status", "checked_at")),
        "notices": list(AlpacaBotNotice.objects.order_by("-id").values("message", "status", "error", "created_at")[:30]), "discord_ready": discord_ready(),
        "policy": {"contracts": 1, "shares": 10, "take_profit_percent": 30, "stop_loss_percent": 20, "max_premium": 1500}}
