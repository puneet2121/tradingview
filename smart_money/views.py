"""Database-only UI endpoints. Provider requests belong to the background reader."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import wraps
import json

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Avg, Case, CharField, Count, F, Max, Q, Sum, Value, When
from django.forms.models import model_to_dict
from django.http import JsonResponse
from django.http.request import split_domain_port
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods

from dashboard.market_alerts import EASTERN, discord_ready
from . import validation
from .config import DEFAULT_SYMBOLS, PREMIUMS, can_view_data, options, permission
from .models import (AlertConfiguration, CollectionSettings, FlowCursor, InsiderTransaction,
    InstitutionalHolding, MarketDataProvider, OptionsTransaction, SecFiling, SmartMoneyWatchlist,
    TransactionPriceSnapshot, UnusualActivityAlert, WorkerState)
from .providers import provider_info, selected
from .disclosures import holding_changes


def access(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = getattr(request, "user", None)
        authenticated = user and user.is_authenticated and user.is_active and user.get_username() in getattr(settings, "SHARING_USERS", ())
        host, _ = split_domain_port(request.get_host())
        local = (not getattr(settings, "SHARING_ENABLED", False)
                 and request.META.get("REMOTE_ADDR") in ("127.0.0.1", "::1")
                 and host in ("localhost", "127.0.0.1", "[::1]"))
        if not (authenticated or local):
            return JsonResponse({"error": "Sign in through the private sharing server."}, status=403)
        try:
            response = view(request, *args, **kwargs)
        except (ValueError, TypeError, KeyError, signing.BadSignature):
            return JsonResponse({"error": "Invalid request. Check the filters, dates and required fields."}, status=400)
        response["Cache-Control"] = "private, no-store"
        return response
    return wrapped


def owner(request):
    user = getattr(request, "user", None)
    return user if user and user.is_authenticated else None


def body(request):
    if len(request.body) > 16384:
        raise ValueError("Request too large")
    data = json.loads(request.body)
    if not isinstance(data, dict):
        raise ValueError("Expected an object")
    return data


def fail(message, code=400):
    return JsonResponse({"error": message}, status=code)


def collection_dict():
    cfg = CollectionSettings.objects.filter(pk=1).first()
    if not cfg:
        return {"enabled": False, "symbols": DEFAULT_SYMBOLS, "min_premium": 50000, "revision": 1,
                "sec_enabled": False, "sec_ciks": [], "sec_status": "Not configured", "sec_checked_at": None}
    return {key: getattr(cfg, key) for key in ("enabled", "symbols", "min_premium", "revision", "sec_enabled", "sec_ciks", "sec_status", "sec_checked_at")}


@access
@require_GET
@ensure_csrf_cookie
def dashboard(request):
    return render(request, "smart_money/dashboard.html")


@access
@require_GET
def status(request):
    info = provider_info()
    db = MarketDataProvider.objects.filter(code=info["code"]).first()
    worker = WorkerState.objects.filter(key="flow").first()
    latest = OptionsTransaction.objects.filter(excluded=False).order_by("-received_at").first() if can_view_data() else None
    return JsonResponse({"provider": info, "collection": collection_dict(), "can_view": can_view_data(),
        "provider_status": db.status if db else "Not connected", "provider_checked_at": db.checked_at if db else None,
        "worker": {"heartbeat": worker.heartbeat if worker else None, "status": worker.status if worker else "Paused"},
        "latest_trade_at": latest.trade_at if latest else None, "latest_received_at": latest.received_at if latest else None,
        "cursors": list(FlowCursor.objects.filter(provider_id=info["code"]).values("symbol", "checked_at", "status")),
        "discord_ready": discord_ready() and permission("allow_discord") and permission("allow_sharing"),
        "sec_ready": "@" in options().get("sec_user_agent", ""), "premiums": PREMIUMS,
        "server_time": timezone.now()})


@access
@require_http_methods(["GET", "POST"])
@csrf_protect
def fundamentals(request):
    from . import fundamentals as service
    from .models import FundamentalSnapshot
    if not service.allowed():
        return fail("Yahoo fundamentals are enabled for local personal research. Shared access requires separate data-use permission.", 403)
    data = body(request) if request.method == "POST" else request.GET.dict()
    if set(data) - {"symbol", "refresh"} or "symbol" not in data:
        raise ValueError
    symbol = validation.symbols([data["symbol"]])[0]
    if request.method == "POST":
        result = service.request_snapshot(symbol, validation.boolean(data.get("refresh", False)))
    else:
        record = FundamentalSnapshot.objects.filter(pk=symbol).first()
        result = service.response_for(record) if record else {"symbol": symbol, "data": None, "pending": False, "stale": False, "error": ""}
    return JsonResponse(result, status=202 if result["pending"] else 200)


@access
@require_http_methods(["GET", "POST"])
@csrf_protect
def research_ratings(request):
    from . import fundamentals as service, research
    if not service.allowed():
        return fail("Yahoo research is local personal use. Shared access requires separate data-use permission.", 403)
    data = body(request) if request.method == "POST" else request.GET.dict()
    if set(data) - {"symbol", "refresh"} or "symbol" not in data:
        raise ValueError
    symbol = validation.symbols([data["symbol"]])[0]
    notices = []
    if request.method == "POST":
        force = validation.boolean(data.get("refresh", False))
        notices.append(service.request_snapshot(symbol, force).get("notice", ""))
        notices.append(research.request_technicals(symbol, force))
    result = research.response_for(symbol)
    if any(notices):
        result["notice"] = " ".join(n for n in notices if n)
    return JsonResponse(result, status=202 if result["pending"] else 200)


@access
@require_http_methods(["POST"])
@csrf_protect
def collection(request):
    data = validation.collection(body(request))
    if data["enabled"] and (not selected() or selected().readiness()):
        return fail(provider_info()["reason"], 409)
    if data["enabled"] and not can_view_data():
        return fail("Shared market-data permission has not been confirmed.", 403)
    if data["sec_enabled"] and (not data["sec_ciks"] or "@" not in options().get("sec_user_agent", "")):
        return fail("SEC collection requires CIKs and a server-side contact identity.")
    with transaction.atomic():
        cfg, _ = CollectionSettings.objects.get_or_create(pk=1)
        old_revision = data.pop("revision")
        changed = CollectionSettings.objects.filter(pk=1, revision=old_revision).update(
            **data, revision=F("revision")+1, armed_at=timezone.now(), updated_at=timezone.now(), sec_checked_at=None)
        if not changed:
            return fail("Settings changed in another session. Reload before saving.", 409)
    return JsonResponse(collection_dict())


@access
@require_http_methods(["GET", "POST"])
@csrf_protect
def preferences(request):
    key = f"user:{owner(request).pk}" if owner(request) else "local"
    record = SmartMoneyWatchlist.objects.filter(owner_key=key).first()
    if request.method == "POST":
        data = body(request)
        if set(data) != {"symbols", "filters"}:
            raise ValueError
        record, _ = SmartMoneyWatchlist.objects.update_or_create(owner_key=key, defaults={
            "symbols": validation.symbols(data["symbols"]), "filters": validation.filters(data["filters"])})
    return JsonResponse({"symbols": record.symbols if record else DEFAULT_SYMBOLS,
                         "filters": record.filters if record else {"min_premium": 50000}})


def filtered(request):
    allowed = {"filters", "from", "to", "sort", "cursor", "group"}
    if set(request.GET) - allowed:
        raise ValueError
    filters = validation.filters(json.loads(request.GET.get("filters", "{}")))
    today = timezone.now().astimezone(EASTERN).date()
    start, end = date.fromisoformat(request.GET.get("from", today.isoformat())), date.fromisoformat(request.GET.get("to", today.isoformat()))
    if not 0 <= (end-start).days <= 31:
        raise ValueError
    qs = OptionsTransaction.objects.filter(excluded=False, trade_at__gte=datetime.combine(start, time(), EASTERN),
        trade_at__lt=datetime.combine(end+timedelta(days=1), time(), EASTERN), premium__gte=filters["min_premium"],
        quantity__gte=filters["min_quantity"], score__gte=filters["min_score"])
    if filters["symbols"]:
        qs = qs.filter(ticker__in=filters["symbols"])
    for field in ("option_type", "classification", "structure"):
        if filters.get(field):
            qs = qs.filter(**{field: filters[field]})
    if filters["min_repeats"]:
        qs = qs.filter(evidence__repeat_count__gte=filters["min_repeats"])
    for key, lookup in {"strike": "contract__strike", "expiry_from": "contract__expiration__gte", "expiry_to": "contract__expiration__lte"}.items():
        if filters.get(key):
            qs = qs.filter(**{lookup: filters[key]})
    if filters.get("confirmation"):
        qs = qs.filter(direction=filters["confirmation"], technical__trend=filters["confirmation"])
    if filters.get("min_vol_oi"):
        qs = qs.filter(open_interest__gt=0, volume__gte=F("open_interest")*Decimal(filters["min_vol_oi"]))
    return qs


def event_dict(event):
    result = model_to_dict(event, exclude=["provider", "contract", "excluded"])
    result.update(id=event.pk, provider=event.provider_id, company=event.contract.company_name,
                  contract_symbol=event.contract.symbol, strike=event.contract.strike, expiration=event.contract.expiration,
                  volume_oi=float(event.volume/event.open_interest) if event.volume is not None and event.open_interest else None)
    return result


@access
@require_GET
def activity(request):
    if not can_view_data():
        return fail("Market-data sharing permission is not configured.", 403)
    qs = filtered(request)
    field = {"time": "trade_at", "premium": "premium", "score": "score"}.get(request.GET.get("sort", "time"))
    if not field:
        raise ValueError
    total = qs.count()
    if request.GET.get("cursor"):
        cursor = signing.loads(request.GET["cursor"], salt="sm-flow", max_age=86400)
        if cursor["sort"] != field:
            raise ValueError
        qs = qs.filter(Q(**{field+"__lt": cursor["value"]}) | Q(**{field: cursor["value"], "pk__lt": cursor["id"]}))
    rows = list(qs.select_related("contract").order_by("-"+field, "-pk")[:51])
    next_cursor = None
    if len(rows) > 50:
        last = rows[49]
        next_cursor = signing.dumps({"sort": field, "value": str(getattr(last, field)), "id": last.pk}, salt="sm-flow")
    return JsonResponse({"results": [event_dict(e) for e in rows[:50]], "next": next_cursor, "count": total})


@access
@require_GET
def detail(request, pk):
    if not can_view_data():
        return fail("Market-data sharing permission is not configured.", 403)
    event = get_object_or_404(OptionsTransaction.objects.select_related("contract"), pk=pk)
    result = event_dict(event)
    result["outcomes"] = list(event.outcomes.order_by("target_at").values("horizon", "target_at", "observed_at", "price", "return_percent", "favorable_percent", "adverse_percent", "status", "source"))
    return JsonResponse(result)


@access
@require_GET
def analytics(request):
    if not can_view_data():
        return fail("Market-data sharing permission is not configured.", 403)
    qs = filtered(request)
    aggregations = {"call": Sum("premium", filter=Q(option_type="CALL")), "put": Sum("premium", filter=Q(option_type="PUT")),
        "bullish": Sum("premium", filter=Q(direction="BULLISH")), "bearish": Sum("premium", filter=Q(direction="BEARISH")),
        "unknown": Sum("premium", filter=Q(direction="UNKNOWN")), "total": Sum("premium"), "count": Count("id"),
        "largest": Max("premium"), "repeats": Count("id", filter=Q(evidence__repeat_count__gte=2))}
    ranks = list(qs.order_by().values("ticker").annotate(**aggregations).order_by("-total")[:25])
    for row in ranks:
        row["net_directional"] = (row["bullish"] or 0)-(row["bearish"] or 0)
    heatmaps = {}
    for key, field in {"strike": "contract__strike", "expiry": "contract__expiration", "type": "option_type", "direction": "direction"}.items():
        heatmaps[key] = [{"label": str(row[field]), "total": row["total"], "count": row["count"]} for row in
                         qs.order_by().values(field).annotate(total=Sum("premium"), count=Count("id")).order_by("-total")[:25]]
    outcomes = TransactionPriceSnapshot.objects.filter(transaction__in=qs, observed_at__isnull=False)
    group = request.GET.get("group", "type")
    groups = {"type": F("transaction__option_type"), "structure": F("transaction__structure"),
        "premium": Case(When(transaction__premium__gte=5000000, then=Value("$5M+")),
            When(transaction__premium__gte=1000000, then=Value("$1M-$5M")), When(transaction__premium__gte=500000, then=Value("$500K-$1M")),
            When(transaction__premium__gte=100000, then=Value("$100K-$500K")), default=Value("Under $100K"), output_field=CharField())}
    if group not in groups:
        raise ValueError
    history = list(outcomes.annotate(category=groups[group]).order_by().values("horizon", "category").annotate(
        count=Count("id"), average_return=Avg("return_percent"), favorable=Avg("favorable_percent"), adverse=Avg("adverse_percent"),
        classified=Count("id", filter=~Q(transaction__direction="UNKNOWN")),
        correct=Count("id", filter=Q(transaction__direction="BULLISH", return_percent__gt=0)|Q(transaction__direction="BEARISH", return_percent__lt=0))))
    return JsonResponse({"summary": qs.aggregate(**aggregations), "rankings": ranks, "heatmaps": heatmaps, "history": history})


@access
@require_GET
def filings(request):
    kind = request.GET.get("kind", "holdings")
    page = validation.integer(request.GET.get("page", "1"), 1, 10000)
    records = SecFiling.objects.order_by("-filed_at")
    filing_id = request.GET.get("filing")
    selected_filing = get_object_or_404(records, pk=validation.integer(filing_id, 1, 10**12)) if filing_id else None
    qs = InsiderTransaction.objects.select_related("filing") if kind == "insiders" else InstitutionalHolding.objects.select_related("filing")
    if kind not in ("insiders", "holdings"):
        raise ValueError
    if selected_filing:
        qs = qs.filter(filing=selected_filing)
    rows = []
    for row in qs.order_by("-filing__filed_at", "pk")[(page-1)*50:page*50]:
        result = model_to_dict(row)
        result.update(filer=row.filing.filer, form=row.filing.form, filed_at=row.filing.filed_at, report_date=row.filing.report_date, url=row.filing.url)
        rows.append(result)
    comparison = holding_changes(selected_filing) if kind == "holdings" else None
    if comparison:
        comparison["rows"] = comparison["rows"][(page-1)*50:page*50]
    return JsonResponse({"results": rows, "count": qs.count(), "page": page, "changes": comparison,
                         "filings": list(records.filter(form__startswith="4" if kind == "insiders" else "13F").values("id", "filer", "form", "report_date", "filed_at", "url")[:100])})


def validate_alert(data):
    if set(data) - {"name", "enabled", "filters", "revision"}:
        raise ValueError
    name = data["name"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
        raise ValueError
    filters = validation.filters(data["filters"])
    # Reject unsupported conditions; never silently turn them into broader alerts.
    if filters["structure"] not in ("", "UNKNOWN") or filters["classification"] not in ("", "UNKNOWN") or filters.get("min_vol_oi") or filters.get("confirmation"):
        raise ValueError("The selected feed cannot establish these conditions")
    enabled = validation.boolean(data["enabled"])
    if enabled and not (can_view_data() and permission("allow_discord") and permission("allow_sharing") and discord_ready()):
        raise ValueError("Discord or redistribution permission unavailable")
    return {"name": name.strip(), "enabled": enabled, "filters": filters}


@access
@require_http_methods(["GET", "POST"])
@csrf_protect
def alerts(request):
    if request.method == "POST":
        data = validate_alert(body(request))
        if AlertConfiguration.objects.filter(owner=owner(request)).count() >= 30:
            return fail("The limit is 30 alert rules per user.")
        AlertConfiguration.objects.create(owner=owner(request), **data)
    rules = list(AlertConfiguration.objects.filter(owner=owner(request)).order_by("pk").values("id", "name", "enabled", "filters", "revision"))
    history = list(UnusualActivityAlert.objects.filter(configuration__owner=owner(request)).order_by("-created_at").values("configuration__name", "status", "created_at", "sent_at", "error")[:50])
    return JsonResponse({"rules": rules, "deliveries": history})


@access
@require_http_methods(["POST", "DELETE"])
@csrf_protect
def alert_item(request, pk):
    record = get_object_or_404(AlertConfiguration, pk=pk, owner=owner(request))
    data = body(request)
    revision = validation.integer(data.pop("revision"), 1, 10**9)
    if revision != record.revision:
        return fail("Rule changed in another session. Reload before saving.", 409)
    with transaction.atomic():
        query = AlertConfiguration.objects.filter(pk=record.pk, revision=revision)
        if request.method == "DELETE":
            query.delete()
        else:
            if not query.update(**validate_alert(data), revision=F("revision")+1, armed_at=timezone.now()):
                return fail("Rule changed in another session.", 409)
    return JsonResponse({"ok": True})
