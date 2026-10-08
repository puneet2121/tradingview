# Local Trading Dashboard

A Django + Channels dashboard for split-screen market charts using Lightweight Charts.

## Run

```bash
source .venv/bin/activate
python manage.py migrate
python -m uvicorn trading_dashboard.asgi:application --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000.

## Private Ngrok Sharing (Four Users)

Normal local startup above is not protected for public access. Do not tunnel port 8000. Sharing mode uses four individual Django logins, one shared paper account, HTTPS-only cookies, CSRF protection, authenticated/origin-checked WebSockets, and a 15-minute per-username lockout after five failed login attempts. It uses no paid ngrok traffic-policy features. All four users can trade, edit shared strategies, and reset the shared account; this is not a read-only or separate-account setup. Chart layouts remain browser-local. Activity currently identifies manual actions as `USER`, not by login name.

Install the extra dependencies and set up your assigned ngrok domain:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python share.py setup --domain YOUR_NGROK_DOMAIN
```

This backs up SQLite into `.local-sharing/`, adds authentication tables without deleting trades, collects static assets, and creates `trader1` through `trader4`. Use `--users name1 name2 name3 name4` at first setup to choose other names. Generated passwords are in `.local-sharing/credentials.txt`, readable only by your OS user. Give each person only their own credentials privately. Never commit or share this file, the secret in `config.json`, database backups, or your ngrok authtoken. Re-running setup preserves existing passwords and updates the domain/static files. Use `share.py manage changepassword trader1` to change a password; the original credentials file is then out of date for that user.

Start the sharing backend in one terminal:

```bash
.venv/bin/python share.py serve
```

Then open the tunnel in another:

```bash
.venv/bin/python share.py tunnel
```

Open `https://YOUR_NGROK_DOMAIN`, not `http://127.0.0.1:8003`, and sign in. The tunnel command checks that the target requires login before opening it, and disables ngrok request inspection. The backend binds only to loopback on port 8003. Set `--port 8004` on **both** commands if 8003 is occupied. Keep both terminals running; Ctrl+C in the tunnel terminal stops public access. Configure your ngrok authtoken privately on your machine if ngrok reports authentication failure. Never paste it into chat. The ngrok HTTPS service terminates TLS and forwards traffic to your local machine; this is not a private VPN.

The sharing server **does not start another paper-trading scanner** by default. Keep the original local paper scanner running, or stop all other paper scanner servers and run `share.py serve --scanner` instead. Use only one paper scanner process, no reload/multiple workers. Saved-alert monitoring runs independently with a database lease. Sharing mode disables Alpaca broker execution; paper account data and configured Discord alerts remain shared. No real-money trading is enabled. Your computer must remain awake and online; ngrok is temporary access, not 24/7 hosting. Four users and frequent chart polling may exhaust ngrok's free request/bandwidth quotas quickly; check [current free-plan limits](https://ngrok.com/docs/pricing-limits/free-plan-limits). It does not improve market-data latency or licensing.

For a lockout, wait 15 minutes or locally run `.venv/bin/python share.py manage axes_reset_username trader1`. Disable a login through a local Django shell by setting that user's `is_active=False`; access is limited to the four usernames in the ignored config file. Do not use the development `SECRET_KEY`, wildcard hosts, or `DEBUG=True` for sharing. Public static JS/CSS assets contain no account data; dashboard/API data require login.

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
curl -c /tmp/trading-cookies.txt http://127.0.0.1:8000/ -o /dev/null
TOKEN=$(awk '$6 == "csrftoken" {print $7}' /tmp/trading-cookies.txt)
curl -b /tmp/trading-cookies.txt -H "X-CSRFToken: $TOKEN" -X POST http://127.0.0.1:8000/api/discord/test/
```

The curl example is for local mode. Sharing mode also requires an authenticated session.

If Discord is unreachable or the webhook rejects the message, the app logs the error but keeps the local paper trade and activity record.

## User-Defined Discord Alerts

Open **Alerts** to create, edit, pause, or delete notification rules. Each sharing login owns its own rules and history; everyone sends to the existing shared Discord webhook. The unprotected local dashboard has a separate local-owner alert list. No webhook URL or secret is sent to the browser. These rules never place or close trades and are independent of the paper watchlist, trading strategies, and browser charts.

Examples:
- Price **Touches** SMA **200**, symbols `AAPL, SOFI, MU`, timeframes **5m** and **Daily**.
- EMA **9** **Crosses above** EMA **21**, symbols `AAPL, MSFT`, timeframe **5m**.
- Price **Crosses below** a fixed price level.

Supported sources are closing price, SMA, and EMA; targets are SMA, EMA, or a fixed price. Periods are 1-200. Conditions are touches, crosses above/below, and moves above/below. A touch means the completed candle's high/low range intersects the target (optionally widened by the chosen percentage). Touches and above/below conditions notify on a new false-to-true match, not every candle that stays matched. A cooldown applies independently to each symbol/timeframe. Unsupported rules are rejected; this builder does not execute arbitrary Pine or free-text strategies.

Stocks use **regular-session candles only** for calculations and an XNYS calendar for US holidays and early closes. Thus an MA can differ from a chart that includes extended-hours bars. Crypto uses its 24-hour candles. Daily stock candles complete at the exchange close, not midnight UTC. The worker waits at least 30 seconds after candle close and permits up to 20 minutes for the closing candle/delayed feed to arrive; it never scans premarket/postmarket stock candles. A daily alert can therefore arrive shortly after the closing bell. Other existing trade-alert session settings are unchanged. Alert feeds use the same providers, not guaranteed real-time or TradingView-identical data. Messages include source/target values, candle-close time, detection time, and observed data age.

Alerts start from the time they are saved or resumed. Edits reset their checkpoint, old candles are not replayed, and data over 20 minutes old does not create notifications. Only the latest completed candle is evaluated each scan; this is monitoring, not historical replay after downtime. SMA/EMA history fetches include extra warm-up, including two years for daily 200-period averages. Invalid symbols, insufficient history, and stale data appear under **Scan Status**.

The independent alert worker starts with the web server, even when `PAPER_SCANNER_ENABLED=false`. A database lease selects a single worker across local/sharing servers. Set `ALERT_SCANNER_ENABLED=false` for a UI-only preview. The scanner checks about once per minute (plus provider request time); keep the server/computer awake. A crashed worker's lease expires after five minutes. Limits per user: 20 saved rules, 10 symbols and four timeframes per rule, 50 distinct active symbol/timeframe pairs. Requests for the same pair are shared within a scan.

**Delivery History** retains candle prices, indicator values, Pacific timestamps, sent/pending/failed/cancelled status, and errors even after a rule is deleted. Duplicate scans do not resend the same signal. Discord rate limits defer queued messages; ambiguous network failures or interrupted sends are marked failed instead of blindly retrying and duplicating notifications. There is no promise of exactly-once delivery across network failures. Discord mentions are disabled in webhook messages. Saved webhooks and existing trade notifications remain unchanged.

After updating: install requirements, run `python manage.py migrate`, collect static files with `python share.py manage collectstatic --noinput` for sharing mode, and restart the web server. No new alert is enabled until a user saves one.

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

## Saved Pine Scripts

Run `python manage.py migrate` after updating, then restart the server and hard-refresh the browser. Open **Pine Editor**, paste a supported Pine v5/v6 `indicator()` or `strategy()` script, give it a name, select a loaded chart, and choose **Validate**, **Save**, or **Save & Add to Chart**. The local SQLite database stores the source and revision. Saved scripts also appear in each chart's **Indicators > Saved Pine scripts** menu. Click an indicator chip to edit its source; the `x` removes that instance. **Delete** removes the saved script and its instances. Chart selections restore after refresh; saving a revision updates its open chart instances without fitting/zooming the chart.

EMA crossover, RSI, Trader B/S, and Trend scalper templates are included. Input defaults are edited in the source in this first version. Scripts use the selected chart's existing OHLCV data, including its feed delay and limited loaded history; they do not fetch TradingView market data. Results can differ from TradingView because of feed, history, session, or runtime differences.

Supported rendering: line/line-break/step plots, circle plots, histogram/columns, horizontal levels, solid-color fills between plots, and price-overlay up/down/circle/square markers. Pine triangles map to chart arrows. Lower-pane plots use the existing resizable oscillator area. Table **text values** are shown in the expandable chart-footer results panel, not at Pine's requested position or with its custom styling. Up to two tables of 10 rows and 4 columns are accepted, without merges. Unsupported visual styles, nonzero offsets, additional-timeframe requests, imports, collections, and drawing objects such as boxes/labels are rejected. `alertcondition()` can compile, but Pine alerts are not routed to Discord or the trading scanner.

### Local Strategy Previews

`strategy()` runs an experimental, browser-only OHLC simulation, separate from the app's paper account and scanner. It never places broker or paper-account orders. Previews currently accept intraday US equity charts, assume a $0.01 minimum tick and a 1x price multiplier, and exclude unfinished candles. Tick/fill recalculation and bar magnifier are rejected. Results are recomputed from loaded history; the source is persisted, but simulated fills are not a permanent trade journal. Commission, slippage, quantity and capital come from the Pine declaration, not your dashboard account. Unspecified costs remain zero. This is not options execution or TradingView-equivalent backtesting; see [PineTS's strategy limitations](https://docs.luxalgo.com/developers/pinets/api-coverage/strategy).

**Template > Trend scalper** loads `strategies/trend_breakdown_scalper.pine`, a compatible adaptation of the supplied starter strategy. It retains the 9/21 EMA trend filter, 20-bar breakout, 10-bar swing stop with 4-tick buffer, and targets of 6/10/16/26/38 ticks. Its default position is 1 simulated unit, split into five 0.2-unit exits (fractional simulation, not five option contracts). The cloud, trigger arrows, active stop dots, simulated entry/exit markers, statistics and recent fill prices are displayed. Dense simulated marker labels thin out when zoomed out; the fills remain in the results panel.

Two PineTS 0.10.0 problems required explicit compatibility changes: direct `strategy.position_size[1]` is replaced with a history-enabled variable, and repeated `qty_percent=20` exit calls are replaced by five fixed-size brackets submitted once when the position opens. The editor rejects direct strategy-property history and named percentage quantities instead of silently producing wrong stops or partial exits. It does not silently rewrite pasted source. Use the corrected template for this starter. Other strategies, especially repeated partial exits, require independent verification; the experimental label is intentional.

When a script titled **Trend Breakdown Scalper** hits either compatibility error, the editor offers **Load compatible scalper** next to the error. After confirmation, it opens the corrected template as a **new unsaved copy**, using template defaults (custom edits are not converted). **Restore original source** recovers the previous draft while you remain in that editor document. Saving the compatible copy never overwrites the original saved script. This draft recovery is in memory, not a permanent backup across reloads or document switches.

PineTS 0.10.0 is vendored locally, with no CDN at runtime. It is experimental and not TradingView's official Pine engine. Source is compiled/executed only in dedicated browser workers with a deny-network CSP, not inside Django or the main UI thread. Limits: 100 saved scripts, 50,000 source characters, four Pine scripts per chart, two simultaneous workers, 4,000 loaded bars, 24 plots, 64 rendered segments, 1,000 markers, 2,000 simulated trade legs, 10,000 iterations per loop, and eight seconds per worker run. Fast ticks are coalesced. A failed script clears its old plots and shows an error on its chip; edit/resave it or remove/re-add it to retry. These safeguards are not a public multi-tenant execution service or a hard memory quota.

The runtime and its AGPL-3.0 license are in `dashboard/static/vendor/pinets/`. Review that license or upstream commercial licensing before distributing a proprietary application or offering a hosted service.
