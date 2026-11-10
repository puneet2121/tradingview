"""Fail-closed configuration for ephemeral web hosts. No local secret fallback."""
import os
import re
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .sharing_config import normalize_domain, validate_config


def flag(name, default=False, env=None):
    env = os.environ if env is None else env
    value = env.get(name, str(default)).lower()
    if value not in {"true", "false"}:
        raise ImproperlyConfigured(f"{name} must be true or false.")
    return value == "true"


def identity(env=None):
    env = os.environ if env is None else env
    hosts = []
    for name in ("APP_HOSTS", "RENDER_EXTERNAL_HOSTNAME", "RAILWAY_PUBLIC_DOMAIN"):
        raw = env.get(name, "")
        values = raw.split(",") if name == "APP_HOSTS" else [raw]
        for value in values:
            if not value.strip():
                continue
            try:
                host = normalize_domain(value.strip())
            except (ValueError, TypeError):
                raise ImproperlyConfigured(
                    f"{name} contains an invalid public hostname. Use a hostname "
                    "such as your-service.onrender.com, without a port, path, "
                    "wildcard, IP address, or surrounding quotes."
                ) from None
            if host not in hosts:
                hosts.append(host)
    if not hosts:
        raise ImproperlyConfigured(
            "APP_HOSTS is missing and no platform public domain is available. "
            "Set APP_HOSTS to your service's public hostname."
        )

    secret = env.get("DJANGO_SECRET_KEY", "")
    if not secret:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is missing. Add it to the service environment "
            "under that exact name, not SECRET_KEY."
        )
    if not isinstance(secret, str) or len(secret) < 50 or len(set(secret)) < 5 or secret.startswith("django-insecure-"):
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is invalid. Use a randomly generated secret of "
            "at least 50 characters, with at least five distinct characters "
            "and without the django-insecure- prefix."
        )

    try:
        cfg = validate_config({"domain": hosts[0], "secret_key": secret,
            "users": [u.strip() for u in env.get("SHARING_USERS", "trader1,trader2,trader3,trader4").split(",")]})
    except (ValueError, TypeError, KeyError):
        raise ImproperlyConfigured(
            "SHARING_USERS must contain exactly four distinct comma-separated "
            "usernames, for example trader1,trader2,trader3,trader4. "
            "Each username must be 1-50 letters, numbers, dots, underscores, or hyphens."
        ) from None
    return {**cfg, "hosts": hosts}


def database(env=None):
    env = os.environ if env is None else env
    raw = env.get("DATABASE_URL", "")
    if not raw or not raw.strip():
        raise ImproperlyConfigured(
            "DATABASE_URL is missing. Add the complete PostgreSQL URI from "
            "Supabase Connect > Session pooler to the service environment."
        )
    try:
        parts = urlsplit(raw)
        host, port = parts.hostname, parts.port
    except (ValueError, TypeError):
        raise ImproperlyConfigured(
            "DATABASE_URL is malformed. Copy the PostgreSQL URI without "
            "surrounding quotes, use a numeric port, and URL-encode the password."
        ) from None
    if parts.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured(
            "DATABASE_URL must start with postgresql:// or postgres://. "
            "Use a database connection URI, not the Supabase HTTPS project URL "
            "or an API key. Paste only the URI, without quotes or a DATABASE_URL= prefix."
        )
    if not host or not parts.path.strip("/"):
        raise ImproperlyConfigured(
            "DATABASE_URL must include the database hostname and database name. "
            "Copy the complete Supabase Session pooler URI, including /postgres."
        )
    if host.endswith(".pooler.supabase.com") and port != 5432:
        raise ImproperlyConfigured(
            "DATABASE_URL must use Supabase Session pooler port 5432. "
            "Copy the Session pooler URI; transaction pooling on port 6543 "
            "is not supported by this configuration."
        )
    secure = flag("DATABASE_SSL_REQUIRE", True, env)
    if not secure and not host.endswith(".railway.internal"):
        raise ImproperlyConfigured(
            "DATABASE_SSL_REQUIRE must be true for Supabase and other public "
            "database connections. Only a Railway private-network database may disable TLS."
        )
    schema_name(env)
    try:
        result = dj_database_url.parse(raw, conn_max_age=0, conn_health_checks=True)
    except (ValueError, TypeError, ImproperlyConfigured):
        raise ImproperlyConfigured(
            "DATABASE_URL could not be parsed. Copy the complete PostgreSQL "
            "connection URI and URL-encode special characters in the database password."
        ) from None
    if secure:
        mode = result.setdefault("OPTIONS", {}).get("sslmode")
        result["OPTIONS"]["sslmode"] = mode if mode in {"verify-ca", "verify-full"} else "require"
    result.setdefault("OPTIONS", {}).pop("options", None)
    result.setdefault("OPTIONS", {})["connect_timeout"] = 10
    result["DISABLE_SERVER_SIDE_CURSORS"] = True
    return result


def schema_name(env=None):
    env = os.environ if env is None else env
    schema = env.get("DATABASE_SCHEMA", "trading_app")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) or schema in {"public", "auth", "storage", "extensions", "information_schema", "graphql", "graphql_public"} or schema.startswith("pg_"):
        raise ImproperlyConfigured("DATABASE_SCHEMA must name a private application schema, such as trading_app.")
    return schema
