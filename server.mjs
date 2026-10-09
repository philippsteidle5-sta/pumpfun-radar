import http from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const directory = path.dirname(fileURLToPath(import.meta.url));
const host = process.env.HOST || "127.0.0.1";
const port = Number(process.env.PORT || 3222);
const rpcUrl = process.env.SOLANA_RPC_URL || "https://api.mainnet-beta.solana.com";
const streamUrl = new URL("wss://pumpportal.fun/api/data");
if (process.env.PUMPPORTAL_API_KEY) streamUrl.searchParams.set("api-key", process.env.PUMPPORTAL_API_KEY);

const coins = [];
const seen = new Set();
const listeners = new Set();
let streamState = "connecting";
let streamError = "";
let retryDelay = 1_000;
let socket;
let shuttingDown = false;

const mintPattern = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const finiteNonnegative = (value) => {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
};

function normalizeCreation(payload) {
  if (!payload || typeof payload !== "object" || !mintPattern.test(String(payload.mint || ""))) return null;
  return {
    mint: String(payload.mint),
    name: String(payload.name || "Unnamed coin").slice(0, 100),
    symbol: String(payload.symbol || "?").slice(0, 24),
    creator: mintPattern.test(String(payload.traderPublicKey || "")) ? String(payload.traderPublicKey) : null,
    initialBuy: finiteNonnegative(payload.initialBuy),
    solAmount: finiteNonnegative(payload.solAmount),
    marketCapSol: finiteNonnegative(payload.marketCapSol),
    receivedAt: new Date().toISOString(),
    signature: typeof payload.signature === "string" ? payload.signature.slice(0, 100) : null,
  };
}

function publish(kind, data) {
  const line = `event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const response of listeners) {
    try { response.write(line); } catch { listeners.delete(response); }
  }
}

function setStreamState(state, error = "") {
  streamState = state;
  streamError = error;
  publish("status", { state, error });
}

function connectStream() {
  setStreamState("connecting");
  try {
    socket = new WebSocket(streamUrl.toString());
  } catch (error) {
    setStreamState("offline", String(error?.message || error));
    scheduleReconnect();
    return;
  }

  socket.addEventListener("open", () => {
    retryDelay = 1_000;
    setStreamState("connected");
    socket.send(JSON.stringify({ method: "subscribeNewToken" }));
  });
  socket.addEventListener("message", (event) => {
    try {
      const payload = JSON.parse(String(event.data));
      if (typeof payload?.error === "string") {
        setStreamState("offline", payload.error.slice(0, 180));
        socket.close();
        return;
      }
      const coin = normalizeCreation(payload);
      if (!coin || seen.has(coin.mint)) return;
      if (streamState !== "live") setStreamState("live");
      seen.add(coin.mint);
      coins.unshift(coin);
      if (coins.length > 250) {
        const removed = coins.pop();
        seen.delete(removed.mint);
      }
      publish("coin", coin);
    } catch {
      // Provider status messages and malformed rows are not coin events.
    }
  });
  socket.addEventListener("error", () => setStreamState("offline", "Datenstream nicht erreichbar"));
  socket.addEventListener("close", () => {
    if (shuttingDown) return;
    setStreamState("offline", "Verbindung unterbrochen");
    scheduleReconnect();
  });
}

function scheduleReconnect() {
  if (shuttingDown) return;
  setTimeout(connectStream, retryDelay);
  retryDelay = Math.min(retryDelay * 2, 30_000);
}

function json(response, status, payload) {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  response.end(JSON.stringify(payload));
}

async function readBalance(address) {
  const response = await fetch(rpcUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "getBalance", params: [address, { commitment: "confirmed" }] }),
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) throw new Error(`RPC HTTP ${response.status}`);
  const body = await response.json();
  if (body.error || !Number.isFinite(body.result?.value)) throw new Error(body.error?.message || "Ungültige RPC-Antwort");
  return body.result.value;
}

const staticFiles = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/particle.js", ["particle.js", "text/javascript; charset=utf-8"]],
  ["/styles.css", ["styles.css", "text/css; charset=utf-8"]],
  ["/particle.css", ["particle.css", "text/css; charset=utf-8"]],
]);

const server = http.createServer(async (request, response) => {
  if (request.method !== "GET") return json(response, 405, { error: "Nur GET erlaubt" });
  const pathname = new URL(request.url || "/", `http://${host}:${port}`).pathname;
  if (pathname === "/api/status") return json(response, 200, { streamState, streamError, coins: coins.length });
  if (pathname === "/api/coins") return json(response, 200, { coins });
  if (pathname === "/api/events") {
    response.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    });
    response.write(`event: status\ndata: ${JSON.stringify({ state: streamState, error: streamError })}\n\n`);
    listeners.add(response);
    const keepAlive = setInterval(() => response.write(": heartbeat\n\n"), 20_000);
    request.on("close", () => { clearInterval(keepAlive); listeners.delete(response); });
    return;
  }
  if (pathname.startsWith("/api/wallet/")) {
    const address = pathname.slice("/api/wallet/".length);
    if (!mintPattern.test(address)) return json(response, 400, { error: "Ungültige Solana-Adresse" });
    try { return json(response, 200, { address, lamports: await readBalance(address) }); }
    catch (error) { return json(response, 502, { error: String(error?.message || error) }); }
  }
  const staticFile = staticFiles.get(pathname);
  if (!staticFile) return json(response, 404, { error: "Nicht gefunden" });
  try {
    const content = await readFile(path.join(directory, "public", staticFile[0]));
    response.writeHead(200, { "Content-Type": staticFile[1], "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
    response.end(content);
  } catch { json(response, 500, { error: "Datei nicht lesbar" }); }
});

server.listen(port, host, () => console.log(`Pump Radar: http://${host}:${port}`));
connectStream();

process.on("SIGINT", () => { shuttingDown = true; socket?.close(); server.close(); });
