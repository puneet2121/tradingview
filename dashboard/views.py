import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods

from .data_source import (
    available_config,
    get_history,
    get_quote,
    get_symbol,
    get_timeframe,
    normalize_symbol,
    validate_symbol_has_data,
)
from .discord_alerts import send_test_alert
from .models import OptionPaperTrade
from .paper_trading import (
    account_state,
    execute_signal,
    mark_symbol,
    replace_watchlist,
    reset_paper_trades,
    scan_enabled_watchlist,
)
from .options_paper import (
    close_option_trade,
    fetch_option_chain,
    get_expirations,
    open_option_trade,
    option_state,
    reset_option_trades,
)
from .strategy_engine import save_strategy, strategy_state
from .trade_activity import activity_page


@require_GET
def trade_activity(request):
    try:
        before = int(request.GET["before"]) if "before" in request.GET else None
        if before is not None and before < 1:
            raise ValueError
        return JsonResponse(activity_page(before))
    except ValueError:
        return JsonResponse({"error": "Invalid activity cursor."}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def discord_test(request):
    try:
        send_test_alert()
        return JsonResponse({"ok": True})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"Discord alert failed: {exc}"}, status=502)


@require_GET
def index(request):
    return render(request, "dashboard/index.html")


@require_GET
def config(request):
    return JsonResponse(available_config())


@require_GET
def history(request):
    symbol = request.GET.get("symbol", "")
    timeframe = request.GET.get("timeframe", "1m")
    try:
        get_symbol(symbol)
        get_timeframe(timeframe)
        return JsonResponse({"symbol": symbol, "timeframe": timeframe, "bars": get_history(symbol, timeframe)})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"Upstream data request failed: {exc}"}, status=502)


@require_GET
def quote(request):
    symbol = request.GET.get("symbol", "")
    try:
        get_symbol(symbol)
        return JsonResponse(get_quote(symbol))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"Upstream quote request failed: {exc}"}, status=502)


@require_GET
def symbol(request):
    raw_symbol = request.GET.get("symbol", "")
    market = request.GET.get("market", "us")
    try:
        normalized = normalize_symbol(raw_symbol, market)
        symbol_config = get_symbol(normalized)
        if symbol_config.provider == "yfinance":
            validate_symbol_has_data(symbol_config.value)
        return JsonResponse(
            {
                "value": symbol_config.value,
                "label": symbol_config.label,
                "provider": symbol_config.provider,
                "exchange": symbol_config.exchange,
                "group": symbol_config.group,
            }
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_GET
def paper_state(request):
    return JsonResponse(account_state())


def json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body.") from exc


@csrf_exempt
@require_http_methods(["POST"])
def paper_signal(request):
    try:
        trade = execute_signal(json_body(request))
        return JsonResponse({"trade": None if trade is None else trade.id, "state": account_state()})
    except (KeyError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def paper_mark(request):
    try:
        payload = json_body(request)
        changed = mark_symbol(str(payload["symbol"]).upper(), str(payload["timeframe"]), payload["bar"])
        return JsonResponse({"changed": [trade.id for trade in changed], "state": account_state()})
    except (KeyError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def paper_reset(request):
    reset_paper_trades()
    return JsonResponse(account_state())


@csrf_exempt
@require_http_methods(["GET", "POST"])
def paper_watchlist(request):
    if request.method == "GET":
        return JsonResponse(account_state())

    try:
        payload = json_body(request)
        symbols = payload.get("symbols", [])
        if isinstance(symbols, str):
            symbols = [item.strip() for item in symbols.split(",")]
        timeframes = payload.get("timeframes", payload.get("timeframe", ["3m", "5m"]))
        replace_watchlist(symbols, timeframes)
        return JsonResponse(account_state())
    except (KeyError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def paper_scan(request):
    scan_enabled_watchlist()
    return JsonResponse(account_state())


@csrf_exempt
@require_http_methods(["GET", "POST"])
def strategies(request):
    if request.method == "GET":
        return JsonResponse(strategy_state())

    try:
        strategy = save_strategy(json_body(request))
        return JsonResponse({"strategy": strategy.id, "state": strategy_state()})
    except (KeyError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_GET
def option_expirations(request):
    try:
        return JsonResponse({"symbol": request.GET.get("symbol", "").upper(), "expirations": get_expirations(request.GET.get("symbol", ""))})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"Yahoo option request failed: {exc}"}, status=502)


@require_GET
def option_chain(request):
    try:
        return JsonResponse(fetch_option_chain(request.GET.get("symbol", ""), request.GET.get("expiration") or None))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"Yahoo option chain request failed: {exc}"}, status=502)


@require_GET
def option_paper_state(request):
    return JsonResponse(option_state())


@csrf_exempt
@require_http_methods(["POST"])
def option_paper_trade(request):
    try:
        trade = open_option_trade(json_body(request))
        return JsonResponse({"trade": trade.id, "state": option_state()})
    except (KeyError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"Option trade failed: {exc}"}, status=502)


@csrf_exempt
@require_http_methods(["POST"])
def option_paper_close(request):
    try:
        trade = close_option_trade(int(json_body(request)["trade_id"]), "MANUAL_CLOSE", actor="USER")
        return JsonResponse({"trade": trade.id, "state": option_state()})
    except OptionPaperTrade.DoesNotExist:
        return JsonResponse({"error": "Option paper trade not found."}, status=404)
    except (KeyError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"Option close failed: {exc}"}, status=502)


@csrf_exempt
@require_http_methods(["POST"])
def option_paper_reset(request):
    reset_option_trades()
    return JsonResponse(option_state())
