(function () {
  "use strict";
  const dialog = document.querySelector("#alpaca-dialog");
  const status = document.querySelector("#alpaca-status");
  const refresh = document.querySelector("#alpaca-refresh");
  const metrics = document.querySelector("#alpaca-metrics");
  const feeds = document.querySelector("#alpaca-feeds");
  const positions = document.querySelector("#alpaca-positions");
  const orders = document.querySelector("#alpaca-orders");
  const appOrders = document.querySelector("#alpaca-app-orders");
  const newOrder = document.querySelector("#alpaca-new-order");
  const pacific = new Intl.DateTimeFormat(undefined, { timeZone: "America/Los_Angeles", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  let busy = false;
  let manualEnabled = false;
  const accountView = document.querySelector("#alpaca-account-view");
  const botView = document.querySelector("#alpaca-bot-view");
  const botStatus = document.querySelector("#alpaca-bot-status");
  const botStart = document.querySelector("#alpaca-bot-start");
  const botPause = document.querySelector("#alpaca-bot-pause");
  const botDte = document.querySelector("#alpaca-bot-dte");
  let botBusy = false;
  let botDteDirty = false;

  function money(value) {
    return value == null || !Number.isFinite(Number(value)) ? "--" : Number(value).toLocaleString(undefined, { style: "currency", currency: "USD" });
  }
  function stamp(value) {
    const date = new Date(value);
    return value && Number.isFinite(date.getTime()) ? pacific.format(date) : "--";
  }
  function pair(target, label, value, grouped) {
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value == null ? "--" : value;
    if (grouped) {
      const group = document.createElement("div");
      group.append(term, detail);
      target.append(group);
    } else target.append(term, detail);
  }
  function row(target, values) {
    const element = document.createElement("tr");
    values.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value == null ? "--" : value;
      element.append(cell);
    });
    target.append(element);
    return element;
  }
  function empty(target, text) {
    const result = row(target, [text]);
    result.firstChild.colSpan = target.closest("table").tHead.rows[0].cells.length;
  }
  function clear() {
    metrics.replaceChildren(); feeds.replaceChildren(); positions.replaceChildren(); orders.replaceChildren(); appOrders.replaceChildren();
    document.querySelector("#alpaca-sync-warning").textContent = "";
  }
  async function load() {
    if (busy) return;
    busy = true;
    refresh.disabled = true;
    newOrder.disabled = true;
    status.textContent = "Checking connection...";
    status.classList.remove("error");
    try {
      const response = await fetch("/api/alpaca/connection/", { cache: "no-store" });
      const data = await response.json();
      clear();
      pair(feeds, "Stock feed", data.stock_label);
      pair(feeds, "Options feed", data.option_label);
      pair(feeds, "Local scanner", data.scanner_source);
      manualEnabled = Boolean(data.manual_trading_enabled && data.connected);
      pair(feeds, "Broker orders", manualEnabled ? "Manual / Paper only" : "Disabled");
      pair(feeds, "Automatic orders", "Separate Strategy bot controls");
      document.querySelector("#alpaca-mode").textContent = botView.hidden ? (manualEnabled ? "Manual paper trading" : "Read only") : "Automated paper strategies";
      if (!response.ok) throw new Error(data.error || "Connection check failed.");
      if (!data.connected) {
        status.textContent = data.configured ? "Connection disabled" : "Not configured";
        empty(positions, "Not connected"); empty(orders, "Not connected");
        return;
      }
      status.textContent = `Connected / Paper / ${data.clock.is_open ? "Market open" : "Market closed"} / Checked ${stamp(data.checked_at * 1000)}`;
      pair(metrics, "Equity", money(data.account.equity), true);
      pair(metrics, "Cash", money(data.account.cash), true);
      pair(metrics, "Buying power", money(data.account.buying_power), true);
      pair(feeds, "Account status", data.account.status);
      pair(feeds, "Options level", data.account.options_trading_level);
      newOrder.disabled = !manualEnabled;
      document.querySelector("#alpaca-sync-warning").textContent = data.sync_warning || "";
      data.positions.forEach((item) => {
        const element = row(positions, [item.symbol, item.side, item.qty, money(item.avg_entry_price), money(item.current_price), money(item.unrealized_pl)]);
        element.lastChild.className = Number(item.unrealized_pl) >= 0 ? "profit" : "loss";
        const cell = document.createElement("td");
        if (manualEnabled && ["us_equity", "us_option"].includes(item.asset_class)) {
          const close = document.createElement("button");
          close.type = "button";
          close.textContent = "Close";
          close.title = `Review a closing paper order for ${item.symbol}`;
          close.addEventListener("click", () => openTicket({ symbol: item.symbol, asset_class: item.asset_class, action: "close", quantity: Math.abs(Number(item.qty)), limit_price: item.current_price }));
          cell.append(close);
        }
        element.append(cell);
      });
      data.orders.forEach((item) => row(orders, [item.symbol, item.side, `${item.filled_qty || "0"} / ${item.qty || "--"}`, money(item.filled_avg_price), item.status, stamp(item.submitted_at)]));
      if (!data.positions.length) empty(positions, "No open Alpaca positions");
      if (!data.orders.length) empty(orders, "No Alpaca orders");
      (data.app_orders || []).forEach((item) => {
        const orderState = item.phase === "cancel_pending" ? `Cancel requested / ${item.status}` : item.phase === "unknown" ? "Submission unconfirmed" : item.status;
        const element = row(appOrders, [item.symbol, item.intent.replace(/_/g, " "), `${item.filled_qty} / ${item.quantity}`, money(item.limit_price), money(item.filled_avg_price), orderState, stamp(item.submitted_at)]);
        const actor = document.createElement("small"); actor.textContent = item.actor; element.firstChild.append(actor);
        const filled = document.createElement("small"); filled.textContent = `Filled: ${stamp(item.filled_at)}`; element.lastChild.append(filled);
        if (item.error) { const detail = document.createElement("small"); detail.textContent = item.error; element.children[5].append(detail); }
        const cell = document.createElement("td");
        if (manualEnabled && ["working", "submitting", "unknown"].includes(item.phase) && item.broker_order_id) {
          const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = "Cancel";
          cancel.addEventListener("click", async () => {
            if (!window.confirm(`Request cancellation of ${item.symbol}, ${item.quantity} units? Filled units remain in your account.`)) return;
            cancel.disabled = true;
            try { await send(`/api/alpaca/orders/${item.id}/cancel/`, { confirmed: true }); await load(); }
            catch (error) { status.textContent = error.message; status.classList.add("error"); }
            finally { cancel.disabled = false; }
          });
          cell.append(cancel);
        }
        element.append(cell);
      });
      if (!(data.app_orders || []).length) empty(appOrders, "No submitted app paper orders");
    } catch (error) {
      metrics.replaceChildren(); positions.replaceChildren(); orders.replaceChildren(); appOrders.replaceChildren();
      manualEnabled = false;
      newOrder.disabled = true;
      status.textContent = error.message;
      status.classList.add("error");
      empty(positions, "Unavailable"); empty(orders, "Unavailable");
    } finally {
      busy = false; refresh.disabled = false;
    }
  }
  const ticket = document.querySelector("#alpaca-ticket");
  const fields = document.querySelector("#alpaca-ticket-fields");
  const asset = document.querySelector("#alpaca-ticket-asset");
  const action = document.querySelector("#alpaca-ticket-action");
  const symbol = document.querySelector("#alpaca-ticket-symbol");
  const quantity = document.querySelector("#alpaca-ticket-quantity");
  const limit = document.querySelector("#alpaca-ticket-limit");
  const ticketStatus = document.querySelector("#alpaca-ticket-status");
  const review = document.querySelector("#alpaca-review-order");
  const confirm = document.querySelector("#alpaca-confirm-order");
  const edit = document.querySelector("#alpaca-edit-order");
  const checkOrder = document.querySelector("#alpaca-check-order");
  const confirmation = document.querySelector("#alpaca-confirmation");
  let prepared = null;
  let ticketBusy = false;

  async function send(url, payload) {
    const csrf = document.cookie.split("; ").find((item) => item.startsWith("csrftoken="));
    const response = await fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf ? decodeURIComponent(csrf.slice(10)) : "" }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Paper order request failed (${response.status})`);
    return data;
  }

  function renderBot(data) {
    const offline = data.manage_exits && !data.worker_running;
    botStatus.textContent = `${data.enabled ? "Entries enabled" : "Entries paused"} / ${data.status}${offline ? " / Scanner offline: exits are not being monitored" : ""}`;
    botStatus.classList.toggle("error", offline);
    botStart.disabled = !data.available || !data.discord_ready || data.enabled;
    botPause.disabled = !data.enabled;
    botDte.disabled = data.enabled;
    if (data.enabled || !botDteDirty) botDte.value = String(data.min_dte);
    const settings = document.querySelector("#alpaca-bot-settings"); settings.replaceChildren();
    pair(settings, "Watchlist", data.symbols.join(", "));
    pair(settings, "Option sizing", "1 long ATM contract / Premium cap $1,500");
    pair(settings, "Expiry", `Nearest available expiry at least ${data.min_dte} days away`);
    pair(settings, "Sessions", "US regular hours only / Daily signals: next session's first 15 minutes");
    pair(settings, "Confirmations", "Completed candle / Volume versus previous candle / Close versus previous close");
    pair(settings, "Scanner heartbeat", stamp(data.heartbeat));
    pair(settings, "Activated", stamp(data.armed_at));
    pair(settings, "Discord", data.discord_ready ? "Shared channel / Regular hours only" : "Not configured or disabled");
    const presets = document.querySelector("#alpaca-bot-presets"); presets.replaceChildren();
    data.presets.forEach((item) => row(presets, [item.name, item.timeframes.join(", "), item.size, item.rule]));
    const held = document.querySelector("#alpaca-bot-positions"); held.replaceChildren();
    data.positions.forEach((item) => {
      const element = row(held, [item.symbol, item.direction, item.quantity, money(item.entry), money(item.exit), money(item.mark), money(item.pnl), stamp(item.quote_time), `${item.phase}: ${item.status}${item.exit_reason ? " / " + item.exit_reason : ""}`]);
      const detail = document.createElement("small"); detail.textContent = `${item.underlying} / ${item.strategy} / ${item.timeframe}`; element.firstChild.append(detail);
      if (item.pnl != null) element.children[6].className = Number(item.pnl) >= 0 ? "profit" : "loss";
    });
    if (!data.positions.length) empty(held, "No bot-owned positions");
    const scans = document.querySelector("#alpaca-bot-scans"); scans.replaceChildren();
    data.scans.forEach((item) => row(scans, [`${item.strategy} / ${item.symbol}`, item.timeframe, item.status, stamp(item.checked_at)]));
    if (!data.scans.length) empty(scans, "No scans recorded");
    const notices = document.querySelector("#alpaca-bot-notices"); notices.replaceChildren();
    data.notices.forEach((item) => {
      const article = document.createElement("article");
      const label = document.createElement("small"); label.textContent = `${stamp(item.created_at)} / ${item.status}${item.error ? " / " + item.error : ""}`;
      const message = document.createElement("p"); message.textContent = item.message;
      article.append(label, message); notices.append(article);
    });
    if (!data.notices.length) notices.textContent = "No bot notifications recorded";
  }
  async function loadBot() {
    if (botBusy) return;
    botBusy = true;
    try {
      const response = await fetch("/api/alpaca/bot/", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Cannot read bot state.");
      renderBot(data);
    } catch (error) { botStatus.textContent = error.message; botStatus.classList.add("error"); botStart.disabled = true; }
    finally { botBusy = false; }
  }
  async function controlBot(action) {
    if (botBusy) return;
    const prompt = action === "start"
      ? `Start automatic Alpaca PAPER orders for the listed 17 stocks? Daily SMA: 10 shares. Intraday: one long ATM option, expiry at least ${botDte.value} days, premium at most $1,500, TP +30%, SL -20%, opposite-cross exit. Limit fills are not guaranteed. The server must stay running.`
      : "Pause new entries? Pending entries will be canceled by the worker. Existing bot positions will still be monitored and sold on their exit rules.";
    if (!window.confirm(prompt)) return;
    botBusy = true; botStart.disabled = true; botPause.disabled = true;
    try {
      const data = await send("/api/alpaca/bot/control/", { action, min_dte: Number(botDte.value), confirmed: true });
      botDteDirty = false; renderBot(data);
    }
    catch (error) { botStatus.textContent = error.message; botStatus.classList.add("error"); }
    finally { botBusy = false; }
  }
  ["account", "bot"].forEach((view) => {
    document.querySelector(`#alpaca-${view}-tab`).addEventListener("click", () => {
      accountView.hidden = view !== "account"; botView.hidden = view !== "bot";
      document.querySelector("#alpaca-mode").textContent = view === "bot" ? "Automated paper strategies" : (manualEnabled ? "Manual paper trading" : "Read only");
      ["account", "bot"].forEach((key) => {
        const tab = document.querySelector(`#alpaca-${key}-tab`);
        tab.setAttribute("aria-selected", String(key === view)); tab.tabIndex = key === view ? 0 : -1;
      });
      if (view === "bot") loadBot(); else load();
    });
  });
  document.querySelector(".alpaca-tabs").addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? "account" : event.key === "End" ? "bot" : botView.hidden ? "bot" : "account";
    const tab = document.querySelector(`#alpaca-${next}-tab`); tab.focus(); tab.click();
  });
  botDte.addEventListener("change", () => { botDteDirty = true; });
  document.querySelector("#alpaca-bot-refresh").addEventListener("click", loadBot);
  botStart.addEventListener("click", () => controlBot("start"));
  botPause.addEventListener("click", () => controlBot("pause"));
  function openTicket(values = {}) {
    if (ticket.open) return;
    prepared = null;
    ticketBusy = false;
    fields.disabled = false;
    review.hidden = false;
    review.disabled = false;
    confirmation.hidden = true;
    checkOrder.hidden = true;
    edit.disabled = false;
    symbol.value = values.symbol || "";
    asset.value = values.asset_class || "us_equity";
    action.value = values.action || "buy";
    quantity.value = values.quantity || 1;
    quantity.max = asset.value === "us_option" ? "100" : "1000";
    limit.value = values.limit_price ? Number(values.limit_price).toFixed(asset.value === "us_option" ? 2 : 4) : "";
    ticketStatus.textContent = "";
    ticketStatus.classList.remove("error");
    document.querySelector("#alpaca-ticket-reference").textContent = values.quote_time ? `Reference quote: ${stamp(values.quote_time * 1000)}` : "";
    ticket.showModal();
    symbol.focus();
  }
  asset.addEventListener("change", () => { quantity.max = asset.value === "us_option" ? "100" : "1000"; });
  function resultStatus(order) {
    if (prepared) prepared.order = order;
    const label = order.phase === "cancel_pending" ? "Cancellation requested" : order.phase === "unknown" ? "Submission unconfirmed" : order.status;
    ticketStatus.textContent = `${order.symbol}: ${label}. Filled ${order.filled_qty} / ${order.quantity}${order.filled_avg_price == null ? "" : " at " + money(order.filled_avg_price)}.${order.error ? " " + order.error : ""}`;
    ticketStatus.classList.toggle("error", ["unknown", "rejected"].includes(order.phase));
    checkOrder.hidden = false;
    confirm.disabled = true;
    edit.disabled = true;
  }
  document.querySelector("#alpaca-ticket-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (ticketBusy) return;
    ticketBusy = true; review.disabled = true; fields.disabled = true;
    ticketStatus.classList.remove("error"); ticketStatus.textContent = "Checking paper account and order terms...";
    try {
      prepared = await send("/api/alpaca/orders/prepare/", { symbol: symbol.value.trim().toUpperCase(), asset_class: asset.value, action: action.value, quantity: quantity.value, limit_price: limit.value });
      const order = prepared.order; const preview = order.preview;
      const summary = document.querySelector("#alpaca-order-summary"); summary.replaceChildren();
      pair(summary, "Account", "Alpaca PAPER / Shared");
      pair(summary, "Contract / Stock", preview.contract_name);
      pair(summary, "Action", preview.intent.replace(/_/g, " "));
      pair(summary, "Quantity", preview.quantity);
      pair(summary, "Limit / unit", money(preview.limit_price));
      pair(summary, "Multiplier", preview.multiplier);
      pair(summary, "Limit value", `${money(preview.limit_value)} excluding fees`);
      pair(summary, "Review expires", stamp(order.expires_at));
      const warnings = [];
      if (!preview.market_open) warnings.push("Market closed. The DAY order may queue for the next session.");
      if (preview.asset_class === "us_option") {
        if (preview.option_feed === "indicative") warnings.push("Indicative option feed: delayed trades and modified quotes.");
        warnings.push("Alpaca may exercise or liquidate expiring options.");
      }
      warnings.push("A submitted limit order is not a confirmed fill. No automatic exit is attached.");
      document.querySelector("#alpaca-order-warning").textContent = warnings.join(" ");
      confirmation.hidden = false; review.hidden = true; confirm.disabled = false; edit.disabled = false;
      ticketStatus.textContent = "Reviewed. No order has been sent to Alpaca.";
    } catch (error) { fields.disabled = false; ticketStatus.textContent = error.message; ticketStatus.classList.add("error"); }
    finally { ticketBusy = false; review.disabled = false; }
  });
  edit.addEventListener("click", () => { prepared = null; confirmation.hidden = true; fields.disabled = false; review.hidden = false; ticketStatus.textContent = ""; });
  confirm.addEventListener("click", async () => {
    if (ticketBusy || !prepared) return;
    ticketBusy = true; confirm.disabled = true; edit.disabled = true;
    prepared.order.phase = "submitting";
    ticketStatus.textContent = "Submitting the confirmed paper order...";
    try {
      const data = await send(`/api/alpaca/orders/${prepared.order.id}/submit/`, { confirmation_token: prepared.confirmation_token });
      resultStatus(data.order);
      if (dialog.open) await load();
    } catch (error) {
      ticketStatus.textContent = `${error.message} Refresh this order's status before creating a replacement.`;
      ticketStatus.classList.add("error"); checkOrder.hidden = false;
    } finally { ticketBusy = false; }
  });
  checkOrder.addEventListener("click", async () => {
    if (!prepared || ticketBusy) return;
    ticketBusy = true; checkOrder.disabled = true;
    try {
      const response = await fetch(`/api/alpaca/orders/${prepared.order.id}/`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Cannot read order status.");
      resultStatus(data.order);
      if (data.order.phase === "prepared") {
        edit.disabled = false;
        confirm.disabled = Date.now() >= Date.parse(data.order.expires_at);
        ticketStatus.textContent = "This ticket has not been submitted. Confirm it or edit and review again.";
      }
      if (dialog.open) await load();
    } catch (error) { ticketStatus.textContent = error.message; ticketStatus.classList.add("error"); }
    finally { ticketBusy = false; checkOrder.disabled = false; }
  });
  document.querySelector("#alpaca-ticket-close").addEventListener("click", () => { if (!ticketBusy) ticket.close(); });
  ticket.addEventListener("cancel", (event) => { if (ticketBusy) event.preventDefault(); });
  newOrder.addEventListener("click", () => { if (manualEnabled) openTicket(); });
  window.AlpacaTrading = { openTicket };
  document.querySelector("#alpaca-toggle").addEventListener("click", () => { dialog.showModal(); if (botView.hidden) load(); else loadBot(); });
  refresh.addEventListener("click", load);
  window.setInterval(() => { if (dialog.open && !document.hidden) { if (botView.hidden) load(); else loadBot(); } }, 15000);
  window.setInterval(() => {
    if (ticket.open && prepared && !ticketBusy && !document.hidden && ["submitting", "working", "unknown", "cancel_pending"].includes(prepared.order.phase)) checkOrder.click();
  }, 10000);
}());
