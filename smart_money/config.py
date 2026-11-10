from django.conf import settings

PREMIUMS = (50000, 100000, 250000, 500000, 1000000, 5000000)
DEFAULT_SYMBOLS = ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "IWM", "SOFI", "SPY", "QQQ", "ABNB", "MCD", "WMT", "APP", "HOOD", "MU"]


def options():
    return getattr(settings, "SMART_MONEY", {})


def permission(name):
    return options().get(name) is True


def can_view_data():
    return not getattr(settings, "SHARING_ENABLED", False) or permission("allow_sharing")
