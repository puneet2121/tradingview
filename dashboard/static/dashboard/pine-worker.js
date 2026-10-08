/* Loaded after the vendored PineTS bundle under a deny-network worker CSP. */
(() => {
  const send = self.postMessage.bind(self);
  const forbidden = ["fetch", "XMLHttpRequest", "WebSocket", "EventSource", "importScripts", "Worker", "SharedWorker", "indexedDB", "caches", "BroadcastChannel"];
  forbidden.forEach((name) => {
    Object.defineProperty(self, name, { value: undefined, writable: false, configurable: false });
  });

  function compatibilityError(code, message) {
    const error = new Error(message);
    error.code = code;
    throw error;
  }

  function checkSource(source) {
    if (typeof source !== "string" || source.length > 50000 || !/^\s*\/\/@version=[56](?:\r?\n|$)/.test(source)) {
      throw new Error("Use Pine v5/v6 source, starting with //@version=5 or //@version=6 (50,000 characters maximum).");
    }
    // This is a capability filter, not a Pine parser. PineTS parses/compiles next.
    const code = source.replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\/\/[^\n]*/g, (text) => text.replace(/[^\n]/g, " "));
    const blocked = /\b(library|import|request|line|box|label|polyline|linefill|matrix|map|array|plotcandle|plotbar|plotarrow|plotchar|eval|Function|globalThis|self|window|document|fetch|XMLHttpRequest|WebSocket|EventSource|importScripts|Worker|SharedWorker|postMessage|indexedDB|caches|navigator|constructor|prototype|__proto__)\b/g;
    const filtered = code;
    const match = blocked.exec(filtered);
    if (match) throw new Error(`Line ${filtered.slice(0, match.index).split("\n").length}: ${match[0]} is not supported in local Pine scripts.`);
    if (/\b(bgcolor|barcolor)\s*\(/.test(code)) throw new Error("bgcolor() and barcolor() are not supported yet.");
    const history = /\bstrategy\s*\.\s*\w+\s*\[/.exec(code);
    if (history) compatibilityError("STRATEGY_HISTORY", `Line ${code.slice(0, history.index).split("\n").length}: PineTS cannot evaluate direct strategy-property history correctly. Assign the property to a variable before reading variable[1].`);
    if (/\bqty_percent\b/.test(code)) compatibilityError("PERCENT_EXITS", "PineTS cannot reliably simulate percentage exits. Use fixed quantities with brackets placed once per entry.");
    if (!/\b(indicator|strategy)\s*\(/.test(code)) throw new Error("An indicator() or strategy() declaration is required.");
  }

  const styles = new Set(["line", "linebr", "stepline", "histogram", "columns", "hline", "shape", "circles", "fill", "table"]);
  self.onmessage = async ({ data }) => {
    try {
      checkSource(data.source);
      if (!Array.isArray(data.bars) || !data.bars.length || data.bars.length > 4000) throw new Error("Load between 1 and 4,000 candles before running a script.");
      const indicator = new PineTSLib.Indicator(data.source);
      const kind = indicator.getDeclarationType();
      if (!["indicator", "strategy"].includes(kind)) throw new Error("Use indicator() or strategy().");
      if (kind === "strategy" && (data.marketType !== "stock" || data.timeZone !== "America/New_York" || data.seconds >= 86400)) {
        throw new Error("Strategy previews currently support intraday US equity charts only (assumed tick size $0.01).");
      }
      // Backtests must not treat a still-forming candle as a completed bar.
      const bars = kind === "strategy" ? data.bars.filter((bar) => (bar.time + data.seconds) * 1000 <= Date.now()) : data.bars;
      if (!bars.length) throw new Error("Wait for at least one candle to close before previewing a strategy.");
      const marketData = bars.map((bar) => ({
        openTime: bar.time * 1000, closeTime: (bar.time + data.seconds) * 1000,
        open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume || 0,
      }));
      const provider = {
        getMarketData: async () => marketData,
        getSymbolInfo: async () => ({ ticker: data.symbol, tickerid: data.symbol, timezone: data.timeZone, session: data.session, mintick: 0.01, pricescale: 100, minmove: 1, type: data.marketType, currency: "USD", pointvalue: 1 }),
        configure: () => {},
      };
      const engine = new PineTSLib.PineTS(provider, data.symbol, data.interval, marketData.length);
      engine.setMaxLoops(10000);
      const result = await engine.run(indicator);
      if (result.warnings.length) throw new Error(result.warnings[0].message);
      const config = kind === "strategy" ? result.strategy.config : result.indicator;
      if (config.timeframe) throw new Error("indicator(timeframe=...) is not supported; use the chart's timeframe.");
      if (kind === "strategy" && (config.calc_on_every_tick || config.calc_on_order_fills || config.use_bar_magnifier)) {
        throw new Error("Local strategy previews use closed OHLC bars; tick/fill recalculation and bar magnifier are not supported.");
      }
      const plots = [];
      const tables = [];
      for (const [key, plot] of Object.entries(result.plots)) {
        if (key.startsWith("__") && plot.data.every((point) => Array.isArray(point.value) && !point.value.length)) continue;
        const options = plot.options || {};
        const style = String(options.style || "line").replace(/^style_/, "");
        if (!styles.has(style)) throw new Error(`Plot style '${style}' is not supported yet (${plot.title || key}).`);
        // PineTS 0.10.0 plot metadata ignores strategy(overlay=true).
        const overlay = Boolean(kind === "strategy" ? config.overlay || options.force_overlay : options.overlay);
        if (style === "table") {
          const last = plot.data[plot.data.length - 1];
          for (const table of (last && last.value) || []) {
            if (table._deleted) continue;
            if (table.rows > 10 || table.columns > 4 || (table.merges || []).length) throw new Error("Tables support up to 10 rows and 4 columns, without merged cells.");
            tables.push({ cells: table.cells.map((row) => row.map((cell) => String(cell.text || "").slice(0, 120))) });
          }
          continue;
        }
        if (style === "shape" && !overlay) throw new Error("plotshape() is currently supported on overlay=true indicators only.");
        if (style === "shape" && !["AboveBar", "BelowBar"].includes(options.location)) throw new Error("Use location.abovebar or location.belowbar for plotshape().");
        if (plot.data.some((point) => Number((point.options || {}).offset || options.offset || 0) !== 0)) throw new Error("Nonzero plot offsets are not supported yet.");
        if (plot.data.some((point) => Number.isFinite(point.value) && Math.abs(point.value) > 1e15)) throw new Error("Plot values are outside the supported numeric range.");
        plots.push({ key, title: String(plot.title || key).slice(0, 80), options: { ...options, style, overlay }, data: plot.data });
      }
      if (tables.length > 2) throw new Error("Use no more than two tables per script.");
      for (const plot of plots.filter((plot) => plot.options.style === "fill")) {
        const first = plots.find((p) => p.key === plot.options.plot1);
        const second = plots.find((p) => p.key === plot.options.plot2);
        if (!first || !second || [first, second].some((p) => ["fill", "shape"].includes(p.options.style))) throw new Error("fill() requires two supported plot/hline references.");
        if (first.options.overlay !== second.options.overlay || [first, second].some((p) => p.options.display === "none")) throw new Error("Local fills require two visible plots in the same pane.");
        if (plot.options.top_color || plot.options.bottom_color) throw new Error("Gradient fills are not supported; use fill(plot1, plot2, color=...).");
      }
      if (!plots.length && !tables.length && kind !== "strategy") throw new Error("No supported plots found. Add plot(), hline(), or plotshape().");
      if (plots.length > 24) throw new Error("Use no more than 24 plots per script.");
      let simulation = null;
      if (kind === "strategy") {
        const s = result.strategy;
        if (s.closedtrades.length + s.opentrades.length > 2000) throw new Error("Strategy exceeds 2,000 simulated trade legs. Load fewer candles.");
        const trades = s.closedtrades.concat(s.opentrades).map((t) => ({
          entryId: t.entry_id, entryTime: t.entry_time / 1000, entryPrice: t.entry_price,
          exitId: t.exit_id, exitReason: t.exit_comment, exitTime: t.exit_time / 1000, exitPrice: t.exit_price, size: t.size, profit: t.profit,
        }));
        simulation = { trades, closed: s.closedtrades.length, open: s.opentrades.length,
          netProfit: s.netprofit, openProfit: s.openprofit, initialCapital: s.initial_capital, tickSize: 0.01,
          bars: bars.length, lastTime: bars[bars.length - 1].time };
      }
      send({ ok: true, kind, overlay: Boolean(config.overlay), plots, tables, simulation });
    } catch (error) {
      send({ ok: false, error: String(error.message || error).slice(0, 1500), code: error.code || null });
    }
  };
})();
