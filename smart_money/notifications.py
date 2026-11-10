from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from dashboard.discord_alerts import DiscordDeliveryError, post_discord_message, format_time
from dashboard.market_alerts import EASTERN, discord_ready, session_bounds
from .config import options, permission
from .models import AlertConfiguration, UnusualActivityAlert, WorkerState


def in_session(now):
    bounds = session_bounds(now.astimezone(EASTERN).date())
    return bool(bounds and bounds[0] <= now.timestamp() < bounds[1])


def matches(event, rule):
    f = rule.filters
    if event.excluded or event.premium is None or event.trade_at < rule.armed_at:
        return False
    if f["symbols"] and event.ticker not in f["symbols"]:
        return False
    if event.premium < f["min_premium"] or event.quantity < f["min_quantity"] or event.score < f["min_score"]:
        return False
    if event.evidence.get("repeat_count", 1) < f["min_repeats"]:
        return False
    for field in ("option_type", "classification", "structure"):
        if f.get(field) and getattr(event, field) != f[field]:
            return False
    if f.get("confirmation") and not (event.direction == f["confirmation"] == event.technical.get("trend")):
        return False
    if f.get("expiry_from") and event.contract.expiration.isoformat() < f["expiry_from"]:
        return False
    if f.get("expiry_to") and event.contract.expiration.isoformat() > f["expiry_to"]:
        return False
    if f.get("strike") and event.contract.strike != Decimal(f["strike"]):
        return False
    if f.get("min_vol_oi") and (not event.open_interest or event.volume is None or event.volume / event.open_interest < float(f["min_vol_oi"])):
        return False
    return True


def queue_events(events):
    rules = list(AlertConfiguration.objects.filter(enabled=True).select_related("owner"))
    for event in events:
        for rule in rules:
            if matches(event, rule):
                contract = event.contract
                text = (f"**OPTIONS ACTIVITY | {event.ticker} {event.option_type}**\n"
                        f"Rule: {rule.name}\nObserved premium: ${event.premium:,.2f}\n"
                        f"{event.quantity:,} contracts x ${event.price:g} x {event.multiplier:g}\n"
                        f"Strike ${contract.strike:g} | Expiry {contract.expiration}\n"
                        f"Execution: {event.classification} | Structure: {event.structure}\n"
                        f"Score {event.score}/100 | Observed repeats: {event.evidence.get('repeat_count', 1)}\n"
                        f"{event.evidence.get('execution', '')}\n"
                        f"Trade: {format_time(event.trade_at)} | Observed: {format_time(event.received_at)}\n"
                        f"Alpaca OPRA / {'delayed' if event.delayed else 'polled'} / watchlist sample\n"
                        "Activity report only. Buyer identity and opening/closing intent are unknown. No trade placed.")
                UnusualActivityAlert.objects.get_or_create(configuration=rule, transaction=event,
                    defaults={"revision": rule.revision, "message": text[:1900]})


def deliver(guard):
    now = timezone.now()
    if not permission("allow_discord") or not permission("allow_sharing") or not discord_ready() or not in_session(now):
        return
    worker = WorkerState.objects.get(key="flow")
    if worker.discord_retry_at > now:
        return
    UnusualActivityAlert.objects.filter(status="sending", retry_at__lt=now-timedelta(minutes=5)).update(status="failed", error="Delivery uncertain after restart; not resent")
    notices = UnusualActivityAlert.objects.filter(status="pending", retry_at__lte=now).select_related("configuration__owner", "transaction").order_by("id")[:10]
    for notice in notices:
        guard()
        if not in_session(timezone.now()):
            break
        rule, event = notice.configuration, notice.transaction
        max_age = int(options().get("delay_seconds", 900)) + 300
        if not rule.enabled or rule.revision != notice.revision or (rule.owner_id and (not rule.owner.is_active or rule.owner.username not in getattr(settings, "SHARING_USERS", ()))):
            notice.status, notice.error = "cancelled", "Rule changed, paused, or owner no longer allowed"
            notice.save(update_fields=["status", "error"])
            continue
        if (now-event.trade_at).total_seconds() > max_age or (now-event.received_at).total_seconds() > 300:
            notice.status, notice.error = "expired", "Old activity retained in history only"
            notice.save(update_fields=["status", "error"])
            continue
        if not UnusualActivityAlert.objects.filter(pk=notice.pk, status="pending").update(status="sending", attempts=notice.attempts+1, retry_at=now):
            continue
        try:
            post_discord_message(notice.message)
        except DiscordDeliveryError as exc:
            if exc.retry_after and notice.attempts < 4:
                retry = now + timedelta(seconds=exc.retry_after)
                WorkerState.objects.filter(key="flow").update(discord_retry_at=retry)
                UnusualActivityAlert.objects.filter(pk=notice.pk).update(status="pending", retry_at=retry, error="Discord rate limited")
                break
            UnusualActivityAlert.objects.filter(pk=notice.pk).update(status="failed", error="Discord rejected delivery")
        except Exception:
            UnusualActivityAlert.objects.filter(pk=notice.pk).update(status="failed", error="Delivery failed or uncertain; not resent")
        else:
            UnusualActivityAlert.objects.filter(pk=notice.pk).update(status="sent", sent_at=timezone.now(), error="")
