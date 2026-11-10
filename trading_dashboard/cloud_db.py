from django.apps import AppConfig
from django.conf import settings
from django.db.backends.signals import connection_created

from .cloud_errors import CloudConfigurationError


def set_private_schema(sender, connection, **kwargs):
    if connection.vendor != "postgresql":
        raise CloudConfigurationError("Cloud storage must use PostgreSQL.")
    # Session poolers may ignore startup options; set and verify each actual session.
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('search_path', %s, false)", [settings.DATABASE_SCHEMA])
        cursor.execute("SELECT current_schema()")
        if cursor.fetchone()[0] != settings.DATABASE_SCHEMA:
            raise CloudConfigurationError(
                "The schema selected by DATABASE_SCHEMA is missing or not accessible "
                "to the database user. Create the private schema (default: trading_app) "
                "in the Supabase SQL Editor and grant the connecting database role "
                "USAGE and CREATE on it. Refusing to use public tables."
            )


class CloudDatabaseConfig(AppConfig):
    name = "trading_dashboard"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        connection_created.connect(set_private_schema, dispatch_uid="cloud-private-schema")
