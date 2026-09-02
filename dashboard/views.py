from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .data_source import (
    available_config,
    get_history,
    get_quote,
    get_symbol,
    get_timeframe,
    normalize_symbol,
    validate_symbol_has_data,
)


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
