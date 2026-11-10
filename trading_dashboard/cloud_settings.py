"""Private Render/Railway dashboard; scanners require a deliberate cutover."""
from .private_settings import *  # noqa: F403
from .cloud_config import database, flag, identity, schema_name

_identity = identity()
CLOUD_DEPLOYMENT = True
SECRET_KEY = _identity["secret_key"]
SHARING_USERS = tuple(_identity["users"])
ALLOWED_HOSTS = _identity["hosts"]
CSRF_TRUSTED_ORIGINS = ["https://" + host for host in ALLOWED_HOSTS]
if os.environ.get("RAILWAY_ENVIRONMENT_ID"):
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, "healthcheck.railway.app"]
ROOT_URLCONF = "trading_dashboard.cloud_urls"
DATABASES = {"default": database()}
DATABASE_SCHEMA = schema_name()
INSTALLED_APPS = [*INSTALLED_APPS, "trading_dashboard.cloud_db.CloudDatabaseConfig"]
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]

PAPER_SCANNER_ENABLED = flag("CLOUD_SCANNERS_ENABLED") and flag("PAPER_SCANNER_ENABLED")
ALERT_SCANNER_ENABLED = flag("CLOUD_SCANNERS_ENABLED") and flag("ALERT_SCANNER_ENABLED")
SMART_MONEY_WORKER_ENABLED = flag("CLOUD_SCANNERS_ENABLED") and flag("SMART_MONEY_WORKER_ENABLED")
ALPACA_AUTO_PAPER_ENABLED = flag("CLOUD_SCANNERS_ENABLED") and flag("ALPACA_AUTO_PAPER_ENABLED")
ALPACA_MANUAL_PAPER_ENABLED = flag("ALPACA_MANUAL_PAPER_ENABLED")
ALPACA_PAPER_ENABLED = False
ALPACA_API_KEY = ""
ALPACA_SECRET_KEY = ""
ALPACA_PAPER_TRADING_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA = {
    "enabled": flag("ALPACA_DATA_ENABLED"),
    "key": os.environ.get("ALPACA_DATA_API_KEY", ""),
    "secret": os.environ.get("ALPACA_DATA_SECRET_KEY", ""),
    "stock_feed": os.environ.get("ALPACA_STOCK_FEED", "iex"),
    "option_feed": os.environ.get("ALPACA_OPTION_FEED", "indicative"),
}
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
DISCORD_ALERTS_ENABLED = flag("DISCORD_ALERTS_ENABLED")
