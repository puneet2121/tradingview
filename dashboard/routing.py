from django.urls import path

from .consumers import AlpacaStockConsumer, HyperliquidCandleConsumer

websocket_urlpatterns = [
    path("ws/alpaca/", AlpacaStockConsumer.as_asgi()),
    path("ws/hyperliquid/", HyperliquidCandleConsumer.as_asgi()),
]
