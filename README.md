# Local Trading Dashboard

A Django + Channels dashboard for split-screen market charts using Lightweight Charts.

## Run

```bash
source .venv/bin/activate
python manage.py migrate
python -m uvicorn trading_dashboard.asgi:application --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000.

## Data Sources

The provider boundary is `dashboard/data_source.py`.

- Hyperliquid crypto markets use the official websocket endpoint for live candle updates and the `/info` `candleSnapshot` endpoint for initial history.
- Indian equities use yfinance symbols such as `RELIANCE.NS`, `TCS.NS`, and `INFY.NS`.

To add another provider, add the provider function in `dashboard/data_source.py`, register it in `HISTORY_PROVIDERS` and/or `QUOTE_PROVIDERS`, then add symbols with that provider name to `SYMBOLS`.

## Optional Alpaca Paper Orders

The app stays fully local by default. To also send generated paper trades to Alpaca Paper Trading, start the server with:

```bash
export ALPACA_PAPER_ENABLED=true
export ALPACA_API_KEY="your_key"
export ALPACA_SECRET_KEY="your_secret"
python -m uvicorn trading_dashboard.asgi:application --host 127.0.0.1 --port 8000
```

When enabled, the local paper-trade history still records every signal and stores the Alpaca order id/status next to it.

## Yahoo Option Chain Testing

Use the `Options` button in the top bar to open the manual option-chain drawer. Enter a US underlying such as `AAPL`, choose an expiration, then use `Buy` or `Sell` on a contract row to create a local manual option paper trade. `Close` exits an open manual option paper trade using the latest Yahoo bid/ask/mid fallback.

Yahoo option data is useful for free UI and strategy testing, but it is not official OPRA market data.

## Trade Control And Activity

The local paper account starts with `$100,000`. The default risk is still `2%` per trade, so automated option strategies can spend up to about `$2,000` by account risk, with an additional `$1,500` maximum premium per option contract.

Manual option positions have manual exits only. The scanner cannot close them, and each strategy can only close its own automated option positions. Use `Sell to Close` for a bought option or `Buy to Close` for a short option; the confirmation identifies the contract and quantity.

Open `History > Activity` or `Options > Activity` to see saved open/close actions, the user or strategy responsible, exit reasons, Pacific timestamps, prices, quantities, and realized P/L. Automated actions also save the strategy rules used at execution. Activity is retained after an account reset, and resets are logged. Older trades are imported as snapshots with unknown actors; missing historical reasons are not inferred.

The activity view refreshes every 10 seconds while visible and supports loading older entries. It reads the local database without fetching market quotes. Restart the server after updating code. For an additional UI-only server, set `PAPER_SCANNER_ENABLED=false` to avoid starting another scanner.

## Discord Strategy Alerts

Create an incoming webhook in your Discord server channel, then start the local server with Discord alerts enabled:

```bash
export DISCORD_ALERTS_ENABLED=true
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python -m uvicorn trading_dashboard.asgi:application --host 127.0.0.1 --port 8000
```

By default, Discord only receives strategy-created paper trade opens and closes. Manual trades and resets stay local. Alerts include the strategy, reason, symbol/timeframe, contract details for options, entry price, quantity, stop/risk settings, Greeks when available, exit price, realized P/L, and Pacific timestamps.

To send one test message after the server starts:

```bash
curl -X POST http://127.0.0.1:8000/api/discord/test/
```

If Discord is unreachable or the webhook rejects the message, the app logs the error but keeps the local paper trade and activity record.

## Strategy Editor

Use the `Strategies` button to edit saved scanner strategies without changing code. The first supported rule shape is a safe EMA/VWAP cross strategy:

```text
BUY CALL WHEN EMA(9) crosses above VWAP
AND volume is increasing
AND close > previous close

BUY PUT WHEN EMA(9) crosses below VWAP
AND volume is increasing
AND close < previous close
```

When a strategy is set to `Options`, BUY signals open long calls and SELL signals open long puts from the Yahoo option chain. Contract selection uses the strategy's DTE range, strike mode, spread limit, and risk percentage. This is still local paper trading, not live brokerage execution.

The default automatic scanner watchlist is `MU`, `SOFI`, `AAPL`, `MSFT`, `HOOD`, `NOW`, `QQQ`, `SPY`, `BTC`, `ETH`, `AVGO`, `DRAM`, `SNDK`, `WMT`, `SNOW`, `MRVL`, and `IREN` on `3m` and `5m`.

Rules are validated in full when saved and before signal execution. The editor reports line-specific errors and flags previously saved invalid strategies as `Signals blocked`. No supported branch is executed from a partially unsupported strategy.

Supported entries are `BUY CALL` / `BUY` for a bullish EMA/VWAP cross and `BUY PUT` / `SELL` for a bearish cross. EMA periods must be 1-500. Inverse expressions such as `VWAP crosses below EMA(9)` are supported. EMA/EMA crosses, RSI conditions, OR clauses, and short-option entry instructions such as `SELL PUT` are rejected. Each direction can have one entry rule, with its own optional `AND volume is increasing` and `AND close > previous close` (buy) or `AND close < previous close` (sell). These confirmations compare the next candle with the cross candle. Conditions can follow on separate lines or inline after `AND`.

Saving changed rules clears that strategy's scan checkpoints. Its next scan establishes a fresh checkpoint before accepting subsequent signals. Existing stops and option take-profit settings remain active independently of entry-rule validation.
