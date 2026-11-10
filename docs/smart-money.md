# Smart Money Intelligence

Independent Django application: `/smart-money/`, linked from the chart dashboard.
It is research-only. It never invokes paper-trading or broker order functions.
Existing balances, positions, strategies, chart state and alerts are not migrated into this module.

## Implemented

- Eleven navigable sections, including Fundamental Analysis; responsive tables, transaction detail dialog, premium bars and clickable strike/expiration distributions.
- On-demand Yahoo company profiles, valuation ratios, annual/quarterly statements, revenue/earnings growth, margins, cash flow and debt metrics. Separate from OPRA flow access and automatic trading.
- Independent shared collection settings, up to 25 US tickers, per-user saved filters and Discord rules.
- Read-only Alpaca historical OPRA transaction adapter, persistent pagination/checkpoints, bounded polling, retry/backoff and a database worker lease.
- Actual contract-size premium calculation. Missing multipliers stay null, not 100. Such records remain in storage but cannot qualify for premium-based rankings/alerts.
- Separate provider/ingestion timestamps, raw nanosecond timestamps, condition codes, provisional unusualness evidence, and explicit unknown fields.
- Observed premium/repeat scoring; CALL/PUT rankings, directional and unknown totals; minimum premium, size, score, expiry, strike and repeat filters.
- Historical 5m RTH technical reconstruction: EMA 9/21, session VWAP, SMA 50/200, Wilder RSI 14, MACD, relative underlying volume, confirmed swing levels.
- Underlying outcomes at 15m, 1h, session close, and 1/5/20 trading-day closes. Aggregation by CALL/PUT, premium category and structure. Unknown direction is excluded from accuracy.
- SEC submissions plus official XML import for Form 4 and 13F. Filing date, report period, transaction codes and original units remain visible. Adjacent complete original quarters can be compared; amendments disable automatic comparisons.
- Shared Discord destination, owner-scoped rules, regular-session delivery including calendar holidays/early closes, persistent deduplication, 429 backoff and no automatic retry of uncertain deliveries.
- Local-only or authenticated private-sharing access; CSRF protection, bounded inputs, no provider calls in UI requests, no credentials in API responses.

## Fundamental Analysis

Open `/smart-money/#fundamentals`, enter a US stock symbol, and choose Annual or Quarterly. No Alpaca subscription or API key is required for local Yahoo lookups. The existing `dashboard/data_source.py` is the provider boundary; `smart_money/fundamentals.py` normalizes the data and manages its independent persistent cache.

- Company profile, sector/industry, market capitalization and quote timestamp.
- Trailing/forward P/E, price/sales, price/book, EV/EBITDA, enterprise value and trailing/forward EPS. Forward values are labeled forecasts; ratios are provider snapshots, not historical valuations for the selected statement period.
- Annual and quarterly income statements, balance sheets and cash-flow statements, with the actual provider period-end dates and financial currency. Quote currency and financial currency are kept separate. Monetary statement rows are displayed in millions; EPS is per share.
- Revenue and net-income charts, year-over-year growth against a comparable period 330-400 days earlier, gross/operating/net margins, debt/equity and current ratio. Quarterly growth is not quarter-over-quarter growth. Nonpositive comparison bases and missing denominators do not produce misleading growth percentages or meaningful P/E/debt-equity ratios.
- Free cash flow uses the provider value when available. A fallback adds operating cash flow to explicitly nonpositive signed capital expenditure, never guesses the sign of positive capex. Summary free cash flow must match the income period.
- Factual observations describe revenue changes, profit/loss, cash flow and nonpositive equity. There is no invented intrinsic value, generic buy/sell score, or broker execution from these observations. ETF/fund symbols explicitly report that corporate statements are not applicable.

Company loads are requested through a CSRF-protected POST and run in bounded background threads; GET only reads the database. Successful snapshots cache for six hours, partial statement responses for fifteen minutes, refreshes have a five-minute cooldown, and failures back off for ten minutes. Failed refreshes retain the last good data with a stale/error label. Two concurrent loads per process and six starts per minute limit provider traffic. A ten-minute per-symbol lease coalesces requests and permits recovery after a process restart. The ticker and annual/quarterly selection persist in that browser; financial data is not a permanent point-in-time archive.

Yahoo data can be incomplete, delayed or restated, and statement period ends are not filing dates. The [yfinance documentation](https://ranaroussi.github.io/yfinance/) describes research/personal-use restrictions; it does not grant a multi-user redistribution license. Local personal research is enabled independently of OPRA. The private sharing server requires separate confirmation through `SMART_MONEY_ALLOW_FUNDAMENTALS_SHARING=true` only after appropriate data-use rights are established. No existing sharing entitlement is assumed to cover Yahoo.

## Not Supplied by This Adapter

Alpaca's option-trade response does not include contemporaneous execution quotes, historical trade-time Greeks/OI, verified sweeps, multi-leg linkage, buyer identity or dark-pool stock prints. These remain unavailable. The relevant alert conditions are rejected, not silently ignored. Current OPRA flow consequently has **UNKNOWN** execution direction; it is not called institutional buying.

There is no normal-options-volume baseline, relative options volume, full-market scanner, adjustment-aware outcome backtest, or expiration-category performance analysis in this release. SEC imports cover recent submissions for configured CIKs, not all EDGAR filers or full historical archives. Large filings above 4 MB and incomplete 13F tables are rejected. These limits are deliberate, not simulated substitutes.

## Provider Decision

Reviewed against official pages on October 24, 2026. Entitlements and commercial terms must be confirmed with the provider before enabling collection or sharing; the app does not purchase data or grant licensing rights.

| Provider | Assessment | Integration |
| --- | --- | --- |
| [Alpaca](https://docs.alpaca.markets/us/docs/about-market-data-api) | Basic is indicative options, not an unmodified executed-options tape. OPRA access depends on entitlement. Advertised paid individual market-data plan: $99/month. | First read-only adapter, explicit OPRA only. No automatic fallback to indicative. |
| [Massive / Polygon](https://massive.com/pricing?product=options) | Advertised options tiers: free basic, $29 aggregates, $79 delayed trades, $199 real-time trades/quotes. Quotes and redistribution rights are separate considerations. | Registry extension point only; not implemented. |
| [Unusual Whales](https://unusualwhales.com/public-api) | Documented paid API, advertised basic API $150/month and limited trial. Personal use does not establish redistribution permission. | Not implemented. |
| [Tradier](https://docs.tradier.com/docs/market-data) | Brokerage access and sandbox have different entitlements. Sandbox is delayed and not a free real-time options transaction firehose. | Not implemented. |
| [Xynth](https://xynth.com/flow) | Website access is not a documented free API license; [terms](https://xynth.com/terms) restrict scraping. | No scraping or integration. |
| [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Public filings without an API key; identifying contact required and [fair-access limits](https://www.sec.gov/about/developer-resources) apply. Not live trading data. | Submissions, Form 4 and 13F XML implemented. |

Alpaca's [redistribution policy](https://alpaca.markets/support/redistribute-alpaca-api) does not grant retail users the right to redistribute API data. Your four-user dashboard and Discord destination need appropriate rights. Storage/non-display rights also need confirmation. The three permission flags below are administrator attestations, not proof of a license.

## Setup

The migration is additive. Back up SQLite before applying it on another machine:

```sh
.venv/bin/python manage.py migrate smart_money
```

Configuration is read from the process environment. This project does not automatically load a `.env` file. Do not paste keys in source, the browser, Discord, or chat. Supply them securely through your existing launcher or process manager:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SMART_MONEY_PROVIDER` | `none` | `alpaca` selects the OPRA adapter. |
| `ALPACA_DATA_ENABLED` | existing setting | Must be `true` for Alpaca reads. |
| `ALPACA_DATA_API_KEY` / `ALPACA_DATA_SECRET_KEY` | unset | Paper account data credentials in the environment; existing private-file-only keys do not satisfy this module's environment requirement. |
| `SMART_MONEY_OPTION_FEED` | `opra` | Only verified OPRA is supported. Independent of the existing chart/option-chain `ALPACA_OPTION_FEED` setting. |
| `ALPACA_STOCK_FEED` | existing setting | `iex` or entitled `sip`; underlying technicals retain the source label. |
| `SMART_MONEY_DELAY_SECONDS` | `900` | Only `900` or `0`. Zero requires real-time entitlement; both modes are REST-polled, not tick-streamed. |
| `SMART_MONEY_ALLOW_STORAGE` | `false` | Confirm rights to collect/store the data. |
| `SMART_MONEY_ALLOW_SHARING` | `false` | Confirm sharing rights before the private multi-user server can expose flow. |
| `SMART_MONEY_ALLOW_FUNDAMENTALS_SHARING` | `false` | Separate rights confirmation for Yahoo fundamentals on the private sharing server; local personal research works without this flag. |
| `SMART_MONEY_ALLOW_DISCORD` | `false` | Confirm rights to send data to the existing shared Discord destination. |
| `SEC_USER_AGENT` | unset | Identifying name and contact email, e.g. `Research Dashboard contact@example.com`. |
| `SMART_MONEY_WORKER_ENABLED` | `true` | Start a reader thread with `runserver`/Uvicorn. Database collection remains OFF by default. |

Restart the server after setting environment variables. In **Smart Money > Alerts & Settings**, set the watchlist and minimum premium, then enable collection. A rejected entitlement leaves the provider in an error/backoff state without affecting charts or trading. SEC can be enabled independently by supplying filer CIKs and a contact identity.

The chart app's normal run command continues to work. For an independently supervised reader:

```sh
.venv/bin/python manage.py run_smart_money
# Or one collection pass, using saved settings:
.venv/bin/python manage.py run_smart_money --once
# Static assets for the existing private sharing server:
.venv/bin/python manage.py collectstatic --settings=trading_dashboard.sharing_settings --noinput
```

Use one reader per database; the DB lease prevents duplicate readers when the web process also starts one. Collection and Discord continue only while a process is running. Keep a single application worker with this SQLite/in-memory-channel deployment. Static assets for the sharing deployment need the usual `collectstatic` run after updating.

Private/ngrok access must use the existing authenticated sharing configuration, not an exposed local development server. Direct local access permits only loopback addresses **and** loopback Host headers. No extra account or password is created by this module.

## Coverage and Calculation Limits

- The worker runs every 60 seconds, with ten flow/catalog/entitlement requests and two enrichment requests per pass, sharing the existing Alpaca request limiter. Each pass probes the documented latest-trade endpoint with `feed=opra`; access denial blocks historical collection. The [historical trades endpoint](https://docs.alpaca.markets/us/reference/optiontrades) has no documented feed selector; this adapter does not send an invented one or fall back to indicative. A wide watchlist, high-volume contracts or backlog adds latency. The UI shows per-ticker coverage/checkpoints and provider/worker freshness.
- A catalog covers at most 100 nearest-strike contracts per underlying, expiring in the next 60 days. Catalogs refresh about every six hours after a complete window. This is a sample, not complete options-market surveillance. The UI retains returned/total counts.
- Pagination is durable. A one-minute overlap catches some late arrivals, not all possible corrections. The Alpaca response has no unique print ID or correction linkage: identity uses full timestamp, contract, price, size, exchange, condition and same-event occurrence count. Exact identical prints within a page/window are retained. Later vendor revisions can still change the observed tape; records are not advertised as final reconciled exchange accounting.
- Above-minimum prints are stored; missing multiplier prints are retained but omitted from premium-based analytics. Raising/lowering collection thresholds changes future collection, not past coverage. Re-enabling or changing settings starts a fresh checkpoint and does not replay a week of old activity into Discord.
- Score `observed-v1` uses absolute premium and repeated observed same-contract prints in ten minutes. Optional quote/volume evidence is supported by pure computation helpers, but is not fabricated by the Alpaca adapter. Repeats count only collected prints, not all contract volume. These are candidate-activity rankings, not statistically established abnormality or trading recommendations.
- Technicals use only **completed** pre-event 5m regular-session bars, but are reconstructed later from the provider's historical database. They are not an immutable point-in-time archive and can reflect subsequent provider revisions. IEX is not consolidated SIP market coverage. Sparse bars and pre/post-market events may have no technical result.
- MACD uses a signal EMA seeded from valid MACD points. This module does not alter chart indicator calculations. Support/resistance pivots require three completed bars on either side, all before the event timestamp.
- Outcome baseline is the last completed pre-event underlying close, not the option price or exact underlying price at the print. Only completed post-event bars contribute; unavailable/out-of-session targets are marked missing. These approximations are not option P/L, executable fills, or corporate-action-adjusted investment returns.
- Fresh-event enrichment and mature outcomes share a small fixed budget. Heavy activity can accumulate a backlog. History is bounded to 31 calendar days per query, with stable 50-record flow pages; change the date range to inspect older periods.
- Form 4 codes stay intact: awards/exercises are not called purchases; derivatives remain flagged in stored records. 13F values retain their original reported units rather than guessing dollars vs thousands. CUSIPs are not guessed into ticker symbols. Holdings changes can reflect splits, identifier changes, transfers or amendments, not necessarily market trades.
- Discord sends only during XNYS regular trading hours. Old pending events expire, changed/disabled rules cancel delivery, and uncertain deliveries are not resent automatically. Trade timestamp and ingestion timestamp are included separately.

## Extending Providers

Add a read-only adapter to `smart_money/providers.py` and register it in `PROVIDERS`. It must declare capabilities and entitlement checks, implement a bounded contract catalog, paginated trades and normalized underlying bars, and honor the worker guard/budget. The current ingestion identity is Alpaca-specific; a provider with native IDs, quote-at-execution data, correction events or multi-leg labels needs a corresponding normalization path and regression fixtures. A new provider is intentionally not a one-line switch that guesses missing semantics.

Never map option-chain snapshots, last prices, or simulated fills into executed flow. Add actual sourced fields before enabling bid/ask/sweep/block alerts or directional analytics.

## Technical, Fundamental and Combined Ratings

Open **Fundamental Analysis** and analyze a US stock. The three research scores are independent of options-flow scores, chart strategies, Discord rules and broker orders. Annual/Quarterly changes the fundamental evidence and combined result; technicals always use **daily** candles, not the selected chart's intraday interval.

`research-v1` is a transparent screening heuristic, not an analyst consensus, a calibrated win probability, a backtested strategy, fair value or an instruction to trade. A high score does not establish an inexpensive share price. The page includes supporting evidence, reasons to wait, coverage, timestamps and a complete points breakdown.

| Technical evidence | Weight | Grade (percent of weight) |
| --- | --- | --- |
| Close vs SMA 200 | 25 | Above 100; equal 50; below 0 |
| SMA 50 vs SMA 200 | 25 | Above 100; equal 50; below 0 |
| EMA 9 vs EMA 21 | 20 | Above 100; equal 50; below 0; alignment, not a fresh crossover |
| Wilder RSI 14 | 15 | 50 to below 70: 100; 40 to below 50 or >=70: 50; below 40: 0 |
| Volume confirmation | 15 | At >=1.2x previous 20-session average: up close 100, unchanged 50, down close 0; lighter volume 50 |

Daily prices use the existing Yahoo provider with two years of regular-session history, up to 550 rows and `auto_adjust=False` (not dividend-adjusted returns). The exchange calendar excludes weekends/holidays, respects early closes, and excludes a daily bar until 15 minutes after its session close. At least 200 consecutive sessions and 90% input coverage are required. A missing latest eligible session or expired cache blocks the score, rather than treating Friday data as stale simply because it is Sunday. RSI extremes carry explicit cautions. EMA/RSI reuse the existing calculation helpers.

| Fundamental evidence | Weight | Grade (percent of weight) |
| --- | --- | --- |
| Revenue growth YoY | 20 | >=10%: 100; >0 to <10%: 70; zero: 50; negative: 0 |
| Net income growth YoY | 15 | Same growth thresholds; nonpositive prior-year bases are not comparable |
| Operating margin | 15 | Positive 100; zero 50; negative 0 |
| Net margin | 15 | Positive 100; zero 50; negative 0 |
| Free cash flow | 15 | Positive 100; zero 50; negative 0; must match income period |
| Debt/equity | 10 | <=1x: 100; >1x to 2x: 50; >2x or nonpositive equity: 0 |
| Current ratio | 10 | >=1.5x: 100; 1x to <1.5x: 50; <1x: 0 |

Fundamentals need at least 80% coverage and evidence from all four groups: growth, profitability, cash flow and balance sheet. Missing fields never become zero or positive evidence; scores normalize available points by available weight. Reporting-age limits are 540 days for annual periods and 180 days for quarterly periods. These limits are research safeguards, not statutory filing deadlines. Financial Services and Real Estate remain unrated pending sector-specific models; ETFs have no corporate fundamental/combined score but can have technicals. Valuation ratios are displayed but not scored because peer/sector and fair-value benchmarks are absent.

Scores >=70 are bullish/strong; 40-69 mixed; <40 weak. Combined = 50% technical + 50% fundamental, only when both can be rated. **Buy candidate** requires combined >=70 and each component >=60; either component <40 gives **Weak buy case** even if the other is strong; otherwise **Watch / wait**. Insufficient or stale evidence gives **Not rated**, not a forced recommendation. News, earnings catalysts, fair value, suitability, execution costs and position sizing are outside this model.

`POST /api/smart-money/research-ratings/` accepts `{ "symbol": "AAPL", "refresh": false }`. It requests company statements through the existing background cache and one bounded daily-history read, without broker writes. `GET` is cache-only. Price snapshots use a separate additive table, a 15-minute cache, two-minute refresh cooldown, five-minute failure backoff, two concurrent reads per process and six lookups/minute. Database leases coalesce same-symbol reads; tokens prevent late results overwriting newer reads. Errors preserve the previous snapshot but invalidate its score. Shared Yahoo research uses the same separate data-use permission as company financials.

The indicator definitions follow [Fidelity's technical indicator guide](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/overview) and financial-statement terminology follows the [SEC's financial statements guide](https://www.sec.gov/about/reports-publications/beginners-guide-financial-statements). **The weights, thresholds and labels are this app's own heuristic, not recommendations endorsed by these sources.**

## Verification

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations smart_money --check --dry-run
.venv/bin/python manage.py test smart_money
```

Tests use isolated synthetic fixtures, not a synthetic production feed. Coverage includes actual/missing multipliers, page-boundary duplicates, nanosecond identity, rollback/checkpoint safety, old-event suppression, no-lookahead technicals, calendar horizons, quote classification, outcomes, unsupported rules, owner isolation, CSRF, tunnel-host rejection, SEC XML rejection and amendment handling, provider backoff, and Discord deduplication.

Browser checks cover all ten sections at 1440, 768 and 390 px, including populated tables/details with browser-only intercepted responses. No real broker order or Discord delivery is used for verification. Live provider entitlement and actual SEC connectivity require configuration and are not established by mocked tests.

Fundamentals have additional coverage for real Yahoo AAPL statements, annual/quarterly switching, saved period selection, and mobile rendering. Unit tests cover currency/date provenance, missing and non-finite values, negative earnings/equity, capex sign handling, period alignment, same-quarter year-over-year calculations, unsupported assets, cache coalescing/cooldowns, stale recovery, token fencing, CSRF and separate shared-access permission.

The existing `dashboard` suite currently has two weekend-sensitive Discord tests that expect delivery without fixing the clock. They also fail when run separately; this module does not change that suite or the existing regular-hours suppression.
