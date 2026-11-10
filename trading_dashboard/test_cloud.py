from contextlib import contextmanager
from io import StringIO
import importlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, SimpleTestCase, TestCase, override_settings

from .cloud_config import database, flag, identity, schema_name
from .cloud_db import set_private_schema
from .discord_config import load_discord_webhook

IDENTITY = {"APP_HOSTS":"trade.example.com", "DJANGO_SECRET_KEY":"test-only-" + "aB8xK2zP5qR0vC7nM4sD9tL6jF3wH1"*2}
USERS = ("trader1", "trader2", "trader3", "trader4")
PASSWORDS = {name: f"{name}-OnlyForTests-89-Long!" for name in USERS}


class CloudConfigurationTests(SimpleTestCase):
    def test_explicit_hosts_and_four_users(self):
        cfg = identity({**IDENTITY,"RENDER_EXTERNAL_HOSTNAME":"sample.onrender.com"})
        self.assertEqual(cfg["hosts"], ["trade.example.com","sample.onrender.com"])
        self.assertEqual(cfg["users"], list(USERS))
        self.assertEqual(identity({**IDENTITY,"APP_HOSTS":"", "RENDER_EXTERNAL_HOSTNAME":"sample.onrender.com"})["hosts"], ["sample.onrender.com"])

    def test_missing_weak_and_wildcard_identity_fails_closed(self):
        for env in ({}, {**IDENTITY,"DJANGO_SECRET_KEY":"short"}, {**IDENTITY,"APP_HOSTS":"*"},
                    {**IDENTITY,"APP_HOSTS":"127.0.0.1"}, {**IDENTITY,"SHARING_USERS":"one,two,three"},
                    {**IDENTITY,"SHARING_USERS":"one,two,three,one"}):
            with self.assertRaises(ImproperlyConfigured):
                identity(env)

    def test_postgres_tls_no_sqlite_fallback_and_sanitized_errors(self):
        data = database({"DATABASE_URL":"postgresql://user:private-password@aws-0-us.pooler.supabase.com:5432/postgres?sslmode=disable"})
        self.assertEqual(data["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(data["OPTIONS"]["sslmode"], "require")
        self.assertEqual(data["CONN_MAX_AGE"], 0)
        self.assertTrue(data["DISABLE_SERVER_SIDE_CURSORS"])
        self.assertEqual(database({"DATABASE_URL":"postgres://user:password@db.example.com/db?sslmode=verify-full"})["OPTIONS"]["sslmode"], "verify-full")
        for url in ("", "sqlite:///db.sqlite3", "mysql://user:secret@host/db", "postgres://user:secret@host", "postgres://user:secret@aws.pooler.supabase.com:6543/postgres"):
            with self.assertRaises(ImproperlyConfigured) as error:
                database({"DATABASE_URL":url})
            self.assertNotIn("secret", str(error.exception))

    def test_public_db_cannot_disable_tls(self):
        with self.assertRaises(ImproperlyConfigured):
            database({"DATABASE_URL":"postgres://user:pass@db.example.com/db", "DATABASE_SSL_REQUIRE":"false"})
        data = database({"DATABASE_URL":"postgres://user:pass@postgres.railway.internal/db", "DATABASE_SSL_REQUIRE":"false"})
        self.assertEqual(data["HOST"], "postgres.railway.internal")

    def test_private_schema_and_strict_boolean(self):
        self.assertEqual(schema_name({}), "trading_app")
        for name in ("public","auth","pg_catalog","a; DROP TABLE x", "a,b", ""):
            with self.assertRaises(ImproperlyConfigured):
                schema_name({"DATABASE_SCHEMA":name})
        self.assertFalse(flag("ON", env={}))
        self.assertTrue(flag("ON", env={"ON":"true"}))
        with self.assertRaises(ImproperlyConfigured):
            flag("ON", env={"ON":"yes"})

    @override_settings(DATABASE_SCHEMA="trading_app")
    def test_every_database_session_sets_and_verifies_private_schema(self):
        conn = MagicMock(vendor="postgresql")
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("trading_app",)
        set_private_schema(None, conn)
        cursor.execute.assert_any_call("SELECT set_config('search_path', %s, false)", ["trading_app"])
        cursor.fetchone.return_value = ("public",)
        with self.assertRaises(ImproperlyConfigured):
            set_private_schema(None, conn)

    def test_cloud_ignores_local_credentials_and_requires_explicit_automation(self):
        env = {**IDENTITY, "DATABASE_URL":"postgres://user:pass@db.example.com/db", "DJANGO_SETTINGS_MODULE":"trading_dashboard.cloud_settings",
               "PAPER_SCANNER_ENABLED":"true", "ALPACA_AUTO_PAPER_ENABLED":"true"}
        with patch.dict(os.environ, env, clear=True):
            cloud = importlib.import_module("trading_dashboard.cloud_settings")
            cloud = importlib.reload(cloud)
            self.assertFalse(cloud.PAPER_SCANNER_ENABLED)
            self.assertFalse(cloud.ALPACA_AUTO_PAPER_ENABLED)
            self.assertFalse(cloud.ALPACA_MANUAL_PAPER_ENABLED)
            self.assertFalse(cloud.ALPACA_DATA["enabled"])
            self.assertEqual(cloud.ALPACA_DATA["key"], "")
            self.assertEqual(cloud.DISCORD_WEBHOOK_URL, "")
            self.assertFalse(cloud.DEBUG)
            self.assertTrue(cloud.SESSION_COOKIE_SECURE)

    def test_discord_local_configuration_is_not_read_in_cloud(self):
        with patch.dict(os.environ, {"DJANGO_SETTINGS_MODULE":"trading_dashboard.cloud_settings"}, clear=True), patch.object(Path, "read_text") as read:
            self.assertEqual(load_discord_webhook("/not-used"), "")
            read.assert_not_called()
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL":"environment-value"}, clear=True):
            self.assertEqual(load_discord_webhook("/not-used"), "environment-value")


@override_settings(CLOUD_DEPLOYMENT=True, SHARING_USERS=USERS)
class CloudBootstrapTests(TestCase):
    def test_bootstrap_is_idempotent_and_never_prints_passwords(self):
        output = StringIO()
        with patch.dict(os.environ,{"CLOUD_INITIAL_PASSWORDS":json.dumps(PASSWORDS)}):
            call_command("bootstrap_cloud_users",stdout=output)
        User = get_user_model()
        self.assertEqual(User.objects.count(), 4)
        hashes = dict(User.objects.values_list("username","password"))
        for user in User.objects.all():
            self.assertTrue(user.check_password(PASSWORDS[user.username]))
        with patch.dict(os.environ,{"CLOUD_INITIAL_PASSWORDS":"invalid"}):
            call_command("bootstrap_cloud_users",stdout=output)
        self.assertEqual(dict(User.objects.values_list("username","password")), hashes)
        for password in PASSWORDS.values():
            self.assertNotIn(password, output.getvalue())

    def test_invalid_passwords_do_not_partially_create_users(self):
        for data in ({}, {**PASSWORDS,"trader4":"short"}, {**PASSWORDS,"trader4":PASSWORDS["trader1"]}):
            with patch.dict(os.environ,{"CLOUD_INITIAL_PASSWORDS":json.dumps(data)}):
                with self.assertRaises(CommandError):
                    call_command("bootstrap_cloud_users")
            self.assertFalse(get_user_model().objects.exists())

    @override_settings(CLOUD_DEPLOYMENT=False)
    def test_bootstrap_cannot_modify_local_accounts(self):
        with self.assertRaises(CommandError):
            call_command("bootstrap_cloud_users")


@contextmanager
def cloud_http_settings():
    from . import private_settings as policy
    keys = ("MIDDLEWARE", "AUTHENTICATION_BACKENDS", "SESSION_COOKIE_NAME", "SESSION_COOKIE_SECURE", "SESSION_COOKIE_HTTPONLY", "CSRF_COOKIE_SECURE", "SECURE_PROXY_SSL_HEADER", "SECURE_SSL_REDIRECT", "LOGIN_URL", "TEMPLATES", "CSRF_FAILURE_VIEW", "LOGIN_REDIRECT_URL")
    with override_settings(**{name:getattr(policy,name) for name in keys},
        INSTALLED_APPS=policy.INSTALLED_APPS, CLOUD_DEPLOYMENT=True, SHARING_ENABLED=True,
        SHARING_USERS=USERS, ROOT_URLCONF="trading_dashboard.cloud_urls", ALLOWED_HOSTS=["trade.example.com"],
        CSRF_TRUSTED_ORIGINS=["https://trade.example.com"], SECURE_REDIRECT_EXEMPT=[r"^healthz/$"],
        STORAGES={"default":{"BACKEND":"django.core.files.storage.FileSystemStorage"},"staticfiles":{"BACKEND":"django.contrib.staticfiles.storage.StaticFilesStorage"}},
        AXES_ENABLED=False):
        yield


class CloudHTTPTests(TestCase):
    def test_login_protected_pages_apis_and_public_health_only(self):
        with cloud_http_settings():
            client = Client(HTTP_HOST="trade.example.com", HTTP_X_FORWARDED_PROTO="https")
            self.assertEqual(client.get("/healthz/").json(), {"status":"ok"})
            self.assertEqual(client.get("/accounts/login/").status_code, 200)
            self.assertEqual(client.get("/smart-money/").status_code, 302)
            self.assertEqual(client.get("/api/config/").status_code, 401)
            self.assertEqual(client.get("/api/smart-money/status/").status_code, 401)
            self.assertEqual(client.get("/healthz/", HTTP_HOST="attacker.example").status_code, 400)
            self.assertEqual(Client(HTTP_HOST="trade.example.com").get("/accounts/login/").status_code, 301)
            self.assertEqual(Client(HTTP_HOST="trade.example.com").get("/healthz/").status_code, 200)

    def test_cookie_login_and_csrf_work_with_allowed_host(self):
        get_user_model().objects.create_user(username="trader1", password=PASSWORDS["trader1"])
        with cloud_http_settings():
            client = Client(enforce_csrf_checks=True, HTTP_HOST="trade.example.com", HTTP_X_FORWARDED_PROTO="https")
            self.assertEqual(client.get("/accounts/login/").status_code, 200)
            data = {"username":"trader1", "password":PASSWORDS["trader1"]}
            self.assertEqual(client.post("/accounts/login/", data).status_code, 403)
            data["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
            response = client.post("/accounts/login/", data, HTTP_ORIGIN="https://trade.example.com")
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.cookies["sharing_sessionid"]["secure"])
            self.assertTrue(response.cookies["sharing_sessionid"]["httponly"])
            self.assertEqual(client.get("/smart-money/").status_code, 200)
            self.assertEqual(client.post("/api/smart-money/preferences/", "{}", content_type="application/json").status_code, 403)

    def test_unlisted_user_cannot_access_dashboard(self):
        user = get_user_model().objects.create_user(username="outsider",password="long-test-password-5")
        with cloud_http_settings():
            client = Client(HTTP_HOST="trade.example.com",HTTP_X_FORWARDED_PROTO="https")
            client.force_login(user,backend="django.contrib.auth.backends.ModelBackend")
            self.assertEqual(client.get("/api/config/").status_code, 401)
