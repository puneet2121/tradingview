from .private_settings import *  # noqa: F403
from .sharing_config import load_config

_sharing = load_config()
SHARING_USERS = tuple(_sharing["users"])
SECRET_KEY = _sharing["secret_key"]
ALLOWED_HOSTS = [_sharing["domain"]]
CSRF_TRUSTED_ORIGINS = ["https://" + _sharing["domain"]]
DATABASES = {"default": {**DATABASES["default"], "OPTIONS": {"timeout": 20}}}

# Sharing must not start a second local paper scanner.
PAPER_SCANNER_ENABLED = os.environ.get("SHARING_SCANNER_ENABLED", "false").lower() == "true"
