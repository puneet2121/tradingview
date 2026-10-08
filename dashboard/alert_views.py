import json

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_http_methods

from .market_alerts import TIMEFRAMES, active_window, discord_ready, rule_label, validate_rule
from .models import MarketAlert, MarketAlertEvent, MarketAlertWorker


def owner(request):
    user = getattr(request, "user", None)
    return user if user and user.is_authenticated else None


def body(request):
    try:
        result = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid JSON.")
    if not isinstance(result, dict):
        raise ValueError("Expected an object.")
    return result


def serialize(rule):
    states = list(rule.scan_states.all())
    now = timezone.now()
    return {
        "id": rule.pk, "name": rule.name, "enabled": rule.enabled, "symbols": rule.symbols,
        "timeframes": rule.timeframes, "condition": rule.condition, "label": rule_label(rule.condition),
        "cooldown_minutes": rule.cooldown_minutes, "revision": rule.revision, "armed_at": rule.armed_at.isoformat(),
        "states": [{"symbol": symbol, "timeframe": timeframe, **next(
            ({"status": state.status, "checked_at": state.checked_at.isoformat() if state.checked_at else None}
             for state in states if state.symbol == symbol and state.timeframe == timeframe),
            {"status": "Waiting for scanner" if active_window(symbol, timeframe, now) else "Waiting for market session / daily close", "checked_at": None},
        )} for symbol in rule.symbols for timeframe in rule.timeframes],
    }


@require_http_methods(["GET", "POST", "PUT", "PATCH", "DELETE"])
@csrf_protect
def alerts(request, alert_id=None):
    user = owner(request)
    if request.method == "GET":
        if alert_id:
            rule = MarketAlert.objects.filter(owner=user, pk=alert_id).prefetch_related("scan_states").first()
            return JsonResponse({"alert": serialize(rule)} if rule else {"error": "Alert not found."}, status=200 if rule else 404)
        worker = MarketAlertWorker.objects.filter(key="alerts").first()
        heartbeat = worker.heartbeat_at if worker else None
        return JsonResponse({
            "alerts": [serialize(rule) for rule in MarketAlert.objects.filter(owner=user).prefetch_related("scan_states")],
            "discord_ready": discord_ready(), "destination": "Shared Discord channel", "timeframes": list(TIMEFRAMES),
            "scanner_online": bool(heartbeat and (timezone.now() - heartbeat).total_seconds() < 180),
            "scanner_checked_at": heartbeat.isoformat() if heartbeat else None,
        })
    try:
        payload = body(request)
        with transaction.atomic():
            rule = None
            if alert_id is not None:
                rule = MarketAlert.objects.select_for_update().filter(owner=user, pk=alert_id).first()
                if rule is None:
                    return JsonResponse({"error": "Alert not found."}, status=404)
                if payload.get("revision") != rule.revision:
                    return JsonResponse({"error": "Alert changed in another tab. Reload it before editing."}, status=409)
            elif request.method != "POST":
                raise ValueError("Select an alert first.")
            if request.method == "POST" and rule:
                raise ValueError("Use PUT to edit an alert.")
            if request.method == "DELETE":
                MarketAlertEvent.objects.filter(alert=rule, status="pending").update(status="cancelled", error="Alert deleted.")
                rule.delete()
                return JsonResponse({"deleted": alert_id})
            if request.method == "PATCH":
                if set(payload) != {"enabled", "revision"} or not isinstance(payload["enabled"], bool):
                    raise ValueError("Only enabled and revision can be patched.")
                changes = {"enabled": payload["enabled"]}
            else:
                changes = validate_rule(payload)
            candidates = list(MarketAlert.objects.filter(owner=user).exclude(pk=alert_id))
            if not rule and len(candidates) >= 20:
                raise ValueError("Limit: 20 saved alerts per user.")
            if rule is None:
                rule = MarketAlert(owner=user)
            for key, value in changes.items():
                setattr(rule, key, value)
            combinations = {(s, t) for item in [*candidates, rule] if item.enabled for s in item.symbols for t in item.timeframes}
            if len(combinations) > 50:
                raise ValueError("Limit: 50 active symbol/timeframe combinations per user. Pause an alert first.")
            if rule.pk:
                rule.revision += 1
                rule.scan_states.all().delete()
                MarketAlertEvent.objects.filter(alert=rule, status="pending").update(status="cancelled", error="Alert changed or paused.")
            rule.armed_at = timezone.now()
            rule.save()
        return JsonResponse({"alert": serialize(rule)}, status=201 if request.method == "POST" else 200)
    except (ValueError, KeyError, TypeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_GET
def events(request):
    queryset = MarketAlertEvent.objects.filter(owner=owner(request))
    try:
        if "before" in request.GET:
            before = int(request.GET["before"])
            if before < 1:
                raise ValueError
            queryset = queryset.filter(id__lt=before)
    except ValueError:
        return JsonResponse({"error": "Invalid history cursor."}, status=400)
    rows = list(queryset[:51])
    return JsonResponse({"events": [{
        "id": event.pk, "name": event.name, "symbol": event.symbol, "timeframe": event.timeframe,
        "status": event.status, "error": event.error, "snapshot": event.snapshot,
        "reason": rule_label(event.snapshot["condition"]) if event.snapshot.get("condition") else "",
        "candle_close": event.bar_closed_at.isoformat(), "detected_at": event.created_at.isoformat(),
        "sent_at": event.sent_at.isoformat() if event.sent_at else None,
    } for event in rows[:50]], "next": rows[49].pk if len(rows) > 50 else None})
