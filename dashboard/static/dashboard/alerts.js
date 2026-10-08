(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const dialog = $("alerts-dialog");
  let rules = [], selected = null, dirty = false, busy = false, historyCursor = null, historyOlder = false;
  const dateFormat = new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  const formatTime = (value) => value ? dateFormat.format(new Date(value)) : "--";
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  function status(text, error = false) {
    $("alerts-status").textContent = text;
    $("alerts-status").classList.toggle("error", error);
  }
  async function api(path, method = "GET", body) {
    const csrf = document.cookie.split("; ").find((cookie) => cookie.startsWith("csrftoken="));
    const response = await fetch(path, {
      method, credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf ? decodeURIComponent(csrf.slice(10)) : "" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status}).`);
    return result;
  }
  function visibility() {
    const ma = $("alert-left").value !== "price";
    $("alert-left-period-label").hidden = !ma;
    $("alert-left-period").disabled = !ma;
    const touch = $("alert-operator").querySelector('[value="touches"]');
    touch.disabled = ma;
    if (ma && $("alert-operator").value === "touches") $("alert-operator").value = "crosses_above";
    const level = $("alert-right").value === "level";
    $("alert-right-period-label").hidden = level;
    $("alert-right-period").disabled = level;
    $("alert-level-label").hidden = !level;
    $("alert-level").disabled = !level;
    $("alert-tolerance-label").hidden = $("alert-operator").value !== "touches";
    $("alert-tolerance").disabled = $("alert-tolerance-label").hidden;
  }
  function openRule(rule, confirmDiscard = true) {
    if (confirmDiscard && dirty && !window.confirm("Discard unsaved alert changes?")) return;
    selected = rule;
    $("alerts-form").reset();
    $("alerts-form-title").textContent = rule ? "Edit Alert" : "New Alert";
    $("alert-delete").hidden = !rule;
    if (rule) {
      $("alert-name").value = rule.name;
      $("alert-symbols").value = rule.symbols.join(", ");
      $("alert-enabled").checked = rule.enabled;
      $("alert-left").value = rule.condition.left.type;
      $("alert-left-period").value = rule.condition.left.period || 9;
      $("alert-operator").value = rule.condition.operator;
      $("alert-right").value = rule.condition.right.type;
      $("alert-right-period").value = rule.condition.right.period || 200;
      $("alert-level").value = rule.condition.right.value || 200;
      $("alert-tolerance").value = rule.condition.tolerance_percent;
      $("alert-cooldown").value = rule.cooldown_minutes;
      document.querySelectorAll('[name="alert-timeframe"]').forEach((input) => { input.checked = rule.timeframes.includes(input.value); });
    }
    dirty = false;
    visibility();
    renderList();
    renderStates(rule);
  }
  function renderStates(rule) {
    $("alert-scan-details").hidden = !rule;
    $("alert-scan-states").replaceChildren();
    if (rule) rule.states.forEach((state) => {
      $("alert-scan-states").appendChild(node("div", `${state.symbol} ${state.timeframe}: ${rule.enabled ? state.status : "Paused"} | ${formatTime(state.checked_at)}`));
    });
  }
  function renderList() {
    const list = $("alerts-list");
    list.replaceChildren();
    if (!rules.length) list.appendChild(node("p", "No saved alerts."));
    rules.forEach((rule) => {
      const row = node("div", undefined, "alert-list-row");
      row.classList.toggle("selected", Boolean(selected && selected.id === rule.id));
      const button = node("button");
      button.type = "button";
      button.append(node("strong", rule.name), node("small", rule.label), node("small", `${rule.symbols.join(", ")} | ${rule.timeframes.join(", ")}`));
      button.addEventListener("click", () => openRule(rule));
      const toggle = node("input");
      toggle.type = "checkbox"; toggle.checked = rule.enabled;
      toggle.setAttribute("aria-label", `Enable ${rule.name}`);
      toggle.title = rule.enabled ? "Pause alert" : "Enable alert";
      toggle.addEventListener("change", () => action(async () => {
        const result = await api(`/api/alerts/${rule.id}/`, "PATCH", { enabled: toggle.checked, revision: rule.revision });
        if (selected && selected.id === rule.id && !dirty) openRule(result.alert, false);
        await refresh();
      }));
      row.append(button, toggle); list.appendChild(row);
    });
  }
  async function refresh() {
    const data = await api("/api/alerts/");
    rules = data.alerts;
    $("alerts-connection").textContent = `${data.destination} | ${data.discord_ready ? "Discord configured" : "Discord unavailable"} | ${data.scanner_online ? "Scanner online" : "Scanner offline"}`;
    renderList();
    if (selected) {
      const current = rules.find((rule) => rule.id === selected.id);
      if (current) renderStates(current);
    }
  }
  function operand(side) {
    const type = $(`alert-${side}`).value;
    if (type === "price") return { type };
    if (type === "level") return { type, value: Number($("alert-level").value) };
    return { type, period: Number($(`alert-${side}-period`).value) };
  }
  async function history(older = false) {
    const data = await api(`/api/alerts/events/${older && historyCursor ? `?before=${historyCursor}` : ""}`);
    if (!older) { $("alerts-events").replaceChildren(); historyOlder = false; }
    else historyOlder = true;
    if (!older && !data.events.length) {
      const td = node("td", "No alert deliveries yet."); td.colSpan = 4;
      const tr = node("tr"); tr.appendChild(td); $("alerts-events").appendChild(tr);
    }
    data.events.forEach((event) => {
      const row = node("tr");
      const name = node("td", event.name); name.appendChild(node("small", `${event.symbol} | ${event.timeframe}`));
      if (event.reason) name.appendChild(node("small", event.reason));
      const price = node("td", Number(event.snapshot.close).toFixed(4));
      price.appendChild(node("small", `Source ${Number(event.snapshot.left).toFixed(4)} / Target ${Number(event.snapshot.right).toFixed(4)}`));
      const candle = node("td", formatTime(event.candle_close)); candle.appendChild(node("small", `Detected ${formatTime(event.detected_at)}`));
      const delivery = node("td", event.status, `alerts-delivery-${event.status}`);
      if (event.sent_at) delivery.appendChild(node("small", formatTime(event.sent_at)));
      if (event.error) delivery.appendChild(node("small", event.error));
      row.append(name, price, candle, delivery); $("alerts-events").appendChild(row);
    });
    historyCursor = data.next;
    $("alerts-older").hidden = !historyCursor;
  }
  async function action(task) {
    if (busy) return;
    busy = true; $("alert-save").disabled = true;
    try { status(""); await task(); }
    catch (error) { status(error.message, true); renderList(); }
    finally { busy = false; $("alert-save").disabled = false; }
  }
  function tab(showHistory) {
    $("alerts-rules-view").hidden = showHistory;
    $("alerts-history-view").hidden = !showHistory;
    $("alerts-rules-tab").setAttribute("aria-pressed", String(!showHistory));
    $("alerts-history-tab").setAttribute("aria-pressed", String(showHistory));
    if (showHistory) action(() => history());
  }
  $("alerts-toggle").addEventListener("click", () => { dialog.showModal(); action(refresh); });
  $("alerts-close").addEventListener("click", () => dialog.close());
  $("alerts-new").addEventListener("click", () => openRule(null));
  $("alerts-rules-tab").addEventListener("click", () => tab(false));
  $("alerts-history-tab").addEventListener("click", () => tab(true));
  $("alerts-refresh").addEventListener("click", () => action(async () => { await refresh(); if (!$("alerts-history-view").hidden) await history(); }));
  $("alerts-older").addEventListener("click", () => action(() => history(true)));
  $("alerts-form").addEventListener("input", () => { dirty = true; });
  ["alert-left", "alert-right", "alert-operator"].forEach((id) => $(id).addEventListener("change", visibility));
  $("alerts-form").addEventListener("submit", (event) => {
    event.preventDefault();
    action(async () => {
      const payload = {
        name: $("alert-name").value, symbols: $("alert-symbols").value.split(/[\s,]+/).filter(Boolean),
        timeframes: Array.from(document.querySelectorAll('[name="alert-timeframe"]:checked')).map((input) => input.value),
        enabled: $("alert-enabled").checked, cooldown_minutes: Number($("alert-cooldown").value),
        condition: { left: operand("left"), operator: $("alert-operator").value, right: operand("right"), tolerance_percent: $("alert-operator").value === "touches" ? Number($("alert-tolerance").value) : 0 },
      };
      if (selected) payload.revision = selected.revision;
      const result = await api(selected ? `/api/alerts/${selected.id}/` : "/api/alerts/", selected ? "PUT" : "POST", payload);
      openRule(result.alert, false); await refresh(); status("Alert saved.");
    });
  });
  $("alert-delete").addEventListener("click", () => {
    if (!selected || !window.confirm(`Delete alert "${selected.name}"? Delivery history will remain.`)) return;
    action(async () => {
      await api(`/api/alerts/${selected.id}/`, "DELETE", { revision: selected.revision });
      openRule(null, false); await refresh(); status("Alert deleted.");
    });
  });
  window.setInterval(() => {
    if (dialog.open && !busy && !document.hidden) action(async () => { await refresh(); if (!$("alerts-history-view").hidden && !historyOlder) await history(); });
  }, 15000);
  visibility();
})();
