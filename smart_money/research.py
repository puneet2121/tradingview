"""On-demand daily research reads, isolated from trading and flow collection."""
from datetime import timedelta
import threading
import uuid

from django.db import transaction
from django.utils import timezone

from dashboard.data_source import fetch_yfinance_history
from . import fundamentals
from .models import FundamentalSnapshot, TechnicalResearchSnapshot
from .ratings import daily_snapshot, fundamental_rating, technical_rating, combined_rating, latest_session

_slots = threading.BoundedSemaphore(2)


def request_technicals(symbol, force=False):
    now = timezone.now()
    record, _ = TechnicalResearchSnapshot.objects.get_or_create(symbol=symbol,
        defaults={"expires_at": now, "retry_at": now, "lease_until": now})
    if record.retry_at > now or record.lease_until > now:
        return "Price refresh cooldown active." if force and record.payload else ""
    if not force and record.payload and record.expires_at > now and record.payload.get("session") == latest_session(now):
        return ""
    if not _slots.acquire(blocking=False):
        return "Two price-history requests are running. Retry shortly."
    token = uuid.uuid4().hex
    try:
        with transaction.atomic():
            if TechnicalResearchSnapshot.objects.filter(attempted_at__gte=now-timedelta(minutes=1)).count() >= 6:
                return "Price lookup rate limit reached. Retry in a minute."
            changed = TechnicalResearchSnapshot.objects.filter(pk=symbol, lease_until__lte=now, retry_at__lte=now).update(
                refresh_token=token, attempted_at=now, lease_until=now+timedelta(minutes=2),
                retry_at=now+timedelta(minutes=2), error="")
        if not changed:
            return ""
        # One bounded provider read per lookup; no background watchlist or broker writes.
        try:
            raw = fetch_yfinance_history(symbol, "1d", period="2y", prepost=False, limit=550, ignore_tz=False)
            data = daily_snapshot(raw, timezone.now())
            now = timezone.now()
            TechnicalResearchSnapshot.objects.filter(pk=symbol, refresh_token=token).update(
                payload=data, fetched_at=now, expires_at=now+timedelta(minutes=15),
                retry_at=now+timedelta(minutes=2), lease_until=now, refresh_token="", error="")
        except Exception:
            now = timezone.now()
            TechnicalResearchSnapshot.objects.filter(pk=symbol, refresh_token=token).update(
                expires_at=now, retry_at=now+timedelta(minutes=5), lease_until=now, refresh_token="",
                error="Daily Yahoo price history unavailable. Retry later; no live-price assumption has been made.")
        return ""
    finally:
        _slots.release()


def response_for(symbol):
    now = timezone.now()
    company = FundamentalSnapshot.objects.filter(pk=symbol).first()
    price = TechnicalResearchSnapshot.objects.filter(pk=symbol).first()
    empty = {"symbol": symbol, "data": None, "pending": False, "stale": False, "error": ""}
    result = fundamentals.response_for(company, now) if company else dict(empty)
    technical = fundamentals.response_for(price, now) if price else dict(empty)
    t = technical_rating(technical["data"], now, technical["stale"])
    ratings = {}
    for frequency in ("yearly", "quarterly"):
        f = fundamental_rating(result["data"], frequency, now, result["stale"])
        ratings[frequency] = {"technical": t, "fundamental": f, "combined": combined_rating(t, f)}
    result.update(technical=technical, ratings=ratings,
                  pending=result["pending"] or technical["pending"])
    return result
