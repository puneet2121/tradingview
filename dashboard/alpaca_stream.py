"""One upstream stock stream per server process, shared by every chart/user."""
import asyncio
import json
import ssl
import time

import certifi
import websockets

from . import alpaca
from .data_source import alpaca_timestamp, finite_number


class StockStream:
    def __init__(self):
        self.subscribers = {}
        self.changed = asyncio.Event()
        self.task = None
        self.status = "connecting"
        self.last_trade_sent = {}

    def put(self, queue, message):
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(message)

    def broadcast(self, message, symbol=None):
        for queue, item in tuple(self.subscribers.items()):
            if symbol is None or item == symbol:
                self.put(queue, message)

    def set_status(self, status, message=""):
        self.status = status
        self.broadcast({"type": "status", "status": status, "message": message})

    async def subscribe(self, symbol):
        alpaca.data_headers()
        symbols = set(self.subscribers.values())
        limit = 30 if alpaca.data_config()["stock_feed"] == "iex" else 64
        if symbol not in symbols and len(symbols) >= limit:
            raise alpaca.AlpacaDataError(f"Stock stream limit reached ({limit} distinct symbols across all users).")
        queue = asyncio.Queue(maxsize=64)
        self.subscribers[queue] = symbol
        self.put(queue, {"type": "status", "status": self.status})
        self.changed.set()
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())
        return queue

    async def unsubscribe(self, queue):
        self.subscribers.pop(queue, None)
        self.changed.set()
        if not self.subscribers and self.task:
            task, self.task = self.task, None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if not self.subscribers:
                self.status = "connecting"
                self.last_trade_sent.clear()

    async def subscriptions(self, socket):
        sent = set()
        while True:
            self.changed.clear()
            desired = set(self.subscribers.values())
            for action, symbols in (("unsubscribe", sent - desired), ("subscribe", desired - sent)):
                if symbols:
                    await socket.send(json.dumps({"action": action, **{channel: sorted(symbols) for channel in ("trades", "bars", "updatedBars", "dailyBars")}}))
            sent = desired
            await self.changed.wait()

    async def authenticate(self, socket):
        config = alpaca.data_config()
        await socket.send(json.dumps({"action": "auth", "key": config["key"], "secret": config["secret"]}))
        for _ in range(4):
            messages = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
            for message in messages:
                if message.get("T") == "error":
                    raise alpaca.AlpacaDataError("Stock stream authentication/subscription denied. Check paper keys, feed access, and other running servers.")
                if message.get("T") == "success" and message.get("msg") == "authenticated":
                    return
        raise alpaca.AlpacaDataError("Stock stream authentication did not complete.")

    async def run(self):
        backoff = 1
        while self.subscribers:
            sender = None
            try:
                feed = alpaca.data_config()["stock_feed"]
                async with websockets.connect(
                    f"wss://stream.data.alpaca.markets/v2/{feed}",
                    ssl=ssl.create_default_context(cafile=certifi.where()),
                    ping_interval=20, ping_timeout=20, open_timeout=10,
                ) as socket:
                    await self.authenticate(socket)
                    sender = asyncio.create_task(self.subscriptions(socket))
                    backoff = 1
                    async for raw in socket:
                        if sender.done():
                            sender.result()
                        for message in json.loads(raw):
                            kind, symbol = message.get("T"), message.get("S")
                            if kind == "error":
                                raise alpaca.AlpacaDataError("Stock stream rejected a subscription. Check feed access and symbol/connection limits.")
                            if kind == "subscription":
                                self.set_status("connected")
                            elif kind in {"b", "u", "d"}:
                                # Re-read native timeframe aggregates, including late-bar corrections.
                                self.broadcast({"type": "refresh"}, symbol)
                            elif kind == "t":
                                price, timestamp = finite_number(message.get("p")), alpaca_timestamp(message.get("t"))
                                now = time.monotonic()
                                if price is not None and timestamp and now - self.last_trade_sent.get(symbol, 0) >= 0.25:
                                    self.last_trade_sent[symbol] = now
                                    self.broadcast({"type": "quote", "symbol": symbol, "price": price, "time": timestamp, "feed": feed}, symbol)
                raise alpaca.AlpacaDataError("Stock stream disconnected.")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc) if isinstance(exc, alpaca.AlpacaDataError) else "Stock stream disconnected; retrying."
                self.set_status("reconnecting", message)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            finally:
                if sender:
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)


stock_stream = StockStream()
