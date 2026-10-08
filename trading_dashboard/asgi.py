import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_dashboard.settings")

http_application = get_asgi_application()

from django.conf import settings
from dashboard.routing import websocket_urlpatterns

websocket_application = URLRouter(websocket_urlpatterns)
if getattr(settings, "SHARING_ENABLED", False):
    from channels.auth import AuthMiddlewareStack
    from channels.security.websocket import OriginValidator

    websocket_application = OriginValidator(
        AuthMiddlewareStack(websocket_application), settings.CSRF_TRUSTED_ORIGINS
    )

application = ProtocolTypeRouter(
    {
        "http": http_application,
        "websocket": websocket_application,
    }
)
