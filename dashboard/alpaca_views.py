import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import alpaca
from . import alpaca_orders, alpaca_bot
from .data_source import alpaca_expirations, fetch_alpaca_option_chain, normalize_symbol
from .models import AlpacaPaperOrder


@never_cache
@require_GET
def connection(request):
    info = alpaca.connection_info()
    if not info["enabled"] or not info["configured"]:
        return JsonResponse({**info, "connected": False})
    try:
        payload = alpaca.read_account()
        account_id = payload.pop("account_id", None)
        payload["sync_warning"] = alpaca_orders.reconcile(account_id, payload["orders"]) if account_id else "Paper account identifier unavailable."
        payload["app_orders"] = alpaca_orders.history(account_id) if account_id else []
        return JsonResponse(payload)
    except alpaca.AlpacaDataError as exc:
        return JsonResponse({**info, "connected": False, "error": str(exc)}, status=502)


@never_cache
@require_GET
def expirations(request):
    try:
        symbol = normalize_symbol(request.GET.get("symbol", ""))
        return JsonResponse({"symbol": symbol, "expirations": alpaca_expirations(symbol), "source": "Alpaca"})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except alpaca.AlpacaDataError as exc:
        return JsonResponse({"error": str(exc)}, status=502)


@never_cache
@require_GET
def chain(request):
    try:
        return JsonResponse(fetch_alpaca_option_chain(normalize_symbol(request.GET.get("symbol", "")), request.GET.get("expiration")))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except alpaca.AlpacaDataError as exc:
        return JsonResponse({"error": str(exc)}, status=502)


def manual_actor(request):
    if getattr(settings, "SHARING_ENABLED", False):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or not user.is_active or user.get_username() not in settings.SHARING_USERS:
            raise PermissionError("Sign in to the shared dashboard to trade.")
        return user.get_username()
    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        raise PermissionError("Use localhost or the signed-in sharing dashboard for Alpaca orders.")
    return "local"


def body(request):
    if len(request.body) > 4096:
        raise ValueError("Order request is too large.")
    try:
        result = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid JSON order request.") from None
    if not isinstance(result, dict):
        raise ValueError("Order request must be an object.")
    return result


def order_response(callback):
    try:
        return JsonResponse(callback())
    except PermissionError as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    except AlpacaPaperOrder.DoesNotExist:
        return JsonResponse({"error": "Paper order not found."}, status=404)
    except (ValueError, TypeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except alpaca.AlpacaDataError as exc:
        return JsonResponse({"error": str(exc)}, status=502)


@never_cache
@csrf_protect
@require_POST
def prepare_order(request):
    def execute():
        actor = manual_actor(request)
        return alpaca_orders.prepare(body(request), actor)
    return order_response(execute)


@never_cache
@csrf_protect
@require_POST
def submit_order(request, order_id):
    def execute():
        actor, data = manual_actor(request), body(request)
        if set(data) != {"confirmation_token"} or not isinstance(data["confirmation_token"], str):
            raise ValueError("Confirm the reviewed ticket without changing its terms.")
        return {"order": alpaca_orders.serialize(alpaca_orders.submit(order_id, data["confirmation_token"], actor))}
    return order_response(execute)


@never_cache
@csrf_protect
@require_POST
def cancel_order(request, order_id):
    def execute():
        actor, data = manual_actor(request), body(request)
        if data != {"confirmed": True}:
            raise ValueError("Confirm cancellation of this paper order.")
        return {"order": alpaca_orders.serialize(alpaca_orders.cancel(order_id, actor))}
    return order_response(execute)


@never_cache
@require_GET
def order_status(request, order_id):
    def execute():
        manual_actor(request)
        record = AlpacaPaperOrder.objects.get(pk=order_id)
        if str(alpaca_orders.current_account()["id"]) != record.account_id:
            raise PermissionError("This order belongs to another paper account.")
        if record.phase in alpaca_orders.ACTIVE_PHASES:
            record = alpaca_orders.lookup(record)
        return {"order": alpaca_orders.serialize(record)}
    return order_response(execute)


@never_cache
@require_GET
def bot_state(request):
    def execute():
        manual_actor(request)
        return alpaca_bot.state()
    return order_response(execute)


@never_cache
@csrf_protect
@require_POST
def bot_control(request):
    def execute():
        actor, data = manual_actor(request), body(request)
        if set(data) != {"action", "min_dte", "confirmed"} or data["confirmed"] is not True:
            raise ValueError("Confirm the paper automation settings before starting or pausing.")
        return alpaca_bot.control(data["action"], actor, data["min_dte"])
    return order_response(execute)
