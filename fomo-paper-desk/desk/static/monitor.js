const storageKey = "desk_token";

function captureFragment() {
  const hash = window.location.hash || "";
  const match = /(?:^#|&)t=([^&]+)/.exec(hash);
  if (match) {
    localStorage.setItem(storageKey, decodeURIComponent(match[1]));
    const clean = window.location.pathname + window.location.search;
    history.replaceState(null, "", clean);
  }
}

function token() {
  return localStorage.getItem(storageKey) || "";
}

const isLocalFile = window.location.protocol === "file:";
if (!isLocalFile) captureFragment();
else document.getElementById("file-start").hidden = false;

async function api(path, options = {}) {
  const headers = Object.assign({ Authorization: "Bearer " + token() }, options.headers || {});
  const response = await fetch(path, Object.assign({}, options, { headers }));
  if (response.status === 401) {
    localStorage.removeItem(storageKey);
    showLogin();
    throw new Error("unauthorized");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : "Eingabe ungültig oder Quelle fehlgeschlagen");
  }
  return body;
}

function stateClass(state) {
  if (state === "pass" || state === "checked" || state === "open") return "pass";
  if (state === "fail" || state === "rejected" || state === "error") return "fail";
  return "missing";
}

function render(view) {
  const banner = document.getElementById("banner");
  banner.innerHTML = "";
  if (view.connection === "error" || view.connection_error) {
    const node = document.createElement("div");
    node.className = "banner";
    node.textContent = view.connection_detail || view.connection_error || "Verbindung gestört";
    banner.appendChild(node);
  }
  const status = document.getElementById("status");
  status.innerHTML = "";
  const rows = [
    ["Handel", view.trading],
    ["Marktlesen", view.market_data],
    ["Quelle", view.source_label],
    ["Verbindung", view.connection_detail || view.connection],
    ["Telegram", "aus"],
    ["Zustellung", "aus, nur diese Ansicht"],
  ];
  for (const [name, value] of rows) {
    const row = document.createElement("div");
    row.className = "row";
    const left = document.createElement("strong");
    left.textContent = name;
    const right = document.createElement("span");
    right.textContent = value == null ? "—" : String(value);
    if (name === "Verbindung") right.className = stateClass(view.connection);
    row.append(left, right);
    status.append(row);
  }
  const host = document.getElementById("checks");
  host.innerHTML = "";
  if (view.empty) {
    const card = document.createElement("section");
    card.className = "card";
    const title = document.createElement("h2");
    title.textContent = "Warte auf Migration";
    const text = document.createElement("p");
    text.className = "empty";
    text.textContent = view.empty_text;
    card.append(title, text);
    host.append(card);
  }
  for (const item of view.waiting || []) {
    const card = document.createElement("section");
    card.className = "card";
    const title = document.createElement("h2");
    title.textContent = "Im Warteraum";
    const mint = document.createElement("p");
    mint.className = "mint";
    mint.textContent = item.mint;
    card.append(title, mint);
    host.append(card);
  }
  for (const check of view.checks || []) {
    host.append(renderCheck(check));
  }
}

function renderCheck(check) {
  const card = document.createElement("article");
  card.className = "card";
  const title = document.createElement("h2");
  title.textContent = check.symbol ? check.name + " · " + check.symbol : (check.name || check.mint);
  const link = document.createElement("a");
  link.href = check.coin_url;
  link.textContent = "Auf FOMO öffnen";
  link.rel = "noreferrer";
  const mint = document.createElement("p");
  mint.className = "mint";
  mint.textContent = check.mint;
  const meta = document.createElement("p");
  meta.className = "empty";
  const kind = check.live_migration ? "Migration" : "Historische Prüfung";
  meta.textContent = kind + " · " + (check.ts || "") + (check.note ? " · " + check.note : "");
  card.append(title, link, mint, meta);
  if (check.error) {
    const err = document.createElement("p");
    err.className = "fail";
    err.textContent = check.error;
    card.append(err);
  }
  card.append(sectionTitle("Scanner"));
  for (const measure of check.scanner.measurements) {
    card.append(line(measure.name, (measure.value ?? "keine Daten") + " · " + measure.rule, measure.state));
  }
  if (!check.scanner.measurements.length) {
    card.append(line("Scanner", check.scanner.reason || "keine Messung", "missing"));
  }
  card.append(sectionTitle("Auditor"));
  for (const layer of check.layers) {
    const label = layer.status === "checked" ? "geprüft" : layer.status === "rejected" ? "rot" : "nicht geprüft";
    card.append(line(layer.n + " " + layer.name, (layer.value || label) + (layer.source ? " · " + layer.source : ""), layer.status));
  }
  card.append(sectionTitle("Analyst"));
  if (!check.analyst.ran) {
    card.append(line("Analyst", check.analyst.reason, "not_checked"));
  } else {
    card.append(line("Score", String(check.analyst.score ?? "—"), check.analyst.status));
    card.append(line("Grund", check.analyst.reason || "bestanden", check.analyst.status));
    card.append(line("Zeit", check.analyst.ts || "—", "pass"));
  }
  return card;
}

function sectionTitle(text) {
  const node = document.createElement("h2");
  node.textContent = text;
  return node;
}

function line(name, value, state) {
  const row = document.createElement("div");
  row.className = "layer";
  const left = document.createElement("strong");
  left.textContent = name;
  const right = document.createElement("span");
  right.className = stateClass(state);
  right.textContent = value;
  row.append(left, right);
  return row;
}

let refreshing = false;
let activeTab = "overview";
let nextActivity = null;
const activityRows = new Map();
const money = value => value == null ? "—" : new Intl.NumberFormat("de-DE", {style: "currency", currency: "USD"}).format(Number(value));
const stamp = value => value ? new Date(value).toLocaleString("de-DE") : "—";

function showLogin() {
  document.getElementById("login").hidden = false;
  document.getElementById("workspace").hidden = true;
}

function announce(message) {
  const node = document.createElement("div");
  node.className = "banner";
  node.textContent = message;
  document.getElementById("banner").replaceChildren(node);
}

async function refresh() {
  if (isLocalFile) return;
  if (!token()) { showLogin(); return; }
  if (refreshing) return;
  refreshing = true;
  try {
    const [view, state, positions, chart, activity] = await Promise.all([
      api("/api/monitor"), api("/api/state"), api("/api/positions"),
      api("/api/chart?range=" + document.getElementById("chart-range").value), api("/api/activity"),
    ]);
    if (!token()) return;
    document.getElementById("login").hidden = true;
    document.getElementById("workspace").hidden = false;
    render(view);
    renderSummary(state);
    renderAgents(state);
    renderPositions(positions.positions);
    renderChart(chart);
    mergeActivity(activity, false);
    if (!document.getElementById("report-date").value) document.getElementById("report-date").value = state.night_id;
    if (activeTab === "report") await loadReport();
    document.getElementById("updated").textContent = "Aktualisiert " + new Date().toLocaleTimeString("de-DE");
    if (state.ticket_warning) {
      const note = document.createElement("div"); note.className = "banner";
      note.textContent = state.ticket_warning;
      document.getElementById("banner").append(note);
    }
  } catch (error) {
    announce(error.message === "unauthorized" ? "Bitte mit deinem lokalen Zugangstoken anmelden." : "Aktualisierung fehlgeschlagen. Angezeigte Daten können veraltet sein. " + error.message);
  } finally {
    refreshing = false;
  }
}

function renderSummary(state) {
  const host = document.getElementById("summary"); host.replaceChildren();
  for (const [label, value] of [["Paper-Kontowert", money(state.balance)], ["Verfügbar", money(state.cash)], ["Reserviert", money(state.reserved)], ["Offene Positionen", state.exposure]]) {
    const card = document.createElement("div"); card.className = "metric";
    const small = document.createElement("small"); small.textContent = label;
    const strong = document.createElement("strong"); strong.textContent = value;
    card.append(small, strong); host.append(card);
  }
}

function renderAgents(state) {
  const host = document.getElementById("agents"); host.replaceChildren();
  const labels = {paused: "Pausiert", running: "Aktiv", starting: "Startet", stalled: "Kein aktueller Heartbeat", idle: "Bereit", held: "Angehalten"};
  for (const agent of state.agents) {
    const card = document.createElement("article"); card.className = "agent";
    const title = document.createElement("h3"); title.textContent = agent.name;
    const status = document.createElement("p"); status.textContent = labels[agent.status] || agent.status;
    const detail = document.createElement("p"); detail.className = "empty";
    detail.textContent = agent.detail || "Wartet auf Aufgabe";
    const button = document.createElement("button"); button.className = "secondary";
    button.textContent = agent.paused ? "Fortsetzen" : "Pausieren";
    button.disabled = agent.name === "risk" && state.exposure > 0;
    if (button.disabled) button.title = "Risk überwacht offene Positionen und bleibt aktiv.";
    button.onclick = async () => {
      button.disabled = true;
      try { await api("/api/agents/" + agent.name + (agent.paused ? "/resume" : "/pause"), {method: "POST"}); await refresh(); }
      catch (error) { announce(error.message); }
      finally { button.disabled = agent.name === "risk" && state.exposure > 0; }
    };
    card.append(title, status, detail, button); host.append(card);
  }
}

function renderPositions(positions) {
  const host = document.getElementById("positions"); host.replaceChildren();
  if (!positions.length) { host.append(empty("Noch keine offene Paper-Position. Der Desk steigt erst nach bestandenen Prüfungen ein.")); return; }
  for (const position of positions) {
    const card = document.createElement("article"); card.className = "position";
    const title = document.createElement("h3"); title.textContent = position.symbol || position.name || position.mint;
    card.append(title);
    for (const [label, value] of [["Status", position.status], ["Einsatz", money(position.size_usd)], ["Einstieg", position.entry_price], ["Letzter Preis", position.last_price ?? "keine Daten"], ["Preiszeitpunkt", stamp(position.last_price_at)], ["Stop", position.stop_price], ["Trailing", position.trail_active ? "aktiv" : "wartet"]]) card.append(line(label, value, ""));
    if (position.testdata) card.append(empty("TESTDATA · simulierte Testposition"));
    host.append(card);
  }
}

function empty(text) { const p = document.createElement("p"); p.className = "empty"; p.textContent = text; return p; }

function renderChart(chart) {
  const host = document.getElementById("chart"); host.replaceChildren();
  const points = chart.points.filter(p => Number.isFinite(Number(p.value)));
  if (!points.length) { host.append(empty("Noch keine Buchungen in diesem Zeitraum.")); return; }
  if (points.length === 1) { host.append(empty(money(points[0].value) + " · Startwert. Weitere Buchungen erscheinen hier.")); return; }
  const values = points.map(p => Number(p.value));
  const low = Math.min(...values), high = Math.max(...values);
  const start = Date.parse(points[0].t), end = Date.parse(points.at(-1).t);
  const coordinates = points.map(p => [20 + (Date.parse(p.t) - start) / (end - start || 1) * 960, 145 - (Number(p.value) - low) / (high - low || 1) * 120]);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 1000 170"); svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Kontoverlauf von " + money(values[0]) + " bis " + money(values.at(-1)));
  const path = document.createElementNS(svg.namespaceURI, "polyline");
  path.setAttribute("points", coordinates.map(p => p.join(",")).join(" "));
  path.setAttribute("fill", "none"); path.setAttribute("stroke", "#b6f36a"); path.setAttribute("stroke-width", "3");
  svg.append(path); host.append(svg);
  const labels = document.createElement("div"); labels.className = "chart-labels";
  for (const value of [stamp(points[0].t), money(low) + " – " + money(high), stamp(points.at(-1).t)]) { const span = document.createElement("span"); span.textContent = value; labels.append(span); }
  host.append(labels);
}

function mergeActivity(page, older) {
  const firstLoad = activityRows.size === 0;
  for (const row of page.rows) activityRows.set(row.id, row);
  if (older || firstLoad) nextActivity = page.next_before;
  const host = document.getElementById("activity"); host.replaceChildren();
  if (!activityRows.size) host.append(empty("Noch keine Aktivitäten."));
  for (const row of [...activityRows.values()].sort((a,b) => b.id - a.id)) host.append(line(row.title || row.kind, [stamp(row.ts), row.subtitle, row.value].filter(Boolean).join(" · "), row.kind === "rejected" ? "fail" : ""));
  document.getElementById("more-activity").hidden = nextActivity == null;
}

let reportRequest = 0;
async function loadReport() {
  const request = ++reportRequest;
  const day = document.getElementById("report-date").value;
  if (!day) return;
  const report = await api("/api/report?date=" + encodeURIComponent(day));
  if (request !== reportRequest || !token()) return;
  const host = document.getElementById("report-body"); host.replaceChildren();
  host.append(empty((report.ongoing ? "Zwischenstand" : "Abgeschlossener Zeitraum") + " · " + report.date + " · UTC" + (report.owner_utc_offset_hours >= 0 ? "+" : "") + report.owner_utc_offset_hours));
  for (const [label, value] of [["Gescannte Coins", report.scanned], ["Auditor bestanden", report.audit_passed], ["Analyst bestanden", report.analyst_passed], ["Signale", report.signals], ["Abgeschlossene Trades", report.closed_trades], ["Netto-PnL", money(report.net_pnl_usd)], ["Modellgebühren", money(report.fees_usd)], ["Gewinnquote", report.win_rate_pct == null ? "keine Trades" : report.win_rate_pct + " %"], ["TESTDATA-Trades (separat)", report.testdata_closed_trades]]) host.append(line(label, value, ""));
  host.append(sectionTitle("Warum Coins ausgeschieden sind"));
  const reasons = [...report.scanner_rejections.map(r => ({...r, prefix: "Scanner"})), ...report.audit_rejections.map(r => ({...r, prefix: "Auditor"}))];
  if (!reasons.length) host.append(empty("Keine Ablehnungen in diesem Zeitraum gespeichert."));
  for (const reason of reasons) host.append(line(reason.prefix + " · " + reason.reason, reason.count + "×", "fail"));
  host.append(empty(report.note));
}

document.getElementById("login-form").addEventListener("submit", async event => {
  event.preventDefault();
  localStorage.setItem(storageKey, document.getElementById("access-token").value.trim());
  document.getElementById("access-token").value = "";
  await refresh();
});
document.getElementById("logout").onclick = () => {
  localStorage.removeItem(storageKey); activityRows.clear(); nextActivity = null; reportRequest++;
  for (const id of ["summary", "positions", "agents", "activity", "chart", "checks", "report-body", "status", "banner"]) document.getElementById(id).replaceChildren();
  showLogin();
};
document.querySelectorAll("[data-tab]").forEach(button => button.onclick = () => {
  activeTab = button.dataset.tab;
  document.querySelectorAll(".tab-panel").forEach(panel => panel.hidden = panel.id !== activeTab);
  document.querySelectorAll("[data-tab]").forEach(tab => tab.setAttribute("aria-pressed", String(tab === button)));
  if (activeTab === "report") loadReport().catch(error => announce(error.message));
});
document.getElementById("chart-range").onchange = refresh;
document.getElementById("report-date").onchange = () => loadReport().catch(error => announce(error.message));
document.getElementById("more-activity").onclick = async () => {
  const button = document.getElementById("more-activity"); button.disabled = true;
  try { mergeActivity(await api("/api/activity?before=" + nextActivity), true); }
  catch (error) { announce(error.message); }
  finally { button.disabled = false; }
};
document.getElementById("export-report").onclick = async () => {
  const button = document.getElementById("export-report"); button.disabled = true;
  try {
    const day = document.getElementById("report-date").value;
    const response = await fetch("/api/report?format=csv&date=" + encodeURIComponent(day), {headers: {Authorization: "Bearer " + token()}});
    if (!response.ok) throw new Error("Export fehlgeschlagen. Bitte Verbindung und Anmeldung prüfen.");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a"); link.href = url; link.download = "paper-report-" + day + ".csv";
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { announce(error.message); }
  finally { button.disabled = false; }
};

document.getElementById("probe").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    await api("/api/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mint: document.getElementById("mint").value.trim() }),
    });
    await refresh();
  } catch (error) {
    const banner = document.getElementById("banner");
    const node = document.createElement("div");
    node.className = "banner";
    node.textContent = error.message;
    banner.prepend(node);
  } finally {
    button.disabled = false;
  }
});

refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 5000);
