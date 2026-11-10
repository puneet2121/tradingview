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
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.db import DatabaseError, connection, connections
    from trading_dashboard.cloud_errors import CloudBootstrapError, CloudConfigurationError

    stage = "configuration"
    try:
        print(f"Cloud startup: {stage}", flush=True)
        django.setup()
        stage = "database/schema"
        print(f"Cloud startup: {stage}", flush=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_schema()")
            if cursor.fetchone()[0] != settings.DATABASE_SCHEMA:
                raise CloudConfigurationError(
                    "The database session is not using DATABASE_SCHEMA. Create the "
                    "private schema (default: trading_app), check the database role's "
                    "schema permissions, and use Supabase Session pooler port 5432."
                )
        stage = "migrations"
        print(f"Cloud startup: {stage}", flush=True)
        call_command("migrate", interactive=False)
        stage = "initial logins"
        print(f"Cloud startup: {stage}", flush=True)
        call_command("bootstrap_cloud_users")
        connections.close_all()
    except (ImproperlyConfigured, CommandError, DatabaseError) as exc:
        if isinstance(exc, (CloudConfigurationError, CloudBootstrapError)):
            detail = str(exc)
        else:
            # Driver/framework errors can embed credentials; only our typed messages are public.
            detail = {
                "configuration": "Check the cloud environment variables and requirements-cloud.txt dependencies.",
                "database/schema": "Check DATABASE_URL credentials, Session pooler port 5432, TLS, network access, and the private schema's permissions.",
                "migrations": "Check the private schema's USAGE/CREATE permissions and the migration progress above. No initial logins were created.",
                "initial logins": "Check database access and CLOUD_INITIAL_PASSWORDS for the four SHARING_USERS logins.",
            }[stage]
        raise SystemExit(
            f"Cloud startup failed during {stage} ({type(exc).__name__}). "
            f"{detail} No web server was started."
        ) from None
    os.environ.pop("CLOUD_INITIAL_PASSWORDS", None)
    print("Cloud startup: starting web server", flush=True)
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "trading_dashboard.asgi:application",
        "--host", "0.0.0.0", "--port", str(port), "--workers", "1", "--no-proxy-headers"])


if __name__ == "__main__":
    main()
