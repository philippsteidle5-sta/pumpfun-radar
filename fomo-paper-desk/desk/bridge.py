"""Event bridge for separate Grok bots. One webhook names a job id. No order is sent."""

from __future__ import annotations

import json
import os
import secrets
import uuid
from typing import Any

import httpx

from desk.agents import AGENTS

KINDS = ("connection_check", "analysis", "order_review")
ROLE_TOKEN_ENV = {
    "scanner": "GROK_ROLE_TOKEN_SCANNER",
    "auditor": "GROK_ROLE_TOKEN_AUDITOR",
    "analyst": "GROK_ROLE_TOKEN_ANALYST",
    "executor": "GROK_ROLE_TOKEN_EXECUTOR",
    "risk": "GROK_ROLE_TOKEN_RISK",
}
WEBHOOK_URL_ENV = "GROK_BOT_WEBHOOK_URL"
WEBHOOK_KEY_ENV = "GROK_BOT_WEBHOOK_KEY"


def fomoscan_lead() -> dict[str, Any]:
    """Identity data only. No request is made and connected stays false without a real read."""
    return {
        "name": "fomoscan",
        "configured": bool(os.environ.get("FOMOSCAN_KEY", "").strip()),
        "connected": False,
        "orders": "disabled",
        "custody": False,
        "purpose": "handle-to-wallet identity, follows, and holders",
        "key_env": "FOMOSCAN_KEY",
        "reads": [
            "GET https://api.fomoscan.sh/v2/user/handle/{handle}",
            "GET https://api.fomoscan.sh/v2/user/wallet/{address}",
            "GET https://api.fomoscan.sh/v2/user/id/{id}",
        ],
        "auth": "Authorization: Bearer <FOMOSCAN_KEY>",
        "note": "No key is stored in the repo. FomoScan does not take custody or execute trades. This desk does not call it.",
    }


def webhook_configured() -> bool:
    return bool(_webhook_url()) and bool(_webhook_key())


def _webhook_url() -> str:
    return os.environ.get(WEBHOOK_URL_ENV, "").strip()


def _webhook_key() -> str:
    return os.environ.get(WEBHOOK_KEY_ENV, "").strip()


def _same(left: str, right: str) -> bool:
    if not left or not right or len(left) != len(right):
        return False
    return secrets.compare_digest(left.encode(), right.encode())


def role_authorized(presented: str, role: str, app_token: str) -> bool:
    if _same(presented, app_token):
        return True
    expected = os.environ.get(ROLE_TOKEN_ENV.get(role, ""), "").strip()
    return _same(presented, expected)


def contract() -> dict[str, Any]:
    return {
        "execution_armed": False,
        "orders": "disabled",
        "trigger": {
            "method": "POST",
            "url_env": WEBHOOK_URL_ENV,
            "key_env": WEBHOOK_KEY_ENV,
            "body": {"job_id": "<id>"},
            "header": "Authorization: Bearer <GROK_BOT_WEBHOOK_KEY>",
            "redirects": "not followed",
        },
        "fetch": {
            "method": "GET",
            "path": "/api/bridge/jobs/{job_id}",
            "header": "Authorization: Bearer <APP_TOKEN or the matching GROK_ROLE_TOKEN_*>",
        },
        "result": {
            "method": "POST",
            "path": "/api/bridge/jobs/{job_id}/result",
            "body": {"ok": True, "summary": "<text>", "correlation_id": "<job_id>"},
        },
        "kinds": list(KINDS),
        "role_token_env": ROLE_TOKEN_ENV,
        "schedule": "none",
        "connected_when": "a connection_check result is stored with ok true and the same correlation id",
        "fomoscan": fomoscan_lead(),
    }


def _public_job(row: dict, *, include_payload: bool = False) -> dict:
    payload = {}
    if include_payload and row.get("payload_json"):
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            payload = {}
    body = {
        "job_id": row["id"],
        "role": row["role"],
        "kind": row["kind"],
        "status": row["status"],
        "subject": row.get("subject"),
        "created_at": row.get("created_at"),
        "dispatched_at": row.get("dispatched_at"),
        "dispatch_status": row.get("dispatch_status"),
        "dispatch_error": row.get("dispatch_error"),
        "claimed_at": row.get("claimed_at"),
        "acked_at": row.get("acked_at"),
        "execution_armed": False,
        "orders": "disabled",
    }
    if include_payload:
        body["payload"] = payload
        body["correlation_id"] = row["id"]
    return body


def bridge_view(desk) -> dict:
    rows = desk.db.bridge_jobs(30)
    by_role = {}
    for role in AGENTS:
        match = next((row for row in rows if row["role"] == role and row["kind"] == "connection_check"), None)
        connected = bool(match and match["status"] == "acked" and match.get("acked_at"))
        by_role[role] = {
            "connected": connected,
            "status": None if match is None else match["status"],
            "job_id": None if match is None else match["id"],
            "acked_at": None if match is None else match.get("acked_at"),
            "dispatch_error": None if match is None else match.get("dispatch_error"),
            "heartbeat_proves_connection": False,
        }
    return {
        "webhook_configured": webhook_configured(),
        "execution_armed": False,
        "orders": "disabled",
        "live_balance": None,
        "fomoscan": fomoscan_lead(),
        "roles": by_role,
        "jobs": [_public_job(row) for row in rows],
        "contract": contract(),
    }


def _clean_payload(kind: str, payload: dict | None, subject: str | None) -> dict:
    raw = payload or {}
    if kind == "connection_check":
        return {"purpose": "round-trip", "execution_armed": False, "orders": "disabled"}
    if kind == "analysis":
        mint = str(raw.get("mint") or "")[:64]
        question = str(raw.get("question") or subject or "")[:240]
        return {"mint": mint, "question": question, "execution_armed": False, "orders": "disabled"}
    mint = str(raw.get("mint") or "")[:64]
    side = raw.get("side") if raw.get("side") in {"buy", "sell"} else None
    amount = str(raw.get("amount") or "")[:64]
    note = str(raw.get("note") or "")[:240]
    return {
        "mint": mint,
        "side": side,
        "amount": amount,
        "note": note,
        "review_only": True,
        "submit": False,
        "execution_armed": False,
        "orders": "disabled",
    }


async def enqueue(desk, *, role: str, kind: str, idempotency_key: str, subject: str | None, payload: dict | None) -> dict:
    if role not in AGENTS:
        raise ValueError("unknown role")
    if kind not in KINDS:
        raise ValueError("unknown kind")
    key = idempotency_key.strip()
    if not key or len(key) > 120:
        raise ValueError("idempotency_key required")
    stored = _clean_payload(kind, payload, subject)
    row = desk.db.ensure_bridge_job(
        id=str(uuid.uuid4()),
        role=role,
        kind=kind,
        idempotency_key=key,
        subject=None if subject is None else str(subject)[:160],
        payload_json=json.dumps(stored, separators=(",", ":")),
        created_at=desk.now().isoformat(),
    )
    if row.get("dispatched_at") is None and row.get("acked_at") is None:
        row = await _dispatch(desk, row)
    return _public_job(desk.db.bridge_job(row["id"]) or row, include_payload=False)


async def open_connection_checks(desk) -> dict:
    if not webhook_configured():
        return bridge_view(desk)
    for role in AGENTS:
        await enqueue(
            desk,
            role=role,
            kind="connection_check",
            idempotency_key=f"connection:{role}",
            subject="connection check",
            payload=None,
        )
    return bridge_view(desk)


def claim(desk, job_id: str) -> dict | None:
    row = desk.db.claim_bridge_job(job_id, desk.now().isoformat())
    if row is None:
        return None
    return _public_job(row, include_payload=True)


def acknowledge(desk, job_id: str, *, ok: bool, summary: str, correlation_id: str) -> dict:
    if correlation_id != job_id:
        raise ValueError("correlation_id must equal job_id")
    text = summary.strip()
    if not text or len(text) > 500:
        raise ValueError("summary must be 1 to 500 characters")
    result = json.dumps(
        {"ok": bool(ok), "summary": text, "correlation_id": job_id, "execution_armed": False, "orders": "disabled"},
        separators=(",", ":"),
    )
    row = desk.db.ack_bridge_job(job_id, ok=bool(ok), result_json=result, now=desk.now().isoformat())
    if row is None:
        raise KeyError(job_id)
    return _public_job(row, include_payload=False)


async def _dispatch(desk, row: dict) -> dict:
    url = _webhook_url()
    key = _webhook_key()
    now = desk.now().isoformat()
    if not url or not key:
        return row
    if not url.startswith("https://") or " " in url:
        updated = desk.db.mark_bridge_dispatch(row["id"], http_status=None, error="webhook_url_rejected", now=now)
        return updated or row
    status = None
    error = None
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.post(
                url,
                json={"job_id": row["id"]},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
        status = response.status_code
        if status < 200 or status >= 300:
            error = f"http {status}"
    except httpx.TimeoutException:
        error = "timeout"
    except httpx.HTTPError:
        error = "transport"
    updated = desk.db.mark_bridge_dispatch(row["id"], http_status=status, error=error, now=now)
    return updated or row
