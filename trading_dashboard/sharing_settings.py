from datetime import timedelta

from .settings import *  # noqa: F403
from .sharing_config import load_config


_sharing = load_config()
SHARING_ENABLED = True
SHARING_USERS = tuple(_sharing["users"])
SECRET_KEY = _sharing["secret_key"]
DEBUG = False
ALLOWED_HOSTS = [_sharing["domain"]]
CSRF_TRUSTED_ORIGINS = ["https://" + _sharing["domain"]]
ROOT_URLCONF = "trading_dashboard.sharing_urls"

INSTALLED_APPS = [
    *INSTALLED_APPS,
    "axes",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "trading_dashboard.sharing_auth.SharingLoginMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]
TEMPLATES = [{**TEMPLATES[0], "OPTIONS": {"context_processors": [
    "django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth",
]}}]
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = ["username"]
AXES_RESET_ON_SUCCESS = True
AXES_CLIENT_IP_CALLABLE = "trading_dashboard.sharing_auth.no_client_ip"
# Four fixed accounts are throttled regardless of IP, cookie, or User-Agent.
# IP throttling here would lock every user behind the same local ngrok proxy.
SILENCED_SYSTEM_CHECKS = ["axes.W006"]
AXES_LOCKOUT_TEMPLATE = "registration/locked_out.html"
AXES_SENSITIVE_PARAMETERS = ["username", "ip_address"]
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = LOGIN_URL
SESSION_COOKIE_NAME = "sharing_sessionid"
SESSION_COOKIE_AGE = 12 * 60 * 60
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_FAILURE_VIEW = "trading_dashboard.sharing_auth.csrf_failure"
# Only the loopback-bound server behind the ngrok HTTPS endpoint uses this profile.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
WHITENOISE_ALLOW_ALL_ORIGINS = False
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATABASES = {"default": {**DATABASES["default"], "OPTIONS": {"timeout": 20}}}

# Sharing must not silently start another scanner or enable broker execution.
PAPER_SCANNER_ENABLED = os.environ.get("SHARING_SCANNER_ENABLED", "false").lower() == "true"
ALPACA_PAPER_ENABLED = False
