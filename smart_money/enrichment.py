from datetime import datetime, timedelta, timezone as utc

from django.utils import timezone

from dashboard import alpaca
from .analytics import horizons, regular_bars, technical_snapshot
from .models import OptionsTransaction, TransactionPriceSnapshot


def enrich_one(adapter, event, guard):
    now = timezone.now()
    start = event.trade_at - timedelta(days=10)
    end = min(now, event.trade_at + timedelta(days=36))
    bars, token = adapter.stock_bars(event.ticker, start, end)
    if token:
        # An incomplete path must never produce misleading MFE/MAE or indicator values.
        event.technical_status = "Historical window exceeds bounded enrichment; unavailable"
        event.technical_at = now
        event.save(update_fields=["technical_status", "technical_at"])
        return
    guard()
    technical = event.technical or technical_snapshot(bars, event.trade_at, event.direction)
    if not technical:
        event.technical_status = "No eligible underlying bar at the transaction time"
        event.technical_at = now
        event.save(update_fields=["technical_status", "technical_at"])
        return
    if not event.technical:
        event.technical, event.technical_at = technical, now
        event.technical_status = "Historical reconstruction; 5m RTH; " + alpaca.data_config()["stock_feed"].upper()
        event.save(update_fields=["technical", "technical_at", "technical_status"])
    for name, target in horizons(event.trade_at).items():
        snapshot, _ = TransactionPriceSnapshot.objects.get_or_create(transaction=event, horizon=name, defaults={"target_at": target})
        if snapshot.observed_at or target > now:
            continue
        eligible = regular_bars(bars, target)
        eligible = [row for row in eligible if row["time"] >= event.trade_at.timestamp()]
        baseline = technical["price"]
        if not eligible or target.timestamp() - (eligible[-1]["time"] + 300) > 300:
            snapshot.status = "No eligible bar near target / outside RTH"
            snapshot.save(update_fields=["status"])
            continue
        price = eligible[-1]["close"]
        returns = (price / baseline - 1) * 100
        up = max((row["high"] / baseline - 1) * 100 for row in eligible)
        down = min((row["low"] / baseline - 1) * 100 for row in eligible)
        snapshot.price, snapshot.return_percent = price, returns
        if event.direction == "BULLISH":
            snapshot.favorable_percent, snapshot.adverse_percent = max(0, up), min(0, down)
        elif event.direction == "BEARISH":
            snapshot.favorable_percent, snapshot.adverse_percent = max(0, -down), min(0, -up)
        snapshot.observed_at = datetime.fromtimestamp(eligible[-1]["time"] + 300, utc.utc)
        snapshot.status = "5m bar approximation; baseline is last completed pre-trade close; not option P/L"
        snapshot.source = "Alpaca " + alpaca.data_config()["stock_feed"].upper()
        snapshot.save()


def enrich_pending(adapter, guard):
    # Fresh events first. Mature observations use the remaining bounded request budget.
    fresh = list(OptionsTransaction.objects.filter(technical_at__isnull=True, excluded=False).order_by("trade_at")[:1])
    due_ids = list(TransactionPriceSnapshot.objects.filter(observed_at__isnull=True, target_at__lte=timezone.now(),
        status="Pending", transaction__trade_at__gte=timezone.now()-timedelta(days=40)).order_by("target_at").values_list("transaction_id", flat=True)[:1])
    events = {event.pk: event for event in fresh}
    for event in OptionsTransaction.objects.filter(pk__in=due_ids):
        events[event.pk] = event
    for event in events.values():
        enrich_one(adapter, event, guard)
