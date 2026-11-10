import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "local-development-only"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "channels",
    "dashboard.apps.DashboardConfig",
    "smart_money.apps.SmartMoneyConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "trading_dashboard.urls"
ASGI_APPLICATION = "trading_dashboard.asgi.application"
WSGI_APPLICATION = "trading_dashboard.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = "America/Los_Angeles"
USE_TZ = True
SMART_MONEY = {
    "provider": os.environ.get("SMART_MONEY_PROVIDER", "none"),
    "option_feed": os.environ.get("SMART_MONEY_OPTION_FEED", "opra"),
    "allow_storage": os.environ.get("SMART_MONEY_ALLOW_STORAGE", "false").lower() == "true",
    "allow_sharing": os.environ.get("SMART_MONEY_ALLOW_SHARING", "false").lower() == "true",
    "allow_fundamentals_sharing": os.environ.get("SMART_MONEY_ALLOW_FUNDAMENTALS_SHARING", "false").lower() == "true",
    "allow_discord": os.environ.get("SMART_MONEY_ALLOW_DISCORD", "false").lower() == "true",
    "delay_seconds": int(os.environ.get("SMART_MONEY_DELAY_SECONDS", "900")),
    "sec_user_agent": os.environ.get("SEC_USER_AGENT", ""),
}
SMART_MONEY_WORKER_ENABLED = os.environ.get("SMART_MONEY_WORKER_ENABLED", "true").lower() == "true"
PAPER_SCANNER_ENABLED = os.environ.get("PAPER_SCANNER_ENABLED", "true").lower() == "true"
PAPER_SCANNER_INTERVAL_SECONDS = 60
ALERT_SCANNER_ENABLED = os.environ.get("ALERT_SCANNER_ENABLED", "true").lower() == "true"

ALPACA_PAPER_ENABLED = os.environ.get("ALPACA_PAPER_ENABLED", "false").lower() == "true"
from .alpaca_config import load_alpaca_config

ALPACA_DATA = load_alpaca_config(BASE_DIR)
ALPACA_MANUAL_PAPER_ENABLED = os.environ.get("ALPACA_MANUAL_PAPER_ENABLED", "true").lower() == "true"
ALPACA_AUTO_PAPER_ENABLED = os.environ.get("ALPACA_AUTO_PAPER_ENABLED", "true").lower() == "true"
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER_TRADING_BASE_URL = os.environ.get(
    "ALPACA_PAPER_TRADING_BASE_URL",
    "https://paper-api.alpaca.markets",
)
ALPACA_ORDER_TIME_IN_FORCE = os.environ.get("ALPACA_ORDER_TIME_IN_FORCE", "day")

DISCORD_ALERTS_ENABLED = os.environ.get("DISCORD_ALERTS_ENABLED", "true")
from .discord_config import load_discord_webhook

DISCORD_WEBHOOK_URL = load_discord_webhook(BASE_DIR)
DISCORD_ALERT_STRATEGY_ONLY = os.environ.get("DISCORD_ALERT_STRATEGY_ONLY", "true").lower() == "true"
DISCORD_REGULAR_MARKET_HOURS_ONLY = os.environ.get("DISCORD_REGULAR_MARKET_HOURS_ONLY", "true").lower() == "true"
DISCORD_ALERT_USERNAME = os.environ.get("DISCORD_ALERT_USERNAME", "Trading Dashboard")
DISCORD_ALERT_TIMEOUT_SECONDS = float(os.environ.get("DISCORD_ALERT_TIMEOUT_SECONDS", "5"))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# source .venv/bin/activate
# python3 -m uvicorn trading_dashboard.asgi:application --host 0.0.0.0 --port 8000
