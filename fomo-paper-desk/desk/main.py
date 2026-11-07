"""PAPER entrypoint. Market reads are opt-in. Trading mode stays PAPER."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from desk import CHUNK_ALLOWS_LIVE
from desk.api import create_app
from desk.clock import SystemClock
from desk.config import assert_known_defaults
from desk.db import Database
from desk.fees import effective_discount
from desk.live import HttpMarketSources, pump_migration_loop
from desk.logging_setup import configure_logging
from desk.money import iso_utc
from desk.monitor import build_monitor
from desk.runtime import Desk
from desk.scrub import scrub


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FOMO paper desk")
    parser.add_argument("--check", action="store_true", help="validate defaults and exit")
    parser.add_argument("--serve", action="store_true", help="PAPER monitor, read-only market data")
    parser.add_argument("--offline", action="store_true", help="serve stored data without public market reads")
    parser.add_argument("--once", metavar="MINT", help="one bounded historical read, then exit")
    parser.add_argument("--db", default=os.environ.get("DESK_DB", "var/desk.sqlite"))
    args = parser.parse_args(argv)
    if args.serve and args.once:
        print("Use either --serve or --once.", file=sys.stderr)
        return 2
    if args.serve:
        return _serve(args.db, offline=args.offline)
    if args.once:
        return asyncio.run(_once(args.db, args.once))
    assert_known_defaults()
    print("mode PAPER")
    print("effective_fee_discount 0")
    print(f"nominal_fee_discount_verified_applied {effective_discount('0.10', False)}")
    print(f"live_allowed {CHUNK_ALLOWS_LIVE}")
    print("network off")
    print("notifier dry_run")
    return 0


def _open_db(path: str) -> Database:
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    return Database(str(database))


def _force_paper(db: Database, now: datetime) -> None:
    if db.get_setting("desk.mode") == "LIVE":
        db.update_settings({"desk.mode": "PAPER"}, "serve-guard", now)


def _secrets() -> list[str]:
    found = []
    for key in (
        "APP_TOKEN",
        "TG_TOKEN",
        "TELEGRAM_TOKEN",
        "TYPESAFE_API_KEY",
        "GROK_BOT_WEBHOOK_KEY",
        "GROK_ROLE_TOKEN_SCANNER",
        "GROK_ROLE_TOKEN_AUDITOR",
        "GROK_ROLE_TOKEN_ANALYST",
        "GROK_ROLE_TOKEN_EXECUTOR",
        "GROK_ROLE_TOKEN_RISK",
        "FOMOSCAN_KEY",
    ):
        value = os.environ.get(key)
        if value:
            found.append(value)
    return found


def _build(path: str, *, live: bool) -> tuple[Desk, HttpMarketSources | None]:
    now = datetime.now(timezone.utc)
    db = _open_db(path)
    _force_paper(db, now)
    secrets = _secrets()
    log_dir = Path(os.environ.get("DESK_LOG_DIR", "logs"))
    sources = None
    if live:
        rpc_url = db.get_setting("desk.rpc_url")
        sources = HttpMarketSources(str(rpc_url), timeout=8)
    desk = Desk(db, clock=SystemClock(), sources=sources, secrets=secrets, log_dir=log_dir)
    if live and sources is not None:
        sources.attach(desk)
        desk.source_label = "live"
        desk.market_data = "read"
    return desk, sources


def _serve(path: str, *, offline: bool = False) -> int:
    token = os.environ.get("APP_TOKEN", "")
    if len(token) < 8:
        print("APP_TOKEN missing. Refusing to serve.", file=sys.stderr)
        return 2
    host = os.environ.get("DESK_HOST", "127.0.0.1")
    port = int(os.environ.get("DESK_PORT", "43123"))
    desk, sources = _build(path, live=not offline)
    desk.open_night(source="offline" if offline else "market-read")
    desk.finite_stream = False
    app = create_app(desk, token)
    print(f"PAPER monitor http://{host}:{port}/ trading PAPER market-read {'off' if offline else 'on'} telegram off", flush=True)
    try:
        asyncio.run(_run_server(app, desk, sources, host, port))
    except KeyboardInterrupt:
        return 0
    return 0


async def _run_server(app, desk, sources, host: str, port: int) -> None:
    import uvicorn

    stop = asyncio.Event()
    config = uvicorn.Config(app, host=host, port=port, access_log=False, log_level="info")
    server = uvicorn.Server(config)
    pump = asyncio.create_task(pump_migration_loop(desk, stop)) if sources is not None else None
    worker = asyncio.create_task(_analysis_loop(desk, stop))
    try:
        await server.serve()
    finally:
        stop.set()
        if pump is not None:
            pump.cancel()
        worker.cancel()
        await asyncio.gather(*([pump] if pump is not None else []), worker, return_exceptions=True)
        if sources is not None:
            await sources.aclose()
        desk.db.close()


async def _analysis_loop(desk, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await desk.step()
        except Exception as exc:
            desk.feed["error"] = type(exc).__name__
            desk.feed["detail"] = "Auswertungsschritt fehlgeschlagen"
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            continue


async def _once(path: str, mint: str) -> int:
    from desk.links import SIGNUP_URL, coin_url

    if coin_url(mint) == SIGNUP_URL:
        print("invalid mint", file=sys.stderr)
        return 2
    desk, sources = _build(path, live=True)
    try:
        await asyncio.wait_for(desk.probe_historical(mint, budget_s=30), timeout=35)
    except TimeoutError:
        desk.db.add_check(
            mint=mint,
            ts=iso_utc(desk.now()),
            kind="on_demand",
            context="historical_on_demand",
            source_label=desk.source_label,
            sized=0,
            on_desk=0,
            note="nicht dimensioniert, nicht auf dem Desk",
            error="keine Antwort innerhalb 30s",
        )
    view = build_monitor(desk)
    match = next((item for item in view["checks"] if item["mint"] == mint), None)
    text = scrub(json.dumps(match or view, ensure_ascii=False, indent=2), desk.secrets)
    print(text)
    if sources is not None:
        await sources.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
