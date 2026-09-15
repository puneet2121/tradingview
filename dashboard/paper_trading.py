from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import math
import time
from typing import Any
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .data_source import get_history, get_timeframe, normalize_symbol
from . import alpaca
from .models import OptionPaperTrade, PaperTrade, PaperWatchSymbol, Strategy, StrategyScanState
from .strategy_engine import ensure_default_strategies, parse_strategy_text
from .trade_activity import record_reset, record_trade_activity


STARTING_BALANCE = Decimal("100000.00")
RISK_PERCENT = Decimal("0.02")
DEFAULT_WATCHLIST = [
    "MU",
    "SOFI",
    "AAPL",
    "MSFT",
    "HOOD",
    "NOW",
    "QQQ",
    "SPY",
    "BTC",
    "ETH",
    "AVGO",
    "DRAM",
    "SNDK",
    "WMT",
    "SNOW",
    "MRVL",
    "IREN",
]
DEFAULT_WATCHLIST_TIMEFRAMES = ["3m", "5m"]
PACIFIC_ZONE = ZoneInfo("America/Los_Angeles")
LIVE_SIGNAL_MAX_AGE_SECONDS = 30 * 60
DEFAULT_IV = 0.30
OPTION_DAYS_TO_EXPIRY = 30 / 365
RISK_FREE_RATE = 0.04


def as_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Invalid decimal value: {value}") from exc


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def price(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"))


def quantity(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)


def metric(value: float | Decimal | None) -> Decimal | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return Decimal(str(value)).quantize(Decimal("0.000001"))


def closed_pnl() -> Decimal:
    total = PaperTrade.objects.filter(status=PaperTrade.CLOSED).aggregate(total=Sum("pnl"))["total"]
    return Decimal(total or "0")


def account_equity() -> Decimal:
    return STARTING_BALANCE + closed_pnl()


def open_notional() -> Decimal:
    return sum(
        (trade.entry_price * trade.quantity for trade in PaperTrade.objects.filter(status=PaperTrade.OPEN)),
        Decimal("0"),
    )


def serialize_trade(trade: PaperTrade) -> dict[str, Any]:
    estimate_type = "CALL" if trade.side == PaperTrade.LONG else "PUT"
    return {
        "id": trade.id,
        "symbol": trade.symbol,
        "timeframe": trade.timeframe,
        "strategy": trade.strategy,
        "side": trade.side,
        "status": trade.status,
        "signal_time": trade.signal_time,
        "entry_time": trade.entry_time,
        "entry_price": float(trade.entry_price),
        "quantity": float(trade.quantity),
        "risk_amount": float(trade.risk_amount),
        "risk_per_share": float(trade.risk_per_share),
        "stop_price": float(trade.stop_price),
        "best_price": float(trade.best_price),
        "contract_price": float(trade.contract_price) if trade.contract_price is not None else None,
        "option_estimate_type": estimate_type,
        "option_estimate_strike": float(trade.entry_price),
        "option_estimate_days": 30,
        "iv": float(trade.iv) if trade.iv is not None else None,
        "delta": float(trade.delta) if trade.delta is not None else None,
        "gamma": float(trade.gamma) if trade.gamma is not None else None,
        "theta": float(trade.theta) if trade.theta is not None else None,
        "vega": float(trade.vega) if trade.vega is not None else None,
        "rho": float(trade.rho) if trade.rho is not None else None,
        "execution_provider": trade.execution_provider,
        "broker_order_id": trade.broker_order_id,
        "broker_status": trade.broker_status,
        "exit_time": trade.exit_time,
        "exit_price": float(trade.exit_price) if trade.exit_price is not None else None,
        "exit_reason": trade.exit_reason,
        "pnl": float(trade.pnl),
    }


def account_state() -> dict[str, Any]:
    ensure_default_watchlist()
    ensure_default_strategies()
    trades = list(PaperTrade.objects.all()[:200])
    open_risk = sum((trade.risk_per_share * trade.quantity for trade in trades if trade.status == PaperTrade.OPEN), Decimal("0"))
    realized = closed_pnl()
    equity = STARTING_BALANCE + realized
    notional = open_notional()
    return {
        "starting_balance": float(STARTING_BALANCE),
        "risk_percent": float(RISK_PERCENT),
        "risk_per_trade": float(money(equity * RISK_PERCENT)),
        "realized_pnl": float(money(realized)),
        "equity": float(money(equity)),
        "open_risk": float(money(open_risk)),
        "open_notional": float(money(notional)),
        "buying_power": float(money(max(Decimal("0"), equity - notional))),
        "trades": [serialize_trade(trade) for trade in trades],
        "watchlist": [serialize_watch_symbol(item) for item in PaperWatchSymbol.objects.all()],
    }


def serialize_watch_symbol(item: PaperWatchSymbol) -> dict[str, Any]:
    return {
        "id": item.id,
        "symbol": item.symbol,
        "timeframe": item.timeframe,
        "enabled": item.enabled,
        "last_signal_time": item.last_signal_time,
        "last_checked_at": item.last_checked_at.isoformat() if item.last_checked_at else None,
        "last_error": item.last_error,
    }


def ensure_default_watchlist() -> None:
    if PaperWatchSymbol.objects.exists():
        return
    for symbol in DEFAULT_WATCHLIST:
        for timeframe in DEFAULT_WATCHLIST_TIMEFRAMES:
            PaperWatchSymbol.objects.get_or_create(symbol=symbol, timeframe=timeframe)


def replace_watchlist(symbols: list[str], timeframes: list[str] | str | None = None) -> list[PaperWatchSymbol]:
    if isinstance(timeframes, str):
        requested_timeframes = [item.strip() for item in timeframes.split(",")]
    else:
        requested_timeframes = timeframes or DEFAULT_WATCHLIST_TIMEFRAMES
    requested_timeframes = [timeframe for timeframe in requested_timeframes if timeframe]
    for timeframe in requested_timeframes:
        get_timeframe(timeframe)
    if not requested_timeframes:
        raise ValueError("Add at least one timeframe.")

    normalized_symbols = []
    for symbol in symbols:
        normalized = normalize_symbol(symbol, "us")
        if normalized and normalized not in normalized_symbols:
            normalized_symbols.append(normalized)
    if not normalized_symbols:
        raise ValueError("Add at least one symbol.")

    PaperWatchSymbol.objects.all().delete()
    return [
        PaperWatchSymbol.objects.create(symbol=symbol, timeframe=timeframe)
        for symbol in normalized_symbols
        for timeframe in requested_timeframes
    ]


@transaction.atomic
def close_trade(trade: PaperTrade, exit_time: int, exit_price: Decimal, reason: str) -> PaperTrade:
    trade = PaperTrade.objects.select_for_update().get(pk=trade.pk)
    if trade.status == PaperTrade.CLOSED:
        return trade
    broker_status = trade.broker_status
    if trade.execution_provider == "alpaca" and alpaca.enabled():
        try:
            order = alpaca.submit_market_order(trade.symbol, trade.side, trade.quantity, closing=True)
            broker_status = str(order.get("status") or "submitted")
        except alpaca.AlpacaOrderError as exc:
            broker_status = f"close_error: {exc}"

    trade.exit_time = exit_time
    trade.exit_price = price(exit_price)
    trade.exit_reason = reason
    trade.status = PaperTrade.CLOSED
    trade.broker_status = broker_status
    if trade.side == PaperTrade.LONG:
        pnl = (trade.exit_price - trade.entry_price) * trade.quantity
    else:
        pnl = (trade.entry_price - trade.exit_price) * trade.quantity
    trade.pnl = money(pnl)
    trade.save()
    record_trade_activity(trade, "CLOSE", "STRATEGY", reason)
    return trade


@transaction.atomic
def mark_symbol(symbol: str, timeframe: str, bar: dict[str, Any]) -> list[PaperTrade]:
    high = as_decimal(bar["high"])
    low = as_decimal(bar["low"])
    close = as_decimal(bar["close"])
    bar_time = int(bar["time"])
    changed = []

    for trade in PaperTrade.objects.select_for_update().filter(symbol=symbol, timeframe=timeframe, status=PaperTrade.OPEN):
        if trade.side == PaperTrade.LONG:
            if low <= trade.stop_price:
                changed.append(close_trade(trade, bar_time, trade.stop_price, "STOP"))
                continue
            if high > trade.best_price:
                trade.best_price = price(high)
            next_stop = trade.best_price - trade.risk_per_share
            if next_stop > trade.stop_price and next_stop < close:
                trade.stop_price = price(next_stop)
                trade.save(update_fields=["best_price", "stop_price", "updated_at"])
                changed.append(trade)
        else:
            if high >= trade.stop_price:
                changed.append(close_trade(trade, bar_time, trade.stop_price, "STOP"))
                continue
            if low < trade.best_price:
                trade.best_price = price(low)
            next_stop = trade.best_price + trade.risk_per_share
            if next_stop < trade.stop_price and next_stop > close:
                trade.stop_price = price(next_stop)
                trade.save(update_fields=["best_price", "stop_price", "updated_at"])
                changed.append(trade)

    return changed


def average(values: list[float]) -> float:
    return sum(values) / len(values)


def calculate_ema(bars: list[dict[str, Any]], period: int) -> list[float | None]:
    values: list[float | None] = []
    multiplier = 2 / (period + 1)
    previous = None
    for index, bar in enumerate(bars):
        if index < period - 1:
            values.append(None)
            continue
        if previous is None:
            previous = average([float(item["close"]) for item in bars[:period]])
        else:
            previous = (float(bar["close"]) - previous) * multiplier + previous
        values.append(previous)
    return values


def calculate_vwap(bars: list[dict[str, Any]]) -> list[float | None]:
    values = []
    cumulative_price_volume = 0.0
    cumulative_volume = 0.0
    current_session = None
    for bar in bars:
        session = datetime.fromtimestamp(int(bar["time"]), PACIFIC_ZONE).date()
        if session != current_session:
            current_session = session
            cumulative_price_volume = 0.0
            cumulative_volume = 0.0
        volume = float(bar.get("volume") or 0)
        source = (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3
        cumulative_price_volume += source * volume
        cumulative_volume += volume
        values.append(cumulative_price_volume / cumulative_volume if cumulative_volume else None)
    return values


def calculate_testing1_signals(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ema = calculate_ema(bars, 9)
    vwap = calculate_vwap(bars)
    signals = []

    for index in range(2, len(bars)):
        before_index = index - 2
        cross_index = index - 1
        before_ema = ema[before_index]
        before_vwap = vwap[before_index]
        cross_ema = ema[cross_index]
        cross_vwap = vwap[cross_index]
        if before_ema is None or before_vwap is None or cross_ema is None or cross_vwap is None:
            continue

        confirmation = bars[index]
        cross_bar = bars[cross_index]
        volume_increasing = float(confirmation.get("volume") or 0) > float(cross_bar.get("volume") or 0)
        crossed_above = before_ema <= before_vwap and cross_ema > cross_vwap
        crossed_below = before_ema >= before_vwap and cross_ema < cross_vwap

        if crossed_above and volume_increasing and float(confirmation["close"]) > float(cross_bar["close"]):
            signals.append({"time": int(confirmation["time"]), "side": "BUY"})
        if crossed_below and volume_increasing and float(confirmation["close"]) < float(cross_bar["close"]):
            signals.append({"time": int(confirmation["time"]), "side": "SELL"})

    return signals


def normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def normal_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2 * math.pi)


def estimate_iv(bars: list[dict[str, Any]], timeframe: str) -> float:
    closes = [float(bar["close"]) for bar in bars[-80:] if float(bar["close"]) > 0]
    if len(closes) < 20:
        return DEFAULT_IV

    returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    mean_return = sum(returns) / len(returns)
    variance = sum((item - mean_return) ** 2 for item in returns) / max(1, len(returns) - 1)
    timeframe_seconds = int(get_timeframe(timeframe)["seconds"])
    periods_per_year = 252 * 390 * 60 / timeframe_seconds
    annualized = math.sqrt(variance) * math.sqrt(periods_per_year)
    return min(2.0, max(0.05, annualized or DEFAULT_IV))


def option_metrics(underlying_price: float, side: str, iv: float) -> dict[str, Decimal | None]:
    if underlying_price <= 0 or iv <= 0:
        return {
            "contract_price": None,
            "iv": None,
            "delta": None,
            "gamma": None,
            "theta": None,
            "vega": None,
            "rho": None,
        }

    option_type = "CALL" if side == "BUY" else "PUT"
    strike = underlying_price
    time_to_expiry = OPTION_DAYS_TO_EXPIRY
    sigma_root_time = iv * math.sqrt(time_to_expiry)
    d1 = (math.log(underlying_price / strike) + (RISK_FREE_RATE + 0.5 * iv * iv) * time_to_expiry) / sigma_root_time
    d2 = d1 - sigma_root_time
    discount = math.exp(-RISK_FREE_RATE * time_to_expiry)

    if option_type == "CALL":
        contract_price = underlying_price * normal_cdf(d1) - strike * discount * normal_cdf(d2)
        delta = normal_cdf(d1)
        theta = (
            -(underlying_price * normal_pdf(d1) * iv) / (2 * math.sqrt(time_to_expiry))
            - RISK_FREE_RATE * strike * discount * normal_cdf(d2)
        ) / 365
        rho = strike * time_to_expiry * discount * normal_cdf(d2) / 100
    else:
        contract_price = strike * discount * normal_cdf(-d2) - underlying_price * normal_cdf(-d1)
        delta = normal_cdf(d1) - 1
        theta = (
            -(underlying_price * normal_pdf(d1) * iv) / (2 * math.sqrt(time_to_expiry))
            + RISK_FREE_RATE * strike * discount * normal_cdf(-d2)
        ) / 365
        rho = -strike * time_to_expiry * discount * normal_cdf(-d2) / 100

    gamma = normal_pdf(d1) / (underlying_price * sigma_root_time)
    vega = underlying_price * normal_pdf(d1) * math.sqrt(time_to_expiry) / 100
    return {
        "contract_price": price(Decimal(str(contract_price))),
        "iv": metric(iv),
        "delta": metric(delta),
        "gamma": metric(gamma),
        "theta": metric(theta),
        "vega": metric(vega),
        "rho": metric(rho),
    }


def calculate_strategy_signals(bars: list[dict[str, Any]], strategy: Strategy) -> list[dict[str, Any]]:
    rules = parse_strategy_text(strategy.rule_text)
    emas = {rule.ema_period: calculate_ema(bars, rule.ema_period) for rule in rules.rules}
    vwap = calculate_vwap(bars)
    signals = []

    for index in range(2, len(bars)):
        before_index = index - 2
        cross_index = index - 1
        before_vwap = vwap[before_index]
        cross_vwap = vwap[cross_index]
        if before_vwap is None or cross_vwap is None:
            continue

        confirmation = bars[index]
        cross_bar = bars[cross_index]
        for rule in rules.rules:
            ema = emas[rule.ema_period]
            before_ema, cross_ema = ema[before_index], ema[cross_index]
            if before_ema is None or cross_ema is None:
                continue
            if rule.require_volume_increasing and float(confirmation.get("volume") or 0) <= float(cross_bar.get("volume") or 0):
                continue
            if rule.side == "BUY":
                crossed = before_ema <= before_vwap and cross_ema > cross_vwap
                close_ok = float(confirmation["close"]) > float(cross_bar["close"])
            else:
                crossed = before_ema >= before_vwap and cross_ema < cross_vwap
                close_ok = float(confirmation["close"]) < float(cross_bar["close"])
            if crossed and (not rule.require_close_confirmation or close_ok):
                signals.append({"time": int(confirmation["time"]), "side": rule.side})

    return signals


def signal_payload(
    symbol: str,
    timeframe: str,
    side: str,
    signal_time: int,
    bars: list[dict[str, Any]],
    strategy_name: str = "testing1",
) -> dict[str, Any] | None:
    signal_index = next((index for index, bar in enumerate(bars) if int(bar["time"]) == signal_time), None)
    if signal_index is None or signal_index < 1:
        return None

    entry_bar = bars[signal_index]
    previous_bar = bars[signal_index - 1]
    entry_price = float(entry_bar["close"])
    if side == "BUY":
        stop_price = min(float(entry_bar["low"]), float(previous_bar["low"]))
        if stop_price >= entry_price:
            stop_price = entry_price * 0.995
    else:
        stop_price = max(float(entry_bar["high"]), float(previous_bar["high"]))
        if stop_price <= entry_price:
            stop_price = entry_price * 1.005

    return {
        "strategy": strategy_name,
        "symbol": symbol,
        "timeframe": timeframe,
        "side": side,
        "signal_time": signal_time,
        "entry_time": int(entry_bar["time"]),
        "entry_price": entry_price,
        "stop_price": stop_price,
        **option_metrics(entry_price, side, estimate_iv(bars, timeframe)),
    }


def mark_automated_option_trades(strategy: Strategy, symbol: str, timeframe: str) -> list[OptionPaperTrade]:
    from .options_paper import close_option_trade, exit_price_for_side, find_contract, option_trade_pnl_percent

    changed = []
    if strategy.trade_asset != Strategy.OPTION:
        return changed

    open_trades = OptionPaperTrade.objects.filter(
        auto_trade=True,
        strategy=strategy.name,
        underlying_symbol=symbol,
        timeframe=timeframe,
        status=OptionPaperTrade.OPEN,
    )
    for trade in open_trades:
        contract = find_contract(trade.underlying_symbol, trade.expiration_date.isoformat(), trade.contract_symbol)
        exit_price = exit_price_for_side(trade.side, contract)
        if exit_price is None:
            continue
        pnl_percent = option_trade_pnl_percent(trade, exit_price)
        stop_loss = strategy.option_stop_loss_percent
        take_profit = strategy.option_take_profit_percent
        if stop_loss is not None and pnl_percent <= -abs(stop_loss):
            changed.append(close_option_trade(trade.id, "STOP_LOSS", actor="STRATEGY", strategy_name=strategy.name))
        elif take_profit is not None and pnl_percent >= take_profit:
            changed.append(close_option_trade(trade.id, "TAKE_PROFIT", actor="STRATEGY", strategy_name=strategy.name))
    return changed


@transaction.atomic
def execute_option_signal(strategy: Strategy, payload: dict[str, Any]) -> OptionPaperTrade | None:
    parse_strategy_text(strategy.rule_text)
    from .options_paper import (
        CONTRACT_MULTIPLIER,
        MAX_AUTO_OPTION_PREMIUM,
        entry_price_for_side,
        open_option_trade,
        option_account_equity,
        option_buying_power,
        select_automated_contract,
    )

    symbol = str(payload["symbol"]).upper()
    timeframe = str(payload["timeframe"])
    signal_side = str(payload["side"]).upper()
    option_type = OptionPaperTrade.CALL if signal_side == "BUY" else OptionPaperTrade.PUT
    signal_time = int(payload["signal_time"])

    for trade in OptionPaperTrade.objects.select_for_update().filter(
        auto_trade=True,
        strategy=strategy.name,
        underlying_symbol=symbol,
        timeframe=timeframe,
        status=OptionPaperTrade.OPEN,
    ):
        if trade.option_type == option_type:
            return None
        from .options_paper import close_option_trade

        close_option_trade(trade.id, "OPPOSITE_SIGNAL", actor="STRATEGY", strategy_name=strategy.name)

    contract = select_automated_contract(
        symbol=symbol,
        signal_side=signal_side,
        min_dte=strategy.option_min_dte,
        max_dte=strategy.option_max_dte,
        strike_mode=strategy.option_strike_mode,
        max_spread_percent=strategy.max_spread_percent,
    )
    entry_price = entry_price_for_side(OptionPaperTrade.LONG, contract)
    premium_per_contract = entry_price * CONTRACT_MULTIPLIER
    if premium_per_contract > MAX_AUTO_OPTION_PREMIUM:
        raise ValueError(
            f"{symbol} {option_type} premium {money(premium_per_contract)} is above the "
            f"automated option premium cap {money(MAX_AUTO_OPTION_PREMIUM)}."
        )
    risk_amount = money(option_account_equity() * strategy.risk_percent)
    budget = min(risk_amount, option_buying_power())
    contracts = int((budget / premium_per_contract).to_integral_value(rounding=ROUND_DOWN))
    if contracts < 1:
        raise ValueError(
            f"{symbol} {option_type} premium {money(premium_per_contract)} is above risk budget {money(risk_amount)}."
        )

    return open_option_trade(
        {
            "underlying_symbol": symbol,
            "contract_symbol": contract["contract_symbol"],
            "expiration_date": contract["expiration"],
            "side": OptionPaperTrade.LONG,
            "quantity": contracts,
            "strategy": strategy.name,
            "timeframe": timeframe,
            "signal_time": signal_time,
            "notes": f"Auto {strategy.name} {signal_side}",
        },
        strategy_name=strategy.name,
    )


def execute_strategy_signal(strategy: Strategy, payload: dict[str, Any]) -> PaperTrade | OptionPaperTrade | None:
    parse_strategy_text(strategy.rule_text)
    if strategy.trade_asset == Strategy.OPTION:
        return execute_option_signal(strategy, payload)
    payload = {**payload, "risk_percent": float(strategy.risk_percent)}
    return execute_signal(payload)


def scan_strategy_for_watch(
    watch: PaperWatchSymbol,
    strategy: Strategy,
    bars: list[dict[str, Any]],
    decision_bar_time: int,
) -> list[PaperTrade | OptionPaperTrade]:
    # Existing exits use saved risk settings, independently of entry-rule validity.
    mark_automated_option_trades(strategy, watch.symbol, watch.timeframe)
    parse_strategy_text(strategy.rule_text)
    state, _ = StrategyScanState.objects.get_or_create(
        strategy=strategy,
        symbol=watch.symbol,
        timeframe=watch.timeframe,
    )
    if state.last_signal_time is None:
        state.last_signal_time = decision_bar_time
        state.last_error = ""
        state.last_checked_at = timezone.now()
        state.save(update_fields=["last_signal_time", "last_error", "last_checked_at", "updated_at"])
        return []

    live_cutoff = int(time.time()) - LIVE_SIGNAL_MAX_AGE_SECONDS
    previous_checkpoint = state.last_signal_time
    new_signals = [
        signal
        for signal in calculate_strategy_signals(bars, strategy)
        if previous_checkpoint < signal["time"] <= decision_bar_time and signal["time"] >= live_cutoff
    ]
    trades = []
    for signal in new_signals:
        payload = signal_payload(watch.symbol, watch.timeframe, signal["side"], signal["time"], bars, strategy.name)
        if payload:
            trade = execute_strategy_signal(strategy, payload)
            if trade:
                trades.append(trade)

    state.last_signal_time = max(state.last_signal_time or 0, decision_bar_time)
    state.last_error = ""
    state.last_checked_at = timezone.now()
    state.save(update_fields=["last_signal_time", "last_error", "last_checked_at", "updated_at"])
    return trades


def scan_watch_symbol(watch: PaperWatchSymbol) -> list[PaperTrade | OptionPaperTrade]:
    bars = get_history(watch.symbol, watch.timeframe)
    if not bars:
        raise ValueError("No bars returned.")

    mark_symbol(watch.symbol, watch.timeframe, bars[-1])
    decision_bar_time = int(bars[-2]["time"] if len(bars) > 1 else bars[-1]["time"])

    trades = []
    for strategy in Strategy.objects.filter(enabled=True):
        try:
            trades.extend(scan_strategy_for_watch(watch, strategy, bars, decision_bar_time))
        except Exception as exc:
            state, _ = StrategyScanState.objects.get_or_create(
                strategy=strategy,
                symbol=watch.symbol,
                timeframe=watch.timeframe,
            )
            if state.last_signal_time is None:
                state.last_signal_time = decision_bar_time
            state.last_error = str(exc)
            state.last_checked_at = timezone.now()
            state.save(update_fields=["last_signal_time", "last_error", "last_checked_at", "updated_at"])
    watch.last_signal_time = max(watch.last_signal_time or 0, decision_bar_time)
    watch.last_error = ""
    watch.last_checked_at = timezone.now()
    watch.save(update_fields=["last_signal_time", "last_error", "last_checked_at", "updated_at"])
    return trades


def scan_enabled_watchlist() -> list[PaperTrade | OptionPaperTrade]:
    ensure_default_watchlist()
    ensure_default_strategies()
    trades = []
    for watch in PaperWatchSymbol.objects.filter(enabled=True):
        try:
            trades.extend(scan_watch_symbol(watch))
        except Exception as exc:
            watch.last_error = str(exc)
            watch.last_checked_at = timezone.now()
            watch.save(update_fields=["last_error", "last_checked_at", "updated_at"])
    return trades


@transaction.atomic
def execute_signal(payload: dict[str, Any]) -> PaperTrade | None:
    symbol = str(payload["symbol"]).upper()
    timeframe = str(payload["timeframe"])
    strategy = str(payload.get("strategy") or "testing1")
    saved_strategy = Strategy.objects.filter(name=strategy).first()
    if saved_strategy:
        parse_strategy_text(saved_strategy.rule_text)
    signal_side = str(payload["side"]).upper()
    side = PaperTrade.LONG if signal_side == "BUY" else PaperTrade.SHORT
    opposite = PaperTrade.SHORT if side == PaperTrade.LONG else PaperTrade.LONG
    signal_time = int(payload["signal_time"])
    entry_time = int(payload["entry_time"])
    entry_price = as_decimal(payload["entry_price"])
    stop_price = as_decimal(payload["stop_price"])
    metrics = option_metrics(float(entry_price), signal_side, float(payload.get("iv") or DEFAULT_IV))
    for key in metrics:
        if payload.get(key) is not None:
            metrics[key] = metric(payload[key]) if key != "contract_price" else price(as_decimal(payload[key]))

    risk_per_share = entry_price - stop_price if side == PaperTrade.LONG else stop_price - entry_price
    if risk_per_share <= 0:
        raise ValueError("Stop price must create positive risk.")

    for trade in PaperTrade.objects.select_for_update().filter(symbol=symbol, timeframe=timeframe, strategy=strategy, status=PaperTrade.OPEN):
        if trade.side == opposite:
            close_trade(trade, entry_time, entry_price, "OPPOSITE_SIGNAL")
        elif trade.side == side:
            return None

    equity = account_equity()
    risk_percent = as_decimal(payload.get("risk_percent", RISK_PERCENT))
    if risk_percent <= 0 or risk_percent > 1:
        raise ValueError("Risk percent must be greater than 0 and no more than 100%.")
    risk_amount = money(equity * risk_percent)
    risk_quantity = quantity(risk_amount / risk_per_share)
    available_notional = equity - open_notional()
    if available_notional <= 0:
        raise ValueError("No paper buying power available.")
    notional_quantity = quantity(available_notional / entry_price)
    trade_quantity = min(risk_quantity, notional_quantity)
    if trade_quantity <= 0:
        raise ValueError("Calculated quantity is zero.")

    try:
        trade = PaperTrade.objects.create(
            symbol=symbol,
            timeframe=timeframe,
            strategy=strategy,
            side=side,
            signal_time=signal_time,
            entry_time=entry_time,
            entry_price=price(entry_price),
            quantity=trade_quantity,
            risk_amount=risk_amount,
            risk_per_share=price(risk_per_share),
            stop_price=price(stop_price),
            best_price=price(entry_price),
            contract_price=metrics["contract_price"],
            iv=metrics["iv"],
            delta=metrics["delta"],
            gamma=metrics["gamma"],
            theta=metrics["theta"],
            vega=metrics["vega"],
            rho=metrics["rho"],
        )
    except IntegrityError:
        return None

    if alpaca.enabled():
        try:
            order = alpaca.submit_market_order(symbol, side, trade_quantity)
            trade.execution_provider = "alpaca"
            trade.broker_order_id = str(order.get("id") or "")
            trade.broker_status = str(order.get("status") or "submitted")
            trade.save(update_fields=["execution_provider", "broker_order_id", "broker_status", "updated_at"])
        except alpaca.AlpacaOrderError as exc:
            trade.execution_provider = "alpaca_error"
            trade.broker_status = str(exc)[:128]
            trade.save(update_fields=["execution_provider", "broker_status", "updated_at"])

    record_trade_activity(trade, "OPEN", "STRATEGY", "STRATEGY_SIGNAL")
    return trade


@transaction.atomic
def reset_paper_trades() -> None:
    record_reset("underlying", PaperTrade.objects.count())
    PaperTrade.objects.all().delete()
