"""Authenticated local API. The token is an environment value, never a query parameter."""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, Field

from desk.agents import AGENTS
from desk.config import SPECS, SettingsError, canonical_key
from desk.fields import aggregate_candles
from desk.links import coin_url
from desk.money import iso_utc
from desk.scrub import scrub

TOKEN_NAME = "APP_TOKEN"


class SettingsBody(BaseModel):
    changes: dict
    who: str = "api"
    confirm: str | None = None
    confirm_again: str | None = None


class ModeBody(BaseModel):
    mode: str
    confirm: str | None = None


class PushBody(BaseModel):
    endpoint: str
    p256dh: str = Field(min_length=16, max_length=256)
    auth: str = Field(min_length=8, max_length=128)


class ProbeBody(BaseModel):
    mint: str


def create_app(desk, app_token: str) -> FastAPI:
    if not app_token or len(app_token) < 8:
        raise RuntimeError("APP_TOKEN is missing")
    if app_token not in desk.secrets:
        desk.secrets.append(app_token)
    app = FastAPI(title="FOMO paper desk", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.desk = desk
    app.state.token = app_token
    static_dir = Path(__file__).resolve().parent / "static"

    @app.middleware("http")
    async def reject_query_token(request: Request, call_next):
        if request.url.path.startswith("/api"):
            for key in request.query_params:
                if key.lower() in {"token", "access_token", "app_token", "authorization"}:
                    return JSONResponse({"detail": "unauthorized"}, status_code=401)
        response = await call_next(request)
        media = response.media_type or ""
        if "application/json" not in media:
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        cleaned = scrub(body.decode("utf-8", "replace"), desk.secrets).encode()
        headers = {key: value for key, value in response.headers.items() if key.lower() != "content-length"}
        return JSONResponse(content=json.loads(cleaned), status_code=response.status_code, headers=headers)

    def authorize(request: Request) -> None:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="unauthorized")
        presented = header[7:]
        if not secrets.compare_digest(presented.encode(), app_token.encode()):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.exception_handler(SettingsError)
    async def settings_error(_request, exc: SettingsError):
        return JSONResponse(exc.payload(), status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "unauthorized"
        return JSONResponse({"detail": scrub(detail, desk.secrets)}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def any_error(_request, exc: Exception):
        return JSONResponse({"detail": scrub(str(exc), desk.secrets)}, status_code=500)

    @app.get("/")
    def monitor_page():
        return FileResponse(static_dir / "monitor.html")

    @app.get("/monitor.css")
    def monitor_css():
        return FileResponse(static_dir / "monitor.css")

    @app.get("/monitor.js")
    def monitor_js():
        return FileResponse(static_dir / "monitor.js")

    @app.get("/api/monitor")
    def monitor(request: Request):
        authorize(request)
        from desk.monitor import build_monitor

        return build_monitor(desk)

    @app.post("/api/probe")
    async def probe(body: ProbeBody, request: Request):
        authorize(request)
        from desk.links import SIGNUP_URL, coin_url

        if coin_url(body.mint) == SIGNUP_URL:
            raise HTTPException(status_code=422, detail="invalid mint")
        if desk.market_data == "off":
            raise HTTPException(status_code=409, detail="market reads are off")
        try:
            result = await desk.probe_historical(body.mint, budget_s=30)
        except Exception as exc:
            from desk.scrub import scrub as scrub_text

            raise HTTPException(status_code=502, detail=scrub_text(str(exc), desk.secrets)[:200]) from exc
        return result

    @app.get("/api/state")
    def state(request: Request):
        authorize(request)
        return desk.state()

    @app.get("/api/positions")
    def positions(request: Request):
        authorize(request)
        return {"positions": desk.db.open_positions()}

    @app.get("/api/report")
    def report(request: Request, date: str | None = None, format: str = "json"):
        authorize(request)
        from desk.report import build_report, report_csv

        if format not in {"json", "csv"}:
            raise HTTPException(status_code=422, detail="format must be json or csv")
        try:
            result = build_report(desk, date)
        except ValueError:
            raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD") from None
        if format == "csv":
            return Response(scrub(report_csv(result), desk.secrets), media_type="text/csv",
                            headers={"Content-Disposition": f'attachment; filename="paper-report-{result["date"]}.csv"'})
        return result

    @app.get("/api/chart")
    def chart(request: Request, range: str = "LIVE"):
        authorize(request)
        return desk.chart(range)

    @app.get("/api/activity")
    def activity(request: Request, before: int | None = None):
        authorize(request)
        rows = desk.db.activity_page(before, 30)
        next_before = rows[-1]["id"] if len(rows) == 30 else None
        return {"rows": rows, "next_before": next_before}

    @app.get("/api/coin/{mint}")
    def coin(mint: str, request: Request):
        authorize(request)
        row = desk.db.coin(mint)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown mint")
        prices = desk.db.prices_for(mint)
        metric = desk.db.metric(mint)
        position = desk.db.position_by_mint(mint) or desk.db.latest_position(mint)
        exit_lines = None
        if position is not None:
            exit_lines = {
                "stop_price": position.get("stop_price"),
                "stop_pct": position.get("stop_pct"),
                "trail_active": bool(position.get("trail_active")),
            }
        return {
            "mint": mint,
            "name": row.get("name"),
            "symbol": row.get("symbol"),
            "price": None if not prices else prices[-1]["price"],
            "candles": aggregate_candles(prices),
            "stats": None if metric is None else metric["payload"],
            "position": position,
            "exit_lines": exit_lines,
            "coin_url": coin_url(mint),
        }

    @app.get("/api/audit/{mint}")
    def audit(mint: str, request: Request):
        authorize(request)
        stored = {row["layer"]: row for row in desk.db.audits_for(mint)}
        layers = []
        for number in range(1, 9):
            row = stored.get(number)
            if row is None:
                layers.append({"n": number, "name": None, "pass": None, "value": "not checked", "source": None, "status": "unchecked"})
            else:
                layers.append(
                    {
                        "n": number,
                        "name": row["name"],
                        "pass": None if row["pass"] is None else bool(row["pass"]),
                        "value": row["value_text"],
                        "source": row["source"],
                        "status": row["status"],
                    }
                )
        return {"mint": mint, "layers": layers, "coin_url": coin_url(mint)}

    @app.get("/api/agents/{name}/decisions")
    def decisions(name: str, request: Request):
        authorize(request)
        if name not in AGENTS:
            raise HTTPException(status_code=404, detail="unknown agent")
        return {"agent": name, "decisions": desk.db.decisions(name, 20)}

    @app.get("/api/account")
    def account(request: Request):
        authorize(request)
        return desk.account()

    @app.get("/api/settings")
    def get_settings(request: Request):
        authorize(request)
        current = desk.settings()
        body = []
        for key, spec in SPECS.items():
            body.append(
                {
                    "key": key,
                    "value": _public(current[key]),
                    "type": spec.type,
                    "min": _public(spec.minimum),
                    "max": _public(spec.maximum),
                    "unit": spec.unit,
                }
            )
        return {"settings": body, "change_log": desk.db.change_log(20)}

    @app.post("/api/settings")
    def post_settings(body: SettingsBody, request: Request):
        authorize(request)
        return desk.apply_settings(body.changes, body.who, body.confirm, body.confirm_again)

    @app.post("/api/agents/{name}/pause")
    def pause(name: str, request: Request):
        authorize(request)
        try:
            return desk.pause(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown agent") from None

    @app.post("/api/agents/{name}/resume")
    def resume(name: str, request: Request):
        authorize(request)
        try:
            return desk.resume(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown agent") from None

    @app.post("/api/mode")
    def mode(body: ModeBody, request: Request):
        authorize(request)
        return desk.set_mode(body.mode, body.confirm)

    @app.post("/api/push")
    def push(body: PushBody, request: Request):
        authorize(request)
        if not body.endpoint.startswith("https://") or " " in body.endpoint:
            raise HTTPException(status_code=422, detail="endpoint must be https")
        desk.db.add_push_sub(body.endpoint, body.p256dh, body.auth, iso_utc(desk.now()))
        return {"stored": True, "sent": False}

    @app.get("/api/stream")
    def stream(request: Request):
        authorize(request)
        last = request.headers.get("last-event-id", "0")
        try:
            cursor = int(last)
        except ValueError:
            cursor = 0

        async def events() -> AsyncIterator[str]:
            rows = desk.db.events_after(cursor, 200)
            for row in rows:
                payload = scrub(json.dumps(row["payload"]), desk.secrets)
                yield f"id: {row['id']}\nevent: {row['type']}\ndata: {payload}\n\n"
            yield ": keepalive\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def _public(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    from decimal import Decimal

    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def canonical_setting(key: str) -> str:
    return canonical_key(key)
