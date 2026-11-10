from datetime import date, timedelta
import hashlib
import json

from django.db import transaction
from django.utils import timezone

from .analytics import classify, directional, unusual_score
from .config import options
from .models import OptionContract, OptionsTransaction
from .providers import ProviderError, decimal, stored_decimal, timestamp


def ingest_page(provider, catalog, payload, state, min_premium, now):
    counts = dict(state.get("occurrences", {}))
    contracts = {row["symbol"]: row for row in catalog}
    added = []
    if not isinstance(payload, dict):
        raise ProviderError("Provider returned an invalid transaction page.")
    for symbol, prints in payload.items():
        if symbol not in contracts or not isinstance(prints, list):
            raise ProviderError("Provider returned an unwatched contract.")
        data = contracts[symbol]
        contract = None
        for item in prints:
            if not isinstance(item, dict):
                raise ProviderError("Provider returned an invalid transaction.")
            event_at = timestamp(item.get("t"))
            if not timestamp(state["start"]) <= event_at <= timestamp(state["end"]) or event_at > now + timedelta(seconds=5):
                raise ProviderError("Provider trade is outside the requested time window.")
            price = stored_decimal(item.get("p"), 14)
            quantity = decimal(item.get("s"), positive=True)
            if quantity != quantity.to_integral_value() or quantity > 1000000000:
                raise ProviderError("Invalid provider contract quantity.")
            multiplier = stored_decimal(data.get("multiplier"), 10, nullable=True)
            premium = price * quantity * multiplier if multiplier is not None else None
            if premium is not None:
                stored_decimal(premium, 18)
            if premium is not None and premium < min_premium:
                continue
            # OPRA's Alpaca schema lacks IDs. Preserve identical prints by occurrence,
            # including page-boundary repetitions, within a fixed replayable window.
            fingerprint = hashlib.sha256(json.dumps([symbol, item["t"], str(price), str(quantity), item.get("x"), item.get("c")], separators=(",", ":")).encode()).hexdigest()
            ordinal = counts.get(fingerprint, 0)
            counts[fingerprint] = ordinal + 1
            key = f"{fingerprint}:{ordinal}"
            if OptionsTransaction.objects.filter(provider=provider, event_key=key).exists():
                continue
            if contract is None:
                contract, _ = OptionContract.objects.get_or_create(provider=provider, symbol=symbol, defaults={
                    **{k: v for k, v in data.items() if k != "symbol"}, "strike": decimal(data["strike"], positive=True),
                    "multiplier": multiplier, "expiration": date.fromisoformat(data["expiration"]), "metadata_at": now})
            classification, explanation = classify(price, None, None, None, event_at)
            repeat = OptionsTransaction.objects.filter(provider=provider, contract=contract, excluded=False,
                trade_at__gte=event_at-timedelta(minutes=10), trade_at__lte=event_at, received_at__lte=now).count() + 1
            score, evidence = unusual_score(premium, repeat, classification)
            evidence.update({"execution": explanation, "multiplier_source": "Alpaca contract size" if multiplier else "Unavailable",
                             "identity": "Timestamp/price/size/exchange/condition plus occurrence; no provider trade ID",
                             "corrections": "No correction linkage in this adapter; provider conditions retained verbatim"})
            event = OptionsTransaction.objects.create(provider=provider, contract=contract, event_key=key, ticker=data["underlying"],
                option_type=data["option_type"], trade_at=event_at, timestamp_raw=item["t"], received_at=now,
                price=price, quantity=int(quantity), multiplier=multiplier, premium=premium,
                exchange=str(item.get("x") or "")[:32], condition=str(item.get("c") or "")[:100],
                classification=classification, direction=directional(data["option_type"], classification), score=score, evidence=evidence,
                delayed=bool(options().get("delay_seconds", 900)))
            added.append(event)
    return added, counts


def collect_cursor(adapter, provider, cursor, cfg, guard):
    now = timezone.now()
    state = dict(cursor.state) if cursor.revision == cfg.revision else {}
    if not state.get("catalog"):
        catalog, total = adapter.catalog(cursor.symbol, now)
        if not catalog:
            raise ProviderError("No option contracts returned for this ticker.")
        state.update(catalog=catalog, catalog_total=total, catalog_at=now.isoformat())
    end = now - timedelta(seconds=options().get("delay_seconds", 900) + 5)
    if not state.get("end"):
        last = timestamp(state["last_end"]) if state.get("last_end") else end - timedelta(minutes=2)
        # Keep the overlap whole-second aligned so equal-time occurrences are stable.
        state.update(start=(last-timedelta(seconds=60)).replace(microsecond=0).isoformat(), end=end.isoformat(), occurrences={}, tokens=[])
    reply = adapter.trades([row["symbol"] for row in state["catalog"]], state["start"], state["end"], state.get("page"))
    token = reply.get("next_page_token")
    if token and (token in state.get("tokens", []) or len(state.get("tokens", [])) >= 100):
        raise ProviderError("Trade pagination repeated or exceeded the page safety limit; cursor retained.")
    guard()
    with transaction.atomic():
        added, counts = ingest_page(provider, state["catalog"], reply.get("trades") or {}, state, cfg.min_premium, timezone.now())
        state["occurrences"] = counts
        if token:
            state["tokens"] = [*state.get("tokens", []), token]
            state["page"] = token
        else:
            state["last_end"] = state.pop("end")
            for key in ("start", "page", "tokens", "occurrences"):
                state.pop(key, None)
            if now - timestamp(state["catalog_at"]) > timedelta(hours=6):
                state.pop("catalog", None)
        cursor.state, cursor.revision, cursor.checked_at = state, cfg.revision, timezone.now()
        cursor.status = f"{len(state.get('catalog', []))}/{state.get('catalog_total', 0)} contracts; {'backlog: more pages pending' if token else 'window complete'}"
        cursor.save(update_fields=["state", "revision", "checked_at", "status"])
    return added
