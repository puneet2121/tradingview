from django.urls import path

from .consumers import HyperliquidCandleConsumer

websocket_urlpatterns = [
    path("ws/hyperliquid/", HyperliquidCandleConsumer.as_asgi()),
]
