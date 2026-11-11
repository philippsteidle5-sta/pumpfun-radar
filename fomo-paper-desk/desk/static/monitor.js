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

let latestView = null;

function render(view) {
  latestView = view;
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
  renderFreshness(view);
  renderExecution();
  renderSearch(view);
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
let chartRange = "12H";
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
    const [view, state, bridge, activity, positions, chart, session] = await Promise.all([
      api("/api/monitor"), api("/api/state"), api("/api/bridge"), api("/api/activity"),
      api("/api/positions"), api("/api/chart?range=" + chartRange), api("/api/session"),
    ]);
    if (!token()) return;
    document.getElementById("login").hidden = true;
    document.getElementById("workspace").hidden = false;
    render(view);
    renderHeader(state);
    renderSummary(state);
    renderBridge(bridge);
    renderAgents(state, bridge);
    renderPositions(positions.positions || []);
    renderChart(chart);
    renderRecap(session);
    mergeActivity(activity, false);
    if (!document.getElementById("report-date").value) document.getElementById("report-date").value = state.night_id;
    if (activeTab === "report") await loadReport();
    document.getElementById("updated").textContent = "Aktualisiert " + new Date().toLocaleTimeString("de-DE");
    void state;
  } catch (error) {
    announce(error.message === "unauthorized" ? "Bitte mit deinem lokalen Zugangstoken anmelden." : "Aktualisierung fehlgeschlagen. Angezeigte Daten können veraltet sein. " + error.message);
  } finally {
    refreshing = false;
  }
}

function renderHeader(state) {
  document.getElementById("header-balance").textContent = money(state.balance);
  const multiple = state.multiplier == null ? "—" : state.multiplier + "×";
  const halted = state.entries_halted ? " · neue Einstiege gestoppt" : "";
  document.getElementById("header-multiple").textContent = "PAPER · " + multiple + " gegen Start " + money(state.start_balance) + halted;
}

function renderSummary(state) {
  const host = document.getElementById("summary"); host.replaceChildren();
  const profit = Number(state.balance) - Number(state.start_balance);
  const rows = [
    ["Paper-Kontostand", money(state.balance), ""],
    ["Gegen Start", (Number.isFinite(profit) && profit > 0 ? "+" : "") + money(profit), profit < 0 ? "neg" : profit > 0 ? "pos" : ""],
    ["Vielfaches", state.multiplier == null ? "—" : state.multiplier + "×", ""],
  ];
  for (const [label, value, tone] of rows) {
    const card = document.createElement("div"); card.className = "metric";
    const small = document.createElement("small"); small.textContent = label;
    const strong = document.createElement("strong"); strong.className = tone; strong.textContent = value;
    card.append(small, strong); host.append(card);
  }
}

function renderBridge(bridge) {
  const host = document.getElementById("bridge-note"); host.replaceChildren();
  const armed = document.createElement("div"); armed.className = "metric";
  const armedLabel = document.createElement("small"); armedLabel.textContent = "Ausführung";
  const armedValue = document.createElement("strong"); armedValue.className = "neg"; armedValue.textContent = "NICHT SCHARF";
  armed.append(armedLabel, armedValue); host.append(armed);
  const hook = document.createElement("div"); hook.className = "metric";
  const hookLabel = document.createElement("small"); hookLabel.textContent = "Webhook";
  const hookValue = document.createElement("strong"); hookValue.textContent = bridge.webhook_configured ? "hinterlegt, noch ohne Ack" : "nicht konfiguriert";
  hook.append(hookLabel, hookValue); host.append(hook);
  const roles = bridge.roles || {};
  const linked = Object.values(roles).filter(role => role.connected).length;
  const count = document.createElement("div"); count.className = "metric";
  const countLabel = document.createElement("small"); countLabel.textContent = "Bots mit echtem Ack";
  const countValue = document.createElement("strong"); countValue.textContent = String(linked) + " / 5";
  count.append(countLabel, countValue); host.append(count);
  const fomoscan = bridge.fomoscan || {};
  host.append(line("FomoScan", fomoscan.configured ? "Schlüssel gesetzt, nicht verbunden, keine Orders" : "nicht konfiguriert · nur Handle/Wallet-Daten · keine Orders", ""));
  for (const job of bridge.jobs || []) {
    if (job.kind !== "order_review") continue;
    host.append(line(job.subject || job.job_id, "Prüfung " + job.status + " · nicht gesendet", ""));
  }
}

function renderFreshness(view) {
  const node = document.getElementById("freshness");
  const feed = view.search && view.search.feed;
  const fresh = Boolean(feed && feed.fresh);
  node.className = fresh ? "freshness fresh" : "freshness";
  node.textContent = fresh ? "Migrationsfeed frisch" : "Kein frischer Migrationsframe";
}

function renderExecution() {
  const text = document.getElementById("execution-copy");
  if (text) text.textContent = "PAPER-Handel läuft nach den bestehenden Regeln. Live-Orders sind nicht scharf. Ein Bot gilt erst nach einem echten Ack als verbunden.";
}

function renderSearch(view) {
  const search = view.search || {};
  const filters = document.getElementById("scan-filters");
  const sources = document.getElementById("scan-sources");
  const candidates = document.getElementById("scan-candidates");
  filters.replaceChildren();
  sources.replaceChildren();
  candidates.replaceChildren();
  const rules = search.filters || {};
  const filterRows = [
    ["Mindestliquidität", rules.min_liquidity_usd == null ? "—" : money(rules.min_liquidity_usd)],
    ["Top 10 höchstens", rules.max_top10_pct == null ? "—" : rules.max_top10_pct + " %"],
    ["1h-Änderung höchstens", rules.max_h1_change_pct == null ? "—" : rules.max_h1_change_pct + " %"],
    ["Mindest-Poolalter", rules.min_pool_age_min == null ? "—" : rules.min_pool_age_min + " min"],
    ["Warteraum höchstens", rules.max_wait_min == null ? "—" : rules.max_wait_min + " min"],
    ["Scan-Abstand", rules.poll_s == null ? "—" : rules.poll_s + " s"],
    ["Letzter Scan", stamp(search.last_scan)],
    ["Nächster Scan", stamp(search.next_scan)],
  ];
  for (const [name, value] of filterRows) filters.append(line(name, value, ""));
  const rate = search.rate || {};
  filters.append(line("Lokale Sperren", String(rate.denied ?? 0), ""));
  filters.append(line("Upstream HTTP 429", String(rate.upstream_429 ?? 0), rate.upstream_429 ? "fail" : ""));
  filters.append(line("Cooldown bis", stamp(rate.backoff_until), rate.block_reason === "upstream_429" ? "fail" : ""));
  for (const source of search.sources || []) {
    const state = source.kind === "ok" || source.kind === "fresh" ? "pass" : source.kind === "upstream_429" || source.kind === "error" || source.kind === "timeout" || source.kind === "http" ? "fail" : "missing";
    sources.append(line(source.name, [source.kind, source.status == null ? "" : "HTTP " + source.status, source.detail, source.url].filter(Boolean).join(" · "), state));
    sources.append(line("Letzter Versuch / Erfolg", stamp(source.last_attempt) + " / " + stamp(source.last_success), ""));
  }
  const waiting = view.waiting || [];
  const recent = search.recent_scans || [];
  if (!waiting.length && !recent.length) candidates.append(empty("Noch keine Migration im Warteraum und kein gespeichertes Scan-Urteil."));
  for (const item of waiting) candidates.append(candidateRow(item, true));
  for (const row of recent) candidates.append(candidateRow(row, false));
  for (const check of view.checks || []) {
    if (!check.stage) continue;
    const button = document.createElement("button");
    button.className = "secondary";
    button.type = "button";
    button.textContent = (check.symbol || check.name || check.mint) + " · " + check.stage.name + " " + check.stage.state;
    button.onclick = () => openCoin(check.mint, check);
    candidates.append(button);
    candidates.append(line(check.stage.name, check.stage.reason || "—", check.stage.state === "reject" ? "fail" : ""));
  }
}

function candidateRow(item, waiting) {
  const button = document.createElement("button");
  button.className = "secondary";
  button.type = "button";
  const name = item.symbol || item.name || item.mint;
  button.textContent = (waiting ? "Warteraum · " : "Scan · ") + name;
  button.onclick = () => openCoin(item.mint, item);
  const wrap = document.createElement("div");
  wrap.append(button);
  wrap.append(line("Mint / Pool", [item.mint, item.pool].filter(Boolean).join(" · "), ""));
  wrap.append(line("Urteil", [item.verdict || "pending", item.reason].filter(Boolean).join(" · "), item.verdict === "kill" ? "fail" : "missing"));
  wrap.append(line("Quellezeit", stamp(item.scan_ts || item.ts || item.seen_at), ""));
  return wrap;
}

async function openCoin(mint, known) {
  const panel = document.getElementById("coin-detail");
  const body = document.getElementById("coin-body");
  panel.hidden = false;
  body.replaceChildren(empty("Lade gespeicherte Münzdaten …"));
  let coin = null;
  try { coin = await api("/api/coin/" + encodeURIComponent(mint)); }
  catch (error) { coin = null; if (error.message !== "unknown mint") announce(error.message); }
  body.replaceChildren();
  const title = document.createElement("h3");
  title.textContent = (coin && (coin.symbol || coin.name)) || (known && (known.symbol || known.name)) || mint;
  body.append(title);
  body.append(line("Netzwerk", "Solana", ""));
  body.append(line("Mint", mint, ""));
  body.append(line("Alter", known && known.age_min != null ? known.age_min + " min" : "keine gespeicherte Pool-Zeit", ""));
  const stats = coin && coin.stats || {};
  body.append(line("Marktkapitalisierung", stats.market_cap == null ? "keine Dex-Angabe" : money(stats.market_cap), ""));
  body.append(line("Dex-Volumen m5", stats.volume_m5 == null ? "kein Snapshot" : money(stats.volume_m5) + " · Snapshot, nicht 1m-Tradevolumen", ""));
  body.append(line("Preis", coin && coin.price != null ? coin.price : "kein gespeicherter Preis", ""));
  const position = coin && coin.position;
  body.append(empty("Quote, keine Order. Keine Live-Position und kein simulierter Fill."));
  if (known && known.reason) body.append(line("Letzter Grund", known.reason, ""));
  const risk = latestView && latestView.search && latestView.search.risk_rules;
  if (risk) {
    body.append(line("Stop-Regel", risk.stop_pct + " %", ""));
    body.append(line("Trail", "Start " + risk.trail_start_pct + " % · Abstand " + risk.trail_pct + " %", ""));
  }
  body.append(renderCandles(coin, null));
  if (coin && coin.coin_url) {
    const link = document.createElement("a");
    link.href = coin.coin_url;
    link.rel = "noreferrer";
    link.textContent = "Öffentlicher FOMO-Link, kein Orderweg";
    body.append(link);
  }
  panel.scrollIntoView({block: "nearest"});
}

function renderCandles(coin, position) {
  const host = document.createElement("div");
  const candles = coin && Array.isArray(coin.candles) ? coin.candles : [];
  if (!candles.length) {
    host.append(empty("Noch keine eigenen Preiszeilen für eine Kerze."));
    return host;
  }
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 320 160");
  svg.setAttribute("class", "candles");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Kerzen aus gespeicherten Desk-Preisen");
  const highs = candles.map(row => Number(row.high));
  const lows = candles.map(row => Number(row.low));
  let hi = Math.max(...highs);
  let lo = Math.min(...lows);
  const entry = position && Number(position.entry_price);
  if (Number.isFinite(entry)) { hi = Math.max(hi, entry); lo = Math.min(lo, entry); }
  const span = hi - lo || 1;
  candles.forEach((row, index) => {
    const x = 8 + index * ((304) / Math.max(candles.length, 1));
    const y = value => 150 - ((value - lo) / span) * 140;
    const open = Number(row.open), close = Number(row.close);
    const up = close >= open;
    const line = document.createElementNS(svg.namespaceURI, "line");
    line.setAttribute("x1", x); line.setAttribute("x2", x);
    line.setAttribute("y1", y(Number(row.high))); line.setAttribute("y2", y(Number(row.low)));
    line.setAttribute("stroke", up ? "#32d74b" : "#ff453a");
    const body = document.createElementNS(svg.namespaceURI, "line");
    body.setAttribute("x1", x); body.setAttribute("x2", x);
    body.setAttribute("y1", y(open)); body.setAttribute("y2", y(close));
    body.setAttribute("stroke", up ? "#32d74b" : "#ff453a");
    body.setAttribute("stroke-width", "4");
    svg.append(line, body);
  });
  if (Number.isFinite(entry)) {
    const y = 150 - ((entry - lo) / span) * 140;
    const guide = document.createElementNS(svg.namespaceURI, "line");
    guide.setAttribute("x1", "0"); guide.setAttribute("x2", "320");
    guide.setAttribute("y1", y); guide.setAttribute("y2", y);
    guide.setAttribute("stroke", "#f5f5f7");
    guide.setAttribute("stroke-dasharray", "3 4");
    svg.append(guide);
    host.append(svg);
    const note = document.createElement("p");
    note.className = "entry-note";
    note.textContent = "Gepunktet: gespeicherter Paper-Einstieg " + entry;
    host.append(note);
    return host;
  }
  host.append(svg);
  return host;
}

function renderAgents(state, bridge) {
  const host = document.getElementById("agents"); host.replaceChildren();
  const labels = {paused: "Pausiert", running: "Lokaler Schritt", starting: "Startet", stalled: "Heartbeat älter als 120s", idle: "Wartet", held: "Auditor aus", backoff: "Cooldown", error: "Quellenfehler"};
  for (const agent of state.agents) {
    const card = document.createElement("article"); card.className = "agent";
    const names = {scanner: "Scanner", auditor: "Auditor", analyst: "Analyst", executor: "Executor / Sizer", risk: "Risk / Exit-Trailing"};
    const title = document.createElement("h3"); title.textContent = names[agent.name] || agent.name;
    const badge = document.createElement("span"); badge.className = "badge " + agent.name; badge.textContent = "Prozessrolle";
    const status = document.createElement("p"); status.textContent = labels[agent.status] || agent.status;
    const detail = document.createElement("p"); detail.className = "empty";
    const queue = agent.queue == null ? "—" : String(agent.queue);
    const link = bridge && bridge.roles && bridge.roles[agent.name];
    const bot = link && link.connected ? "Bot-Ack " + stamp(link.acked_at) : "Bot nicht verbunden";
    detail.textContent = [agent.detail || "kein Detail", "Zähler " + queue, "Prozess-Heartbeat " + stamp(agent.last_beat), "kein Verbindungsbeleg", bot].join(" · ");
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
    card.append(title, badge, status, detail, button); host.append(card);
  }
}

function renderPositions(positions) {
  const host = document.getElementById("positions"); host.replaceChildren();
  if (!positions.length) { host.append(empty("Keine offene PAPER-Position. Ein Kandidat erscheint hier erst, wenn die bestehenden Regeln passen.")); return; }
  for (const position of positions) {
    const card = document.createElement("article"); card.className = "position";
    const title = document.createElement("h3"); title.textContent = "PAPER · " + (position.symbol || position.name || position.mint);
    card.append(title);
    const pnl = position.unrealized_pnl_usd;
    const pnlText = pnl == null ? "kein Feed-Preis" : money(pnl) + (position.unrealized_pct == null ? "" : " · " + position.unrealized_pct + " %");
    card.append(line("Mark-to-Market", pnlText, pnl == null ? "missing" : Number(pnl) < 0 ? "fail" : "pass"));
    card.append(line("Market Cap", position.market_cap == null ? "keine Dex-Angabe" : money(position.market_cap), ""));
    card.append(line("Einsatz", money(position.size_usd), ""));
    card.append(line("Einstieg", position.entry_price ?? "—", ""));
    card.append(line("Letzter Preis", position.last_price ?? "keine Daten", ""));
    card.append(line("Stop", position.stop_price ?? "—", ""));
    card.append(line("Trail", position.trail_active ? "aktiv" : "wartet", ""));
    host.append(card);
  }
}

function renderRecap(session) {
  const host = document.getElementById("recap"); host.replaceChildren();
  const best = session.best_trade ? session.best_trade.name + " · " + money(session.best_trade.pnl_usd) : "—";
  const multiple = session.best_multiple ? session.best_multiple.name + " · " + session.best_multiple.multiple + "×" : "—";
  const rate = session.win_rate_pct == null ? "keine Trades" : session.win_rate_pct + " %";
  for (const [label, value] of [["Gewinn", money(session.profit_usd)], ["Trades", String(session.trade_count)], ["Trefferquote", rate], ["Stops", String(session.stops_triggered)], ["Bester Trade", best], ["Bestes Vielfaches", multiple]]) {
    host.append(line(label, value, ""));
  }
  host.append(empty("Nur geschlossene PAPER-Fills. Kein Live-Ergebnis."));
}

function empty(text) { const p = document.createElement("p"); p.className = "empty"; p.textContent = text; return p; }

function renderChart(chart) {
  const host = document.getElementById("chart"); host.replaceChildren();
  const points = (chart.points || []).filter(point => Number.isFinite(Number(point.value)));
  if (!points.length) { host.append(empty("Noch kein gebuchter Paper-Snapshot in diesem Fenster.")); return; }
  if (points.length === 1) { host.append(empty(money(points[0].value) + " · ein Snapshot, keine Linie.")); return; }
  const values = points.map(point => Number(point.value));
  const low = Math.min(...values), high = Math.max(...values);
  const start = Date.parse(points[0].t), end = Date.parse(points.at(-1).t);
  const coordinates = points.map(point => [12 + (Date.parse(point.t) - start) / (end - start || 1) * 976, 150 - (Number(point.value) - low) / (high - low || 1) * 120]);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 1000 170");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Paper-Equity von " + money(values[0]) + " bis " + money(values.at(-1)));
  const path = document.createElementNS(svg.namespaceURI, "polyline");
  path.setAttribute("points", coordinates.map(point => point.join(",")).join(" "));
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#f5f5f7");
  path.setAttribute("stroke-width", "3");
  svg.append(path);
  host.append(svg);
}

function mergeActivity(page, older) {
  const firstLoad = activityRows.size === 0;
  for (const row of page.rows) activityRows.set(row.id, row);
  if (older || firstLoad) nextActivity = page.next_before;
  const host = document.getElementById("activity"); host.replaceChildren();
  if (!activityRows.size) host.append(empty("Noch keine Aktivitäten."));
  for (const row of [...activityRows.values()].sort((a,b) => b.id - a.id)) {
    const title = row.kind === "bought" || row.kind === "sold" || row.kind === "stopped" ? "PAPER · " + (row.title || row.kind) : (row.title || row.kind);
    host.append(line(title, [stamp(row.ts), row.subtitle, row.value].filter(Boolean).join(" · "), row.kind === "rejected" || row.kind === "stopped" ? "fail" : ""));
  }
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
  for (const id of ["summary", "positions", "agents", "activity", "chart", "checks", "report-body", "status", "banner", "scan-filters", "scan-sources", "scan-candidates", "coin-body"]) {
    const node = document.getElementById(id);
    if (node) node.replaceChildren();
  }
  document.getElementById("coin-detail").hidden = true;
  showLogin();
};
document.querySelectorAll("[data-tab]").forEach(button => button.onclick = () => {
  activeTab = button.dataset.tab;
  document.querySelectorAll(".tab-panel").forEach(panel => panel.hidden = panel.id !== activeTab);
  document.querySelectorAll("[data-tab]").forEach(tab => tab.setAttribute("aria-pressed", String(tab === button)));
  if (activeTab === "report") loadReport().catch(error => announce(error.message));
});
document.querySelectorAll("[data-range]").forEach(button => button.onclick = () => {
  chartRange = button.dataset.range;
  document.querySelectorAll("[data-range]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
  refresh();
});
document.getElementById("stop-all").onclick = () => document.getElementById("stop-dialog").showModal();
document.getElementById("stop-cancel").onclick = () => document.getElementById("stop-dialog").close();
document.getElementById("stop-confirm").onclick = async () => {
  const button = document.getElementById("stop-confirm");
  button.disabled = true;
  try {
    await api("/api/paper/stop-all", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({confirm: "STOP ALL"})});
    document.getElementById("stop-dialog").close();
    await refresh();
  } catch (error) { announce(error.message); }
  finally { button.disabled = false; }
};
document.getElementById("bridge-check").onclick = async () => {
  const button = document.getElementById("bridge-check");
  button.disabled = true;
  try { await api("/api/bridge/connection-check", {method: "POST"}); await refresh(); }
  catch (error) { announce(error.message); }
  finally { button.disabled = false; }
};
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
