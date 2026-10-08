const PineScripts = (() => {
  const records = new Map();
  const queue = [];
  let running = 0;
  let selected = null;
  let clean = "";
  let editorJob = null;
  let busy = false;
  let libraryError = "";
  let originalDraft = null;
  const $ = (id) => document.getElementById(`pine-${id}`);
  const isPine = (id) => String(id).startsWith("pine:");
  const templates = {
    ema: { name: "EMA crossover", source: `//@version=6
indicator("EMA crossover", overlay=true)
fastLength = input.int(9, "Fast length", minval=1, maxval=500)
slowLength = input.int(21, "Slow length", minval=1, maxval=500)
fast = ta.ema(close, fastLength)
slow = ta.ema(close, slowLength)
plot(fast, "Fast EMA", color=color.orange, linewidth=2)
plot(slow, "Slow EMA", color=color.blue, linewidth=2)
buy = ta.crossover(fast, slow) and barstate.isconfirmed
sell = ta.crossunder(fast, slow) and barstate.isconfirmed
plotshape(buy, title="Buy", style=shape.triangleup, location=location.belowbar, color=color.green, text="BUY")
plotshape(sell, title="Sell", style=shape.triangledown, location=location.abovebar, color=color.red, text="SELL")
` },
    rsi: { name: "RSI", source: `//@version=6
indicator("RSI", overlay=false)
length = input.int(14, "Length", minval=1, maxval=500)
value = ta.rsi(close, length)
plot(value, "RSI", color=color.purple, linewidth=2)
plot(ta.sma(value, 14), "RSI average", color=color.orange)
hline(70, "Upper", color=color.gray)
hline(50, "Middle", color=color.gray)
hline(30, "Lower", color=color.gray)
` },
  };

  function definition(id) {
    if (!isPine(id)) return null;
    const record = records.get(Number(id.slice(5)));
    return record ? { id, label: record.name, target: record.overlay ? "main" : "lower" } : null;
  }

  async function api(url, method = "GET", body) {
    const csrf = document.cookie.split("; ").find((cookie) => cookie.startsWith("csrftoken="));
    const response = await fetch(url, {
      method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf ? decodeURIComponent(csrf.slice(10)) : "" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Pine library request failed (${response.status}).`);
    return payload;
  }

  async function load() {
    try {
      const payload = await api("/api/pine/");
      const ids = new Set(payload.scripts.map((record) => record.id));
      state.panes.forEach((pane) => {
        pane.indicatorInstances.filter((instance) => isPine(instance.type) && !ids.has(Number(instance.type.slice(5))))
          .forEach((instance) => removeIndicator(pane, instance.uid));
      });
      records.clear();
      payload.scripts.forEach((record) => records.set(record.id, record));
      state.panes.forEach((pane) => {
        appendOptions(pane.indicatorSelect);
        renderIndicatorChips(pane);
        pane.indicatorInstances.filter((instance) => isPine(instance.type)).forEach((instance) => update(pane, instance));
        withPreservedVisibleRange(pane, () => updateLowerChartVisibility(pane));
      });
      libraryError = "";
    } catch (error) {
      libraryError = error.message;
    }
    renderLibrary();
  }

  function appendOptions(select) {
    const old = select.querySelector("[data-pine-options]");
    if (old) old.remove();
    const group = document.createElement("optgroup");
    group.dataset.pineOptions = "true";
    group.label = "Saved Pine scripts";
    [...records.values()].sort((a, b) => a.name.localeCompare(b.name)).forEach((record) => {
      const option = document.createElement("option");
      option.value = `pine:${record.id}`;
      option.textContent = record.name;
      group.append(option);
    });
    if (records.size) select.append(group);
  }

  function dispatch() {
    while (running < 2 && queue.length) {
      const job = queue.shift();
      if (job.cancelled) continue;
      running += 1;
      job.started = true;
      job.finish = (error, result) => {
        if (job.done) return;
        job.done = true;
        clearTimeout(job.timer);
        if (job.worker) job.worker.terminate();
        running -= 1;
        if (error) job.reject(error); else job.resolve(result);
        dispatch();
      };
      try {
        job.worker = new Worker("/api/pine/worker/");
        job.timer = setTimeout(() => job.finish(new Error("Script exceeded the 8-second execution limit.")), 8000);
        job.worker.onmessage = ({ data }) => {
          const error = data.ok ? null : new Error(data.error);
          if (error) error.code = data.code;
          job.finish(error, data);
        };
        job.worker.onerror = (event) => { event.preventDefault(); job.finish(new Error(event.message || "Pine worker could not start.")); };
        job.worker.postMessage(job.payload);
      } catch (error) { job.finish(error); }
    }
  }

  function execute(source, pane) {
    const interval = /^(\d+)([mhd])$/.exec(pane.timeframe);
    if (!interval) throw new Error("This chart timeframe is not supported by Pine indicators.");
    const seconds = Number(interval[1]) * { m: 60, h: 3600, d: 86400 }[interval[2]];
    const crypto = providerFor(pane.symbol) === "hyperliquid";
    const india = /\.(NS|BO)$/.test(pane.symbol);
    const job = { payload: {
      source, bars: pane.bars, seconds, symbol: pane.symbol,
      interval: interval[2] === "d" ? "D" : String(seconds / 60),
      timeZone: crypto ? "UTC" : india ? "Asia/Kolkata" : "America/New_York",
      session: crypto ? "24x7" : india ? "0915-1530" : "0930-1600", marketType: crypto ? "crypto" : "stock",
    } };
    job.promise = new Promise((resolve, reject) => { job.resolve = resolve; job.reject = reject; });
    job.cancel = () => {
      if (job.done || job.cancelled) return;
      job.cancelled = true;
      const error = new Error("Cancelled");
      error.name = "AbortError";
      if (job.started) job.finish(error); else { job.reject(error); const i = queue.indexOf(job); if (i >= 0) queue.splice(i, 1); }
    };
    queue.push(job);
    dispatch();
    return job;
  }

  function cancel(instance) {
    clearTimeout(instance.pineTimer);
    if (instance.pineJob) instance.pineJob.cancel();
    instance.pineJob = null;
    instance.pineKey = null;
    instance.pineContext = null;
    instance.pineFailedContext = null;
    instance.pineTimer = null;
  }

  function resetPane(pane) {
    pane.indicatorInstances.filter((instance) => isPine(instance.type)).forEach((instance) => {
      cancel(instance);
      removeIndicatorSeries(pane, instance.uid);
    });
  }

  function clearVisuals(pane, uid) {
    const instance = pane.indicatorInstances.find((item) => item.uid === uid);
    if (!instance) return;
    (instance.pineFills || []).forEach(({ series, primitive }) => series.detachPrimitive(primitive));
    instance.pineFills = [];
    if (instance.pineResultNode) instance.pineResultNode.remove();
    instance.pineResultNode = null;
    instance.pineSimulation = null;
  }

  function color(value, fallback = "#2962ff") {
    return typeof value === "string" && CSS.supports("color", value) ? value : fallback;
  }

  class PlotFill {
    constructor(points) {
      this.points = points;
      this.view = { zOrder: () => "bottom", renderer: () => this };
    }
    attached({ chart, series, requestUpdate }) { this.chart = chart; this.series = series; this.requestUpdate = requestUpdate; }
    detached() { this.chart = null; this.series = null; this.requestUpdate = null; }
    paneViews() { return [this.view]; }
    updateAllViews() {}
    draw(target) {
      if (!this.chart) return;
      target.useMediaCoordinateSpace(({ context, mediaSize }) => {
        let previous = null;
        for (const point of this.points) {
          if (!point) { previous = null; continue; }
          const x = this.chart.timeScale().timeToCoordinate(point.time);
          const top = this.series.priceToCoordinate(point.top);
          const bottom = this.series.priceToCoordinate(point.bottom);
          if (x === null || top === null || bottom === null) { previous = null; continue; }
          if (previous && x >= 0 && previous.x <= mediaSize.width) {
            context.fillStyle = point.color;
            context.beginPath();
            context.moveTo(previous.x, previous.top);
            context.lineTo(x, top);
            context.lineTo(x, bottom);
            context.lineTo(previous.x, previous.bottom);
            context.closePath();
            context.fill();
          }
          previous = { x, top, bottom };
        }
      });
    }
  }

  function renderResults(pane, instance, result) {
    const simulation = result.simulation;
    instance.pineSimulation = simulation;
    if (!simulation && !(result.tables || []).length) {
      if (instance.pineResultNode) instance.pineResultNode.remove();
      instance.pineResultNode = null;
      return;
    }
    if (!instance.pineResultNode) {
      instance.pineResultNode = document.createElement("details");
      instance.pineResultNode.className = "pine-result";
      pane.root.querySelector(".chart-footer").append(instance.pineResultNode);
    }
    const node = instance.pineResultNode;
    const summary = document.createElement("summary");
    const name = definition(instance.type).label;
    summary.textContent = simulation ? `Simulation: ${name}` : name;
    summary.title = name;
    const content = document.createElement("div");
    content.className = "pine-result-content";
    function addTable(rows) {
      const table = document.createElement("table");
      rows.forEach((row) => {
        const tr = document.createElement("tr");
        row.forEach((value) => { const td = document.createElement("td"); td.textContent = value; tr.append(td); });
        table.append(tr);
      });
      content.append(table);
    }
    if (simulation) {
      addTable([
        ["Local simulation", `${simulation.bars} closed bars`],
        ["Starting capital", simulation.initialCapital.toFixed(2)],
        ["Closed legs / open positions", `${simulation.closed} / ${simulation.open}`],
        ["Net P/L", simulation.netProfit.toFixed(2)], ["Open P/L", simulation.openProfit.toFixed(2)],
        ["Assumed tick", "$0.01"],
      ]);
      content.title = "Experimental OHLC simulation, not broker fills or TradingView-verified results. Independent of your paper account.";
    }
    (result.tables || []).forEach((table) => addTable(table.cells));
    if (simulation && simulation.trades.length) {
      const heading = document.createElement("strong");
      heading.textContent = "Recent simulated fills";
      content.append(heading);
      addTable([["Side", "Entry", "Exit", "P/L"]].concat(simulation.trades.slice(-20).reverse().map((trade) => [
        trade.size > 0 ? "BUY" : "SELL", trade.entryPrice.toFixed(2),
        Number.isFinite(trade.exitPrice) ? trade.exitPrice.toFixed(2) : "Open",
        Number.isFinite(trade.profit) ? trade.profit.toFixed(2) : "-",
      ])));
    }
    const previousScroll = node.querySelector(".pine-result-content");
    const scrollTop = previousScroll ? previousScroll.scrollTop : 0;
    node.replaceChildren(summary, content);
    content.scrollTop = scrollTop;
  }

  function readableMarkers(pane, markers) {
    const lastX = { aboveBar: -Infinity, belowBar: -Infinity };
    const scale = pane.chart.timeScale();
    const width = pane.chart.paneSize().width;
    return markers.map((marker) => {
      if (!marker.simulationLabel) return marker;
      const x = scale.timeToCoordinate(marker.time);
      const show = scale.options().barSpacing >= 7 && x !== null && x >= 30 && x < width - 30 && x - lastX[marker.position] >= 85;
      if (show) lastX[marker.position] = x;
      const { simulationLabel, ...rest } = marker;
      return { ...rest, text: show ? simulationLabel : "" };
    });
  }

  function render(pane, instance, result) {
    const definitions = [];
    const sets = [];
    const markers = [];
    const seriesIndices = new Map();
    const times = new Set(pane.bars.map((bar) => bar.time));
    result.plots.forEach((plot) => {
      const options = plot.options;
      if (options.display === "none") return;
      const chart = options.overlay ? pane.chart : pane.lowerChart;
      const style = options.style;
      if (style === "fill") return;
      if (style === "shape") {
        plot.data.forEach((point) => {
          if (!point.value || !times.has(point.time / 1000)) return;
          const opts = { ...options, ...(point.options || {}) };
          const shape = String(opts.shape || "shape_circle");
          if (!/triangle_up|triangle_down|arrow_up|arrow_down|circle|square/.test(shape)) throw new Error(`Shape '${shape}' is not supported yet.`);
          markers.push({ time: point.time / 1000, position: opts.location === "BelowBar" ? "belowBar" : "aboveBar",
            shape: /up/.test(shape) ? "arrowUp" : /down/.test(shape) ? "arrowDown" : /square/.test(shape) ? "square" : "circle",
            color: color(opts.color), size: opts.size === "tiny" ? 0.4 : opts.size === "small" ? 0.7 : 1,
            text: String(opts.text || "").slice(0, 40) });
        });
        return;
      }
      const histogram = style === "histogram" || style === "columns";
      let segments = [[]];
      plot.data.forEach((point) => {
        const time = point.time / 1000;
        if (!times.has(time)) return;
        const value = point.value;
        if (typeof value !== "number" || !Number.isFinite(value)) {
          if (style === "linebr" && segments[segments.length - 1].length) segments.push([]);
          else if (style !== "linebr") segments[0].push({ time });
          return;
        }
        const pointColor = (point.options || {}).color;
        segments[segments.length - 1].push({ time, value, color: color(pointColor, color(options.color)) });
      });
      segments = segments.filter((segment) => segment.length);
      segments.forEach((points) => {
        if (!seriesIndices.has(plot.key)) seriesIndices.set(plot.key, definitions.length);
        const lineStyle = /dash/.test(String(options.linestyle)) ? 2 : /dot/.test(String(options.linestyle)) ? 1 : 0;
        definitions.push({ chart, create: () => histogram
          ? chart.addHistogramSeries({ color: color(options.color), title: "", priceLineVisible: false })
          : chart.addLineSeries({ color: color(options.color), title: "", priceLineVisible: false, lastValueVisible: style !== "hline",
            lineWidth: Math.max(1, Math.min(4, Number(options.linewidth) || 1)), lineStyle,
            lineType: style === "stepline" ? 1 : 0,
            lineVisible: style !== "circles", pointMarkersVisible: style === "circles",
            pointMarkersRadius: Math.max(2, Math.min(5, Number(options.linewidth) || 2)) }) });
        sets.push(points);
      });
    });
    if (result.simulation && result.overlay) {
      const entries = new Set();
      const exits = new Map();
      result.simulation.trades.forEach((trade) => {
        const entryKey = `${trade.entryTime}:${trade.entryId}`;
        if (!entries.has(entryKey) && times.has(trade.entryTime)) {
          entries.add(entryKey);
          markers.push({ time: trade.entryTime, position: trade.size > 0 ? "belowBar" : "aboveBar", shape: trade.size > 0 ? "arrowUp" : "arrowDown",
            color: trade.size > 0 ? "#089981" : "#f23645", size: 0.7, simulationLabel: trade.size > 0 ? "SIM BUY" : "SIM SELL" });
        }
        if (times.has(trade.exitTime)) {
          const key = `${trade.exitTime}:${trade.size > 0}`;
          if (!exits.has(key)) exits.set(key, { time: trade.exitTime, position: trade.size > 0 ? "aboveBar" : "belowBar", shape: "circle", color: "#b88600", size: 0.5, simulationLabel: "SIM EXIT" });
        }
      });
      markers.push(...exits.values());
    }
    if (definitions.length > 64 || markers.length > 1000) throw new Error("Script exceeds the chart limit of 64 line segments or 1,000 markers.");
    // Series types cannot change through applyOptions; rebuild only when the plot schema changes.
    const schema = JSON.stringify(result.plots.map((plot) => [plot.key, plot.options, plot.data.length && plot.data[0].options]));
    withPreservedVisibleRange(pane, () => {
      if (schema !== instance.pineSchema) removeIndicatorSeries(pane, instance.uid);
      instance.pineSchema = schema;
      const { series } = ensureIndicatorSeries(pane, instance, definitions);
      series.forEach((line, index) => line.setData(sets[index]));
      (instance.pineFills || []).forEach((fill) => fill.series.detachPrimitive(fill.primitive));
      instance.pineFills = [];
      result.plots.filter((plot) => plot.options.style === "fill" && plot.options.display !== "none").forEach((fill) => {
        const first = result.plots.find((plot) => plot.key === fill.options.plot1);
        const second = result.plots.find((plot) => plot.key === fill.options.plot2);
        const owner = series[seriesIndices.get(first.key)];
        if (!owner) return;
        const top = new Map(first.data.map((p) => [p.time, p.value]));
        const bottom = new Map(second.data.map((p) => [p.time, p.value]));
        const points = [];
        fill.data.forEach((p) => {
          if (times.has(p.time / 1000) && Number.isFinite(top.get(p.time)) && Number.isFinite(bottom.get(p.time))) {
            points.push({ time: p.time / 1000, top: top.get(p.time), bottom: bottom.get(p.time), color: color((p.options || {}).color, color(fill.options.color, "transparent")) });
          } else if (fill.options.fillgaps === false) points.push(null);
        });
        const primitive = new PlotFill(points);
        owner.attachPrimitive(primitive);
        instance.pineFills.push({ series: owner, primitive });
      });
      replaceIndicatorMarkers(pane, instance, markers);
      renderResults(pane, instance, result);
      updateLowerChartVisibility(pane);
    });
  }

  function update(pane, instance) {
    const record = records.get(Number(instance.type.slice(5)));
    if (!record || !pane.bars.length) return;
    const context = JSON.stringify([record.revision, pane.symbol, pane.timeframe]);
    if (instance.pineContext !== context) { cancel(instance); instance.pineContext = context; }
    const interval = /^(\d+)([mh])$/.exec(pane.timeframe);
    const duration = interval ? Number(interval[1]) * (interval[2] === "h" ? 3600 : 60) : 86400;
    const calculationBars = instance.pineKind === "strategy" ? pane.bars.filter((bar) => (bar.time + duration) * 1000 <= Date.now()) : pane.bars;
    const key = JSON.stringify([record.revision, pane.symbol, pane.timeframe, calculationBars]);
    instance.pineRequestedKey = key;
    if (instance.pineKey === key || instance.pineTimer || instance.pineJob || instance.pineFailedContext === context) return;
    // Coalesce incoming ticks without repeatedly cancelling a running calculation.
    instance.pineTimer = setTimeout(() => {
      instance.pineTimer = null;
      const executedKey = instance.pineRequestedKey;
      instance.pineKey = executedKey;
      try {
        const job = execute(record.source, pane);
        instance.pineJob = job;
        job.promise.then((result) => {
          if (pane.destroyed || instance.pineContext !== context || !pane.indicatorInstances.includes(instance)) return;
          instance.pineKind = result.kind;
          render(pane, instance, result);
          instance.pineError = "";
          renderIndicatorChips(pane);
        }).catch((error) => {
          if (error.name === "AbortError" || pane.destroyed || instance.pineContext !== context) return;
          instance.pineError = error.message;
          instance.pineFailedContext = context;
          withPreservedVisibleRange(pane, () => removeIndicatorSeries(pane, instance.uid));
          renderIndicatorChips(pane);
        }).finally(() => {
          if (instance.pineJob !== job) return;
          instance.pineJob = null;
          if (!pane.destroyed && instance.pineRequestedKey !== executedKey) update(pane, instance);
        });
      } catch (error) { instance.pineFailedContext = context; instance.pineError = error.message; renderIndicatorChips(pane); }
    }, 200);
  }

  function status(message, error = false) {
    $("status").textContent = message;
    $("status").classList.toggle("error", error);
    $("compatible").hidden = true;
  }

  function showError(error) {
    status(error.message, true);
    const scalper = /\bstrategy\s*\(\s*(["'])Trend Breakdown Scalper[^"'\n]*\1/.test($("source").value);
    if (scalper && ["STRATEGY_HISTORY", "PERCENT_EXITS"].includes(error.code)) {
      $("compatible").hidden = false;
    }
  }

  function snapshot() { return `${$("name").value}\n${$("source").value}`; }
  function mayDiscard() { return snapshot() === clean || window.confirm("Discard unsaved Pine changes?"); }

  function setBusy(value) {
    busy = value;
    ["validate", "save", "add", "new", "template", "chart", "compatible", "restore"].forEach((id) => { $(id).disabled = value; });
    $("delete").disabled = value || !selected;
    $("name").readOnly = value;
    $("source").readOnly = value;
  }

  function renderLibrary() {
    $("library-list").replaceChildren();
    records.forEach((record) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = record.name;
      button.classList.toggle("active", Boolean(selected && selected.id === record.id));
      button.addEventListener("click", () => { if (!busy && mayDiscard()) fill(record); });
      $("library-list").append(button);
    });
  }

  function fill(record, preserveOriginal = false) {
    if (!preserveOriginal) originalDraft = null;
    $("restore").hidden = !originalDraft;
    selected = record && record.id ? record : null;
    $("name").value = record.name;
    $("source").value = record.source;
    clean = snapshot();
    setBusy(false);
    renderLibrary();
    status(selected ? `Saved revision ${selected.revision}` : "Unsaved");
  }

  function target() {
    const pane = state.panes[Number($("chart").value)];
    if (!pane || !pane.bars.length) throw new Error("The selected chart has no candles yet.");
    return pane;
  }

  async function open(id, chartIndex = 0) {
    if (busy || ($("editor").open && !mayDiscard())) return;
    $("chart").replaceChildren();
    state.panes.forEach((pane) => {
      const option = document.createElement("option");
      option.value = String(pane.index);
      option.textContent = `Chart ${pane.index + 1}: ${pane.symbol} ${pane.timeframe}`;
      $("chart").append(option);
    });
    $("chart").value = String(chartIndex);
    await load();
    fill(records.get(id) || templates.ema);
    if (libraryError) status(libraryError, true);
    if (!$("editor").open) $("editor").showModal();
    $("source").focus();
  }

  async function validate() {
    const pane = target();
    if (!$("name").value.trim()) throw new Error("Give the script a name.");
    status(`Validating on ${pane.symbol} ${pane.timeframe} (${pane.bars.length} candles)...`);
    editorJob = execute($("source").value, pane);
    const result = await editorJob.promise;
    // Validate renderer features even if no signals were triggered in this dataset.
    for (const plot of result.plots) {
      if (plot.options.style === "shape" && !/triangle_up|triangle_down|arrow_up|arrow_down|circle|square/.test(String(plot.options.shape || "shape_circle"))) {
        throw new Error(`Shape '${plot.options.shape}' is not supported yet.`);
      }
    }
    status(`Valid on ${pane.symbol} ${pane.timeframe}: ${result.plots.length} plots. ${result.overlay ? "Price overlay" : "Lower pane"}.${simulationNotice(result)}`);
    return result;
  }

  async function action(kind) {
    if (busy) return;
    setBusy(true);
    try {
      if (kind === "add") {
        const pane = target();
        if (!pane.indicatorInstances.some((instance) => selected && instance.type === `pine:${selected.id}`)
            && pane.indicatorInstances.filter((instance) => isPine(instance.type)).length >= 4) {
          throw new Error("Use no more than four Pine indicators per chart.");
        }
      }
      const result = await validate();
      if (kind !== "validate") {
        const payload = await api(selected ? `/api/pine/${selected.id}/` : "/api/pine/", selected ? "PUT" : "POST", {
          name: $("name").value, source: $("source").value, overlay: result.overlay, revision: selected ? selected.revision : undefined,
        });
        records.set(payload.script.id, payload.script);
        fill(payload.script, Boolean(originalDraft));
        state.panes.forEach((pane) => {
          appendOptions(pane.indicatorSelect);
          pane.indicatorInstances.filter((instance) => instance.type === `pine:${selected.id}`).forEach((instance) => {
            cancel(instance); update(pane, instance);
          });
          renderIndicatorChips(pane);
          withPreservedVisibleRange(pane, () => updateLowerChartVisibility(pane));
        });
        if (kind === "add") {
          const pane = target();
          addIndicator(pane, `pine:${selected.id}`);
          status(`Saved and added to chart ${pane.index + 1}.${simulationNotice(result)}`);
        } else status(`Saved revision ${selected.revision}.${simulationNotice(result)}`);
      }
    } catch (error) { if (error.name !== "AbortError") showError(error); }
    finally { editorJob = null; setBusy(false); }
  }

  function simulationNotice(result) {
    return result.simulation ? ` Local simulation only: ${result.simulation.closed} closed legs on ${result.simulation.bars} closed candles. Tick $0.01. No account orders. Experimental results can differ from TradingView.` : "";
  }

  function close() {
    if (busy || !mayDiscard()) return;
    $("editor").close();
  }

  $("compatible").addEventListener("click", async () => {
    if (busy || !window.confirm("Load the corrected Trend scalper as a new draft? This uses template defaults, not your custom edits. Your current source will be available under Restore original source. Saved scripts will not be changed.")) return;
    setBusy(true);
    try {
      const template = await api("/api/pine/scalper-template/");
      originalDraft = {
        record: { ...(selected || {}), name: $("name").value, source: $("source").value },
        clean, template: $("template").value,
      };
      // A compatible copy is a new document, never an update to the original.
      let name = "Trend Breakdown Scalper - Local";
      let suffix = 2;
      const names = new Set([...records.values()].map((record) => record.name));
      while (names.has(name)) name = `Trend Breakdown Scalper - Local ${suffix++}`;
      fill({ name, source: template.source }, true);
      clean = "";
      $("template").value = "scalper";
      status("Compatible scalper loaded as a new, unsaved draft. Template defaults applied; original source retained.");
      $("validate").focus();
    } catch (error) { status(error.message, true); $("compatible").hidden = false; }
    finally { setBusy(false); }
  });
  $("restore").addEventListener("click", () => {
    if (busy || !originalDraft || !mayDiscard()) return;
    const original = originalDraft;
    fill(original.record);
    clean = original.clean;
    $("template").value = original.template;
    status("Original source restored.");
  });

  $("toggle").addEventListener("click", () => open(selected && selected.id));
  $("close").addEventListener("click", close);
  $("editor").addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  $("new").addEventListener("click", () => { if (mayDiscard()) fill(templates.ema); });
  $("template").addEventListener("change", async () => {
    if (!mayDiscard()) return;
    try {
      setBusy(true);
      const template = $("template").value;
      const sources = { trader: ["Trader B/S", "/api/pine/trader-template/"], scalper: ["Trend Breakdown Scalper - Local", "/api/pine/scalper-template/"] };
      fill(sources[template] ? { name: sources[template][0], source: (await api(sources[template][1])).source } : templates[template]);
    } catch (error) { status(error.message, true); }
    finally { setBusy(false); }
  });
  ["validate", "save", "add"].forEach((kind) => $(kind).addEventListener("click", () => action(kind)));
  $("source").addEventListener("keydown", (event) => {
    if (event.key === "Tab" && !busy) {
      event.preventDefault();
      $("source").setRangeText("    ", $("source").selectionStart, $("source").selectionEnd, "end");
      status("Unsaved");
    }
  });
  ["name", "source"].forEach((id) => $(id).addEventListener("input", () => status(snapshot() === clean ? "Unchanged" : "Unsaved")));
  $("delete").addEventListener("click", async () => {
    if (!selected || busy || !window.confirm(`Delete '${selected.name}' and remove it from all charts?`)) return;
    setBusy(true);
    try {
      await api(`/api/pine/${selected.id}/`, "DELETE", { revision: selected.revision });
      const id = selected.id;
      state.panes.forEach((pane) => pane.indicatorInstances.filter((instance) => instance.type === `pine:${id}`).forEach((instance) => removeIndicator(pane, instance.uid)));
      records.delete(id);
      state.panes.forEach((pane) => appendOptions(pane.indicatorSelect));
      fill(templates.ema);
      status("Deleted");
    } catch (error) { status(error.message, true); }
    finally { setBusy(false); }
  });
  window.addEventListener("beforeunload", (event) => {
    if ($("editor").open && snapshot() !== clean) { event.preventDefault(); event.returnValue = ""; }
  });
  return { isPine, definition, appendOptions, load, open, update, cancel, resetPane, clearVisuals, readableMarkers };
})();
