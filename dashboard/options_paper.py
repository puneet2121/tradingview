from __future__ import annotations

from datetime import date, datetime, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
import math
import time
from typing import Any

from django.db import transaction
from django.utils import timezone

from .models import OptionPaperTrade
from .paper_trading import metric, money, normal_cdf, normal_pdf, price
from .trade_activity import record_reset, record_trade_activity


CONTRACT_MULTIPLIER = Decimal("100")
OPTION_STARTING_BALANCE = Decimal("100000.00")
MAX_AUTO_OPTION_PREMIUM = Decimal("1500.00")
RISK_FREE_RATE = 0.04
OPTIONS_CACHE_TTL_SECONDS = 60
_expiration_cache: dict[str, tuple[float, list[str]]] = {}
_chain_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def serialize_decimal(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def optional_price(value: Any) -> Decimal | None:
    result = as_decimal(value)
    return price(result) if result is not None else None


def get_expirations(symbol: str) -> list[str]:
    symbol = normalize_underlying(symbol)
    now = time.monotonic()
    cached = _expiration_cache.get(symbol)
    if cached and now - cached[0] < OPTIONS_CACHE_TTL_SECONDS:
        return cached[1]

    import yfinance as yf

    expirations = list(yf.Ticker(symbol).options or [])
    _expiration_cache[symbol] = (now, expirations)
    return expirations


def fetch_option_chain(symbol: str, expiration: str | None = None) -> dict[str, Any]:
    symbol = normalize_underlying(symbol)
    expirations = get_expirations(symbol)
    if not expirations:
        raise ValueError(f"No Yahoo option expirations found for {symbol}.")
    expiration = expiration or expirations[0]
    if expiration not in expirations:
        raise ValueError(f"{expiration} is not a valid expiration for {symbol}.")

    cache_key = (symbol, expiration)
    now = time.monotonic()
    cached = _chain_cache.get(cache_key)
    if cached and now - cached[0] < OPTIONS_CACHE_TTL_SECONDS:
        return cached[1]

    import yfinance as yf

    ticker = yf.Ticker(symbol)
    chain = ticker.option_chain(expiration)
    underlying_price = latest_underlying_price(ticker)
    payload = {
        "symbol": symbol,
        "expiration": expiration,
        "expirations": expirations,
        "underlying_price": underlying_price,
        "calls": frame_to_contracts(chain.calls, "call", symbol, expiration, underlying_price),
        "puts": frame_to_contracts(chain.puts, "put", symbol, expiration, underlying_price),
        "source": "Yahoo Finance",
        "updated_at": int(time.time()),
    }
    _chain_cache[cache_key] = (now, payload)
    return payload


def latest_underlying_price(ticker) -> float | None:
    try:
        history = ticker.history(period="2d", interval="1m")
        closes = history["Close"].dropna()
        if len(closes):
            return float(closes.iloc[-1])
    except Exception:
        return None
    return None


def frame_to_contracts(frame, option_type: str, symbol: str, expiration: str, underlying_price: float | None) -> list[dict[str, Any]]:
    contracts = []
    if frame is None or frame.empty:
        return contracts

    for row in frame.fillna("").to_dict("records"):
        bid = number(row.get("bid"))
        ask = number(row.get("ask"))
        last = number(row.get("lastPrice"))
        mid = mid_price(bid, ask, last)
        iv = number(row.get("impliedVolatility"))
        strike = number(row.get("strike"))
        greeks = option_greeks(underlying_price, strike, expiration, option_type, iv)
        contracts.append(
            {
                "contract_symbol": str(row.get("contractSymbol") or ""),
                "option_type": option_type,
                "strike": strike,
                "expiration": expiration,
                "last_price": last,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "change": number(row.get("change")),
                "percent_change": number(row.get("percentChange")),
                "volume": int(number(row.get("volume")) or 0),
                "open_interest": int(number(row.get("openInterest")) or 0),
                "iv": iv,
                "in_the_money": bool(row.get("inTheMoney")),
                **greeks,
            }
        )
    return contracts


def number(value: Any) -> float | None:
    if value in {"", None}:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def mid_price(bid: float | None, ask: float | None, last: float | None) -> float | None:
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2
    return last


def option_greeks(
    underlying_price: float | None,
    strike: float | None,
    expiration: str,
    option_type: str,
    iv: float | None,
) -> dict[str, float | None]:
    if not underlying_price or not strike or not iv or underlying_price <= 0 or strike <= 0 or iv <= 0:
        return empty_greeks()

    expiry = datetime.strptime(expiration, "%Y-%m-%d").replace(tzinfo=datetime_timezone.utc)
    seconds = max((expiry - datetime.now(datetime_timezone.utc)).total_seconds(), 0)
    time_to_expiry = max(seconds / (365 * 24 * 60 * 60), 1 / 365)
    sigma_root_time = iv * math.sqrt(time_to_expiry)
    if sigma_root_time <= 0:
        return empty_greeks()

    d1 = (math.log(underlying_price / strike) + (RISK_FREE_RATE + 0.5 * iv * iv) * time_to_expiry) / sigma_root_time
    d2 = d1 - sigma_root_time
    discount = math.exp(-RISK_FREE_RATE * time_to_expiry)

    if option_type == "call":
        delta = normal_cdf(d1)
        theta = (
            -(underlying_price * normal_pdf(d1) * iv) / (2 * math.sqrt(time_to_expiry))
            - RISK_FREE_RATE * strike * discount * normal_cdf(d2)
        ) / 365
        rho = strike * time_to_expiry * discount * normal_cdf(d2) / 100
    else:
        delta = normal_cdf(d1) - 1
        theta = (
            -(underlying_price * normal_pdf(d1) * iv) / (2 * math.sqrt(time_to_expiry))
            + RISK_FREE_RATE * strike * discount * normal_cdf(-d2)
        ) / 365
        rho = -strike * time_to_expiry * discount * normal_cdf(-d2) / 100

    gamma = normal_pdf(d1) / (underlying_price * sigma_root_time)
    vega = underlying_price * normal_pdf(d1) * math.sqrt(time_to_expiry) / 100
    return {
        "delta": round(delta, 6),
        "gamma": round(gamma, 6),
        "theta": round(theta, 6),
        "vega": round(vega, 6),
        "rho": round(rho, 6),
    }


def empty_greeks() -> dict[str, None]:
    return {"delta": None, "gamma": None, "theta": None, "vega": None, "rho": None}


def normalize_underlying(symbol: str) -> str:
    symbol = str(symbol or "").strip().upper()
    if not symbol or not symbol.replace(".", "").replace("-", "").isalnum():
        raise ValueError("Enter a valid underlying symbol.")
    return symbol


def find_contract(symbol: str, expiration: str, contract_symbol: str) -> dict[str, Any]:
    chain = fetch_option_chain(symbol, expiration)
    for contract in chain["calls"] + chain["puts"]:
        if contract["contract_symbol"] == contract_symbol:
            return {**contract, "underlying_price": chain["underlying_price"], "updated_at": chain["updated_at"]}
    raise ValueError(f"Contract {contract_symbol} was not found in the Yahoo chain.")


def option_realized_pnl() -> Decimal:
    return sum((trade.pnl for trade in OptionPaperTrade.objects.filter(status=OptionPaperTrade.CLOSED)), Decimal("0"))


def option_open_cost() -> Decimal:
    return sum(
        (
            trade.entry_price * Decimal(trade.quantity) * CONTRACT_MULTIPLIER
            for trade in OptionPaperTrade.objects.filter(status=OptionPaperTrade.OPEN, side=OptionPaperTrade.LONG)
        ),
        Decimal("0"),
    )


def option_account_equity() -> Decimal:
    return OPTION_STARTING_BALANCE + option_realized_pnl()


def option_buying_power() -> Decimal:
    return max(Decimal("0"), option_account_equity() - option_open_cost())


def option_state() -> dict[str, Any]:
    trades = list(OptionPaperTrade.objects.all()[:200])
    serialized_trades = [serialize_trade(trade, current_option_quote(trade)) for trade in trades]
    realized = option_realized_pnl()
    open_value = sum(
        (
            as_decimal(trade.get("current_value")) or Decimal("0")
            for trade in serialized_trades
            if trade["status"] == OptionPaperTrade.OPEN
        ),
        Decimal("0"),
    )
    unrealized = sum(
        (
            as_decimal(trade.get("unrealized_pnl")) or Decimal("0")
            for trade in serialized_trades
            if trade["status"] == OptionPaperTrade.OPEN
        ),
        Decimal("0"),
    )
    return {
        "starting_balance": float(OPTION_STARTING_BALANCE),
        "realized_pnl": float(money(realized)),
        "equity": float(money(option_account_equity())),
        "buying_power": float(money(option_buying_power())),
        "open_value": float(money(open_value)),
        "unrealized_pnl": float(money(unrealized)),
        "trades": serialized_trades,
    }


def current_option_quote(trade: OptionPaperTrade) -> dict[str, Any] | None:
    if trade.status != OptionPaperTrade.OPEN:
        return None
    try:
        contract = find_contract(trade.underlying_symbol, trade.expiration_date.isoformat(), trade.contract_symbol)
        exit_price = exit_price_for_side(trade.side, contract)
        if exit_price is None:
            return {"current_quote_error": "Yahoo does not have a usable exit price for this contract."}
        current_value = money(exit_price * Decimal(trade.quantity) * CONTRACT_MULTIPLIER)
        return {
            "current_underlying_price": optional_price(contract.get("underlying_price")),
            "current_bid": optional_price(contract.get("bid")),
            "current_ask": optional_price(contract.get("ask")),
            "current_mid": optional_price(contract.get("mid")),
            "current_last_price": optional_price(contract.get("last_price")),
            "current_exit_price": exit_price,
            "current_value": current_value,
            "unrealized_pnl": option_trade_pnl(trade, exit_price),
            "current_iv": metric(contract.get("iv")),
            "current_delta": metric(contract.get("delta")),
            "current_gamma": metric(contract.get("gamma")),
            "current_theta": metric(contract.get("theta")),
            "current_vega": metric(contract.get("vega")),
            "current_rho": metric(contract.get("rho")),
            "current_quote_time": contract.get("updated_at"),
            "current_quote_error": "",
        }
    except Exception as exc:
        return {"current_quote_error": str(exc)}


def current_trade_value(trade: OptionPaperTrade) -> Decimal:
    quote = current_option_quote(trade)
    return as_decimal((quote or {}).get("current_value")) or Decimal("0")


def serialize_trade(trade: OptionPaperTrade, current_quote: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "id": trade.id,
        "underlying_symbol": trade.underlying_symbol,
        "contract_symbol": trade.contract_symbol,
        "option_type": trade.option_type,
        "expiration_date": trade.expiration_date.isoformat(),
        "strike": serialize_decimal(trade.strike),
        "strategy": trade.strategy,
        "timeframe": trade.timeframe,
        "signal_time": trade.signal_time,
        "auto_trade": trade.auto_trade,
        "side": trade.side,
        "status": trade.status,
        "quantity": trade.quantity,
        "entry_time": trade.entry_time.isoformat(),
        "entry_price": serialize_decimal(trade.entry_price),
        "entry_underlying_price": serialize_decimal(trade.entry_underlying_price),
        "entry_bid": serialize_decimal(trade.entry_bid),
        "entry_ask": serialize_decimal(trade.entry_ask),
        "entry_iv": serialize_decimal(trade.entry_iv),
        "entry_delta": serialize_decimal(trade.entry_delta),
        "entry_gamma": serialize_decimal(trade.entry_gamma),
        "entry_theta": serialize_decimal(trade.entry_theta),
        "entry_vega": serialize_decimal(trade.entry_vega),
        "entry_rho": serialize_decimal(trade.entry_rho),
        "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
        "exit_price": serialize_decimal(trade.exit_price),
        "exit_underlying_price": serialize_decimal(trade.exit_underlying_price),
        "exit_reason": trade.exit_reason,
        "pnl": float(trade.pnl),
        "notes": trade.notes,
    }
    if current_quote is None:
        current_quote = {}
    payload.update(
        {
            "current_underlying_price": serialize_decimal(current_quote.get("current_underlying_price")),
            "current_bid": serialize_decimal(current_quote.get("current_bid")),
            "current_ask": serialize_decimal(current_quote.get("current_ask")),
            "current_mid": serialize_decimal(current_quote.get("current_mid")),
            "current_last_price": serialize_decimal(current_quote.get("current_last_price")),
            "current_exit_price": serialize_decimal(current_quote.get("current_exit_price")),
            "current_value": serialize_decimal(current_quote.get("current_value")),
            "unrealized_pnl": serialize_decimal(current_quote.get("unrealized_pnl")),
            "current_iv": serialize_decimal(current_quote.get("current_iv")),
            "current_delta": serialize_decimal(current_quote.get("current_delta")),
            "current_gamma": serialize_decimal(current_quote.get("current_gamma")),
            "current_theta": serialize_decimal(current_quote.get("current_theta")),
            "current_vega": serialize_decimal(current_quote.get("current_vega")),
            "current_rho": serialize_decimal(current_quote.get("current_rho")),
            "current_quote_time": current_quote.get("current_quote_time"),
            "current_quote_error": current_quote.get("current_quote_error", ""),
        }
    )
    return payload


def entry_price_for_side(side: str, contract: dict[str, Any]) -> Decimal:
    if side == OptionPaperTrade.LONG:
        value = contract.get("ask") or contract.get("mid") or contract.get("last_price")
    else:
        value = contract.get("bid") or contract.get("mid") or contract.get("last_price")
    result = as_decimal(value)
    if result is None or result <= 0:
        raise ValueError("Yahoo does not have a usable price for this contract.")
    return price(result)


def exit_price_for_side(side: str, contract: dict[str, Any]) -> Decimal | None:
    if side == OptionPaperTrade.LONG:
        value = contract.get("bid") or contract.get("mid") or contract.get("last_price")
    else:
        value = contract.get("ask") or contract.get("mid") or contract.get("last_price")
    result = as_decimal(value)
    return price(result) if result and result > 0 else None


def option_trade_pnl(trade: OptionPaperTrade, exit_price: Decimal) -> Decimal:
    if trade.side == OptionPaperTrade.LONG:
        pnl = (exit_price - trade.entry_price) * Decimal(trade.quantity) * CONTRACT_MULTIPLIER
    else:
        pnl = (trade.entry_price - exit_price) * Decimal(trade.quantity) * CONTRACT_MULTIPLIER
    return money(pnl)


def option_trade_pnl_percent(trade: OptionPaperTrade, exit_price: Decimal) -> Decimal:
    if trade.entry_price <= 0:
        return Decimal("0")
    if trade.side == OptionPaperTrade.LONG:
        return (exit_price - trade.entry_price) / trade.entry_price
    return (trade.entry_price - exit_price) / trade.entry_price


def expiration_dte(expiration: str) -> int:
    expiry = date.fromisoformat(expiration)
    return (expiry - timezone.localdate()).days


def select_expiration(symbol: str, min_dte: int, max_dte: int) -> str:
    expirations = get_expirations(symbol)
    if not expirations:
        raise ValueError(f"No Yahoo option expirations found for {symbol}.")

    valid = [(expiration_dte(expiration), expiration) for expiration in expirations if expiration_dte(expiration) >= 0]
    preferred = [item for item in valid if min_dte <= item[0] <= max_dte]
    fallback = [item for item in valid if item[0] >= min_dte]
    choices = preferred or fallback or valid
    if not choices:
        raise ValueError(f"No usable Yahoo option expirations found for {symbol}.")
    return sorted(choices, key=lambda item: (item[0], item[1]))[0][1]


def spread_percent(contract: dict[str, Any]) -> Decimal | None:
    bid = as_decimal(contract.get("bid"))
    ask = as_decimal(contract.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    midpoint = (bid + ask) / Decimal("2")
    if midpoint <= 0:
        return None
    return (ask - bid) / midpoint


def spread_is_usable(contract: dict[str, Any], max_spread_percent: Decimal | None) -> bool:
    if max_spread_percent is None:
        return True
    spread = spread_percent(contract)
    return spread is None or spread <= max_spread_percent


def automated_premium_is_usable(contract: dict[str, Any]) -> bool:
    try:
        entry_price = entry_price_for_side(OptionPaperTrade.LONG, contract)
    except ValueError:
        return False
    return entry_price * CONTRACT_MULTIPLIER <= MAX_AUTO_OPTION_PREMIUM


def contract_bucket(contract: dict[str, Any], option_type: str, underlying_price: float, strike_mode: str) -> bool:
    strike = contract.get("strike")
    if strike is None:
        return False
    if strike_mode == "itm":
        return strike <= underlying_price if option_type == "call" else strike >= underlying_price
    if strike_mode == "otm":
        return strike > underlying_price if option_type == "call" else strike < underlying_price
    return True


def select_automated_contract(
    symbol: str,
    signal_side: str,
    min_dte: int,
    max_dte: int,
    strike_mode: str,
    max_spread_percent: Decimal | None,
) -> dict[str, Any]:
    option_type = "call" if signal_side.upper() == "BUY" else "put"
    expiration = select_expiration(symbol, min_dte, max_dte)
    chain = fetch_option_chain(symbol, expiration)
    underlying_price = chain["underlying_price"]
    if not underlying_price:
        raise ValueError(f"Yahoo does not have a usable underlying price for {symbol}.")

    contracts = chain["calls"] if option_type == "call" else chain["puts"]
    filtered = [
        contract
        for contract in contracts
        if contract_bucket(contract, option_type, underlying_price, strike_mode)
        and spread_is_usable(contract, max_spread_percent)
        and automated_premium_is_usable(contract)
        and as_decimal(contract.get("strike")) is not None
    ]
    if not filtered and strike_mode != "atm":
        filtered = [
            contract
            for contract in contracts
            if spread_is_usable(contract, max_spread_percent)
            and automated_premium_is_usable(contract)
            and as_decimal(contract.get("strike")) is not None
        ]
    if not filtered:
        raise ValueError(
            f"No usable {option_type} contracts found for {symbol} {expiration} under the "
            f"${money(MAX_AUTO_OPTION_PREMIUM)} automated premium cap."
        )

    def sort_key(contract: dict[str, Any]) -> tuple[float, float]:
        strike = float(contract["strike"])
        distance = abs(strike - float(underlying_price))
        if strike_mode == "otm":
            distance = strike - float(underlying_price) if option_type == "call" else float(underlying_price) - strike
        if strike_mode == "itm":
            distance = float(underlying_price) - strike if option_type == "call" else strike - float(underlying_price)
        return (abs(distance), strike)

    selected = sorted(filtered, key=sort_key)[0]
    return {**selected, "underlying_price": underlying_price}


@transaction.atomic
def open_option_trade(payload: dict[str, Any], *, strategy_name: str | None = None) -> OptionPaperTrade:
    symbol = normalize_underlying(payload["underlying_symbol"])
    expiration = str(payload["expiration_date"])
    contract = find_contract(symbol, expiration, str(payload["contract_symbol"]))
    side = str(payload["side"]).upper()
    if side not in {OptionPaperTrade.LONG, OptionPaperTrade.SHORT}:
        raise ValueError("Side must be LONG or SHORT.")
    quantity = int(payload.get("quantity") or 1)
    if quantity < 1 or quantity > 1000:
        raise ValueError("Quantity must be between 1 and 1000.")

    entry_price = entry_price_for_side(side, contract)
    trade = OptionPaperTrade.objects.create(
        underlying_symbol=symbol,
        contract_symbol=contract["contract_symbol"],
        option_type=contract["option_type"],
        expiration_date=date.fromisoformat(expiration),
        strike=price(as_decimal(contract["strike"]) or Decimal("0")),
        strategy=strategy_name or "",
        timeframe=str(payload.get("timeframe") or "")[:8],
        signal_time=int(payload["signal_time"]) if strategy_name and payload.get("signal_time") is not None else None,
        auto_trade=bool(strategy_name),
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        entry_underlying_price=optional_price(contract.get("underlying_price")),
        entry_bid=optional_price(contract.get("bid")),
        entry_ask=optional_price(contract.get("ask")),
        entry_iv=metric(contract.get("iv")),
        entry_delta=metric(contract.get("delta")),
        entry_gamma=metric(contract.get("gamma")),
        entry_theta=metric(contract.get("theta")),
        entry_vega=metric(contract.get("vega")),
        entry_rho=metric(contract.get("rho")),
        notes=str(payload.get("notes") or "")[:160],
    )
    record_trade_activity(
        trade, "OPEN", "STRATEGY" if strategy_name else "USER",
        "STRATEGY_SIGNAL" if strategy_name else "MANUAL_OPEN",
    )
    return trade


@transaction.atomic
def close_option_trade(trade_id: int, reason: str, *, actor: str, strategy_name: str = "") -> OptionPaperTrade:
    trade = OptionPaperTrade.objects.select_for_update().get(id=trade_id)
    if actor == "STRATEGY":
        if not trade.auto_trade or not strategy_name or trade.strategy != strategy_name:
            raise ValueError("Strategies can only close their own automated trades. Manual trades are protected.")
        if reason not in {"STOP_LOSS", "TAKE_PROFIT", "OPPOSITE_SIGNAL"}:
            raise ValueError("Invalid automatic exit reason.")
    elif actor != "USER" or reason != "MANUAL_CLOSE":
        raise ValueError("An explicit user close or owning strategy is required.")
    if trade.status == OptionPaperTrade.CLOSED:
        return trade
    contract = find_contract(trade.underlying_symbol, trade.expiration_date.isoformat(), trade.contract_symbol)
    exit_price = exit_price_for_side(trade.side, contract)
    if exit_price is None:
        raise ValueError("Yahoo does not have a usable exit price for this contract.")
    trade.exit_time = timezone.now()
    trade.exit_price = exit_price
    trade.exit_underlying_price = optional_price(contract.get("underlying_price"))
    trade.exit_reason = str(reason or "CLOSED")[:32]
    trade.pnl = option_trade_pnl(trade, exit_price)
    trade.status = OptionPaperTrade.CLOSED
    trade.save()
    record_trade_activity(trade, "CLOSE", actor, reason)
    return trade


@transaction.atomic
def reset_option_trades() -> None:
    record_reset("option", OptionPaperTrade.objects.count())
    OptionPaperTrade.objects.all().delete()
