# Local Trading Dashboard

A Django + Channels dashboard for split-screen market charts using Lightweight Charts.

## Run

```bash
source .venv/bin/activate
python -m uvicorn trading_dashboard.asgi:application --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000.

## Data Sources

The provider boundary is `dashboard/data_source.py`.

- Hyperliquid crypto markets use the official websocket endpoint for live candle updates and the `/info` `candleSnapshot` endpoint for initial history.
- Indian equities use yfinance symbols such as `RELIANCE.NS`, `TCS.NS`, and `INFY.NS`.

To add another provider, add the provider function in `dashboard/data_source.py`, register it in `HISTORY_PROVIDERS` and/or `QUOTE_PROVIDERS`, then add symbols with that provider name to `SYMBOLS`.
