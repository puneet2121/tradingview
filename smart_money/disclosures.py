from datetime import date, timedelta
from django.db.models import Sum
from .models import SecFiling


def holding_changes(filing):
    """Compare complete adjacent original quarters, never assume an amendment replaces one."""
    unavailable = {"status": "Select a complete original 13F filing with an adjacent imported quarter.", "rows": []}
    if not filing or filing.form != "13F-HR" or not filing.report_date:
        return unavailable
    quarter_start = date(filing.report_date.year, ((filing.report_date.month-1)//3)*3+1, 1)
    previous_date = quarter_start-timedelta(days=1)
    previous = SecFiling.objects.filter(cik=filing.cik, form="13F-HR", report_date=previous_date).order_by("-filed_at").first()
    if not previous:
        return unavailable
    if SecFiling.objects.filter(cik=filing.cik, form="13F-HR/A", report_date__in=[filing.report_date, previous.report_date]).exists():
        return {"status": "An amendment affects these quarters. Review the original filings; automatic comparison withheld.", "rows": []}
    keys = ("cusip", "security_class", "put_call", "shares_type")
    def positions(record):
        return {tuple(row[key] for key in keys): row for row in record.holdings.values(*keys).annotate(shares=Sum("shares"))}
    before, after = positions(previous), positions(filing)
    rows = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key, {}).get("shares", 0), after.get(key, {}).get("shares", 0)
        status = "NEW" if key not in before else "CLOSED" if key not in after else "INCREASED" if new > old else "REDUCED" if new < old else "UNCHANGED"
        rows.append({**dict(zip(keys, key)), "previous": old, "current": new, "change": new-old, "status": status})
    return {"status": "Reported share changes, not inferred trades. Splits, CUSIP changes and transfers can affect comparisons.",
            "previous_period": previous.report_date, "current_period": filing.report_date, "count": len(rows), "rows": rows}
