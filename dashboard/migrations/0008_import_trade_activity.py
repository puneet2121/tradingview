from datetime import date, datetime
from decimal import Decimal

from django.db import migrations


def import_existing_trades(apps, schema_editor):
    Activity = apps.get_model("dashboard", "TradeActivity")
    database = schema_editor.connection.alias
    # Import snapshots, without claiming to know who triggered older actions.
    for model_name, asset in [("PaperTrade", "underlying"), ("OptionPaperTrade", "option")]:
        Trade = apps.get_model("dashboard", model_name)
        for trade in Trade.objects.using(database).order_by("pk").iterator():
            snapshot = {}
            for field in Trade._meta.fields:
                value = getattr(trade, field.attname)
                if isinstance(value, Decimal):
                    value = float(value)
                elif isinstance(value, (date, datetime)):
                    value = value.isoformat()
                snapshot[field.name] = value
            Activity.objects.using(database).create(
                actor="UNKNOWN", action="IMPORT", reason="LEGACY_RECORD",
                asset=asset, trade_id=trade.pk, strategy=trade.strategy, snapshot=snapshot,
            )


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0007_tradeactivity")]
    operations = [migrations.RunPython(import_existing_trades, migrations.RunPython.noop)]
