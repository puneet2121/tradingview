from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from django.db import transaction

from .models import Strategy, StrategyScanState


DEFAULT_STRATEGY_NAME = "testing1"
DEFAULT_STRATEGY_RULE_TEXT = """BUY CALL WHEN EMA(9) crosses above VWAP
AND volume is increasing
AND close > previous close

BUY PUT WHEN EMA(9) crosses below VWAP
AND volume is increasing
AND close < previous close"""


@dataclass(frozen=True)
class SignalRule:
    side: str
    ema_period: int
    require_volume_increasing: bool = False
    require_close_confirmation: bool = False


@dataclass(frozen=True)
class ParsedStrategy:
    rules: tuple[SignalRule, ...]


class StrategyValidationError(ValueError):
    pass


INDICATOR_PATTERN = r"(?:vwap|ema\s*\(\s*\d+\s*\)|ema\s+\d+|\d+\s+ema)"
CROSS_PATTERN = re.compile(
    rf"(buy call|buy put|buy|sell)\s+when\s+({INDICATOR_PATTERN})"
    rf"\s+cross(?:es|ing)?\s+(above|over|up|below|under|down)\s+({INDICATOR_PATTERN})"
)


def rule_error(line_number: int, message: str):
    raise StrategyValidationError(f"Line {line_number}: {message}")


def parse_strategy_text(rule_text: str) -> ParsedStrategy:
    if not isinstance(rule_text, str) or not rule_text.strip():
        raise StrategyValidationError("Strategy rules cannot be empty.")

    rules = []
    current = None
    for line_number, raw_line in enumerate(rule_text.splitlines(), 1):
        line = re.sub(r"\s+", " ", raw_line.strip().lower())
        if not line:
            continue
        # Consume the whole line. Every AND clause belongs to its preceding entry rule.
        clauses = re.split(r"\band\b", line)
        header = clauses[0].strip()
        if header:
            match = CROSS_PATTERN.fullmatch(header)
            if not match:
                rule_error(line_number, "Unsupported entry rule. Use BUY CALL, BUY PUT, BUY or SELL WHEN EMA(9) crosses above/below VWAP.")
            action, left, direction, right = match.groups()
            if (left == "vwap") == (right == "vwap"):
                rule_error(line_number, "Only EMA/VWAP crosses are supported; EMA/EMA and VWAP/VWAP crosses are not supported.")
            ema = right if left == "vwap" else left
            period = int(re.search(r"\d+", ema).group())
            if not 1 <= period <= 500:
                rule_error(line_number, "EMA period must be between 1 and 500.")
            above = direction in {"above", "over", "up"}
            bullish = not above if left == "vwap" else above
            side = "BUY" if action in {"buy", "buy call"} else "SELL"
            if bullish != (side == "BUY"):
                rule_error(line_number, "Action and cross direction disagree. BUY/CALL requires a bullish EMA/VWAP cross; SELL/PUT requires a bearish cross.")
            if any(rule["side"] == side for rule in rules):
                rule_error(line_number, f"Duplicate {side} rule. Only one rule per direction is supported.")
            current = {"side": side, "ema_period": period}
            rules.append(current)
        elif current is None:
            rule_error(line_number, "AND requires a preceding entry rule.")

        for clause in clauses[1:]:
            clause = clause.strip()
            if re.fullmatch(r"volume is increasing|volume (?:>|greater than) previous(?: volume)?", clause):
                key = "require_volume_increasing"
            elif re.fullmatch(r"close [<>] previous close", clause):
                expected = ">" if current["side"] == "BUY" else "<"
                if clause != f"close {expected} previous close":
                    rule_error(line_number, f"Use close {expected} previous close for this {current['side']} rule.")
                key = "require_close_confirmation"
            else:
                rule_error(line_number, f"Unsupported condition: {clause or '(empty AND)'}. Supported confirmations: volume is increasing; close > previous close; close < previous close.")
            if current.get(key):
                rule_error(line_number, f"Duplicate condition: {clause}.")
            current[key] = True

    return ParsedStrategy(rules=tuple(SignalRule(**rule) for rule in rules))


def ensure_default_strategies() -> None:
    Strategy.objects.get_or_create(
        name=DEFAULT_STRATEGY_NAME,
        defaults={
            "enabled": True,
            "rule_text": DEFAULT_STRATEGY_RULE_TEXT,
            "trade_asset": Strategy.OPTION,
            "risk_percent": Decimal("0.020000"),
            "option_min_dte": 7,
            "option_max_dte": 14,
            "option_strike_mode": Strategy.ATM,
            "option_take_profit_percent": Decimal("1.000000"),
            "option_stop_loss_percent": Decimal("0.500000"),
            "max_spread_percent": Decimal("0.500000"),
        },
    )


def serialize_strategy(strategy: Strategy) -> dict[str, Any]:
    try:
        parse_strategy_text(strategy.rule_text)
        validation_error = ""
    except StrategyValidationError as exc:
        validation_error = str(exc)
    return {
        "id": strategy.id,
        "name": strategy.name,
        "enabled": strategy.enabled,
        "validation_error": validation_error,
        "valid": not validation_error,
        "rule_text": strategy.rule_text,
        "trade_asset": strategy.trade_asset,
        "risk_percent": percent_to_ui(strategy.risk_percent),
        "option_min_dte": strategy.option_min_dte,
        "option_max_dte": strategy.option_max_dte,
        "option_strike_mode": strategy.option_strike_mode,
        "option_take_profit_percent": percent_to_ui(strategy.option_take_profit_percent),
        "option_stop_loss_percent": percent_to_ui(strategy.option_stop_loss_percent),
        "max_spread_percent": percent_to_ui(strategy.max_spread_percent),
        "updated_at": strategy.updated_at.isoformat() if strategy.updated_at else None,
    }


def strategy_state() -> dict[str, Any]:
    ensure_default_strategies()
    return {
        "strategies": [serialize_strategy(strategy) for strategy in Strategy.objects.all()],
        "scan_states": [serialize_scan_state(scan_state) for scan_state in StrategyScanState.objects.select_related("strategy")[:300]],
        "template": DEFAULT_STRATEGY_RULE_TEXT,
    }


@transaction.atomic
def save_strategy(payload: dict[str, Any]) -> Strategy:
    strategy_id = payload.get("id")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Strategy name is required.")
    if len(name) > 64:
        raise ValueError("Strategy name must be 64 characters or less.")

    rule_text = str(payload.get("rule_text") or "").strip()
    parse_strategy_text(rule_text)

    trade_asset = str(payload.get("trade_asset") or Strategy.OPTION).lower()
    if trade_asset not in {Strategy.UNDERLYING, Strategy.OPTION}:
        raise ValueError("Trade asset must be underlying or option.")

    option_min_dte = parse_int(payload.get("option_min_dte"), 0, 365, "Minimum DTE")
    option_max_dte = parse_int(payload.get("option_max_dte"), 0, 365, "Maximum DTE")
    if option_max_dte < option_min_dte:
        raise ValueError("Maximum DTE must be greater than or equal to minimum DTE.")

    option_strike_mode = str(payload.get("option_strike_mode") or Strategy.ATM).lower()
    if option_strike_mode not in {Strategy.ATM, Strategy.ITM, Strategy.OTM}:
        raise ValueError("Strike mode must be ATM, ITM, or OTM.")

    values = {
        "name": name,
        "enabled": bool(payload.get("enabled", True)),
        "rule_text": rule_text,
        "trade_asset": trade_asset,
        "risk_percent": parse_percent(payload.get("risk_percent"), "Risk percent"),
        "option_min_dte": option_min_dte,
        "option_max_dte": option_max_dte,
        "option_strike_mode": option_strike_mode,
        "option_take_profit_percent": parse_optional_percent(payload.get("option_take_profit_percent"), "Take profit percent"),
        "option_stop_loss_percent": parse_optional_percent(payload.get("option_stop_loss_percent"), "Stop loss percent"),
        "max_spread_percent": parse_optional_percent(payload.get("max_spread_percent"), "Max spread percent"),
    }

    if strategy_id:
        try:
            strategy = Strategy.objects.get(id=int(strategy_id))
        except (TypeError, ValueError, Strategy.DoesNotExist) as exc:
            raise ValueError("Strategy was not found.") from exc
        rules_changed = strategy.rule_text != rule_text
        for key, value in values.items():
            setattr(strategy, key, value)
        strategy.save()
        if rules_changed:
            StrategyScanState.objects.filter(strategy=strategy).update(last_signal_time=None, last_error="", last_checked_at=None)
        return strategy

    previous = Strategy.objects.filter(name=name).first()
    strategy, _ = Strategy.objects.update_or_create(name=name, defaults=values)
    if previous and previous.rule_text != rule_text:
        StrategyScanState.objects.filter(strategy=strategy).update(last_signal_time=None, last_error="", last_checked_at=None)
    return strategy


def parse_int(value: Any, minimum: int, maximum: int, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if result < minimum or result > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return result


def parse_percent(value: Any, label: str) -> Decimal:
    result = parse_optional_percent(value, label)
    if result is None:
        raise ValueError(f"{label} is required.")
    if result <= 0 or result > Decimal("1"):
        raise ValueError(f"{label} must be greater than 0 and no more than 100.")
    return result


def parse_optional_percent(value: Any, label: str) -> Decimal | None:
    if value in {"", None}:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if result < 0:
        raise ValueError(f"{label} cannot be negative.")
    if result > 1:
        result = result / Decimal("100")
    if result > Decimal("9.999999"):
        raise ValueError(f"{label} must be less than 1000.")
    return result.quantize(Decimal("0.000001"))


def percent_to_ui(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value * Decimal("100"))


def serialize_scan_state(scan_state: StrategyScanState) -> dict[str, Any]:
    return {
        "strategy": scan_state.strategy.name,
        "symbol": scan_state.symbol,
        "timeframe": scan_state.timeframe,
        "last_signal_time": scan_state.last_signal_time,
        "last_checked_at": scan_state.last_checked_at.isoformat() if scan_state.last_checked_at else None,
        "last_error": scan_state.last_error,
    }
