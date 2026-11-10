from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.backends.signals import connection_created


def set_private_schema(sender, connection, **kwargs):
    if connection.vendor != "postgresql":
        raise ImproperlyConfigured("Cloud storage must use PostgreSQL.")
    # Session poolers may ignore startup options; set and verify each actual session.
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('search_path', %s, false)", [settings.DATABASE_SCHEMA])
        cursor.execute("SELECT current_schema()")
        if cursor.fetchone()[0] != settings.DATABASE_SCHEMA:
            raise ImproperlyConfigured("Create the private database schema before deploying. Refusing to use public tables.")


class CloudDatabaseConfig(AppConfig):
    name = "trading_dashboard"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        connection_created.connect(set_private_schema, dispatch_uid="cloud-private-schema")
