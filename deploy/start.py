"""Migrate an explicitly configured cloud DB, provision logins, then start one ASGI worker."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    os.chdir(ROOT)
    os.environ["DJANGO_SETTINGS_MODULE"] = "trading_dashboard.cloud_settings"
    try:
        port = int(os.environ.get("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit("PORT must be an integer between 1 and 65535.") from None
    import django
    from django.core.exceptions import ImproperlyConfigured
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.db import DatabaseError, connection, connections

    try:
        django.setup()
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_schema()")
            if cursor.fetchone()[0] != os.environ.get("DATABASE_SCHEMA", "trading_app"):
                raise CommandError("Create the private database schema before deploying.")
        call_command("migrate", interactive=False)
        call_command("bootstrap_cloud_users")
        connections.close_all()
    except (ImproperlyConfigured, CommandError, DatabaseError) as exc:
        raise SystemExit(f"Cloud startup failed ({type(exc).__name__}). Check the PostgreSQL connection, private schema, migrations, and initial login configuration. No web server was started.") from None
    os.environ.pop("CLOUD_INITIAL_PASSWORDS", None)
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "trading_dashboard.asgi:application",
        "--host", "0.0.0.0", "--port", str(port), "--workers", "1", "--no-proxy-headers"])


if __name__ == "__main__":
    main()
