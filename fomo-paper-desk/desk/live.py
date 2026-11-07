"""Read-only public market adapters. Trading stays PAPER. No trade-priced streams."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from desk.fields import (
    DEX_URL_PREFIX,
    PUMP_WS_URL,
    RUGCHECK_GRAPH,
    RUGCHECK_REPORT,
    SUBSCRIBE_MIGRATION,
)
from desk.money import iso_utc
from desk.scrub import scrub
from desk.sources import RateLimitError, SourceFailure


class LiveDataDisabled(RuntimeError):
    pass


class HttpMarketSources:
    """One shared HTTP client. Callers inject it. Tests use MapSources instead."""

    def __init__(self, rpc_url: str, client: httpx.AsyncClient | None = None, timeout: float = 8.0):
        self.rpc_url = rpc_url
        self.timeout = timeout
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self.desk = None
        self.source_label = "live"

    def attach(self, desk) -> None:
        self.desk = desk

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def _remember(self, source: str, mint: str | None, url: str, status: int, body: str, error: str | None) -> None:
        if self.desk is None:
            return
        secrets = getattr(self.desk, "secrets", [])
        self.desk.db.add_raw(
            ts=iso_utc(self.desk.now()),
            source=source,
            mint=mint,
            url=url,
            status=status,
            error=None if error is None else scrub(error, secrets)[:300],
            body=scrub(body or "", secrets),
        )

    async def _get_json(self, source: str, mint: str | None, url: str) -> Any:
        if self.desk is not None and not self.desk.rate.acquire():
            raise RateLimitError("global dex budget")
        try:
            response = await self.client.get(url, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            self._remember(source, mint, url, 0, "", "timeout")
            raise SourceFailure(f"{source} timeout") from exc
        except httpx.HTTPError as exc:
            self._remember(source, mint, url, 0, "", type(exc).__name__)
            raise SourceFailure(f"{source} {type(exc).__name__}") from exc
        self._remember(source, mint, url, response.status_code, response.text, None)
        if response.status_code == 429:
            if self.desk is not None:
                self.desk.rate.note_429()
            raise RateLimitError(f"{source} 429")
        if response.status_code >= 400:
            raise SourceFailure(f"{source} http {response.status_code}")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise SourceFailure(f"{source} not json") from exc

    async def dex_tokens(self, mints: list[str]) -> Any:
        url = DEX_URL_PREFIX + ",".join(mints)
        return await self._get_json("dex", mints[0] if len(mints) == 1 else None, url)

    async def rugcheck_report(self, mint: str) -> Any:
        return await self._get_json("rugcheck_report", mint, RUGCHECK_REPORT.format(mint=mint))

    async def rugcheck_graph(self, mint: str) -> Any:
        return await self._get_json("rugcheck_graph", mint, RUGCHECK_GRAPH.format(mint=mint))

    async def rpc(self, method: str, params: list) -> Any:
        if self.desk is not None and not self.desk.rate.acquire():
            raise RateLimitError("rpc budget")
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        mint = _mint_from_rpc(method, params)
        try:
            response = await self.client.post(self.rpc_url, json=payload, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            self._remember("rpc", mint, self.rpc_url, 0, "", f"{method} timeout")
            raise SourceFailure(f"rpc {method} timeout") from exc
        except httpx.HTTPError as exc:
            self._remember("rpc", mint, self.rpc_url, 0, "", type(exc).__name__)
            raise SourceFailure(f"rpc {type(exc).__name__}") from exc
        self._remember("rpc", mint, self.rpc_url, response.status_code, response.text, None if response.status_code < 400 else method)
        if response.status_code == 429:
            if self.desk is not None:
                self.desk.rate.note_429()
            raise RateLimitError("rpc 429")
        if response.status_code >= 400:
            raise SourceFailure(f"rpc http {response.status_code}")
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise SourceFailure("rpc not json") from exc
        if isinstance(body, dict) and body.get("error"):
            message = body["error"].get("message") if isinstance(body["error"], dict) else "rpc error"
            raise SourceFailure(f"rpc {method} {str(message)[:160]}")
        return body


def _mint_from_rpc(method: str, params: list) -> str | None:
    if method == "getAccountInfo" and params and isinstance(params[0], str):
        return params[0]
    if method == "getProgramAccounts" and len(params) > 1 and isinstance(params[1], dict):
        for item in params[1].get("filters") or []:
            memcmp = item.get("memcmp") if isinstance(item, dict) else None
            if isinstance(memcmp, dict) and isinstance(memcmp.get("bytes"), str):
                return memcmp["bytes"]
    return None


def subscription_payload() -> dict:
    return dict(SUBSCRIBE_MIGRATION)


async def pump_migration_loop(desk, stop: asyncio.Event) -> None:
    """Exactly one migration subscription. No trade or account streams."""
    import websockets

    if getattr(desk, "pump_started", False):
        return
    desk.pump_started = True
    while not stop.is_set():
        try:
            async with websockets.connect(PUMP_WS_URL, open_timeout=8, ping_interval=20) as socket:
                await socket.send(json.dumps(subscription_payload()))
                desk.feed["error"] = None
                desk.feed["detail"] = "Migrationen abonniert, eine Verbindung"
                desk.note_feed(opened=True)
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=20)
                    except TimeoutError:
                        info = desk.note_feed()
                        if info["reconnect"]:
                            desk.feed["detail"] = "Verbindung still, neuer Versuch"
                            break
                        continue
                    desk.note_feed(message_seen=True)
                    desk.db.add_raw(
                        ts=iso_utc(desk.now()),
                        source="pumpportal",
                        mint=None,
                        url=PUMP_WS_URL,
                        status=200,
                        error=None,
                        body=scrub(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"), desk.secrets),
                    )
                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(message, dict) and message.get("txType") == "migrate" and message.get("mint"):
                        desk.ingest_migration(message)
                        desk.db.add_event(
                            desk.now(),
                            "scan",
                            {"agent": "scanner", "mint": message["mint"], "verdict": "waiting", "ts": iso_utc(desk.now())},
                        )
        except Exception as exc:
            desk.feed["error"] = type(exc).__name__
            desk.feed["detail"] = "PumpPortal nicht erreichbar"
            info = desk.note_feed(closed=True)
            delay = info.get("backoff_s") or 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                continue
