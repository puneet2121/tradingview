const DEFAULT_COUNT = 4;
const COUNTS = [1, 2, 4, 6, 8];
const STORAGE_KEY = "trading-dashboard-chart-count";
const CUSTOM_SYMBOLS_STORAGE_KEY = "trading-dashboard-custom-symbols";
const DRAWINGS_STORAGE_KEY = "trading-dashboard-drawings";
const PANE_LAYOUTS_STORAGE_KEY = "trading-dashboard-pane-layouts";
const PAPER_ENABLED_STORAGE_KEY = "trading-dashboard-paper-enabled";
const PAPER_STRATEGY = "testing1";
const APP_TIME_ZONE = "America/Los_Angeles";
const LOWER_PANE_DEFAULT_PERCENT = 38;
const LOWER_PANE_MIN_PERCENT = 20;
const LOWER_PANE_MAX_PERCENT = 72;
const TIME_LABEL_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: APP_TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const DATE_LABEL_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: APP_TIME_ZONE,
  month: "short",
  day: "2-digit",
});
const DATE_TIME_LABEL_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: APP_TIME_ZONE,
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const INDICATORS = [
  { id: "sma", label: "SMA", target: "main", repeatable: true, defaults: { period: 20 } },
  { id: "ema", label: "EMA", target: "main", repeatable: true, defaults: { period: 20 } },
  { id: "vwap", label: "VWAP", target: "main" },
  { id: "testing1", label: "testing1 Buy/Sell", target: "main" },
  { id: "bb", label: "Bollinger Bands", target: "main" },
  { id: "volume", label: "Volume", target: "main" },
  { id: "supertrend", label: "Supertrend", target: "main" },
  { id: "rsi", label: "RSI", target: "lower", defaults: { period: 14, maPeriod: 14 } },
  { id: "macd", label: "MACD", target: "lower" },
  { id: "atr", label: "ATR 14", target: "lower" },
];

const OVERLAY_COLORS = ["#f2cc8f", "#4dabf7", "#da77f2", "#20c997", "#ff922b", "#74c0fc", "#f06595"];

const grid = document.querySelector("#chart-grid");
const countSelect = document.querySelector("#chart-count");
const paperEquity = document.querySelector("#paper-equity");
const paperPnl = document.querySelector("#paper-pnl");
const paperReset = document.querySelector("#paper-reset");
const paperHistoryToggle = document.querySelector("#paper-history-toggle");
const paperHistory = document.querySelector("#paper-history");
const paperHistoryClose = document.querySelector("#paper-history-close");
const paperHistoryList = document.querySelector("#paper-history-list");
const paperWatchlistInput = document.querySelector("#paper-watchlist-input");
const paperWatchlistTimeframes = document.querySelector("#paper-watchlist-timeframes");
const paperWatchlistSave = document.querySelector("#paper-watchlist-save");
const paperScanNow = document.querySelector("#paper-scan-now");
const strategyToggle = document.querySelector("#strategy-toggle");
const strategyDrawer = document.querySelector("#strategy-drawer");
const strategyClose = document.querySelector("#strategy-close");
const strategyList = document.querySelector("#strategy-list");
const strategyForm = document.querySelector("#strategy-form");
const strategyId = document.querySelector("#strategy-id");
const strategyName = document.querySelector("#strategy-name");
const strategyEnabled = document.querySelector("#strategy-enabled");
const strategyTradeAsset = document.querySelector("#strategy-trade-asset");
const strategyRisk = document.querySelector("#strategy-risk");
const strategyMinDte = document.querySelector("#strategy-min-dte");
const strategyMaxDte = document.querySelector("#strategy-max-dte");
const strategyStrikeMode = document.querySelector("#strategy-strike-mode");
const strategyTakeProfit = document.querySelector("#strategy-take-profit");
const strategyStopLoss = document.querySelector("#strategy-stop-loss");
const strategyMaxSpread = document.querySelector("#strategy-max-spread");
const strategyRuleText = document.querySelector("#strategy-rule-text");
const strategyNew = document.querySelector("#strategy-new");
const strategyStatus = document.querySelector("#strategy-status");
const optionChainToggle = document.querySelector("#option-chain-toggle");
const optionChainDrawer = document.querySelector("#option-chain-drawer");
const optionChainClose = document.querySelector("#option-chain-close");
const optionUnderlying = document.querySelector("#option-underlying");
const optionExpiration = document.querySelector("#option-expiration");
const optionQuantity = document.querySelector("#option-quantity");
const optionChainRefresh = document.querySelector("#option-chain-refresh");
const optionChainSummary = document.querySelector("#option-chain-summary");
const optionCalls = document.querySelector("#option-calls");
const optionPuts = document.querySelector("#option-puts");
const optionPaperList = document.querySelector("#option-paper-list");
const optionPaperReset = document.querySelector("#option-paper-reset");
const activityDrawer = document.querySelector("#trade-activity");
const activityList = document.querySelector("#activity-list");
const activityStatus = document.querySelector("#activity-status");
const activityMore = document.querySelector("#activity-more");
let activityBefore = null;
let activityLoading = false;
let activityBrowsingOlder = false;
const activityTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: APP_TIME_ZONE, year: "numeric", month: "short", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short",
});

function activityTime(value) {
  if (value === null || value === undefined) return "--";
  return activityTimeFormatter.format(new Date(typeof value === "number" ? value * 1000 : value));
}

function renderActivityEvent(event) {
  const trade = event.snapshot;
  const item = document.createElement("div");
  item.className = "paper-history-item";
  item.dataset.eventId = String(event.id);
  const title = document.createElement("strong");
  const actor = event.actor === "USER" ? "You" : event.actor === "STRATEGY" ? `Strategy: ${event.strategy}` : "Unknown (older record)";
  title.textContent = `${event.action} | ${actor}`;
  item.append(title);
  const addLine = (text) => {
    const line = document.createElement("span");
    line.textContent = text;
    item.append(line);
    return line;
  };
  addLine(`Recorded ${activityTime(event.recorded_at)} | ${event.reason.replaceAll("_", " ")}`);
  if (event.action === "RESET") {
    addLine(`${event.asset} account | ${trade.deleted_trades} trades cleared`);
    return item;
  }
  addLine(`${event.asset} #${event.trade_id} | ${trade.contract_symbol || trade.symbol} | ${trade.side} | Qty ${trade.quantity} ${trade.timeframe || ""}`);
  if (event.asset === "option") {
    addLine(`${trade.underlying_symbol} ${trade.option_type.toUpperCase()} | Strike ${formatPrice(trade.strike)} | Expiry ${trade.expiration_date}`);
  }
  const unit = event.asset === "option" ? " / share" : "";
  const bought = trade.side === "LONG";
  addLine(`${bought ? "Bought" : "Sold"} to open @ ${formatPrice(trade.entry_price)}${unit} | ${activityTime(trade.entry_time)}`);
  if (event.asset === "option") addLine(`Entry premium ${formatOptionPremium(trade.entry_price, trade.quantity)}`);
  if (event.action === "CLOSE" || trade.status === "CLOSED") {
    addLine(`${bought ? "Sold" : "Bought"} to close @ ${formatPrice(trade.exit_price)}${unit} | ${activityTime(trade.exit_time)}`);
    if (event.asset === "option") addLine(`Exit premium ${formatOptionPremium(trade.exit_price, trade.quantity)}`);
    addLine(`Exit reason: ${(trade.exit_reason || "Unknown (not recorded)").replaceAll("_", " ")}`);
    addLine(`Realized P/L ${formatMoney(trade.pnl)}`).className = Number(trade.pnl) >= 0 ? "profit" : "loss";
  }
  if (trade.strategy_rules) {
    if (trade.signal_time != null) addLine(`Signal candle ${activityTime(trade.signal_time)}`);
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Strategy at execution";
    const rules = document.createElement("pre");
    rules.textContent = trade.strategy_rules;
    details.append(summary, rules);
    item.append(details);
  }
  return item;
}

async function loadTradeActivity(older = false) {
  if (activityLoading) return;
  activityLoading = true;
  activityMore.disabled = true;
  try {
    const query = older && activityBefore ? `?before=${activityBefore}` : "";
    const response = await fetch(`/api/paper/activity/${query}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not load activity.");
    const existing = new Map([...activityList.children].map((item) => [item.dataset.eventId, item]));
    const items = payload.events.map((event) => existing.get(String(event.id)) || renderActivityEvent(event));
    if (older) {
      activityList.append(...items.filter((item) => !item.isConnected));
    } else if (items.length !== activityList.children.length || items.some((item, index) => item !== activityList.children[index])) {
      activityList.replaceChildren(...items);
    }
    activityBefore = payload.next_before;
    activityBrowsingOlder = older;
    activityMore.hidden = !activityBefore;
    activityStatus.textContent = activityList.childElementCount ? "Pacific time | Paper trading" : "No activity recorded yet.";
  } catch (error) {
    activityStatus.textContent = error.message;
  } finally {
    activityLoading = false;
    activityMore.disabled = false;
  }
}

const state = {
  config: null,
  panes: [],
  paper: null,
  strategies: null,
  selectedStrategyId: null,
  optionChain: null,
  optionsPaper: null,
};

function getSavedCount() {
  const value = Number(localStorage.getItem(STORAGE_KEY));
  return COUNTS.includes(value) ? value : DEFAULT_COUNT;
}

function readPaneLayouts() {
  try {
    const layouts = JSON.parse(localStorage.getItem(PANE_LAYOUTS_STORAGE_KEY) || "[]");
    return Array.isArray(layouts) ? layouts : [];
  } catch (error) {
    localStorage.removeItem(PANE_LAYOUTS_STORAGE_KEY);
    return [];
  }
}

function savePaneLayouts() {
  const layouts = state.panes.map((pane) => ({
    symbol: pane.symbol,
    timeframe: pane.timeframe,
    lowerPanePercent: pane.lowerPanePercent,
    indicators: pane.indicatorInstances.map((instance) => ({
      type: instance.type,
      params: { ...instance.params },
      color: instance.color,
    })),
  }));
  localStorage.setItem(PANE_LAYOUTS_STORAGE_KEY, JSON.stringify(layouts));
}

function savedPaneLayout(index) {
  return readPaneLayouts()[index] || null;
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
  const layout = savedPaneLayout(index);
  const defaultSymbol = state.config.symbols[index % state.config.symbols.length].value;
  const savedSymbol = layout && state.config.symbols.some((item) => item.value === layout.symbol) ? layout.symbol : defaultSymbol;
  const savedTimeframe = layout && state.config.timeframes.includes(layout.timeframe) ? layout.timeframe : "1m";
  const savedLowerPanePercent = Number(layout && layout.lowerPanePercent);
  const pane = {
    index,
    symbol: savedSymbol,
    timeframe: savedTimeframe,
    chart: null,
    lowerChart: null,
    series: null,
    indicatorSeries: new Map(),
    indicatorMarkers: new Map(),
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
    paperEnabled: localStorage.getItem(PAPER_ENABLED_STORAGE_KEY) === "true",
    processedPaperSignals: new Set(),
    paperBusy: false,
    paperPending: false,
    lowerPanePercent:
      Number.isFinite(savedLowerPanePercent) && savedLowerPanePercent >= LOWER_PANE_MIN_PERCENT
        ? Math.min(savedLowerPanePercent, LOWER_PANE_MAX_PERCENT)
        : LOWER_PANE_DEFAULT_PERCENT,
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
  const paperTools = document.createElement("div");
  paperTools.className = "paper-tools";
  const paperButton = document.createElement("button");
  paperButton.type = "button";
  paperButton.textContent = pane.paperEnabled ? "Paper on" : "Paper off";
  paperButton.classList.toggle("active", pane.paperEnabled);
  const paperStatus = document.createElement("span");
  paperStatus.className = "paper-status";
  paperTools.append(paperButton, paperStatus);
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
  const lowerResizer = document.createElement("div");
  lowerResizer.className = "lower-resizer";
  lowerResizer.title = "Drag to resize indicator pane";
  const lowerChartEl = document.createElement("div");
  lowerChartEl.className = "chart lower-chart";
  chartShell.append(chartEl, lowerResizer, lowerChartEl);

  header.append(ticker, symbolSelect, timeframeSelect, indicatorSelect, customForm, drawingTools, paperTools, chips);
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
  pane.paperButton = paperButton;
  pane.paperStatus = paperStatus;
  pane.chips = chips;
  pane.chartShell = chartShell;
  pane.chartEl = chartEl;
  pane.lowerResizer = lowerResizer;
  pane.lowerChartEl = lowerChartEl;
  applyLowerPaneSize(pane);

  symbolSelect.addEventListener("change", () => {
    clearDrawingLevels(pane, { save: false });
    pane.symbol = symbolSelect.value;
    savePaneLayouts();
    startPaneFeed(pane);
  });
  timeframeSelect.addEventListener("change", () => {
    clearDrawingLevels(pane, { save: false });
    pane.timeframe = timeframeSelect.value;
    savePaneLayouts();
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
  paperButton.addEventListener("click", () => togglePaperTrading(pane));
  lowerResizer.addEventListener("pointerdown", (event) => startLowerPaneResize(pane, event));
  lowerResizer.addEventListener("dblclick", () => {
    pane.lowerPanePercent = LOWER_PANE_DEFAULT_PERCENT;
    applyLowerPaneSize(pane);
    savePaneLayouts();
  });
  setPaperEnabled(pane, pane.paperEnabled);

  return pane;
}

function chartOptions(background) {
  return {
    autoSize: true,
    layout: { background: { color: background }, textColor: "#c8d0cc" },
    grid: { vertLines: { color: "#263036" }, horzLines: { color: "#263036" } },
    localization: {
      timeFormatter: formatChartDateTime,
    },
    rightPriceScale: { borderColor: "#2a3338" },
    timeScale: {
      borderColor: "#2a3338",
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: formatChartTick,
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  };
}

function timestampFromChartTime(time) {
  if (typeof time === "number") {
    return time * 1000;
  }
  if (time && typeof time === "object" && "year" in time) {
    return Date.UTC(time.year, time.month - 1, time.day);
  }
  return Date.now();
}

function formatChartTick(time, tickMarkType) {
  const timestamp = timestampFromChartTime(time);
  if (tickMarkType === 0 || tickMarkType === 1 || tickMarkType === 2) {
    return DATE_LABEL_FORMATTER.format(timestamp);
  }
  return TIME_LABEL_FORMATTER.format(timestamp);
}

function formatChartDateTime(time) {
  return DATE_TIME_LABEL_FORMATTER.format(timestampFromChartTime(time));
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

async function postJson(url, body = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

async function fetchPaperState() {
  const response = await fetch("/api/paper/state/");
  if (!response.ok) {
    return null;
  }
  return response.json();
}

function formatMoney(value) {
  return Number(value || 0).toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatOptionalMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return formatMoney(value);
}

function formatPaperTime(time) {
  if (!time) {
    return "--";
  }
  return DATE_TIME_LABEL_FORMATTER.format(time * 1000);
}

function renderPaperState(payload) {
  if (!payload) {
    return;
  }
  state.paper = payload;
  paperEquity.textContent = formatMoney(payload.equity);
  paperPnl.textContent = formatMoney(payload.realized_pnl);
  paperPnl.classList.toggle("profit", Number(payload.realized_pnl) > 0);
  paperPnl.classList.toggle("loss", Number(payload.realized_pnl) < 0);

  paperHistoryList.replaceChildren();
  if (payload.watchlist && payload.watchlist.length) {
    const symbols = [...new Set(payload.watchlist.map((item) => item.symbol))];
    const timeframes = [...new Set(payload.watchlist.map((item) => item.timeframe))];
    paperWatchlistInput.value = symbols.join(", ");
    paperWatchlistTimeframes.value = timeframes.join(", ");
  }
  payload.trades.forEach((trade) => {
    const item = document.createElement("div");
    item.className = `paper-history-item ${trade.status.toLowerCase()} ${trade.side.toLowerCase()}`;

    const title = document.createElement("strong");
    title.textContent = `Underlying ${trade.side} ${trade.symbol} ${trade.timeframe}`;

    const entry = document.createElement("span");
    const entryAction = trade.side === "LONG" ? "Underlying bought" : "Underlying short sold";
    entry.textContent = `${entryAction} @ ${formatPrice(trade.entry_price)} x ${formatPrice(
      trade.quantity,
    )} | Value ${formatTradeValue(trade.entry_price, trade.quantity)} | ${formatPaperTime(trade.entry_time)}`;

    const stop = document.createElement("span");
    stop.textContent = `Stop ${formatPrice(trade.stop_price)}`;

    const contract = document.createElement("span");
    const estimateType = trade.option_estimate_type || (trade.side === "LONG" ? "CALL" : "PUT");
    contract.textContent = `Model option estimate: ${estimateType} ATM ${formatPrice(
      trade.option_estimate_strike || trade.entry_price,
    )} ${trade.option_estimate_days || 30}D premium ${formatOptionalMoney(trade.contract_price)}/unit | IV ${formatPercent(
      trade.iv,
    )}`;

    const greeks = document.createElement("span");
    greeks.textContent = `Model Greeks: Delta ${formatMetric(trade.delta)} Gamma ${formatMetric(trade.gamma)} Theta ${formatMetric(
      trade.theta,
    )} Vega ${formatMetric(trade.vega)} Rho ${formatMetric(trade.rho)}`;

    const broker = document.createElement("span");
    broker.textContent =
      trade.execution_provider === "alpaca"
        ? `Alpaca ${trade.broker_status || "submitted"} ${trade.broker_order_id || ""}`.trim()
        : trade.execution_provider === "alpaca_error"
          ? `Alpaca error: ${trade.broker_status || "order failed"}`
          : "Local paper";

    const exit = document.createElement("span");
    if (trade.status === "CLOSED") {
      const exitAction = trade.side === "LONG" ? "Underlying sold" : "Underlying bought back";
      exit.textContent = `${exitAction} @ ${formatPrice(trade.exit_price)} | Value ${formatTradeValue(
        trade.exit_price,
        trade.quantity,
      )} | ${trade.exit_reason} ${formatPaperTime(trade.exit_time)}`;
    } else {
      exit.textContent = "Open";
    }

    const pnl = document.createElement("span");
    pnl.className = Number(trade.pnl) >= 0 ? "profit" : "loss";
    pnl.textContent = `P/L ${formatMoney(trade.pnl)}`;

    item.append(title, entry, stop, contract, greeks, broker, exit, pnl);
    paperHistoryList.append(item);
  });
}

async function syncPaperState() {
  renderPaperState(await fetchPaperState());
}

function formatMetric(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return Number(value).toFixed(4);
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function formatTradeValue(priceValue, quantity) {
  const price = Number(priceValue);
  const shares = Number(quantity);
  if (!Number.isFinite(price) || !Number.isFinite(shares)) {
    return "--";
  }
  return formatMoney(price * shares);
}

function scannerSymbols() {
  return paperWatchlistInput.value
    .split(",")
    .map((symbol) => symbol.trim().toUpperCase())
    .filter(Boolean);
}

function scannerTimeframes() {
  return paperWatchlistTimeframes.value
    .split(",")
    .map((timeframe) => timeframe.trim())
    .filter(Boolean);
}

async function saveScannerWatchlist() {
  renderPaperState(
    await postJson("/api/paper/watchlist/", {
      symbols: scannerSymbols(),
      timeframes: scannerTimeframes(),
    }),
  );
}

async function fetchStrategyState() {
  const response = await fetch("/api/strategies/");
  if (!response.ok) {
    return null;
  }
  return response.json();
}

async function syncStrategyState() {
  renderStrategyState(await fetchStrategyState());
}

function renderStrategyState(payload) {
  if (!payload) {
    return;
  }
  state.strategies = payload;
  strategyList.replaceChildren();
  const strategies = payload.strategies || [];
  if (!state.selectedStrategyId && strategies.length) {
    state.selectedStrategyId = strategies[0].id;
  }

  strategies.forEach((strategy) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `strategy-list-item ${strategy.enabled && !strategy.validation_error ? "enabled" : "disabled"}`;
    item.classList.toggle("active", strategy.id === state.selectedStrategyId);

    const title = document.createElement("strong");
    title.textContent = strategy.name;
    const meta = document.createElement("span");
    meta.textContent = `${strategy.validation_error ? "Signals blocked" : strategy.enabled ? "On" : "Off"} | ${strategy.trade_asset} | risk ${formatStrategyPercent(
      strategy.risk_percent,
    )}`;
    item.append(title, meta);

    const error = latestStrategyError(strategy.name);
    if (strategy.validation_error || error) {
      const errorEl = document.createElement("span");
      errorEl.className = "strategy-error";
      errorEl.textContent = strategy.validation_error || `${error.symbol} ${error.timeframe}: ${error.last_error}`;
      item.append(errorEl);
    }

    item.addEventListener("click", () => {
      state.selectedStrategyId = strategy.id;
      renderStrategyState(state.strategies);
      fillStrategyForm(strategy);
    });
    strategyList.append(item);
  });

  const selected = strategies.find((strategy) => strategy.id === state.selectedStrategyId) || strategies[0];
  if (selected) {
    fillStrategyForm(selected);
  } else {
    clearStrategyForm();
  }
}

function latestStrategyError(strategyName) {
  const states = ((state.strategies && state.strategies.scan_states) || []).filter(
    (scanState) => scanState.strategy === strategyName && scanState.last_error,
  );
  states.sort((left, right) => String(right.last_checked_at || "").localeCompare(String(left.last_checked_at || "")));
  return states[0] || null;
}

function formatStrategyPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return `${Number(value).toFixed(Number(value) % 1 === 0 ? 0 : 2)}%`;
}

function fillStrategyForm(strategy) {
  strategyId.value = strategy.id || "";
  strategyName.value = strategy.name || "";
  strategyEnabled.checked = Boolean(strategy.enabled);
  strategyTradeAsset.value = strategy.trade_asset || "option";
  strategyRisk.value = strategy.risk_percent === null || strategy.risk_percent === undefined ? 2 : strategy.risk_percent;
  strategyMinDte.value = strategy.option_min_dte === null || strategy.option_min_dte === undefined ? 7 : strategy.option_min_dte;
  strategyMaxDte.value = strategy.option_max_dte === null || strategy.option_max_dte === undefined ? 14 : strategy.option_max_dte;
  strategyStrikeMode.value = strategy.option_strike_mode || "atm";
  strategyTakeProfit.value =
    strategy.option_take_profit_percent === null || strategy.option_take_profit_percent === undefined
      ? 100
      : strategy.option_take_profit_percent;
  strategyStopLoss.value =
    strategy.option_stop_loss_percent === null || strategy.option_stop_loss_percent === undefined
      ? 50
      : strategy.option_stop_loss_percent;
  strategyMaxSpread.value = strategy.max_spread_percent === null || strategy.max_spread_percent === undefined ? 50 : strategy.max_spread_percent;
  strategyRuleText.value = strategy.rule_text || "";
  strategyStatus.textContent = strategy.validation_error || (strategy.updated_at ? `Saved ${formatOptionDateTime(strategy.updated_at)}` : "");
}

function clearStrategyForm() {
  strategyId.value = "";
  strategyName.value = "";
  strategyEnabled.checked = true;
  strategyTradeAsset.value = "option";
  strategyRisk.value = 2;
  strategyMinDte.value = 7;
  strategyMaxDte.value = 14;
  strategyStrikeMode.value = "atm";
  strategyTakeProfit.value = 100;
  strategyStopLoss.value = 50;
  strategyMaxSpread.value = 50;
  strategyRuleText.value = (state.strategies && state.strategies.template) || "";
  strategyStatus.textContent = "";
}

function strategyPayloadFromForm() {
  return {
    id: strategyId.value || null,
    name: strategyName.value.trim(),
    enabled: strategyEnabled.checked,
    trade_asset: strategyTradeAsset.value,
    risk_percent: Number(strategyRisk.value),
    option_min_dte: Number.parseInt(strategyMinDte.value || "0", 10),
    option_max_dte: Number.parseInt(strategyMaxDte.value || "0", 10),
    option_strike_mode: strategyStrikeMode.value,
    option_take_profit_percent: strategyTakeProfit.value === "" ? null : Number(strategyTakeProfit.value),
    option_stop_loss_percent: strategyStopLoss.value === "" ? null : Number(strategyStopLoss.value),
    max_spread_percent: strategyMaxSpread.value === "" ? null : Number(strategyMaxSpread.value),
    rule_text: strategyRuleText.value,
  };
}

async function saveStrategyForm() {
  const result = await postJson("/api/strategies/", strategyPayloadFromForm());
  state.selectedStrategyId = result.strategy;
  renderStrategyState(result.state);
  strategyStatus.textContent = "Saved";
}

async function fetchOptionExpirations(symbol) {
  const response = await fetch(`/api/options/expirations/?symbol=${encodeURIComponent(symbol)}`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Option expirations failed: ${response.status}`);
  }
  return payload.expirations || [];
}

async function fetchOptionChain(symbol, expiration) {
  const params = new URLSearchParams({ symbol });
  if (expiration) {
    params.set("expiration", expiration);
  }
  const response = await fetch(`/api/options/chain/?${params.toString()}`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Option chain failed: ${response.status}`);
  }
  return payload;
}

async function fetchOptionPaperState() {
  const response = await fetch("/api/options/paper/state/");
  if (!response.ok) {
    return null;
  }
  return response.json();
}

async function loadOptionExpirations(selectedExpiration = "") {
  const symbol = optionUnderlying.value.trim().toUpperCase();
  if (!symbol) {
    return;
  }
  optionChainSummary.textContent = "Loading expirations...";
  const expirations = await fetchOptionExpirations(symbol);
  optionExpiration.replaceChildren();
  expirations.forEach((expiration) => {
    const option = document.createElement("option");
    option.value = expiration;
    option.textContent = expiration;
    if (expiration === selectedExpiration) {
      option.selected = true;
    }
    optionExpiration.append(option);
  });
  if (!expirations.length) {
    optionChainSummary.textContent = `No Yahoo options found for ${symbol}`;
  }
}

async function loadOptionChain() {
  const symbol = optionUnderlying.value.trim().toUpperCase();
  if (!symbol) {
    window.alert("Enter an underlying symbol.");
    return;
  }
  optionUnderlying.value = symbol;
  optionCalls.replaceChildren();
  optionPuts.replaceChildren();
  optionChainSummary.textContent = "Loading option chain...";
  if (!optionExpiration.options.length) {
    await loadOptionExpirations();
  }
  const chain = await fetchOptionChain(symbol, optionExpiration.value);
  state.optionChain = chain;
  renderOptionChain(chain);
}

function renderOptionChain(chain) {
  optionChainSummary.textContent = `${chain.symbol} ${chain.expiration} | Underlying ${formatPrice(
    chain.underlying_price,
  )} | Source ${chain.source} | ${formatPaperTime(chain.updated_at)}`;
  renderOptionContracts(optionCalls, chain.calls || []);
  renderOptionContracts(optionPuts, chain.puts || []);
}

function renderOptionContracts(target, contracts) {
  target.replaceChildren();
  contracts.forEach((contract) => {
    const row = document.createElement("tr");
    row.classList.toggle("in-money", contract.in_the_money);

    [
      formatPrice(contract.strike),
      formatPrice(contract.bid),
      formatPrice(contract.ask),
      formatPrice(contract.last_price),
      formatPercent(contract.iv),
      formatMetric(contract.delta),
      formatMetric(contract.theta),
      String(contract.volume || 0),
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });

    const actionCell = document.createElement("td");
    const buy = document.createElement("button");
    buy.type = "button";
    buy.textContent = "Buy to Open";
    buy.title = "Open a long option paper trade";
    buy.addEventListener("click", () => openManualOptionTrade(contract, "LONG"));
    const sell = document.createElement("button");
    sell.type = "button";
    sell.textContent = "Sell to Open";
    sell.title = "Open a short option paper trade";
    sell.addEventListener("click", () => openManualOptionTrade(contract, "SHORT"));
    actionCell.append(buy, sell);
    row.append(actionCell);
    target.append(row);
  });
}

async function openManualOptionTrade(contract, side) {
  try {
    if (
      side === "SHORT" &&
      !window.confirm("Sell to Open creates a short option paper trade. To exit a bought option, use Sell to Close on the open trade.")
    ) {
      return;
    }
    const result = await postJson("/api/options/paper/trade/", {
      underlying_symbol: state.optionChain.symbol,
      contract_symbol: contract.contract_symbol,
      expiration_date: contract.expiration,
      side,
      quantity: Number.parseInt(optionQuantity.value || "1", 10),
    });
    renderOptionPaperState(result.state);
  } catch (error) {
    window.alert(error.message);
  }
}

function renderOptionPaperState(payload) {
  if (!payload) {
    return;
  }
  state.optionsPaper = payload;
  optionPaperList.replaceChildren();
  const summary = document.createElement("div");
  summary.className = "option-paper-summary";
  summary.textContent = `Equity ${formatMoney(payload.equity)} | Buying power ${formatMoney(
    payload.buying_power,
  )} | Realized ${formatMoney(payload.realized_pnl)} | Open P/L ${formatMoney(
    payload.unrealized_pnl,
  )} | Open value ${formatMoney(payload.open_value)}`;
  optionPaperList.append(summary);

  payload.trades.forEach((trade) => {
    const item = document.createElement("div");
    item.className = `option-paper-item ${trade.status.toLowerCase()} ${trade.side.toLowerCase()}`;

    const title = document.createElement("strong");
    title.textContent = `${trade.auto_trade ? "AUTO" : "MANUAL"} ${trade.side} ${trade.quantity} ${trade.contract_symbol}`;

    const detail = document.createElement("span");
    const strategyDetail = trade.auto_trade ? ` | ${trade.strategy} ${trade.timeframe}` : "";
    detail.textContent = `${trade.underlying_symbol} ${trade.option_type.toUpperCase()} ${trade.expiration_date} ${formatPrice(
      trade.strike,
    )}${strategyDetail}`;

    const entry = document.createElement("span");
    const entryAction = trade.side === "LONG" ? "Bought to open" : "Sold to open";
    entry.textContent = `${entryAction} @ ${formatPrice(trade.entry_price)} | Premium ${formatOptionPremium(
      trade.entry_price,
      trade.quantity,
    )} | Underlying ${formatPrice(trade.entry_underlying_price)} | IV ${formatPercent(trade.entry_iv)}`;

    const greeks = document.createElement("span");
    greeks.textContent = `Delta ${formatMetric(trade.entry_delta)} Gamma ${formatMetric(
      trade.entry_gamma,
    )} Theta ${formatMetric(trade.entry_theta)} Vega ${formatMetric(trade.entry_vega)} Rho ${formatMetric(trade.entry_rho)}`;

    const status = document.createElement("span");
    if (trade.status === "CLOSED") {
      const exitAction = trade.side === "LONG" ? "Sold to close" : "Bought to close";
      status.textContent = `${exitAction} @ ${formatPrice(trade.exit_price)} | Premium ${formatOptionPremium(
        trade.exit_price,
        trade.quantity,
      )} | ${trade.exit_reason || "CLOSED"} ${formatOptionDateTime(trade.exit_time)}`;
    } else {
      const exitAction = trade.side === "LONG" ? "Sell value" : "Buyback cost";
      const currentQuote = trade.current_quote_error
        ? `Quote issue: ${trade.current_quote_error}`
        : `${exitAction} @ ${formatPrice(trade.current_exit_price)} | Current value ${formatOptionPremium(
            trade.current_exit_price,
            trade.quantity,
          )} | Bid ${formatPrice(trade.current_bid)} Ask ${formatPrice(trade.current_ask)} Last ${formatPrice(
            trade.current_last_price,
          )} | ${formatPaperTime(trade.current_quote_time)}`;
      status.textContent = `Open since ${formatOptionDateTime(trade.entry_time)} | ${
        trade.auto_trade ? "Strategy-managed exits" : "Manual exits only"
      } | ${currentQuote}`;
    }

    const pnl = document.createElement("span");
    const displayPnl = trade.status === "OPEN" ? trade.unrealized_pnl : trade.pnl;
    pnl.className = Number(displayPnl) >= 0 ? "profit" : "loss";
    pnl.textContent = `${trade.status === "OPEN" ? "Open P/L" : "Realized P/L"} ${formatMoney(displayPnl)}`;

    item.append(title, detail, entry, greeks, status, pnl);
    if (trade.status === "OPEN") {
      const close = document.createElement("button");
      close.type = "button";
      close.textContent = trade.side === "LONG" ? "Sell to Close" : "Buy to Close";
      close.addEventListener("click", () => closeManualOptionTrade(trade, close));
      item.append(close);
    }
    optionPaperList.append(item);
  });
}

async function syncOptionPaperState() {
  renderOptionPaperState(await fetchOptionPaperState());
}

async function closeManualOptionTrade(trade, button) {
  const action = trade.side === "LONG" ? "Sell to Close" : "Buy to Close";
  if (!window.confirm(`${action} ${trade.quantity} ${trade.contract_symbol}?`)) {
    return;
  }
  button.disabled = true;
  try {
    const result = await postJson("/api/options/paper/close/", { trade_id: trade.id });
    renderOptionPaperState(result.state);
  } catch (error) {
    window.alert(error.message);
  } finally {
    button.disabled = false;
  }
}

function formatOptionDateTime(value) {
  if (!value) {
    return "--";
  }
  return DATE_TIME_LABEL_FORMATTER.format(new Date(value));
}

function formatOptionPremium(priceValue, quantity) {
  const price = Number(priceValue);
  const contracts = Number(quantity || 0);
  if (!Number.isFinite(price) || !Number.isFinite(contracts)) {
    return "--";
  }
  return formatMoney(price * contracts * 100);
}

function paperSignalKey(signal) {
  return `${signal.text}|${signal.time}`;
}

function seedPaperSignals(pane) {
  calculateTesting1Signals(pane.bars, pane.symbol).signals.forEach((signal) => {
    pane.processedPaperSignals.add(paperSignalKey(signal));
  });
}

function ensureTesting1Indicator(pane) {
  if (!pane.indicatorInstances.some((instance) => instance.type === PAPER_STRATEGY)) {
    addIndicator(pane, PAPER_STRATEGY);
  }
}

function setPaperEnabled(pane, enabled) {
  pane.paperEnabled = enabled;
  pane.paperButton.textContent = enabled ? "Paper on" : "Paper off";
  pane.paperButton.classList.toggle("active", enabled);
  pane.paperStatus.textContent = enabled ? "2% risk" : "";
}

function togglePaperTrading(pane) {
  setPaperEnabled(pane, !pane.paperEnabled);
  localStorage.setItem(PAPER_ENABLED_STORAGE_KEY, String(pane.paperEnabled));
  if (pane.paperEnabled) {
    ensureTesting1Indicator(pane);
    seedPaperSignals(pane);
    syncPaperState();
  }
}

function signalOrderPayload(pane, signal) {
  const signalIndex = pane.bars.findIndex((bar) => bar.time === signal.time);
  if (signalIndex < 1) {
    return null;
  }

  const entryBar = pane.bars[signalIndex];
  const previousBar = pane.bars[signalIndex - 1];
  const side = signal.text;
  let stopPrice = side === "BUY" ? Math.min(entryBar.low, previousBar.low) : Math.max(entryBar.high, previousBar.high);
  if (side === "BUY" && stopPrice >= entryBar.close) {
    stopPrice = entryBar.close * 0.995;
  }
  if (side === "SELL" && stopPrice <= entryBar.close) {
    stopPrice = entryBar.close * 1.005;
  }

  return {
    strategy: PAPER_STRATEGY,
    symbol: pane.symbol,
    timeframe: pane.timeframe,
    side,
    signal_time: signal.time,
    entry_time: entryBar.time,
    entry_price: entryBar.close,
    stop_price: stopPrice,
  };
}

async function processPaperTrading(pane) {
  if (!pane.paperEnabled || !pane.bars.length) {
    return;
  }
  if (pane.paperBusy) {
    pane.paperPending = true;
    return;
  }

  pane.paperBusy = true;
  pane.paperPending = false;
  try {
    const latestBar = pane.bars[pane.bars.length - 1];
    const markResult = await postJson("/api/paper/mark/", {
      symbol: pane.symbol,
      timeframe: pane.timeframe,
      bar: latestBar,
    });
    renderPaperState(markResult.state);

    const signals = calculateTesting1Signals(pane.bars, pane.symbol).signals;
    for (const signal of signals) {
      const key = paperSignalKey(signal);
      if (pane.processedPaperSignals.has(key)) {
        continue;
      }

      pane.processedPaperSignals.add(key);
      const payload = signalOrderPayload(pane, signal);
      if (payload) {
        const signalResult = await postJson("/api/paper/signal/", payload);
        renderPaperState(signalResult.state);
      }
    }
  } catch (error) {
    pane.paperStatus.textContent = "Paper error";
  } finally {
    pane.paperBusy = false;
    if (pane.paperPending) {
      processPaperTrading(pane);
    }
  }
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
    savePaneLayouts();
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

function getVisibleTimeRanges(pane) {
  return {
    main: pane.chart.timeScale().getVisibleRange(),
    lower: pane.lowerChart.timeScale().getVisibleRange(),
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

function restoreVisibleTimeRanges(pane, ranges) {
  window.requestAnimationFrame(() => {
    if (ranges.main) {
      pane.chart.timeScale().setVisibleRange(ranges.main);
    }
    if (ranges.lower) {
      pane.lowerChart.timeScale().setVisibleRange(ranges.lower);
    }
  });
}

function withPreservedVisibleRange(pane, callback) {
  const ranges = getVisibleRanges(pane);
  callback();
  restoreVisibleRanges(pane, ranges);
}

function withPreservedVisibleTimeRange(pane, callback) {
  const ranges = getVisibleTimeRanges(pane);
  callback();
  restoreVisibleTimeRanges(pane, ranges);
}

function setBars(pane, bars, options = {}) {
  const shouldFit = Boolean(options.fit);
  const applyBars = () => {
    pane.bars = bars;
    pane.series.setData(bars);
    updateIndicators(pane);
    if (shouldFit && pane.paperEnabled) {
      seedPaperSignals(pane);
    }
  };

  if (shouldFit) {
    applyBars();
  } else {
    withPreservedVisibleTimeRange(pane, applyBars);
  }

  if (bars.length) {
    setTicker(pane, pane.symbol, bars[bars.length - 1].close);
    processPaperTrading(pane);
    if (shouldFit) {
      pane.chart.timeScale().fitContent();
      pane.lowerChart.timeScale().fitContent();
    }
  } else {
    setTicker(pane, pane.symbol, null);
  }
}

function updateBar(pane, bar) {
  const ranges = getVisibleTimeRanges(pane);
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
  processPaperTrading(pane);
  restoreVisibleTimeRanges(pane, ranges);
  setTicker(pane, pane.symbol, bar.close);
}

async function loadInitialHistory(pane, options = {}) {
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
  pane.processedPaperSignals.clear();
  pane.symbolEl.textContent = symbolLabel(pane.symbol);
  pane.priceEl.textContent = "--";

  if (providerFor(pane.symbol) === "hyperliquid") {
    await loadInitialHistory(pane, { fit: true, restoreDrawings: true });
    startHyperliquidFeed(pane);
  } else {
    startYfinanceFeed(pane);
  }
}

function nextIndicatorColor(pane) {
  return OVERLAY_COLORS[(pane.nextIndicatorId - 1) % OVERLAY_COLORS.length];
}

function makeIndicatorInstance(pane, id, options = {}) {
  const indicator = indicatorById(id);
  if (!indicator) {
    return null;
  }
  const instance = {
    uid: `${id}-${pane.nextIndicatorId}`,
    type: id,
    params: { ...(indicator.defaults || {}), ...(options.params || {}) },
    color: options.color || nextIndicatorColor(pane),
  };
  pane.nextIndicatorId += 1;
  return instance;
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

  const instance = makeIndicatorInstance(pane, id);
  if (!instance) {
    return;
  }

  if (indicator.repeatable && !editIndicatorParams(instance, true)) {
    return;
  }

  pane.indicatorInstances.push(instance);
  renderIndicatorChips(pane);
  updateLowerChartVisibility(pane);
  updateIndicator(pane, instance);
  savePaneLayouts();
  restoreVisibleRanges(pane, ranges);
}

function restorePaneIndicators(pane) {
  const layout = savedPaneLayout(pane.index);
  if (!layout || !Array.isArray(layout.indicators)) {
    return;
  }

  layout.indicators.forEach((savedIndicator) => {
    const indicator = indicatorById(savedIndicator.type);
    if (!indicator) {
      return;
    }
    if (!indicator.repeatable && pane.indicatorInstances.some((instance) => instance.type === savedIndicator.type)) {
      return;
    }

    const instance = makeIndicatorInstance(pane, savedIndicator.type, {
      params: savedIndicator.params || {},
      color: savedIndicator.color,
    });
    if (instance) {
      pane.indicatorInstances.push(instance);
    }
  });

  renderIndicatorChips(pane);
  updateLowerChartVisibility(pane);
}

function editIndicatorParams(instance, isNew = false) {
  if (instance.type !== "sma" && instance.type !== "ema" && instance.type !== "rsi") {
    return true;
  }

  const label = instance.type === "rsi" ? "RSI" : instance.type.toUpperCase();
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
  if (instance.type === "rsi") {
    const maInput = window.prompt("RSI-based SMA length", String(instance.params.maPeriod || 14));
    if (maInput === null) {
      return !isNew;
    }
    const maPeriod = Number.parseInt(maInput, 10);
    if (!Number.isInteger(maPeriod) || maPeriod < 1 || maPeriod > 500) {
      window.alert("Use a whole number from 1 to 500.");
      return editIndicatorParams(instance, isNew);
    }
    instance.params.maPeriod = maPeriod;
  }
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
  savePaneLayouts();
  restoreVisibleRanges(pane, ranges);
}

function removeIndicator(pane, uid) {
  const ranges = getVisibleRanges(pane);
  pane.indicatorInstances = pane.indicatorInstances.filter((instance) => instance.uid !== uid);
  removeIndicatorSeries(pane, uid);
  renderIndicatorChips(pane);
  updateLowerChartVisibility(pane);
  savePaneLayouts();
  restoreVisibleRanges(pane, ranges);
}

function indicatorInstanceLabel(instance) {
  const indicator = indicatorById(instance.type);
  if (instance.type === "sma" || instance.type === "ema") {
    return `${indicator.label} ${instance.params.period}`;
  }
  if (instance.type === "rsi") {
    return `RSI ${instance.params.period}`;
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
  applyLowerPaneSize(pane);
}

function clampLowerPanePercent(value) {
  return Math.min(LOWER_PANE_MAX_PERCENT, Math.max(LOWER_PANE_MIN_PERCENT, value));
}

function applyLowerPaneSize(pane) {
  pane.chartShell.style.setProperty("--lower-pane-height", `${clampLowerPanePercent(pane.lowerPanePercent)}%`);
}

function startLowerPaneResize(pane, event) {
  if (!pane.chartShell.classList.contains("has-lower-chart")) {
    return;
  }

  event.preventDefault();
  pane.lowerResizer.setPointerCapture(event.pointerId);
  document.body.classList.add("resizing-lower-pane");

  const resize = (pointerEvent) => {
    const rect = pane.chartShell.getBoundingClientRect();
    if (!rect.height) {
      return;
    }
    const lowerHeight = rect.bottom - pointerEvent.clientY;
    pane.lowerPanePercent = clampLowerPanePercent((lowerHeight / rect.height) * 100);
    applyLowerPaneSize(pane);
  };

  const stop = () => {
    document.body.classList.remove("resizing-lower-pane");
    pane.lowerResizer.removeEventListener("pointermove", resize);
    pane.lowerResizer.removeEventListener("pointerup", stop);
    pane.lowerResizer.removeEventListener("pointercancel", stop);
    savePaneLayouts();
  };

  pane.lowerResizer.addEventListener("pointermove", resize);
  pane.lowerResizer.addEventListener("pointerup", stop);
  pane.lowerResizer.addEventListener("pointercancel", stop);
  resize(event);
}

function removeIndicatorSeries(pane, uid) {
  const entries = pane.indicatorSeries.get(uid) || [];
  entries.forEach(({ chart, series }) => chart.removeSeries(series));
  pane.indicatorSeries.delete(uid);
  pane.indicatorMarkers.delete(uid);
  applyIndicatorMarkers(pane);
}

function replaceIndicatorSeries(pane, instance, entries) {
  removeIndicatorSeries(pane, instance.uid);
  pane.indicatorSeries.set(instance.uid, entries);
}

function replaceIndicatorMarkers(pane, instance, markers) {
  pane.indicatorMarkers.set(instance.uid, markers);
  applyIndicatorMarkers(pane);
}

function applyIndicatorMarkers(pane) {
  const markers = Array.from(pane.indicatorMarkers.values())
    .flat()
    .sort((left, right) => left.time - right.time);
  pane.series.setMarkers(markers);
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
  return APP_TIME_ZONE;
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

function calculateTesting1Signals(bars, symbol) {
  const ema = calculateEma(bars, 9);
  const vwap = calculateVwap(bars, symbol);
  const signals = [];

  for (let index = 2; index < bars.length; index += 1) {
    const previousIndex = index - 1;
    const beforeCrossIndex = index - 2;
    const beforeEma = ema[beforeCrossIndex];
    const beforeVwap = vwap[beforeCrossIndex];
    const crossEma = ema[previousIndex];
    const crossVwap = vwap[previousIndex];

    if (beforeEma === null || beforeVwap === null || crossEma === null || crossVwap === null) {
      continue;
    }

    const confirmation = bars[index];
    const crossBar = bars[previousIndex];
    const volumeIncreasing = Number(confirmation.volume || 0) > Number(crossBar.volume || 0);
    const crossedAbove = beforeEma <= beforeVwap && crossEma > crossVwap;
    const crossedBelow = beforeEma >= beforeVwap && crossEma < crossVwap;

    if (crossedAbove && volumeIncreasing && confirmation.close > crossBar.close) {
      signals.push({
        time: confirmation.time,
        position: "belowBar",
        color: "#2fce72",
        shape: "arrowUp",
        text: "BUY",
      });
    }

    if (crossedBelow && volumeIncreasing && confirmation.close < crossBar.close) {
      signals.push({
        time: confirmation.time,
        position: "aboveBar",
        color: "#f23d4f",
        shape: "arrowDown",
        text: "SELL",
      });
    }
  }

  return { ema, vwap, signals };
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
  values[period] = rsiFromAverages(averageGain, averageLoss);

  for (let index = period + 1; index < bars.length; index += 1) {
    const change = bars[index].close - bars[index - 1].close;
    averageGain = (averageGain * (period - 1) + Math.max(change, 0)) / period;
    averageLoss = (averageLoss * (period - 1) + Math.max(-change, 0)) / period;
    values[index] = rsiFromAverages(averageGain, averageLoss);
  }

  return values;
}

function rsiFromAverages(averageGain, averageLoss) {
  if (averageGain === 0 && averageLoss === 0) {
    return 50;
  }
  if (averageLoss === 0) {
    return 100;
  }
  if (averageGain === 0) {
    return 0;
  }
  return 100 - 100 / (1 + averageGain / averageLoss);
}

function calculateSmaValues(values, period) {
  return values.map((_, index) => {
    if (index < period - 1) {
      return null;
    }
    const windowValues = values.slice(index - period + 1, index + 1);
    if (windowValues.some((value) => value === null)) {
      return null;
    }
    return average(windowValues);
  });
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
  testing1(pane, instance) {
    const result = calculateTesting1Signals(pane.bars, pane.symbol);
    const emaSeries = pane.chart.addLineSeries({ color: "#f6b51d", lineWidth: 2, title: "testing1 9 EMA" });
    const vwapSeries = pane.chart.addLineSeries({ color: "#4dabf7", lineWidth: 2, title: "testing1 VWAP Session" });
    emaSeries.setData(linePoints(pane.bars, result.ema));
    vwapSeries.setData(linePoints(pane.bars, result.vwap));
    replaceIndicatorSeries(pane, instance, [
      { chart: pane.chart, series: emaSeries },
      { chart: pane.chart, series: vwapSeries },
    ]);
    replaceIndicatorMarkers(pane, instance, result.signals);
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
    const period = instance.params.period || 14;
    const maPeriod = instance.params.maPeriod || 14;
    const autoscaleInfoProvider = () => ({
      priceRange: {
        minValue: 0,
        maxValue: 100,
      },
    });
    const rsiValues = calculateRsi(pane.bars, period);
    const maValues = calculateSmaValues(rsiValues, maPeriod);
    const series = pane.lowerChart.addLineSeries({
      color: "#8e63d7",
      lineWidth: 2,
      title: `RSI ${period} close`,
      autoscaleInfoProvider,
    });
    const maSeries = pane.lowerChart.addLineSeries({
      color: "#ffd43b",
      lineWidth: 2,
      title: `RSI-based SMA ${maPeriod}`,
      autoscaleInfoProvider,
    });
    series.setData(linePoints(pane.bars, rsiValues));
    maSeries.setData(linePoints(pane.bars, maValues));
    series.createPriceLine({
      price: 70,
      color: "#8b949e",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: "70",
    });
    series.createPriceLine({
      price: 50,
      color: "#616b76",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: "50",
    });
    series.createPriceLine({
      price: 30,
      color: "#8b949e",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: "30",
    });
    replaceIndicatorSeries(pane, instance, [
      { chart: pane.lowerChart, series },
      { chart: pane.lowerChart, series: maSeries },
    ]);
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
    restorePaneIndicators(pane);
    startPaneFeed(pane);
  }
}

async function boot() {
  document.querySelectorAll("[data-open-activity]").forEach((button) => {
    button.addEventListener("click", () => {
      activityDrawer.hidden = false;
      loadTradeActivity();
    });
  });
  document.querySelector("#activity-close").addEventListener("click", () => { activityDrawer.hidden = true; });
  document.querySelector("#activity-refresh").addEventListener("click", () => loadTradeActivity());
  activityMore.addEventListener("click", () => loadTradeActivity(true));
  window.setInterval(() => {
    if (!activityDrawer.hidden && !activityBrowsingOlder && !document.hidden) loadTradeActivity();
  }, 10000);
  const response = await fetch("/api/config/");
  state.config = await response.json();
  loadCustomSymbols();
  await syncPaperState();
  await syncStrategyState();

  paperHistoryToggle.addEventListener("click", () => {
    paperHistory.hidden = !paperHistory.hidden;
  });
  paperHistoryClose.addEventListener("click", () => {
    paperHistory.hidden = true;
  });
  paperReset.addEventListener("click", async () => {
    if (window.confirm("Reset all saved paper trades?")) {
      renderPaperState(await postJson("/api/paper/reset/"));
    }
  });
  paperWatchlistSave.addEventListener("click", async () => {
    try {
      await saveScannerWatchlist();
    } catch (error) {
      window.alert(error.message);
    }
  });
  paperWatchlistInput.addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      await saveScannerWatchlist();
    }
  });
  paperScanNow.addEventListener("click", async () => {
    renderPaperState(await postJson("/api/paper/scan/"));
    await syncStrategyState();
    await syncOptionPaperState();
  });
  strategyToggle.addEventListener("click", async () => {
    strategyDrawer.hidden = false;
    await syncStrategyState();
  });
  strategyClose.addEventListener("click", () => {
    strategyDrawer.hidden = true;
  });
  strategyNew.addEventListener("click", () => {
    state.selectedStrategyId = null;
    strategyList.querySelectorAll(".strategy-list-item").forEach((item) => item.classList.remove("active"));
    clearStrategyForm();
  });
  strategyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await saveStrategyForm();
    } catch (error) {
      strategyStatus.textContent = error.message;
    }
  });
  optionChainToggle.addEventListener("click", async () => {
    optionChainDrawer.hidden = false;
    await syncOptionPaperState();
    if (!state.optionChain) {
      try {
        await loadOptionChain();
      } catch (error) {
        optionChainSummary.textContent = error.message;
      }
    }
  });
  optionChainClose.addEventListener("click", () => {
    optionChainDrawer.hidden = true;
  });
  optionUnderlying.addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      try {
        await loadOptionExpirations();
        await loadOptionChain();
      } catch (error) {
        window.alert(error.message);
      }
    }
  });
  optionUnderlying.addEventListener("change", async () => {
    try {
      await loadOptionExpirations();
    } catch (error) {
      optionChainSummary.textContent = error.message;
    }
  });
  optionExpiration.addEventListener("change", async () => {
    try {
      await loadOptionChain();
    } catch (error) {
      window.alert(error.message);
    }
  });
  optionChainRefresh.addEventListener("click", async () => {
    try {
      await loadOptionChain();
      await syncOptionPaperState();
    } catch (error) {
      window.alert(error.message);
    }
  });
  optionPaperReset.addEventListener("click", async () => {
    if (window.confirm("Reset all option paper trades?")) {
      renderOptionPaperState(await postJson("/api/options/paper/reset/"));
    }
  });

  const savedCount = getSavedCount();
  countSelect.value = String(savedCount);
  countSelect.addEventListener("change", () => {
    const count = Math.min(8, Number(countSelect.value));
    savePaneLayouts();
    localStorage.setItem(STORAGE_KEY, String(count));
    renderGrid(count);
  });

  renderGrid(savedCount);
}

window.addEventListener("beforeunload", () => {
  state.panes.forEach(stopPaneFeed);
});

boot();
