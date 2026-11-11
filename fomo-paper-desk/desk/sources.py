"""Market-source ports. The default implementation performs no network I/O."""

from __future__ import annotations

from typing import Any


class OfflineError(RuntimeError):
    pass


class RateLimitError(RuntimeError):
    pass


class SourceFailure(RuntimeError):
    pass


class OfflineSources:
    async def dex_tokens(self, mints: list[str]) -> Any:
        raise OfflineError("Dex is disabled until live-data capture is explicitly enabled")

    async def rugcheck_report(self, mint: str) -> Any:
        raise OfflineError("RugCheck is disabled until live-data capture is explicitly enabled")

    async def rugcheck_graph(self, mint: str) -> Any:
        raise OfflineError("RugCheck graph is disabled until live-data capture is explicitly enabled")

    async def rpc(self, method: str, params: list) -> Any:
        raise OfflineError("RPC is disabled until live-data capture is explicitly enabled")


def rpc_key(method: str, params: list) -> tuple:
    first = params[0] if params else ""
    second = params[1] if len(params) > 1 else ""
    if isinstance(second, dict):
        mint = ""
        for item in second.get("filters") or []:
            memcmp = item.get("memcmp") if isinstance(item, dict) else None
            if isinstance(memcmp, dict):
                mint = str(memcmp.get("bytes") or "")
        encoding = str(second.get("encoding") or "")
        second = f"{encoding}:{mint}" if mint else (encoding or "dict")
    elif not isinstance(second, (str, int)):
        second = type(second).__name__
    return (method, str(first), str(second))


class MapSources:
    """In-memory adapter for fixtures and synthetic tests."""

    def __init__(self, dex=None, reports=None, graphs=None, rpc=None, dex_error=None, rpc_error=None):
        self.dex = dex or {}
        self.reports = reports or {}
        self.graphs = graphs or {}
        self.rpc_map = rpc or {}
        self.dex_error = dex_error
        self.rpc_error = rpc_error
        self.dex_calls: list[tuple[str, ...]] = []
        self.report_calls: list[str] = []
        self.graph_calls: list[str] = []
        self.rpc_calls: list[tuple] = []

    async def dex_tokens(self, mints: list[str]):
        self.dex_calls.append(tuple(mints))
        if self.dex_error is not None:
            error = self.dex_error
            self.dex_error = None
            raise error
        rows = []
        for mint in mints:
            payload = self.dex.get(mint)
            if payload is None:
                continue
            if isinstance(payload, list):
                rows.extend(payload)
            else:
                rows.append(payload)
        return rows

    async def rugcheck_report(self, mint: str):
        self.report_calls.append(mint)
        if mint not in self.reports:
            raise SourceFailure(f"report missing for {mint}")
        return self.reports[mint]

    async def rugcheck_graph(self, mint: str):
        self.graph_calls.append(mint)
        if mint not in self.graphs:
            raise SourceFailure(f"graph missing for {mint}")
        return self.graphs[mint]

    async def rpc(self, method: str, params: list):
        self.rpc_calls.append((method, tuple(str(p) if not isinstance(p, dict) else "obj" for p in params)))
        if self.rpc_error is not None:
            raise self.rpc_error
        key = rpc_key(method, params)
        if key not in self.rpc_map:
            raise SourceFailure(f"rpc {method} missing")
        value = self.rpc_map[key]
        if isinstance(value, Exception):
            raise value
        return value
