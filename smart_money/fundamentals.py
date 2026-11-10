"""Company fundamentals, separate from options activity and trading decisions."""
from datetime import date, datetime, timedelta, timezone as utc
import math
import threading
import uuid

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from dashboard.data_source import fetch_yfinance_fundamentals
from .config import permission
from .models import FundamentalSnapshot

FIELDS = {
    "income": {"revenue": "TotalRevenue", "gross_profit": "GrossProfit", "operating_income": "OperatingIncome",
               "net_income": "NetIncome", "ebitda": "EBITDA", "diluted_eps": "DilutedEPS"},
    "balance": {"assets": "TotalAssets", "liabilities": "TotalLiabilitiesNetMinorityInterest", "equity": "StockholdersEquity",
                "cash": "CashAndCashEquivalents", "debt": "TotalDebt", "current_assets": "CurrentAssets",
                "current_liabilities": "CurrentLiabilities"},
    "cash_flow": {"operating_cash_flow": "OperatingCashFlow", "capital_expenditure": "CapitalExpenditure", "free_cash_flow": "FreeCashFlow"},
}
_slots = threading.BoundedSemaphore(2)


def allowed():
    return not getattr(settings, "SHARING_ENABLED", False) or permission("allow_fundamentals_sharing")


def number(value):
    if value is None or isinstance(value, (bool, str)):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and abs(value) < 1e20 else None
    except (ValueError, TypeError, OverflowError):
        return None


def text(value, limit=200):
    return value[:limit] if isinstance(value, str) else ""


def ratio(numerator, denominator):
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return number(numerator / denominator)


def statement_rows(frame, kind, today):
    if frame is None or not hasattr(frame, "columns") or frame.empty:
        return []
    rows = {}
    for column in frame.columns:
        try:
            period = column.date() if hasattr(column, "date") else date.fromisoformat(str(column))
            if period > today:
                continue
        except (TypeError, ValueError):
            continue
        row = {"period_end": period.isoformat()}
        for key, name in FIELDS[kind].items():
            row[key] = number(frame.loc[name, column]) if name in frame.index else None
        if all(row[key] is None for key in FIELDS[kind]):
            continue
        if kind == "cash_flow":
            row["fcf_method"] = "Provider-reported" if row["free_cash_flow"] is not None else "Unavailable"
            # Do not guess whether a positive capex field represents an outflow.
            if row["free_cash_flow"] is None and row["operating_cash_flow"] is not None and row["capital_expenditure"] is not None and row["capital_expenditure"] <= 0:
                row["free_cash_flow"] = row["operating_cash_flow"] + row["capital_expenditure"]
                row["fcf_method"] = "Operating cash flow + signed (nonpositive) capital expenditure"
        rows[row["period_end"]] = row
    return [rows[key] for key in sorted(rows, reverse=True)[:8]]


def period_analysis(statements):
    income, balance, cash_flow = (statements.get(key, []) for key in ("income", "balance", "cash_flow"))
    current = income[0] if income else {}
    period = current.get("period_end")
    prior = next((row for row in income[1:] if 330 <= (date.fromisoformat(period)-date.fromisoformat(row["period_end"])).days <= 400), None) if period else None
    growth = {}
    for key in ("revenue", "net_income", "diluted_eps"):
        old, new = (prior or {}).get(key), current.get(key)
        comparison = ratio(new, old)
        growth[key+"_yoy"] = number((comparison-1)*100) if comparison is not None else None
    margin = lambda key: ratio(current.get(key), current.get("revenue"))
    latest_balance = balance[0] if balance else {}
    matched_cash = next((row for row in cash_flow if row["period_end"] == period), {})
    values = {"period_end": period, "prior_period_end": (prior or {}).get("period_end"), **growth,
              "net_margin": margin("net_income"), "operating_margin": margin("operating_income"),
              "gross_margin": margin("gross_profit"), "free_cash_flow": matched_cash.get("free_cash_flow"),
              "balance_period_end": latest_balance.get("period_end"),
              "debt_equity": ratio(latest_balance.get("debt"), latest_balance.get("equity")),
              "current_ratio": ratio(latest_balance.get("current_assets"), latest_balance.get("current_liabilities"))}
    observations = []
    if growth["revenue_yoy"] is not None:
        observations.append(f"Revenue {'rose' if growth['revenue_yoy'] >= 0 else 'fell'} {abs(growth['revenue_yoy']):.1f}% versus the comparable prior-year period ending {prior['period_end']}.")
    if current.get("net_income") is not None:
        label = "Net profit" if current["net_income"] > 0 else "Net loss" if current["net_income"] < 0 else "Breakeven net income"
        observations.append(f"{label} reported for the period ending {period}.")
    if matched_cash.get("free_cash_flow") is not None:
        label = "Positive" if matched_cash["free_cash_flow"] > 0 else "Negative" if matched_cash["free_cash_flow"] < 0 else "Zero"
        observations.append(f"{label} free cash flow for the same period.")
    if latest_balance.get("equity") is not None and latest_balance["equity"] <= 0:
        observations.append(f"Nonpositive shareholder equity at {latest_balance['period_end']}; debt/equity is not meaningful.")
    values["observations"] = observations
    return values


def normalize(symbol, raw, now):
    info = raw["info"]
    supported = info.get("quoteType") == "EQUITY"
    data = {"symbol": symbol, "source": "Yahoo Finance via yfinance", "source_url": f"https://finance.yahoo.com/quote/{symbol}/financials/",
        "supported": supported, "company": text(info.get("longName") or info.get("shortName")),
        "asset_type": text(info.get("quoteType"), 30), "sector": text(info.get("sector")), "industry": text(info.get("industry")),
        "country": text(info.get("country")), "exchange": text(info.get("fullExchangeName") or info.get("exchange")),
        "description": text(info.get("longBusinessSummary"), 4000), "quote_currency": text(info.get("currency"), 12),
        "financial_currency": text(info.get("financialCurrency"), 12), "fetched_at": now.isoformat(),
        "quote_at": None, "price": number(info.get("regularMarketPrice") or info.get("currentPrice")),
        "market_cap": number(info.get("marketCap")), "warnings": list(raw.get("warnings", [])), "periods": {}}
    market_time = number(info.get("regularMarketTime"))
    if market_time and 0 < market_time <= now.timestamp()+300:
        data["quote_at"] = datetime.fromtimestamp(market_time, utc.utc).isoformat()
    if not supported:
        data["warnings"] = ["Company income statements and corporate ratios do not apply to this asset type."]
        return data
    valuations = {key: number(info.get(field)) for key, field in {
        "trailing_pe":"trailingPE", "forward_pe":"forwardPE", "price_sales":"priceToSalesTrailing12Months",
        "price_book":"priceToBook", "ev_ebitda":"enterpriseToEbitda", "enterprise_value":"enterpriseValue",
        "trailing_eps":"trailingEps", "forward_eps":"forwardEps"}.items()}
    for key in ("trailing_pe", "forward_pe", "price_sales", "price_book", "ev_ebitda"):
        if valuations[key] is not None and valuations[key] <= 0:
            valuations[key] = None
    for prefix in ("trailing", "forward"):
        eps = valuations[prefix+"_eps"]
        if eps is not None and eps <= 0:
            valuations[prefix+"_pe"] = None
    data["valuation"] = valuations
    for frequency in ("yearly", "quarterly"):
        source = raw.get("statements", {}).get(frequency, {})
        statements = {kind: statement_rows(source.get(kind), kind, now.date()) for kind in FIELDS}
        data["periods"][frequency] = {"statements": statements, "analysis": period_analysis(statements)}
    return data


def response_for(record, now=None):
    now = now or timezone.now()
    return {"symbol": record.symbol, "data": record.payload or None, "fetched_at": record.fetched_at,
            "pending": bool(record.refresh_token and record.lease_until > now),
            "stale": bool(record.payload and record.expires_at <= now), "error": record.error,
            "retry_at": record.retry_at, "expires_at": record.expires_at}


def fetch_and_store(symbol, token):
    now = timezone.now()
    try:
        data = normalize(symbol, fetch_yfinance_fundamentals(symbol), timezone.now())
        now = timezone.now()
        ttl = timedelta(minutes=15) if data["supported"] and data["warnings"] else timedelta(hours=6)
        FundamentalSnapshot.objects.filter(pk=symbol, refresh_token=token).update(payload=data, fetched_at=now,
            expires_at=now+ttl, retry_at=now+timedelta(minutes=5), refresh_token="", lease_until=now, error="")
    except Exception:
        # Keep the last good snapshot, with an explicit stale/error state. Never expose upstream URLs or cookies.
        FundamentalSnapshot.objects.filter(pk=symbol, refresh_token=token).update(error="Yahoo fundamentals are unavailable. Check the symbol or retry later.",
            expires_at=now, retry_at=now+timedelta(minutes=10), refresh_token="", lease_until=now)


def _background(symbol, token):
    close_old_connections()
    try:
        fetch_and_store(symbol, token)
    finally:
        close_old_connections()
        _slots.release()


def request_snapshot(symbol, force=False):
    now = timezone.now()
    record, _ = FundamentalSnapshot.objects.get_or_create(symbol=symbol, defaults={"expires_at": now, "retry_at": now, "lease_until": now})
    if record.retry_at > now or record.lease_until > now or (record.payload and record.expires_at > now and not force):
        result = response_for(record)
        if force and record.retry_at > now and record.payload and not record.error:
            result["notice"] = "Refresh cooldown active; showing the saved company snapshot."
        return result
    if not _slots.acquire(blocking=False):
        return {**response_for(record), "notice": "Two company refreshes are already running. Retry shortly."}
    launched = False
    token = uuid.uuid4().hex
    try:
        with transaction.atomic():
            if FundamentalSnapshot.objects.filter(attempted_at__gte=now-timedelta(minutes=1)).count() >= 6:
                return {**response_for(record), "notice": "Company lookup rate limit reached. Retry in a minute."}
            query = FundamentalSnapshot.objects.filter(pk=symbol, lease_until__lte=now, retry_at__lte=now)
            if not force:
                query = query.filter(Q(expires_at__lte=now) | Q(payload={}))
            changed = query.update(refresh_token=token, lease_until=now+timedelta(minutes=10), attempted_at=now, error="")
        if changed:
            thread = threading.Thread(target=_background, args=(symbol, token), name="fundamentals-reader", daemon=True)
            thread.start()
            launched = True
        record.refresh_from_db()
        return response_for(record)
    finally:
        if not launched:
            FundamentalSnapshot.objects.filter(pk=symbol, refresh_token=token).update(refresh_token="", lease_until=now)
            _slots.release()
