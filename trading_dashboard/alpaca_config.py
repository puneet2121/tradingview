"""Read-only data credentials are intentionally separate from order credentials."""
import json
import os
from pathlib import Path


def load_alpaca_config(base_dir):
    path = Path(base_dir) / ".local-sharing" / "alpaca-data.json"
    saved = {}
    if os.environ.get("DJANGO_SETTINGS_MODULE") != "trading_dashboard.cloud_settings" and path.exists():
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("Alpaca data credentials must be a private file (chmod 600).")
        try:
            saved = json.loads(path.read_text())
            if not isinstance(saved, dict):
                raise ValueError
        except (ValueError, OSError):
            raise ValueError("Invalid private Alpaca data configuration.") from None
    stock_feed = os.environ.get("ALPACA_STOCK_FEED", saved.get("stock_feed", "iex"))
    option_feed = os.environ.get("ALPACA_OPTION_FEED", saved.get("option_feed", "indicative"))
    if stock_feed not in {"iex", "sip"} or option_feed not in {"indicative", "opra"}:
        raise ValueError("Alpaca feeds must be iex/sip and indicative/opra.")
    return {
        "enabled": os.environ.get("ALPACA_DATA_ENABLED", str(saved.get("enabled", False))).lower() == "true",
        "key": os.environ.get("ALPACA_DATA_API_KEY", saved.get("key", "")),
        "secret": os.environ.get("ALPACA_DATA_SECRET_KEY", saved.get("secret", "")),
        "stock_feed": stock_feed,
        "option_feed": option_feed,
    }
