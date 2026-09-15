import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "local-development-only"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "channels",
    "dashboard.apps.DashboardConfig",
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
PAPER_SCANNER_ENABLED = os.environ.get("PAPER_SCANNER_ENABLED", "true").lower() == "true"
PAPER_SCANNER_INTERVAL_SECONDS = 60

ALPACA_PAPER_ENABLED = os.environ.get("ALPACA_PAPER_ENABLED", "false").lower() == "true"
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER_TRADING_BASE_URL = os.environ.get(
    "ALPACA_PAPER_TRADING_BASE_URL",
    "https://paper-api.alpaca.markets",
)
ALPACA_ORDER_TIME_IN_FORCE = os.environ.get("ALPACA_ORDER_TIME_IN_FORCE", "day")

DISCORD_ALERTS_ENABLED = os.environ.get("DISCORD_ALERTS_ENABLED", "true")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1546922693952016485/zaTewCRMTkbw8-UurJeeIFPQA6dyBnMLNBycZEPqqLjFRrE1x8TQlFoV2-clBeQHA0oA")
DISCORD_ALERT_STRATEGY_ONLY = os.environ.get("DISCORD_ALERT_STRATEGY_ONLY", "true").lower() == "true"
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
