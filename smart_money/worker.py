from datetime import timedelta
import logging
import os
import sys
import threading
import time
import uuid

from django.conf import settings
from django.db import OperationalError, ProgrammingError, close_old_connections
from django.db.models import Q
from django.utils import timezone

from .config import options, permission
from .models import CollectionSettings, FlowCursor, MarketDataProvider, OptionsTransaction, WorkerState
from .providers import BudgetExhausted, ProviderError, selected

logger = logging.getLogger(__name__)
_started = False


def claim(owner):
    now = timezone.now()
    WorkerState.objects.get_or_create(key="flow", defaults={"lease_until": now})
    return bool(WorkerState.objects.filter(key="flow").filter(Q(lease_owner=owner) | Q(lease_until__lte=now)).update(
        lease_owner=owner, lease_until=now+timedelta(minutes=5), heartbeat=now))


def guard(owner, revision=None):
    worker = WorkerState.objects.get(key="flow")
    if worker.lease_owner != owner or worker.lease_until <= timezone.now():
        raise ProviderError("Worker lease expired.")
    if revision is not None:
        cfg = CollectionSettings.objects.get(pk=1)
        if not cfg.enabled or cfg.revision != revision or not permission("allow_storage"):
            raise ProviderError("Collection paused or settings changed.")
    if not claim(owner):
        raise ProviderError("Another worker owns collection.")


def run_once(owner):
    from .ingestion import collect_cursor
    from .enrichment import enrich_pending
    from .notifications import deliver, queue_events
    from .sec import SecClient

    cfg = CollectionSettings.objects.filter(pk=1).first()
    if not cfg or not (cfg.enabled or cfg.sec_enabled):
        return
    if not claim(owner):
        return
    now = timezone.now()
    provider_cls = selected()
    if cfg.enabled and provider_cls:
        problem = provider_cls.readiness()
        provider, _ = MarketDataProvider.objects.get_or_create(code=provider_cls.code, defaults={"name": provider_cls.name, "capabilities": provider_cls.capabilities})
        if problem:
            provider.status = problem
            provider.save(update_fields=["status"])
        elif provider.retry_at <= now:
            check = lambda: guard(owner, cfg.revision)
            adapter = provider_cls(check, budget=10)
            for symbol in cfg.symbols:
                FlowCursor.objects.get_or_create(provider=provider, symbol=symbol)
            try:
                for cursor in FlowCursor.objects.filter(provider=provider, symbol__in=cfg.symbols).order_by("checked_at", "symbol"):
                    try:
                        collect_cursor(adapter, provider, cursor, cfg, check)
                    except BudgetExhausted:
                        break
                    except ProviderError as exc:
                        cursor.checked_at, cursor.status = timezone.now(), str(exc)[:240]
                        cursor.save(update_fields=["checked_at", "status"])
                        raise
                provider.status, provider.checked_at, provider.failures = "Connected / bounded watchlist polling", timezone.now(), 0
                provider.save(update_fields=["status", "checked_at", "failures"])
                # Reserve at most two extra historical requests; never fetch in browser requests.
                adapter.remaining = 2
                try:
                    enrich_pending(adapter, check)
                except BudgetExhausted:
                    pass
                recent = OptionsTransaction.objects.filter(provider=provider, received_at__gte=now-timedelta(minutes=5)).select_related("contract")[:500]
                queue_events(recent)
            except ProviderError as exc:
                provider.failures += 1
                provider.status, provider.retry_at = str(exc)[:240], timezone.now()+timedelta(seconds=min(900, 30*2**min(provider.failures, 5)))
                provider.save(update_fields=["failures", "status", "retry_at"])
                logger.warning("Smart Money provider deferred: %s", provider.code)
    if cfg.sec_enabled and (not cfg.sec_checked_at or now-cfg.sec_checked_at > timedelta(hours=1)):
        def sec_guard():
            guard(owner)
            if not CollectionSettings.objects.filter(pk=1, sec_enabled=True, revision=cfg.revision).exists():
                raise ProviderError("SEC collection settings changed.")
        client = SecClient(sec_guard)
        added = 0
        try:
            for cik in cfg.sec_ciks:
                added += client.sync(cik)
            message = f"SEC check complete; {added} filings imported"
        except BudgetExhausted:
            message = f"SEC request budget reached; {added} filings imported; more on next check"
        except (ProviderError, ValueError, KeyError, TypeError):
            message = "SEC import deferred; check contact identity, CIKs and source availability"
        CollectionSettings.objects.filter(pk=1, revision=cfg.revision).update(sec_checked_at=timezone.now(), sec_status=message)
    deliver(lambda: guard(owner))
    WorkerState.objects.filter(key="flow", lease_owner=owner).update(heartbeat=timezone.now(), status="Collection pass completed")


def loop():
    owner = uuid.uuid4().hex
    while True:
        close_old_connections()
        try:
            run_once(owner)
        except (OperationalError, ProgrammingError):
            logger.debug("Smart Money waiting for database availability.")
        except Exception:
            logger.warning("Smart Money pass interrupted; no trading functions were invoked.")
            try:
                WorkerState.objects.filter(key="flow", lease_owner=owner).update(status="Pass interrupted; check provider status")
            except (OperationalError, ProgrammingError):
                pass
        finally:
            close_old_connections()
        time.sleep(60)


def start_once():
    global _started
    if _started or not getattr(settings, "SMART_MONEY_WORKER_ENABLED", False):
        return
    if not ("uvicorn" in sys.argv[0] or "runserver" in sys.argv):
        return
    if "runserver" in sys.argv and "--noreload" not in sys.argv and os.environ.get("RUN_MAIN") != "true":
        return
    _started = True
    threading.Thread(target=loop, name="smart-money-reader", daemon=True).start()
