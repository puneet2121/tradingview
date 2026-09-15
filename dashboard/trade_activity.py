from django.db import transaction

from .discord_alerts import dispatch_trade_activity_alert
from .models import OptionPaperTrade, Strategy, TradeActivity


def record_trade_activity(trade, action, actor, reason):
    is_option = isinstance(trade, OptionPaperTrade)
    if is_option:
        from .options_paper import serialize_trade
    else:
        from .paper_trading import serialize_trade

    snapshot = serialize_trade(trade)
    if actor == "STRATEGY":
        strategy = Strategy.objects.filter(name=trade.strategy).first()
        if strategy:
            snapshot["strategy_rules"] = strategy.rule_text
            snapshot["strategy_risk_percent"] = float(strategy.risk_percent)
            snapshot["strategy_stop_loss_percent"] = float(strategy.option_stop_loss_percent) if strategy.option_stop_loss_percent is not None else None
            snapshot["strategy_take_profit_percent"] = float(strategy.option_take_profit_percent) if strategy.option_take_profit_percent is not None else None
    event = TradeActivity.objects.create(
        actor=actor,
        action=action,
        reason=reason,
        asset="option" if is_option else "underlying",
        trade_id=trade.pk,
        strategy=trade.strategy if actor == "STRATEGY" else "",
        snapshot=snapshot,
    )
    transaction.on_commit(lambda event_id=event.pk: dispatch_trade_activity_alert(event_id))
    return event


def record_reset(asset, count):
    TradeActivity.objects.create(
        actor="USER", action="RESET", reason="ACCOUNT_RESET", asset=asset,
        snapshot={"deleted_trades": count},
    )


def activity_page(before=None, limit=50):
    events = TradeActivity.objects.all()
    if before is not None:
        events = events.filter(id__lt=before)
    page = list(events[:limit + 1])
    return {
        "events": [
            {
                "id": event.pk,
                "recorded_at": event.recorded_at.isoformat(),
                "actor": event.actor,
                "action": event.action,
                "reason": event.reason,
                "asset": event.asset,
                "trade_id": event.trade_id,
                "strategy": event.strategy,
                "snapshot": event.snapshot,
            }
            for event in page[:limit]
        ],
        "next_before": page[limit - 1].pk if len(page) > limit else None,
    }
