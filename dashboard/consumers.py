import asyncio
import json
import ssl

import certifi
import websockets
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .data_source import (
    HYPERLIQUID_WS_URL,
    build_hyperliquid_candle_subscription,
    get_symbol,
    get_timeframe,
    normalize_hyperliquid_candle,
    get_chart_symbol,
    validate_alpaca_symbol,
)
from . import alpaca
from .alpaca_stream import stock_stream


class HyperliquidCandleConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.stream_task = None
        if getattr(settings, "SHARING_ENABLED", False):
            user = self.scope.get("user")
            if not user or not user.is_authenticated or user.get_username() not in settings.SHARING_USERS:
                await self.close(code=4401)
                return
        await self.accept()

    async def disconnect(self, close_code):
        if self.stream_task:
            self.stream_task.cancel()
            await asyncio.gather(self.stream_task, return_exceptions=True)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        try:
            message = json.loads(text_data)
            symbol = str(message["symbol"])
            timeframe = str(message["timeframe"])
            symbol_config = get_symbol(symbol)
            get_timeframe(timeframe)
            if symbol_config.provider != "hyperliquid":
                await self.send_json({"type": "error", "message": "Symbol is not a Hyperliquid market."})
                return
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            await self.send_json({"type": "error", "message": str(exc)})
            return

        if self.stream_task:
            self.stream_task.cancel()
            await asyncio.gather(self.stream_task, return_exceptions=True)

        self.stream_task = asyncio.create_task(self._stream_candles(symbol, timeframe))

    async def _stream_candles(self, symbol: str, timeframe: str):
        subscription = build_hyperliquid_candle_subscription(symbol, timeframe)
        backoff = 1

        while True:
            try:
                ssl_context = ssl.create_default_context(cafile=certifi.where())
                async with websockets.connect(
                    HYPERLIQUID_WS_URL,
                    ping_interval=20,
                    ping_timeout=20,
                    ssl=ssl_context,
                ) as socket:
                    await socket.send(json.dumps(subscription))
                    await self.send_json({"type": "status", "status": "connected", "symbol": symbol})
                    backoff = 1

                    async for raw_message in socket:
                        message = json.loads(raw_message)
                        if message.get("channel") != "candle":
                            continue

                        candles = message.get("data", [])
                        if isinstance(candles, dict):
                            candles = [candles]

                        for candle in candles:
                            await self.send_json({"type": "bar", "bar": normalize_hyperliquid_candle(candle)})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.send_json({"type": "status", "status": "reconnecting", "message": str(exc)})
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def send_json(self, content):
        await self.send(text_data=json.dumps(content))


class AlpacaStockConsumer(HyperliquidCandleConsumer):
    async def connect(self):
        self.queue = None
        await super().connect()

    async def disconnect(self, close_code):
        await super().disconnect(close_code)
        if self.queue is not None:
            await stock_stream.unsubscribe(self.queue)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            if not text_data or len(text_data) > 512:
                raise ValueError("Invalid stock subscription.")
            payload = json.loads(text_data)
            if not isinstance(payload, dict):
                raise ValueError("Invalid stock subscription.")
            symbol = get_chart_symbol(str(payload.get("symbol", "")))
            if symbol.provider != "alpaca":
                raise ValueError("Alpaca stock data is not enabled for this symbol.")
            await asyncio.to_thread(validate_alpaca_symbol, symbol.value)
            if self.stream_task:
                self.stream_task.cancel()
                await asyncio.gather(self.stream_task, return_exceptions=True)
            if self.queue is not None:
                await stock_stream.unsubscribe(self.queue)
                self.queue = None
            self.queue = await stock_stream.subscribe(symbol.value)
            self.stream_task = asyncio.create_task(self.forward())
        except (ValueError, alpaca.AlpacaDataError) as exc:
            await self.send_json({"type": "error", "message": str(exc)})

    async def forward(self):
        while True:
            await self.send_json(await self.queue.get())
