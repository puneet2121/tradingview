"""Keep the optional local Discord secret outside source control."""
import json
import os
from pathlib import Path


def load_discord_webhook(base_dir):
    if "DISCORD_WEBHOOK_URL" in os.environ:
        return os.environ["DISCORD_WEBHOOK_URL"]
    if os.environ.get("DJANGO_SETTINGS_MODULE") == "trading_dashboard.cloud_settings":
        return ""
    path = Path(base_dir) / ".local-sharing" / "discord.json"
    if not path.exists():
        return ""
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("Discord configuration must be a private file (chmod 600).")
    try:
        value = json.loads(path.read_text())["webhook_url"]
        if not isinstance(value, str):
            raise ValueError
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Invalid private Discord configuration.") from None
