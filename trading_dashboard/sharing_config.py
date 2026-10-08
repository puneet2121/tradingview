import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("SHARING_CONFIG", ROOT / ".local-sharing/config.json"))


def normalize_domain(value):
    parts = urlsplit(value if "://" in value else "https://" + value)
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or parts.netloc != host
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or len(host) > 253
        or "." not in host
        or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split("."))
        or host.replace(".", "").isdigit()
    ):
        raise ValueError("Enter your exact public ngrok hostname, without a port or path.")
    return host


def validate_config(config):
    config["domain"] = normalize_domain(config["domain"])
    if not isinstance(config["secret_key"], str) or len(config["secret_key"]) < 50:
        raise ValueError("Invalid sharing secret.")
    users = config["users"]
    if (
        not isinstance(users, list) or len(users) != 4
        or not all(isinstance(user, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,50}", user) for user in users)
        or len(set(users)) != 4
    ):
        raise ValueError("Exactly four distinct usernames are required.")
    return config


def load_config():
    try:
        return validate_config(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ImproperlyConfigured("Run .venv/bin/python share.py setup --domain YOUR_NGROK_DOMAIN first.") from exc
