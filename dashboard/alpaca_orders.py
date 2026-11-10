"""Confirmed manual or guarded bot paper orders; no live host or automatic POST retry."""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
import uuid
from zoneinfo import ZoneInfo

from django.core import signing
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
import requests

from . import alpaca
from .models import AlpacaOrderEvent, AlpacaPaperOrder


ACTIVE_PHASES = ("submitting", "working", "unknown", "cancel_pending")
TERMINAL_STATUSES = ("filled", "canceled", "expired", "rejected", "replaced")
SIGNING_SALT = "alpaca-manual-paper-order-v1"
BOT_ACTOR = "ALPACA_BOT"


def decimal(value, name="value"):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid {name}.") from None


def require_manual():
    if not alpaca.manual_enabled():
        raise ValueError("Manual Alpaca paper trading is disabled.")
    alpaca.data_headers()


def current_account():
    account = alpaca.read_get("/v2/account", paper=True)
    if not account.get("id"):
        raise alpaca.AlpacaDataError("Alpaca did not identify the paper account.")
    return account


def optional_get(path, params=None):
    try:
        return alpaca.read_get(path, params, paper=True)
    except alpaca.AlpacaDataError as exc:
        if exc.status == 404:
            return None
        raise


def validate_ticket(ticket, account, ignore_order=None):
    require_manual()
    if not isinstance(ticket, dict) or set(ticket) != {"symbol", "asset_class", "action", "quantity", "limit_price"}:
        raise ValueError("Use a symbol, asset class, action, quantity, and limit price only.")
    symbol = str(ticket["symbol"]).strip().upper()
    asset_class, action = ticket["asset_class"], ticket["action"]
    if asset_class not in {"us_equity", "us_option"} or action not in {"buy", "close"}:
        raise ValueError("Only stock/ETF or single-option buys and position closes are supported.")
    pattern = r"[A-Z][A-Z0-9.\-]{0,14}" if asset_class == "us_equity" else r"[A-Z0-9]{1,6}[0-9]{6}[CP][0-9]{8}"
    if not re.fullmatch(pattern, symbol):
        raise ValueError("Invalid stock or option contract symbol.")
    qty, limit = decimal(ticket["quantity"], "quantity"), decimal(ticket["limit_price"], "limit price")
    max_qty = 100 if asset_class == "us_option" else 1000
    if qty != qty.to_integral_value() or not 1 <= qty <= max_qty:
        raise ValueError(f"Quantity must be a whole number from 1 to {max_qty}.")
    tick = Decimal("0.0001") if asset_class == "us_equity" and limit < 1 else Decimal("0.01")
    if not 0 < limit <= 1000000 or limit % tick:
        raise ValueError(f"Limit price must be positive, at most 1000000, and in increments of {tick}.")
    if account.get("status") != "ACTIVE" or account.get("trading_blocked") or account.get("account_blocked"):
        raise ValueError("The Alpaca paper account is not currently permitted to trade.")
    pending = AlpacaPaperOrder.objects.filter(account_id=account["id"], symbol=symbol, phase__in=ACTIVE_PHASES)
    if ignore_order:
        pending = pending.exclude(pk=ignore_order.pk)
    if pending.exists():
        raise ValueError("An app order for this symbol is unresolved. Refresh or cancel it before placing another.")
    # No stale account/position cache is used for risk or close-size checks.
    open_orders = alpaca.read_get("/v2/orders", {"status": "open", "symbols": symbol, "limit": 500, "nested": "true"}, paper=True)
    if open_orders:
        raise ValueError("Alpaca has an open order for this symbol. Resolve it before placing another.")
    position = optional_get(f"/v2/positions/{symbol}")
    position_qty = decimal(position["qty"], "position quantity") if position else Decimal(0)
    if action == "close":
        if not position or position.get("asset_class") != asset_class or abs(position_qty) < qty:
            raise ValueError("Close quantity exceeds the current Alpaca position.")
        available = abs(decimal(position.get("qty_available", position_qty), "available quantity"))
        if qty > available:
            raise ValueError("Some of this position is reserved by another order.")
        side = "sell" if position_qty > 0 else "buy"
        intent = "sell_to_close" if side == "sell" else "buy_to_close"
    else:
        if position_qty < 0:
            raise ValueError("Use Close position to cover an existing short position.")
        side, intent = "buy", "buy_to_open"
    multiplier, contract = Decimal(1), None
    if asset_class == "us_option":
        contract = alpaca.read_get(f"/v2/options/contracts/{symbol}", paper=True)
        if contract.get("symbol") != symbol or contract.get("status") != "active" or not contract.get("tradable"):
            raise ValueError("This Alpaca option contract is not tradable.")
        multiplier = decimal(contract.get("size"), "contract multiplier")
        if multiplier != 100:
            raise ValueError("Only standard 100-share option contracts are supported.")
        if date.fromisoformat(contract["expiration_date"]) < datetime.now(ZoneInfo("America/New_York")).date():
            raise ValueError("This contract has expired.")
        if action == "buy" and int(account.get("options_trading_level") or 0) < 2:
            raise ValueError("Buying calls/puts requires options trading level 2 on this paper account.")
    else:
        asset = alpaca.read_get(f"/v2/assets/{symbol}", paper=True)
        if asset.get("class") != "us_equity" or asset.get("status") != "active" or not asset.get("tradable"):
            raise ValueError("This stock/ETF is not tradable through Alpaca.")
    value = qty * limit * multiplier
    if side == "buy":
        power = account.get("options_buying_power") if asset_class == "us_option" else account.get("buying_power")
        if value > decimal(power, "buying power"):
            raise ValueError("The limit order value exceeds Alpaca buying power.")
    clock = alpaca.read_get("/v2/clock", paper=True)
    payload = {"symbol": symbol, "qty": str(int(qty)), "side": side, "type": "limit", "limit_price": format(limit, "f"), "time_in_force": "day", "extended_hours": False}
    if asset_class == "us_option":
        payload["position_intent"] = intent
    preview = {"symbol": symbol, "asset_class": asset_class, "action": action, "intent": intent, "quantity": str(int(qty)),
               "limit_price": format(limit, "f"), "multiplier": str(multiplier), "limit_value": str(value),
               "market_open": bool(clock.get("is_open")), "option_feed": alpaca.data_config()["option_feed"],
               "contract_name": contract.get("name", symbol) if contract else symbol, "mode": "paper"}
    return payload, preview


def prepare(ticket, actor, *, automation_key=None):
    require_manual()
    if (actor == BOT_ACTOR) != (automation_key is not None):
        raise ValueError("Bot orders require a persistent automation action.")
    order_id = uuid.uuid5(uuid.NAMESPACE_URL, "alpaca-paper-bot:" + str(automation_key)) if automation_key else uuid.uuid4()
    existing = AlpacaPaperOrder.objects.filter(pk=order_id).first()
    if existing:
        return {"order": serialize(existing), "confirmation_token": signing.dumps({"id": str(existing.pk), "actor": actor}, salt=SIGNING_SALT)}
    account = current_account()
    payload, preview = validate_ticket(ticket, account)
    client_id = ("auto-" if automation_key else "manual-") + order_id.hex
    record = AlpacaPaperOrder.objects.create(
        id=order_id,
        account_id=account["id"], actor=actor, symbol=preview["symbol"], asset_class=preview["asset_class"],
        intent=preview["intent"], client_order_id=client_id, payload={**payload, "client_order_id": client_id},
        preview=preview, expires_at=timezone.now() + timedelta(seconds=120),
    )
    token = signing.dumps({"id": str(record.pk), "actor": actor}, salt=SIGNING_SALT)
    return {"order": serialize(record), "confirmation_token": token}


def serialize(record):
    return {"id": str(record.pk), "symbol": record.symbol, "asset_class": record.asset_class, "intent": record.intent,
            "phase": record.phase, "status": record.broker_status or record.phase, "actor": record.actor,
            "quantity": record.payload["qty"], "limit_price": record.payload["limit_price"],
            "filled_qty": str(record.filled_qty), "filled_avg_price": str(record.filled_avg_price) if record.filled_avg_price is not None else None,
            "broker_order_id": record.broker_order_id, "client_order_id": record.client_order_id, "error": record.error,
            "submitted_at": record.submitted_at.isoformat() if record.submitted_at else None,
            "filled_at": record.filled_at.isoformat() if record.filled_at else None, "preview": record.preview,
            "last_checked_at": record.last_checked_at.isoformat() if record.last_checked_at else None,
            "created_at": record.created_at.isoformat(), "expires_at": record.expires_at.isoformat()}


def event(record, status, actor, **detail):
    AlpacaOrderEvent.objects.create(order=record, status=status, actor=actor, detail=detail)


def apply_snapshot(record, snapshot):
    if snapshot.get("client_order_id") != record.client_order_id or snapshot.get("symbol") != record.symbol:
        raise alpaca.AlpacaDataError("Broker order identity did not match this app order.")
    if snapshot.get("side") != record.payload["side"] or decimal(snapshot.get("qty")) != decimal(record.payload["qty"]):
        raise alpaca.AlpacaDataError("Broker order terms did not match this app order.")
    filled_qty = decimal(snapshot.get("filled_qty", "0"))
    if not 0 <= filled_qty <= decimal(record.payload["qty"]):
        raise alpaca.AlpacaDataError("Broker returned inconsistent fill quantity.")
    updated = parse_datetime(snapshot.get("updated_at") or "")
    status = str(snapshot.get("status") or "")[:32]
    if not status or not snapshot.get("id"):
        raise alpaca.AlpacaDataError("Broker response did not contain an order status and ID.")
    average = decimal(snapshot["filled_avg_price"]) if snapshot.get("filled_avg_price") is not None else None
    for _ in range(3):
        current = AlpacaPaperOrder.objects.get(pk=record.pk)
        if filled_qty < current.filled_qty or (updated and current.broker_updated_at and updated < current.broker_updated_at):
            return current
        if current.phase == "done" and status not in TERMINAL_STATUSES:
            return current
        phase = "done" if status in TERMINAL_STATUSES else ("cancel_pending" if current.phase == "cancel_pending" else "working")
        changed = (current.broker_status, current.filled_qty, current.filled_avg_price) != (status, filled_qty, average)
        # Compare-and-swap protects newer fills and cancellation state on SQLite too.
        with transaction.atomic():
            written = AlpacaPaperOrder.objects.filter(
                pk=record.pk, phase=current.phase, updated_at=current.updated_at,
                filled_qty=current.filled_qty, broker_updated_at=current.broker_updated_at,
            ).update(
                broker_order_id=str(snapshot["id"]), broker_status=status,
                phase=phase, filled_qty=filled_qty, filled_avg_price=average,
                broker_updated_at=updated or current.broker_updated_at, filled_at=parse_datetime(snapshot.get("filled_at") or ""),
                last_checked_at=timezone.now(), error="", updated_at=timezone.now(),
            )
            if written and changed:
                event(current, status, "alpaca", filled_qty=str(filled_qty), filled_avg_price=str(average) if average is not None else None)
        if written:
            break
    return AlpacaPaperOrder.objects.get(pk=record.pk)


def lookup(record):
    snapshot = optional_get("/v2/orders:by_client_order_id", {"client_order_id": record.client_order_id})
    if snapshot is not None:
        return apply_snapshot(record, snapshot)
    AlpacaPaperOrder.objects.filter(pk=record.pk).update(last_checked_at=timezone.now())
    return AlpacaPaperOrder.objects.get(pk=record.pk)


def submit(order_id, token, actor, *, before_send=None):
    require_manual()
    record = AlpacaPaperOrder.objects.get(pk=order_id)
    if record.actor == BOT_ACTOR and not callable(before_send):
        raise ValueError("Bot submission requires the active worker's safety checks.")
    if record.actor != actor:
        raise ValueError("Only the user who reviewed this ticket can confirm it.")
    account = current_account()
    if str(account["id"]) != record.account_id:
        raise ValueError("The connected account changed. Review a new paper order.")
    # A repeated confirmation only reads the same broker order, never resends it.
    if record.phase != "prepared":
        return lookup(record) if record.phase in ACTIVE_PHASES else record
    try:
        confirmed = signing.loads(token, salt=SIGNING_SALT, max_age=120)
    except signing.BadSignature:
        raise ValueError("Confirmation expired or invalid. Review a new order.") from None
    if confirmed != {"id": str(record.pk), "actor": actor} or timezone.now() > record.expires_at:
        raise ValueError("Confirmation expired or invalid. Review a new order.")
    ticket = {key: record.preview[key] for key in ("symbol", "asset_class", "action", "quantity", "limit_price")}
    payload, preview = validate_ticket(ticket, account, record)
    if {**payload, "client_order_id": record.client_order_id} != record.payload or preview["intent"] != record.intent:
        raise ValueError("The position changed. Review a new paper order.")
    if before_send:
        before_send()
    try:
        with transaction.atomic():
            claimed = AlpacaPaperOrder.objects.filter(pk=record.pk, phase="prepared").update(phase="submitting", submitted_at=timezone.now())
            if claimed:
                event(record, "submitting", actor, payload=record.payload)
    except IntegrityError:
        raise ValueError("Another order for this symbol is active. Refresh before trading again.") from None
    if not claimed:
        return AlpacaPaperOrder.objects.get(pk=record.pk)
    try:
        response = requests.post(
            "https://paper-api.alpaca.markets/v2/orders", json=record.payload,
            headers=alpaca.data_headers(), timeout=(5, 15), allow_redirects=False,
        )
        if response.status_code in {200, 201}:
            return apply_snapshot(record, response.json())
        if response.status_code in {400, 401, 403, 404, 422}:
            message = f"Alpaca rejected the paper order (HTTP {response.status_code}). Check price increments, permissions and available buying power."
            with transaction.atomic():
                changed = AlpacaPaperOrder.objects.filter(pk=record.pk, phase="submitting").update(phase="rejected", broker_status="rejected", error=message)
                if changed:
                    event(record, "rejected", actor, message=message)
        else:
            raise alpaca.AlpacaDataError("Order response was inconclusive.")
    except (requests.RequestException, ValueError, alpaca.AlpacaDataError):
        with transaction.atomic():
            changed = AlpacaPaperOrder.objects.filter(pk=record.pk, phase="submitting").update(phase="unknown", error="Submission not confirmed. Refresh status; do not submit a replacement order.")
            if changed:
                event(record, "unknown", actor)
    finally:
        alpaca.invalidate_account_cache()
    return AlpacaPaperOrder.objects.get(pk=record.pk)


def cancel(order_id, actor, *, before_send=None):
    require_manual()
    if actor == BOT_ACTOR and not callable(before_send):
        raise ValueError("Bot cancellation requires the active worker's safety checks.")
    record = AlpacaPaperOrder.objects.get(pk=order_id)
    if str(current_account()["id"]) != record.account_id:
        raise ValueError("This order belongs to a different paper account.")
    if record.phase not in ACTIVE_PHASES:
        return record
    record = lookup(record)
    if record.phase == "done" or record.phase == "cancel_pending":
        return record
    if not record.broker_order_id:
        raise ValueError("Alpaca has not confirmed an order ID. Refresh status before attempting cancellation.")
    broker_id = str(uuid.UUID(record.broker_order_id))
    if before_send:
        before_send()
    with transaction.atomic():
        claimed = AlpacaPaperOrder.objects.filter(pk=record.pk, phase__in=["submitting", "working", "unknown"]).update(phase="cancel_pending")
        if claimed:
            event(record, "cancel_requested", actor)
    if not claimed:
        return AlpacaPaperOrder.objects.get(pk=record.pk)
    message = "Cancellation requested; waiting for Alpaca confirmation."
    try:
        response = requests.delete(
            f"https://paper-api.alpaca.markets/v2/orders/{broker_id}", headers=alpaca.data_headers(),
            timeout=(5, 15), allow_redirects=False,
        )
        if response.status_code not in {200, 204}:
            message = f"Cancellation not confirmed (HTTP {response.status_code}). Refresh broker status."
            if 400 <= response.status_code < 500:
                AlpacaPaperOrder.objects.filter(pk=record.pk, phase="cancel_pending").update(phase="working")
    except requests.RequestException:
        message = "Cancellation response unavailable. Refresh broker status; the order may still fill."
    AlpacaPaperOrder.objects.filter(pk=record.pk, phase__in=ACTIVE_PHASES).update(error=message)
    alpaca.invalidate_account_cache()
    return AlpacaPaperOrder.objects.get(pk=record.pk)


def reconcile(account_id, snapshots):
    by_client = {row.get("client_order_id"): row for row in snapshots}
    pending = AlpacaPaperOrder.objects.filter(account_id=account_id, phase__in=ACTIVE_PHASES).order_by("last_checked_at")[:10]
    warning = ""
    for record in pending:
        try:
            snapshot = by_client.get(record.client_order_id)
            if snapshot is not None:
                apply_snapshot(record, snapshot)
            else:
                lookup(record)
        except (alpaca.AlpacaDataError, ValueError):
            warning = "Some app orders could not be synchronized. Their last confirmed status is retained."
    return warning


def history(account_id):
    return [serialize(record) for record in AlpacaPaperOrder.objects.filter(account_id=account_id).exclude(phase="prepared")[:100]]
