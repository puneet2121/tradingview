from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django.db import transaction
from django.utils import timezone

from .models import Strategy, StrategyScanState


DEFAULT_STRATEGY_NAME = "testing1"
DEFAULT_STRATEGY_RULE_TEXT = """BUY CALL WHEN EMA(9) crosses above VWAP
AND volume is increasing
AND close > previous close

BUY PUT WHEN EMA(9) crosses below VWAP
AND volume is increasing
AND close < previous close"""
IMPULSIVE_STRATEGY_NAME = "Impulsive Trader Auto B & S Signal"
LEGACY_IMPULSIVE_STRATEGY_RULE_TEXT = """ASIA SESSION BREAKOUT
SESSION 00:00-06:00 America/New_York
SIGNALS UNTIL 19:30 America/New_York
REWARD:RISK 4:1
ONE SIGNAL PER SESSION"""
IMPULSIVE_STRATEGY_RULE_TEXT = """US PREMARKET BREAKOUT
SESSION 03:30-09:30 America/New_York
SIGNALS 09:30-16:00 America/New_York
REWARD:RISK 4:1
ONE SIGNAL PER SESSION"""


@dataclass(frozen=True)
class SignalRule:
    side: str
    ema_period: int
    require_volume_increasing: bool = False
    require_close_confirmation: bool = False


@dataclass(frozen=True)
class ParsedStrategy:
    rules: tuple[SignalRule, ...]
    strategy_type: str = "ema_vwap"
    session_start: str = "00:00"
    session_end: str = "06:00"
    cutoff: str = "19:30"
    timezone: str = "America/New_York"
    reward_risk_ratio: Decimal = Decimal("4")
    signal_start: str | None = None
    regular_hours_only: bool = False


class StrategyValidationError(ValueError):
    pass


INDICATOR_PATTERN = r"(?:vwap|ema\s*\(\s*\d+\s*\)|ema\s+\d+|\d+\s+ema)"
CROSS_PATTERN = re.compile(
    rf"(buy call|buy put|buy|sell)\s+when\s+({INDICATOR_PATTERN})"
    rf"\s+cross(?:es|ing)?\s+(above|over|up|below|under|down)\s+({INDICATOR_PATTERN})"
)
SESSION_PATTERN = re.compile(r"session\s+(\d{2}:\d{2})-(\d{2}:\d{2})\s+([a-z_]+/[a-z_]+)")
CUTOFF_PATTERN = re.compile(r"signals until\s+(\d{2}:\d{2})\s+([a-z_]+/[a-z_]+)")
SIGNAL_SESSION_PATTERN = re.compile(r"signals\s+(\d{2}:\d{2})-(\d{2}:\d{2})\s+([a-z_]+/[a-z_]+)")
REWARD_RISK_PATTERN = re.compile(r"reward\s*:\s*risk\s+(\d+(?:\.\d+)?)\s*:\s*1")


def rule_error(line_number: int, message: str):
    raise StrategyValidationError(f"Line {line_number}: {message}")


def parse_strategy_text(rule_text: str) -> ParsedStrategy:
    if not isinstance(rule_text, str) or not rule_text.strip():
        raise StrategyValidationError("Strategy rules cannot be empty.")
    if is_session_breakout_strategy(rule_text):
        return parse_session_breakout_text(rule_text)

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


def is_session_breakout_strategy(rule_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", rule_text.strip().lower())
    return any(header in normalized for header in ("asia session breakout", "us premarket breakout", "impulsive trader"))


def parse_session_breakout_text(rule_text: str) -> ParsedStrategy:
    session_start = "00:00"
    session_end = "06:00"
    cutoff = "19:30"
    tz = "America/New_York"
    reward_risk = Decimal("4")
    saw_header = False
    regular_hours_only = False
    signal_start = None
    cutoff_tz = None
    seen = set()

    for line_number, raw_line in enumerate(rule_text.splitlines(), 1):
        line = re.sub(r"\s+", " ", raw_line.strip())
        lowered = line.lower()
        if not lowered:
            continue
        if lowered in {"asia session breakout", "us premarket breakout", "impulsive trader auto b & s signal"}:
            if saw_header or seen:
                rule_error(line_number, "Use one session breakout header, before the settings.")
            saw_header = True
            regular_hours_only = lowered == "us premarket breakout"
            if regular_hours_only:
                session_start, session_end, signal_start, cutoff = "03:30", "09:30", "09:30", "16:00"
            continue
        session_match = SESSION_PATTERN.fullmatch(lowered)
        if session_match:
            if "session" in seen:
                rule_error(line_number, "Duplicate SESSION setting.")
            seen.add("session")
            session_start, session_end, tz = session_match.groups()
            validate_time_token(session_start, line_number)
            validate_time_token(session_end, line_number)
            continue
        cutoff_match = CUTOFF_PATTERN.fullmatch(lowered)
        signal_session_match = SIGNAL_SESSION_PATTERN.fullmatch(lowered)
        if cutoff_match or signal_session_match:
            if "signals" in seen:
                rule_error(line_number, "Duplicate SIGNALS setting.")
            seen.add("signals")
            if signal_session_match:
                signal_start, cutoff, cutoff_tz = signal_session_match.groups()
                validate_time_token(signal_start, line_number)
            else:
                cutoff, cutoff_tz = cutoff_match.groups()
            validate_time_token(cutoff, line_number)
            continue
        rr_match = REWARD_RISK_PATTERN.fullmatch(lowered)
        if rr_match:
            if "reward" in seen:
                rule_error(line_number, "Duplicate REWARD:RISK setting.")
            seen.add("reward")
            reward_risk = Decimal(rr_match.group(1))
            if reward_risk <= 0 or reward_risk > Decimal("20"):
                rule_error(line_number, "Reward:risk must be greater than 0 and no more than 20:1.")
            continue
        if lowered == "one signal per session":
            continue
        rule_error(line_number, "Unsupported session breakout setting.")

    if not saw_header:
        raise StrategyValidationError("Start with US PREMARKET BREAKOUT or ASIA SESSION BREAKOUT.")
    if cutoff_tz and cutoff_tz.lower() != tz.lower():
        raise StrategyValidationError("Session and signal timezone must match.")
    signal_start = signal_start or session_end
    if not session_start < session_end <= signal_start < cutoff:
        raise StrategyValidationError("Use same-day times: session start < session end <= signals start < signals end.")
    tz = canonical_timezone(tz)
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise StrategyValidationError(f"Unsupported timezone: {tz}.") from exc
    if regular_hours_only and (tz != "America/New_York" or session_end > "09:30" or signal_start < "09:30" or cutoff > "16:00"):
        raise StrategyValidationError("US premarket must end by 09:30; signals must stay within 09:30-16:00 America/New_York.")
    return ParsedStrategy(
        rules=(),
        strategy_type="session_breakout",
        session_start=session_start,
        session_end=session_end,
        cutoff=cutoff,
        timezone=tz,
        reward_risk_ratio=reward_risk,
        signal_start=signal_start,
        regular_hours_only=regular_hours_only,
    )


def validate_time_token(value: str, line_number: int) -> None:
    hour, minute = [int(part) for part in value.split(":")]
    if hour > 23 or minute > 59:
        rule_error(line_number, "Times must use HH:MM in 24-hour format.")


@lru_cache(maxsize=64)
def canonical_timezone(value: str) -> str:
    if value.lower() == "america/new_york":
        return "America/New_York"
    return next((zone for zone in available_timezones() if zone.lower() == value.lower()), value)


@transaction.atomic
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
    Strategy.objects.get_or_create(
        name=IMPULSIVE_STRATEGY_NAME,
        defaults={
            "enabled": True,
            "rule_text": IMPULSIVE_STRATEGY_RULE_TEXT,
            "trade_asset": Strategy.OPTION,
            "risk_percent": Decimal("0.020000"),
            "option_min_dte": 7,
            "option_max_dte": 14,
            "option_strike_mode": Strategy.ATM,
            "option_take_profit_percent": Decimal("4.000000"),
            "option_stop_loss_percent": Decimal("1.000000"),
            "max_spread_percent": Decimal("0.500000"),
        },
    )
    # Upgrade only the untouched built-in rules; never replace user-edited rules.
    upgraded = Strategy.objects.filter(
        name=IMPULSIVE_STRATEGY_NAME, rule_text=LEGACY_IMPULSIVE_STRATEGY_RULE_TEXT,
    ).update(rule_text=IMPULSIVE_STRATEGY_RULE_TEXT, updated_at=timezone.now())
    if upgraded:
        StrategyScanState.objects.filter(strategy__name=IMPULSIVE_STRATEGY_NAME).update(
            last_signal_time=None, last_error="", last_checked_at=None,
        )


def serialize_strategy(strategy: Strategy) -> dict[str, Any]:
    chart_settings = None
    try:
        parsed = parse_strategy_text(strategy.rule_text)
        validation_error = ""
        if parsed.strategy_type == "session_breakout":
            chart_settings = {
                "sessionStart": parsed.session_start,
                "sessionEnd": parsed.session_end,
                "signalStart": parsed.signal_start,
                "cutoff": parsed.cutoff,
                "timeZone": parsed.timezone,
                "rewardRisk": float(parsed.reward_risk_ratio),
                "regularHoursOnly": parsed.regular_hours_only,
            }
    except StrategyValidationError as exc:
        validation_error = str(exc)
    return {
        "id": strategy.id,
        "name": strategy.name,
        "enabled": strategy.enabled,
        "validation_error": validation_error,
        "valid": not validation_error,
        "chart_settings": chart_settings,
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
        "impulsive_template": IMPULSIVE_STRATEGY_RULE_TEXT,
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
