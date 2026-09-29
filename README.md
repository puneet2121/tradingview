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

Strategy notifications are sent only during the US regular market session by default: 9:30 AM to 4:00 PM Eastern, Monday through Friday. Premarket and after-hours trades remain in the app's activity history without sending Discord messages. Set `DISCORD_REGULAR_MARKET_HOURS_ONLY=false` to allow alerts at all times.

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

The app also creates `Impulsive Trader Auto B & S Signal` as a supported strategy:

```text
US PREMARKET BREAKOUT
SESSION 03:30-09:30 America/New_York
SIGNALS 09:30-16:00 America/New_York
REWARD:RISK 4:1
ONE SIGNAL PER SESSION
```

This uses available candles in the New York 03:30-09:30 range, then takes at most one confirmed BUY or SELL breakout per weekday during 09:30-16:00. Today's range is reset every New York calendar day; missing premarket data means no signal, not reuse of yesterday's levels. Intraday bars must finish before the cutoff for a new entry. Bars that straddle the range's end are excluded from the range; use aligned 3m/5m candles for the supplied strategy. These are clock-based weekday windows, not a holiday/early-close calendar.

The stop is the opposite premarket level. The 4R target is measured from the signal candle's close, not the breakout boundary. TP/SL markers require a later candle to close through the target/stop; a wick touch alone does not qualify. `END` means the tracked signal reached the final session candle, not a TP/SL or an executed broker order. The indicator reads the saved built-in strategy's validated settings, so edited session times and reward/risk are shared with the scanner. Invalid rules block chart signals too. The signal stores its stop/target in the trade note; automated option exits still use the separate option-premium P/L settings. Changing chart rules does not change open positions or those option exit settings.

The untouched old built-in 00:00-06:00 / 19:30 rule text is upgraded on strategy initialization, preserving its risk, enabled state, and option settings. Its scanner checkpoints are reinitialized so old signals are not replayed. User-edited rule text is not overwritten, and the legacy ASIA SESSION BREAKOUT syntax remains supported.

Intraday yfinance history requests extended hours; the range uses only candles actually returned by the feed. The matching Pine indicator is in `strategies/impulsive_trader.pine`. Its default is 4R, retaining the app's requested setting rather than the pasted script's 0.6R. Enable extended-hours candles in TradingView to provide premarket data. The built-in Impulsive indicator/scanner uses Python/JavaScript; the separate Pine Editor can run this file locally through PineTS. PineTS validation is not compilation by TradingView's official engine.

Rules are validated in full when saved and before signal execution. The editor reports line-specific errors and flags previously saved invalid strategies as `Signals blocked`. No supported branch is executed from a partially unsupported strategy.

Supported entries are `BUY CALL` / `BUY` for a bullish EMA/VWAP cross and `BUY PUT` / `SELL` for a bearish cross. EMA periods must be 1-500. Inverse expressions such as `VWAP crosses below EMA(9)` are supported. EMA/EMA crosses, RSI conditions, OR clauses, and short-option entry instructions such as `SELL PUT` are rejected. Each direction can have one entry rule, with its own optional `AND volume is increasing` and `AND close > previous close` (buy) or `AND close < previous close` (sell). These confirmations compare the next candle with the cross candle. Conditions can follow on separate lines or inline after `AND`.

Saving changed rules clears that strategy's scan checkpoints. Its next scan establishes a fresh checkpoint before accepting subsequent signals. Existing stops and option take-profit settings remain active independently of entry-rule validation.

## Saved Pine Indicators

Run `python manage.py migrate` after updating, then restart the server and hard-refresh the browser. Open **Pine Editor**, paste a Pine v5/v6 `indicator()` script, give it a name, select a loaded chart, and choose **Validate**, **Save**, or **Save & Add to Chart**. The local SQLite database stores the source and revision. Saved scripts also appear in each chart's **Indicators > Saved Pine indicators** menu. Click an indicator chip to edit its source; the `x` removes that instance. **Delete** removes the saved script and its instances. Chart selections restore after refresh; saving a revision updates its open chart instances without fitting/zooming the chart.

EMA crossover, RSI, and Trader B/S templates are included. Input defaults are edited in the source in this first version. Scripts use the selected chart's existing OHLCV data, including its feed delay and limited loaded history; they do not fetch TradingView market data. Results can differ from TradingView because of feed, history, session, or runtime differences.

Supported rendering: line/line-break/step plots, histogram/columns, horizontal levels, and price-overlay up/down/circle/square markers. Pine triangles map to chart arrows. Lower-pane plots use the existing resizable oscillator area. Unsupported visual styles, nonzero offsets, additional-timeframe requests, imports, strategy orders, collections, and drawing objects such as tables/boxes/labels are rejected. `alertcondition()` can compile, but Pine alerts are not routed to Discord or the trading scanner. Saved Pine indicators are chart-only and cannot open or close trades.

PineTS 0.10.0 is vendored locally, with no CDN at runtime. It is experimental and not TradingView's official Pine engine. Source is compiled/executed only in dedicated browser workers with a deny-network CSP, not inside Django or the main UI thread. Limits: 100 saved scripts, 50,000 source characters, four Pine indicators per chart, two simultaneous workers, 4,000 loaded bars, 24 plots, 64 rendered segments, 1,000 markers, 10,000 iterations per loop, and eight seconds per worker run. Fast ticks are coalesced. A failed indicator clears its old plots and shows an error on its chip; edit/resave it or remove/re-add it to retry. These safeguards are not a public multi-tenant execution service or a hard memory quota.

The runtime and its AGPL-3.0 license are in `dashboard/static/vendor/pinets/`. Review that license or upstream commercial licensing before distributing a proprietary application or offering a hosted service.
