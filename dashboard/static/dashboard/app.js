const DEFAULT_COUNT = 4;
const COUNTS = [1, 2, 4, 6, 8];
const STORAGE_KEY = "trading-dashboard-chart-count";
const CUSTOM_SYMBOLS_STORAGE_KEY = "trading-dashboard-custom-symbols";
const DRAWINGS_STORAGE_KEY = "trading-dashboard-drawings";

const INDICATORS = [
  { id: "sma", label: "SMA", target: "main", repeatable: true, defaults: { period: 20 } },
  { id: "ema", label: "EMA", target: "main", repeatable: true, defaults: { period: 20 } },
  { id: "vwap", label: "VWAP", target: "main" },
  { id: "bb", label: "Bollinger Bands", target: "main" },
  { id: "volume", label: "Volume", target: "main" },
  { id: "supertrend", label: "Supertrend", target: "main" },
  { id: "rsi", label: "RSI 14", target: "lower" },
  { id: "macd", label: "MACD", target: "lower" },
  { id: "atr", label: "ATR 14", target: "lower" },
];

const OVERLAY_COLORS = ["#f2cc8f", "#4dabf7", "#da77f2", "#20c997", "#ff922b", "#74c0fc", "#f06595"];

const grid = document.querySelector("#chart-grid");
const countSelect = document.querySelector("#chart-count");

const state = {
  config: null,
  panes: [],
};

function getSavedCount() {
  const value = Number(localStorage.getItem(STORAGE_KEY));
  return COUNTS.includes(value) ? value : DEFAULT_COUNT;
}

function formatPrice(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: Number(value) >= 100 ? 2 : 4,
    maximumFractionDigits: Number(value) >= 100 ? 2 : 6,
  });
}

function providerFor(symbol) {
  const symbolConfig = state.config.symbols.find((item) => item.value === symbol);
  return symbolConfig ? symbolConfig.provider : undefined;
}

function symbolLabel(symbol) {
  const symbolConfig = state.config.symbols.find((item) => item.value === symbol);
  return symbolConfig ? symbolConfig.label : symbol;
}

function upsertSymbol(symbolConfig) {
  const existing = state.config.symbols.find((item) => item.value === symbolConfig.value);
  if (existing) {
    return existing;
  }
  state.config.symbols.push(symbolConfig);
  return symbolConfig;
}

function loadCustomSymbols() {
  try {
    const symbols = JSON.parse(localStorage.getItem(CUSTOM_SYMBOLS_STORAGE_KEY) || "[]");
    if (Array.isArray(symbols)) {
      symbols.forEach((symbolConfig) => {
        if (symbolConfig && symbolConfig.value && symbolConfig.provider) {
          upsertSymbol(symbolConfig);
        }
      });
    }
  } catch (error) {
    localStorage.removeItem(CUSTOM_SYMBOLS_STORAGE_KEY);
  }
}

function saveCustomSymbol(symbolConfig) {
  const symbols = JSON.parse(localStorage.getItem(CUSTOM_SYMBOLS_STORAGE_KEY) || "[]");
  const nextSymbols = Array.isArray(symbols) ? symbols.filter((item) => item.value !== symbolConfig.value) : [];
  nextSymbols.push(symbolConfig);
  localStorage.setItem(CUSTOM_SYMBOLS_STORAGE_KEY, JSON.stringify(nextSymbols.slice(-100)));
}

function makeSelect(values, selected, label, formatter = (item) => item) {
  const select = document.createElement("select");
  select.setAttribute("aria-label", label);
  values.forEach((item) => {
    const option = document.createElement("option");
    option.value = typeof item === "string" ? item : item.value;
    option.textContent = formatter(item);
    if (option.value === selected) {
      option.selected = true;
    }
    select.append(option);
  });
  return select;
}

function makeSymbolSelect(selected, label) {
  const select = document.createElement("select");
  select.setAttribute("aria-label", label);
  let currentGroup = null;
  let group = null;

  state.config.symbols.forEach((item) => {
    if (item.group !== currentGroup) {
      currentGroup = item.group;
      group = document.createElement("optgroup");
      group.label = currentGroup;
      select.append(group);
    }

    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = `${item.label} - ${item.exchange}`;
    if (option.value === selected) {
      option.selected = true;
    }
    group.append(option);
  });

  return select;
}

function createIndicatorSelect(index) {
  const select = document.createElement("select");
  select.className = "indicator-select";
  select.setAttribute("aria-label", `Add indicator to chart ${index + 1}`);

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Indicators";
  select.append(placeholder);

  INDICATORS.forEach((indicator) => {
    const option = document.createElement("option");
    option.value = indicator.id;
    option.textContent = indicator.repeatable ? `${indicator.label}...` : indicator.label;
    select.append(option);
  });

  return select;
}

function applyFlash(pane, nextPrice) {
  if (!pane.lastPrice || !nextPrice || nextPrice === pane.lastPrice) {
    pane.lastPrice = nextPrice;
    return;
  }

  pane.ticker.classList.remove("up", "down");
  pane.ticker.classList.add(nextPrice > pane.lastPrice ? "up" : "down");
  pane.lastPrice = nextPrice;
  window.clearTimeout(pane.flashTimer);
  pane.flashTimer = window.setTimeout(() => pane.ticker.classList.remove("up", "down"), 420);
}

function setTicker(pane, symbol, price) {
  pane.symbolEl.textContent = symbolLabel(symbol);
  pane.priceEl.textContent = formatPrice(price);
  applyFlash(pane, price);
}

function indicatorById(id) {
  return INDICATORS.find((indicator) => indicator.id === id);
}

function buildPane(index) {
  const defaultSymbol = state.config.symbols[index % state.config.symbols.length].value;
  const pane = {
    index,
    symbol: defaultSymbol,
    timeframe: "1m",
    chart: null,
    lowerChart: null,
    series: null,
    indicatorSeries: new Map(),
    indicatorInstances: [],
    nextIndicatorId: 1,
    drawings: [],
    nextDrawingId: 1,
    drawingMode: null,
    bars: [],
    socket: null,
    pollTimer: null,
    lastPrice: null,
    flashTimer: null,
  };

  const root = document.createElement("section");
  root.className = "pane";

  const header = document.createElement("div");
  header.className = "pane-header";

  const ticker = document.createElement("div");
  ticker.className = "ticker";
  const symbolEl = document.createElement("span");
  symbolEl.className = "ticker-symbol";
  const priceEl = document.createElement("span");
  priceEl.className = "ticker-price";
  ticker.append(symbolEl, priceEl);

  const symbolSelect = makeSymbolSelect(pane.symbol, `Symbol for chart ${index + 1}`);
  const timeframeSelect = makeSelect(state.config.timeframes, pane.timeframe, `Timeframe for chart ${index + 1}`);
  const indicatorSelect = createIndicatorSelect(index);
  const chips = document.createElement("div");
  chips.className = "indicator-chips";
  const drawingTools = document.createElement("div");
  drawingTools.className = "drawing-tools";
  const supportButton = document.createElement("button");
  supportButton.type = "button";
  supportButton.textContent = "Support";
  supportButton.dataset.mode = "support";
  const resistanceButton = document.createElement("button");
  resistanceButton.type = "button";
  resistanceButton.textContent = "Resistance";
  resistanceButton.dataset.mode = "resistance";
  const drawingHint = document.createElement("span");
  drawingHint.className = "drawing-hint";
  const drawingChips = document.createElement("div");
  drawingChips.className = "drawing-chips";
  drawingTools.append(supportButton, resistanceButton, drawingHint, drawingChips);
  const customForm = document.createElement("form");
  customForm.className = "custom-symbol";
  const customMarket = makeSelect(
    [
      { value: "us", label: "US" },
      { value: "india_nse", label: "India NSE" },
      { value: "india_bse", label: "India BSE" },
      { value: "raw", label: "Raw Yahoo" },
    ],
    "us",
    `Market for custom symbol ${index + 1}`,
    (item) => item.label,
  );
  const customInput = document.createElement("input");
  customInput.type = "text";
  customInput.placeholder = "AAPL / SBIN / 500325.BO";
  customInput.setAttribute("aria-label", `Custom symbol for chart ${index + 1}`);
  const customButton = document.createElement("button");
  customButton.type = "submit";
  customButton.textContent = "Add";
  customForm.append(customMarket, customInput, customButton);

  const chartShell = document.createElement("div");
  chartShell.className = "chart-shell";
  const chartEl = document.createElement("div");
  chartEl.className = "chart main-chart";
  const lowerChartEl = document.createElement("div");
  lowerChartEl.className = "chart lower-chart";
  chartShell.append(chartEl, lowerChartEl);

  header.append(ticker, symbolSelect, timeframeSelect, indicatorSelect, customForm, drawingTools, chips);
  root.append(header, chartShell);
  grid.append(root);

  pane.root = root;
  pane.ticker = ticker;
  pane.symbolEl = symbolEl;
  pane.priceEl = priceEl;
  pane.symbolSelect = symbolSelect;
  pane.timeframeSelect = timeframeSelect;
  pane.indicatorSelect = indicatorSelect;
  pane.customMarket = customMarket;
  pane.customInput = customInput;
  pane.supportButton = supportButton;
  pane.resistanceButton = resistanceButton;
  pane.drawingHint = drawingHint;
  pane.drawingChips = drawingChips;
  pane.chips = chips;
  pane.chartShell = chartShell;
  pane.chartEl = chartEl;
  pane.lowerChartEl = lowerChartEl;

  symbolSelect.addEventListener("change", () => {
    clearDrawingLevels(pane, { save: false });
    pane.symbol = symbolSelect.value;
    startPaneFeed(pane);
  });
  timeframeSelect.addEventListener("change", () => {
    clearDrawingLevels(pane, { save: false });
    pane.timeframe = timeframeSelect.value;
    startPaneFeed(pane);
  });
  indicatorSelect.addEventListener("change", () => {
    if (indicatorSelect.value) {
      addIndicator(pane, indicatorSelect.value);
      indicatorSelect.value = "";
    }
  });
  customForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await addCustomSymbol(pane);
  });
  supportButton.addEventListener("click", () => setDrawingMode(pane, "support"));
  resistanceButton.addEventListener("click", () => setDrawingMode(pane, "resistance"));

  return pane;
}

function chartOptions(background) {
  return {
    autoSize: true,
    layout: { background: { color: background }, textColor: "#c8d0cc" },
    grid: { vertLines: { color: "#263036" }, horzLines: { color: "#263036" } },
    rightPriceScale: { borderColor: "#2a3338" },
    timeScale: { borderColor: "#2a3338", timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  };
}

function initChart(pane) {
  pane.chart = LightweightCharts.createChart(pane.chartEl, chartOptions("#1a1f22"));
  pane.series = pane.chart.addCandlestickSeries({
    upColor: "#12b886",
    downColor: "#fa5252",
    borderUpColor: "#12b886",
    borderDownColor: "#fa5252",
    wickUpColor: "#12b886",
    wickDownColor: "#fa5252",
  });

  pane.lowerChart = LightweightCharts.createChart(pane.lowerChartEl, chartOptions("#171c1f"));
  pane.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (range) {
      pane.lowerChart.timeScale().setVisibleLogicalRange(range);
    }
  });
  pane.lowerChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (range) {
      pane.chart.timeScale().setVisibleLogicalRange(range);
    }
  });
  pane.chart.subscribeClick((param) => handleChartClick(pane, param));
}

function setDrawingMode(pane, mode) {
  pane.drawingMode = pane.drawingMode === mode ? null : mode;
  pane.supportButton.classList.toggle("active", pane.drawingMode === "support");
  pane.resistanceButton.classList.toggle("active", pane.drawingMode === "resistance");
  pane.drawingHint.textContent = pane.drawingMode ? "Click chart" : "";
}

function handleChartClick(pane, param) {
  if (!pane.drawingMode || !param.point) {
    return;
  }

  const price = pane.series.coordinateToPrice(param.point.y);
  if (price === null || price === undefined || Number.isNaN(Number(price))) {
    return;
  }

  addDrawingLevel(pane, pane.drawingMode, Number(price));
  setDrawingMode(pane, null);
}

function drawingOptions(type, price) {
  const isSupport = type === "support";
  return {
    price,
    color: isSupport ? "#12b886" : "#fa5252",
    lineWidth: 2,
    lineStyle: LightweightCharts.LineStyle.Solid,
    axisLabelVisible: true,
    title: isSupport ? "Support" : "Resistance",
  };
}

function addDrawingLevel(pane, type, price) {
  const ranges = getVisibleRanges(pane);
  const id = `${type}-${pane.nextDrawingId}`;
  pane.nextDrawingId += 1;
  const line = pane.series.createPriceLine(drawingOptions(type, price));
  pane.drawings.push({ id, type, price, line });
  saveDrawingLevels(pane);
  renderDrawingChips(pane);
  restoreVisibleRanges(pane, ranges);
}

function removeDrawingLevel(pane, id) {
  const ranges = getVisibleRanges(pane);
  const drawing = pane.drawings.find((item) => item.id === id);
  if (drawing) {
    pane.series.removePriceLine(drawing.line);
  }
  pane.drawings = pane.drawings.filter((item) => item.id !== id);
  saveDrawingLevels(pane);
  renderDrawingChips(pane);
  restoreVisibleRanges(pane, ranges);
}

function clearDrawingLevels(pane, options = {}) {
  pane.drawings.forEach((drawing) => pane.series.removePriceLine(drawing.line));
  pane.drawings = [];
  if (options.save) {
    saveDrawingLevels(pane);
  }
  renderDrawingChips(pane);
  setDrawingMode(pane, null);
}

function drawingStorageKey(pane) {
  return `${pane.symbol}|${pane.timeframe}`;
}

function readDrawingStore() {
  try {
    const drawings = JSON.parse(localStorage.getItem(DRAWINGS_STORAGE_KEY) || "{}");
    return drawings && typeof drawings === "object" && !Array.isArray(drawings) ? drawings : {};
  } catch (error) {
    localStorage.removeItem(DRAWINGS_STORAGE_KEY);
    return {};
  }
}

function writeDrawingStore(store) {
  localStorage.setItem(DRAWINGS_STORAGE_KEY, JSON.stringify(store));
}

function saveDrawingLevels(pane) {
  const store = readDrawingStore();
  const key = drawingStorageKey(pane);
  const drawings = pane.drawings.map((drawing) => ({
    type: drawing.type,
    price: drawing.price,
  }));

  if (drawings.length) {
    store[key] = drawings;
  } else {
    delete store[key];
  }
  writeDrawingStore(store);
}

function restoreDrawingLevels(pane) {
  const store = readDrawingStore();
  const savedDrawings = store[drawingStorageKey(pane)] || [];
  clearDrawingLevels(pane, { save: false });
  savedDrawings.forEach((drawing) => {
    const id = `${drawing.type}-${pane.nextDrawingId}`;
    pane.nextDrawingId += 1;
    const line = pane.series.createPriceLine(drawingOptions(drawing.type, drawing.price));
    pane.drawings.push({ id, type: drawing.type, price: drawing.price, line });
  });
  renderDrawingChips(pane);
}

function renderDrawingChips(pane) {
  pane.drawingChips.replaceChildren();
  pane.drawings.forEach((drawing) => {
    const chip = document.createElement("span");
    chip.className = `drawing-chip ${drawing.type}`;

    const label = document.createElement("span");
    label.textContent = `${drawing.type === "support" ? "S" : "R"} ${formatPrice(drawing.price)}`;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "x";
    remove.title = `Remove ${drawing.type}`;
    remove.addEventListener("click", () => removeDrawingLevel(pane, drawing.id));

    chip.append(label, remove);
    pane.drawingChips.append(chip);
  });
}

async function fetchHistory(symbol, timeframe) {
  const response = await fetch(`/api/history/?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`);
  if (!response.ok) {
    throw new Error(`History request failed: ${response.status}`);
  }
  const payload = await response.json();
  return payload.bars || [];
}

async function fetchQuote(symbol) {
  const response = await fetch(`/api/quote/?symbol=${encodeURIComponent(symbol)}`);
  if (!response.ok) {
    return null;
  }
  return response.json();
}

async function fetchSymbol(symbol, market) {
  const params = new URLSearchParams({ symbol, market });
  const response = await fetch(`/api/symbol/?${params.toString()}`);
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.error || "Invalid symbol");
  }
  return response.json();
}

function addSymbolOption(select, symbolConfig) {
  let group = Array.from(select.querySelectorAll("optgroup")).find((item) => item.label === symbolConfig.group);
  if (!group) {
    group = document.createElement("optgroup");
    group.label = symbolConfig.group;
    select.append(group);
  }

  let option = Array.from(select.options).find((item) => item.value === symbolConfig.value);
  if (!option) {
    option = document.createElement("option");
    option.value = symbolConfig.value;
    option.textContent = `${symbolConfig.label} - ${symbolConfig.exchange}`;
    group.append(option);
  }
}

async function addCustomSymbol(pane) {
  const rawSymbol = pane.customInput.value.trim();
  if (!rawSymbol) {
    return;
  }

  try {
    const symbolConfig = upsertSymbol(await fetchSymbol(rawSymbol, pane.customMarket.value));
    saveCustomSymbol(symbolConfig);
    state.panes.forEach((item) => addSymbolOption(item.symbolSelect, symbolConfig));
    clearDrawingLevels(pane, { save: false });
    pane.symbol = symbolConfig.value;
    pane.symbolSelect.value = symbolConfig.value;
    pane.customInput.value = "";
    startPaneFeed(pane);
  } catch (error) {
    window.alert(error.message);
  }
}

function stopPaneFeed(pane) {
  if (pane.socket) {
    pane.socket.close();
    pane.socket = null;
  }
  if (pane.pollTimer) {
    window.clearInterval(pane.pollTimer);
    pane.pollTimer = null;
  }
}

function getVisibleRanges(pane) {
  return {
    main: pane.chart.timeScale().getVisibleLogicalRange(),
    lower: pane.lowerChart.timeScale().getVisibleLogicalRange(),
  };
}

function restoreVisibleRanges(pane, ranges) {
  window.requestAnimationFrame(() => {
    if (ranges.main) {
      pane.chart.timeScale().setVisibleLogicalRange(ranges.main);
    }
    if (ranges.lower) {
      pane.lowerChart.timeScale().setVisibleLogicalRange(ranges.lower);
    }
  });
}

function withPreservedVisibleRange(pane, callback) {
  const ranges = getVisibleRanges(pane);
  callback();
  restoreVisibleRanges(pane, ranges);
}

function setBars(pane, bars, options = {}) {
  const shouldFit = Boolean(options.fit);
  const applyBars = () => {
    pane.bars = bars;
    pane.series.setData(bars);
    updateIndicators(pane);
  };

  if (shouldFit) {
    applyBars();
  } else {
    withPreservedVisibleRange(pane, applyBars);
  }

  if (bars.length) {
    setTicker(pane, pane.symbol, bars[bars.length - 1].close);
    if (shouldFit) {
      pane.chart.timeScale().fitContent();
      pane.lowerChart.timeScale().fitContent();
    }
  } else {
    setTicker(pane, pane.symbol, null);
  }
}

function updateBar(pane, bar) {
  const ranges = getVisibleRanges(pane);
  const last = pane.bars[pane.bars.length - 1];
  if (last && last.time === bar.time) {
    pane.bars[pane.bars.length - 1] = bar;
  } else {
    pane.bars.push(bar);
    if (pane.bars.length > 600) {
      pane.bars.shift();
    }
  }
  pane.series.update(bar);
  updateIndicators(pane);
  restoreVisibleRanges(pane, ranges);
  setTicker(pane, pane.symbol, bar.close);
}

async function loadInitialHistory(pane, options = {}) {
  setBars(pane, []);
  setBars(pane, await fetchHistory(pane.symbol, pane.timeframe), options);
  if (options.restoreDrawings) {
    restoreDrawingLevels(pane);
  }
}

function startHyperliquidFeed(pane) {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${window.location.host}/ws/hyperliquid/`);
  pane.socket = socket;

  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ symbol: pane.symbol, timeframe: pane.timeframe }));
  });

  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type !== "bar") {
      return;
    }
    updateBar(pane, payload.bar);
  });
}

function startYfinanceFeed(pane) {
  let isFirstPoll = true;
  const poll = async () => {
    try {
      await loadInitialHistory(pane, { fit: isFirstPoll, restoreDrawings: isFirstPoll });
      isFirstPoll = false;
      const quote = await fetchQuote(pane.symbol);
      if (quote && quote.price !== null && quote.price !== undefined) {
        setTicker(pane, pane.symbol, quote.price);
      }
    } catch (error) {
      setTicker(pane, pane.symbol, null);
    }
  };
  poll();
  pane.pollTimer = window.setInterval(poll, 20000);
}

async function startPaneFeed(pane) {
  stopPaneFeed(pane);
  pane.lastPrice = null;
  pane.symbolEl.textContent = symbolLabel(pane.symbol);
  pane.priceEl.textContent = "--";

  if (providerFor(pane.symbol) === "hyperliquid") {
    await loadInitialHistory(pane, { fit: true, restoreDrawings: true });
    startHyperliquidFeed(pane);
  } else {
    startYfinanceFeed(pane);
  }
}

function addIndicator(pane, id) {
  const ranges = getVisibleRanges(pane);
  const indicator = indicatorById(id);
  if (!indicator) {
    return;
  }
  if (!indicator.repeatable && pane.indicatorInstances.some((instance) => instance.type === id)) {
    return;
  }

  const instance = {
    uid: `${id}-${pane.nextIndicatorId}`,
    type: id,
    params: { ...(indicator.defaults || {}) },
    color: OVERLAY_COLORS[(pane.nextIndicatorId - 1) % OVERLAY_COLORS.length],
  };
  pane.nextIndicatorId += 1;

  if (indicator.repeatable && !editIndicatorParams(instance, true)) {
    return;
  }

  pane.indicatorInstances.push(instance);
  renderIndicatorChips(pane);
  updateLowerChartVisibility(pane);
  updateIndicator(pane, instance);
  restoreVisibleRanges(pane, ranges);
}

function editIndicatorParams(instance, isNew = false) {
  if (instance.type !== "sma" && instance.type !== "ema") {
    return true;
  }

  const label = instance.type.toUpperCase();
  const input = window.prompt(`${label} length`, String(instance.params.period));
  if (input === null) {
    return !isNew;
  }

  const period = Number.parseInt(input, 10);
  if (!Number.isInteger(period) || period < 1 || period > 500) {
    window.alert("Use a whole number from 1 to 500.");
    return editIndicatorParams(instance, isNew);
  }
  instance.params.period = period;
  return true;
}

function editIndicator(pane, uid) {
  const ranges = getVisibleRanges(pane);
  const instance = pane.indicatorInstances.find((item) => item.uid === uid);
  if (!instance || !editIndicatorParams(instance)) {
    return;
  }
  renderIndicatorChips(pane);
  updateIndicator(pane, instance);
  restoreVisibleRanges(pane, ranges);
}

function removeIndicator(pane, uid) {
  const ranges = getVisibleRanges(pane);
  pane.indicatorInstances = pane.indicatorInstances.filter((instance) => instance.uid !== uid);
  removeIndicatorSeries(pane, uid);
  renderIndicatorChips(pane);
  updateLowerChartVisibility(pane);
  restoreVisibleRanges(pane, ranges);
}

function indicatorInstanceLabel(instance) {
  const indicator = indicatorById(instance.type);
  if (instance.type === "sma" || instance.type === "ema") {
    return `${indicator.label} ${instance.params.period}`;
  }
  return indicator.label;
}

function renderIndicatorChips(pane) {
  pane.chips.replaceChildren();
  pane.indicatorInstances.forEach((instance) => {
    const chip = document.createElement("span");
    chip.className = "indicator-chip";

    const label = document.createElement("button");
    label.className = "indicator-chip-label";
    label.type = "button";
    label.textContent = indicatorInstanceLabel(instance);
    label.title = `Edit ${indicatorInstanceLabel(instance)}`;
    label.addEventListener("click", () => editIndicator(pane, instance.uid));

    const remove = document.createElement("button");
    remove.className = "indicator-chip-remove";
    remove.type = "button";
    remove.textContent = "x";
    remove.title = `Remove ${indicatorInstanceLabel(instance)}`;
    remove.addEventListener("click", () => removeIndicator(pane, instance.uid));

    chip.append(label, remove);
    pane.chips.append(chip);
  });
}

function updateLowerChartVisibility(pane) {
  const hasLowerIndicator = pane.indicatorInstances.some((instance) => indicatorById(instance.type).target === "lower");
  pane.chartShell.classList.toggle("has-lower-chart", hasLowerIndicator);
}

function removeIndicatorSeries(pane, uid) {
  const entries = pane.indicatorSeries.get(uid) || [];
  entries.forEach(({ chart, series }) => chart.removeSeries(series));
  pane.indicatorSeries.delete(uid);
}

function replaceIndicatorSeries(pane, instance, entries) {
  removeIndicatorSeries(pane, instance.uid);
  pane.indicatorSeries.set(instance.uid, entries);
}

function updateIndicators(pane) {
  pane.indicatorInstances.forEach((instance) => updateIndicator(pane, instance));
}

function updateIndicator(pane, instance) {
  const updater = indicatorUpdaters[instance.type];
  if (updater) {
    updater(pane, instance);
  }
}

function average(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function linePoints(bars, values) {
  return values
    .map((value, index) => (value === null ? null : { time: bars[index].time, value }))
    .filter(Boolean);
}

function calculateSma(bars, period) {
  return bars.map((_, index) => {
    if (index < period - 1) {
      return null;
    }
    return average(bars.slice(index - period + 1, index + 1).map((bar) => bar.close));
  });
}

function calculateEma(bars, period) {
  const multiplier = 2 / (period + 1);
  let previous = null;
  return bars.map((bar, index) => {
    if (index < period - 1) {
      return null;
    }
    if (previous === null) {
      previous = average(bars.slice(0, period).map((item) => item.close));
      return previous;
    }
    previous = (bar.close - previous) * multiplier + previous;
    return previous;
  });
}

function vwapTimeZoneForSymbol(symbol) {
  if (symbol.endsWith(".NS") || symbol.endsWith(".BO")) {
    return "Asia/Kolkata";
  }
  if (providerFor(symbol) === "hyperliquid") {
    return "UTC";
  }
  return "America/New_York";
}

function calculateVwap(bars, symbol) {
  const sessionFormatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: vwapTimeZoneForSymbol(symbol),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  let cumulativePriceVolume = 0;
  let cumulativeVolume = 0;
  let currentSession = null;

  return bars.map((bar) => {
    const session = sessionFormatter.format(new Date(bar.time * 1000));
    if (session !== currentSession) {
      currentSession = session;
      cumulativePriceVolume = 0;
      cumulativeVolume = 0;
    }

    const volume = Number(bar.volume || 0);
    const source = (bar.high + bar.low + bar.close) / 3;
    cumulativePriceVolume += source * volume;
    cumulativeVolume += volume;
    return cumulativeVolume ? cumulativePriceVolume / cumulativeVolume : null;
  });
}

function calculateBollingerBands(bars, period = 20, deviations = 2) {
  return bars.map((_, index) => {
    if (index < period - 1) {
      return null;
    }
    const closes = bars.slice(index - period + 1, index + 1).map((bar) => bar.close);
    const middle = average(closes);
    const variance = average(closes.map((close) => (close - middle) ** 2));
    const offset = Math.sqrt(variance) * deviations;
    return { middle, upper: middle + offset, lower: middle - offset };
  });
}

function calculateRsi(bars, period = 14) {
  const values = Array(bars.length).fill(null);
  if (bars.length <= period) {
    return values;
  }

  let gains = 0;
  let losses = 0;
  for (let index = 1; index <= period; index += 1) {
    const change = bars[index].close - bars[index - 1].close;
    gains += Math.max(change, 0);
    losses += Math.max(-change, 0);
  }

  let averageGain = gains / period;
  let averageLoss = losses / period;
  values[period] = averageLoss === 0 ? 100 : 100 - 100 / (1 + averageGain / averageLoss);

  for (let index = period + 1; index < bars.length; index += 1) {
    const change = bars[index].close - bars[index - 1].close;
    averageGain = (averageGain * (period - 1) + Math.max(change, 0)) / period;
    averageLoss = (averageLoss * (period - 1) + Math.max(-change, 0)) / period;
    values[index] = averageLoss === 0 ? 100 : 100 - 100 / (1 + averageGain / averageLoss);
  }

  return values;
}

function calculateMacd(bars) {
  const ema12 = calculateEma(bars, 12);
  const ema26 = calculateEma(bars, 26);
  const macd = ema12.map((fast, index) => (fast === null || ema26[index] === null ? null : fast - ema26[index]));
  const macdBars = macd.map((value, index) => ({ ...bars[index], close: value === null ? 0 : value }));
  const signal = calculateEma(macdBars, 9).map((value, index) => (macd[index] === null ? null : value));
  const histogram = macd.map((value, index) => (value === null || signal[index] === null ? null : value - signal[index]));
  return { macd, signal, histogram };
}

function calculateAtr(bars, period = 14) {
  const values = Array(bars.length).fill(null);
  if (bars.length < period + 1) {
    return values;
  }

  const trueRanges = bars.map((bar, index) => {
    if (index === 0) {
      return bar.high - bar.low;
    }
    const previousClose = bars[index - 1].close;
    return Math.max(bar.high - bar.low, Math.abs(bar.high - previousClose), Math.abs(bar.low - previousClose));
  });

  let atr = average(trueRanges.slice(1, period + 1));
  values[period] = atr;
  for (let index = period + 1; index < bars.length; index += 1) {
    atr = (atr * (period - 1) + trueRanges[index]) / period;
    values[index] = atr;
  }
  return values;
}

function calculateSupertrend(bars, period = 10, multiplier = 3) {
  const atr = calculateAtr(bars, period);
  const values = Array(bars.length).fill(null);
  const directions = Array(bars.length).fill(1);
  let finalUpper = null;
  let finalLower = null;

  bars.forEach((bar, index) => {
    if (atr[index] === null || index === 0) {
      return;
    }

    const hl2 = (bar.high + bar.low) / 2;
    const basicUpper = hl2 + multiplier * atr[index];
    const basicLower = hl2 - multiplier * atr[index];
    const previous = bars[index - 1];

    finalUpper = finalUpper === null || basicUpper < finalUpper || previous.close > finalUpper ? basicUpper : finalUpper;
    finalLower = finalLower === null || basicLower > finalLower || previous.close < finalLower ? basicLower : finalLower;

    if (values[index - 1] === finalUpper) {
      directions[index] = bar.close <= finalUpper ? -1 : 1;
    } else {
      directions[index] = bar.close >= finalLower ? 1 : -1;
    }
    values[index] = directions[index] === 1 ? finalLower : finalUpper;
  });

  return { values, directions };
}

const indicatorUpdaters = {
  sma(pane, instance) {
    const period = instance.params.period;
    const series = pane.chart.addLineSeries({ color: instance.color, lineWidth: 2, title: `SMA ${period}` });
    series.setData(linePoints(pane.bars, calculateSma(pane.bars, period)));
    replaceIndicatorSeries(pane, instance, [{ chart: pane.chart, series }]);
  },
  ema(pane, instance) {
    const period = instance.params.period;
    const series = pane.chart.addLineSeries({ color: instance.color, lineWidth: 2, title: `EMA ${period}` });
    series.setData(linePoints(pane.bars, calculateEma(pane.bars, period)));
    replaceIndicatorSeries(pane, instance, [{ chart: pane.chart, series }]);
  },
  vwap(pane, instance) {
    const series = pane.chart.addLineSeries({ color: "#2962ff", lineWidth: 2, title: "VWAP Session" });
    series.setData(linePoints(pane.bars, calculateVwap(pane.bars, pane.symbol)));
    replaceIndicatorSeries(pane, instance, [{ chart: pane.chart, series }]);
  },
  bb(pane, instance) {
    const bands = calculateBollingerBands(pane.bars);
    const upper = pane.chart.addLineSeries({ color: "#adb5bd", lineWidth: 1, title: "BB Upper" });
    const middle = pane.chart.addLineSeries({ color: "#868e96", lineWidth: 1, title: "BB Mid" });
    const lower = pane.chart.addLineSeries({ color: "#adb5bd", lineWidth: 1, title: "BB Lower" });
    upper.setData(bands.map((band, index) => (band ? { time: pane.bars[index].time, value: band.upper } : null)).filter(Boolean));
    middle.setData(bands.map((band, index) => (band ? { time: pane.bars[index].time, value: band.middle } : null)).filter(Boolean));
    lower.setData(bands.map((band, index) => (band ? { time: pane.bars[index].time, value: band.lower } : null)).filter(Boolean));
    replaceIndicatorSeries(pane, instance, [
      { chart: pane.chart, series: upper },
      { chart: pane.chart, series: middle },
      { chart: pane.chart, series: lower },
    ]);
  },
  volume(pane, instance) {
    const series = pane.chart.addHistogramSeries({
      color: "#495057",
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      title: "Volume",
    });
    pane.chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    series.setData(
      pane.bars.map((bar) => ({
        time: bar.time,
        value: bar.volume || 0,
        color: bar.close >= bar.open ? "rgba(18, 184, 134, 0.32)" : "rgba(250, 82, 82, 0.32)",
      })),
    );
    replaceIndicatorSeries(pane, instance, [{ chart: pane.chart, series }]);
  },
  supertrend(pane, instance) {
    const result = calculateSupertrend(pane.bars);
    const series = pane.chart.addLineSeries({ color: "#20c997", lineWidth: 2, title: "Supertrend" });
    series.setData(
      result.values
        .map((value, index) =>
          value === null
            ? null
            : {
                time: pane.bars[index].time,
                value,
                color: result.directions[index] === 1 ? "#20c997" : "#ff6b6b",
              },
        )
        .filter(Boolean),
    );
    replaceIndicatorSeries(pane, instance, [{ chart: pane.chart, series }]);
  },
  rsi(pane, instance) {
    const series = pane.lowerChart.addLineSeries({ color: "#fcc419", lineWidth: 2, title: "RSI 14" });
    series.setData(linePoints(pane.bars, calculateRsi(pane.bars)));
    replaceIndicatorSeries(pane, instance, [{ chart: pane.lowerChart, series }]);
  },
  macd(pane, instance) {
    const result = calculateMacd(pane.bars);
    const macd = pane.lowerChart.addLineSeries({ color: "#4dabf7", lineWidth: 2, title: "MACD" });
    const signal = pane.lowerChart.addLineSeries({ color: "#f06595", lineWidth: 2, title: "Signal" });
    const histogram = pane.lowerChart.addHistogramSeries({ color: "#868e96", title: "MACD Hist" });
    macd.setData(linePoints(pane.bars, result.macd));
    signal.setData(linePoints(pane.bars, result.signal));
    histogram.setData(
      result.histogram
        .map((value, index) =>
          value === null
            ? null
            : {
                time: pane.bars[index].time,
                value,
                color: value >= 0 ? "rgba(18, 184, 134, 0.55)" : "rgba(250, 82, 82, 0.55)",
              },
        )
        .filter(Boolean),
    );
    replaceIndicatorSeries(pane, instance, [
      { chart: pane.lowerChart, series: macd },
      { chart: pane.lowerChart, series: signal },
      { chart: pane.lowerChart, series: histogram },
    ]);
  },
  atr(pane, instance) {
    const series = pane.lowerChart.addLineSeries({ color: "#ff922b", lineWidth: 2, title: "ATR 14" });
    series.setData(linePoints(pane.bars, calculateAtr(pane.bars)));
    replaceIndicatorSeries(pane, instance, [{ chart: pane.lowerChart, series }]);
  },
};

function destroyPane(pane) {
  stopPaneFeed(pane);
  window.clearTimeout(pane.flashTimer);
  if (pane.chart) {
    pane.chart.remove();
  }
  if (pane.lowerChart) {
    pane.lowerChart.remove();
  }
  pane.root.remove();
}

function renderGrid(count) {
  state.panes.forEach(destroyPane);
  state.panes = [];
  grid.className = `chart-grid chart-grid-${count}`;

  for (let index = 0; index < count; index += 1) {
    const pane = buildPane(index);
    state.panes.push(pane);
    initChart(pane);
    startPaneFeed(pane);
  }
}

async function boot() {
  const response = await fetch("/api/config/");
  state.config = await response.json();
  loadCustomSymbols();

  const savedCount = getSavedCount();
  countSelect.value = String(savedCount);
  countSelect.addEventListener("change", () => {
    const count = Math.min(8, Number(countSelect.value));
    localStorage.setItem(STORAGE_KEY, String(count));
    renderGrid(count);
  });

  renderGrid(savedCount);
}

window.addEventListener("beforeunload", () => {
  state.panes.forEach(stopPaneFeed);
});

boot();
