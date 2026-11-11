"""SQLite WAL store. UTC text, finite decimals as strings, one writer lock."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from desk.config import canonical_key, decode, defaults, encode
from desk.money import D, cents, iso_utc

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS schema_migrations (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS coins (
    mint TEXT PRIMARY KEY,
    name TEXT,
    symbol TEXT,
    pool TEXT,
    pool_label TEXT,
    migrated_at TEXT,
    migration_signature TEXT,
    creator TEXT,
    creator_balance_at_migration TEXT,
    creator_balance_observed TEXT,
    creator_observed_at TEXT,
    creator_observed_slot INTEGER,
    creator_balance_provenance TEXT,
    holders INTEGER,
    scanner_liquidity TEXT,
    scanner_price TEXT,
    analyst_price TEXT,
    analyst_score TEXT,
    migration_provenance TEXT,
    testdata INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts TEXT NOT NULL,
    liquidity_usd TEXT,
    top10_pct TEXT,
    h1_change_pct TEXT,
    age_min TEXT,
    verdict TEXT NOT NULL,
    reason TEXT,
    night_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audits (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts TEXT NOT NULL,
    layer INTEGER,
    name TEXT,
    value_text TEXT,
    pass INTEGER,
    source TEXT,
    status TEXT,
    night_id TEXT NOT NULL,
    latency_ms INTEGER,
    provenance TEXT
);

CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts TEXT NOT NULL,
    alive_6h TEXT,
    organic TEXT,
    flush TEXT,
    chase TEXT,
    score TEXT,
    pass INTEGER,
    reason TEXT,
    input_tokens INTEGER,
    model TEXT,
    night_id TEXT NOT NULL,
    provenance TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts TEXT NOT NULL,
    price TEXT,
    size_usd TEXT,
    stack_pct TEXT,
    link TEXT,
    mode TEXT,
    status TEXT,
    reason TEXT,
    night_id TEXT NOT NULL,
    name TEXT,
    score TEXT,
    UNIQUE (mint, night_id)
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    entry_price TEXT NOT NULL,
    size_usd TEXT NOT NULL,
    peak_price TEXT NOT NULL,
    stop_price TEXT NOT NULL,
    stop_pct TEXT NOT NULL,
    initial_stop_pct TEXT NOT NULL,
    trail_active INTEGER NOT NULL DEFAULT 0,
    trail_activated_at TEXT,
    closed_at TEXT,
    exit_price TEXT,
    exit_reason TEXT,
    pnl_usd TEXT,
    fees_usd TEXT,
    entry_fee_usd TEXT,
    exit_fee_usd TEXT,
    gas_usd TEXT,
    gas_model TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    night_id TEXT NOT NULL,
    name TEXT,
    symbol TEXT,
    link TEXT,
    last_price TEXT,
    last_price_at TEXT,
    no_price_polls INTEGER NOT NULL DEFAULT 0,
    missing_price_alerted INTEGER NOT NULL DEFAULT 0,
    liquidity_usd TEXT,
    exit_price_provenance TEXT,
    pnl_booked INTEGER NOT NULL DEFAULT 0,
    reminder_sent INTEGER NOT NULL DEFAULT 0,
    exiting_since TEXT,
    gross_pct TEXT,
    net_pct TEXT,
    trigger_price TEXT,
    testdata INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS one_open_position
    ON positions (mint) WHERE status IN ('open', 'exiting', 'close_blocked');

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('bought', 'sold', 'stopped', 'rejected')),
    mint TEXT,
    agent TEXT,
    title TEXT,
    subtitle TEXT,
    value TEXT,
    link TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts TEXT NOT NULL,
    price TEXT NOT NULL,
    volume_m5 TEXT,
    volume_m5_delta TEXT,
    liquidity_usd TEXT,
    writer TEXT NOT NULL,
    UNIQUE (mint, ts)
);

CREATE TABLE IF NOT EXISTS balance (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    value TEXT NOT NULL,
    cash TEXT,
    reserved TEXT,
    label TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    key TEXT NOT NULL,
    old TEXT,
    new TEXT,
    who TEXT
);

CREATE TABLE IF NOT EXISTS skips (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    until TEXT NOT NULL,
    night_id TEXT NOT NULL,
    reason TEXT,
    UNIQUE (mint, night_id)
);

CREATE TABLE IF NOT EXISTS heartbeats (
    agent TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    status TEXT,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS bridge_jobs (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    subject TEXT,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    dispatched_at TEXT,
    dispatch_status INTEGER,
    dispatch_error TEXT,
    claimed_at TEXT,
    acked_at TEXT
);

CREATE TABLE IF NOT EXISTS push_subs (
    endpoint TEXT PRIMARY KEY,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    channel TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL,
    mint TEXT,
    ts TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    night_id TEXT
);

CREATE TABLE IF NOT EXISTS night_runs (
    night_id TEXT PRIMARY KEY,
    started_at TEXT,
    ended_at TEXT,
    synthetic INTEGER NOT NULL DEFAULT 1,
    qualifying INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    warning TEXT,
    blocked INTEGER NOT NULL DEFAULT 0,
    start_equity TEXT
);

CREATE TABLE IF NOT EXISTS processed (
    kind TEXT NOT NULL,
    mint TEXT NOT NULL,
    night_id TEXT NOT NULL,
    PRIMARY KEY (kind, mint, night_id)
);

CREATE TABLE IF NOT EXISTS dev_snapshots (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts TEXT NOT NULL,
    slot INTEGER,
    creator TEXT,
    balance_raw TEXT,
    provenance TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS layer8_incidents (
    night_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS callback_receipts (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    action TEXT,
    mint TEXT
);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash TEXT NOT NULL,
    reserved TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY,
    mint TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    context TEXT NOT NULL,
    source_label TEXT NOT NULL,
    sized INTEGER NOT NULL DEFAULT 0,
    on_desk INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS raw_captures (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    mint TEXT,
    url TEXT,
    status INTEGER,
    error TEXT,
    body TEXT
);

CREATE TABLE IF NOT EXISTS runners (
    night_id TEXT NOT NULL,
    mint TEXT NOT NULL,
    name TEXT,
    h24 TEXT,
    PRIMARY KEY (night_id, mint)
);

CREATE TABLE IF NOT EXISTS metrics (
    mint TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_claims (
    mint TEXT PRIMARY KEY,
    writer TEXT NOT NULL
);
"""


def _json_default(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(type(value).__name__)


# close_blocked still reserves cash, so it stays an exposure: one slot, Risk locked.
EXPOSURE_STATUSES = ("open", "exiting", "close_blocked")


def _exposure_sql() -> str:
    return ", ".join(f"'{status}'" for status in EXPOSURE_STATUSES)


def dumps(payload: Any) -> str:
    return json.dumps(payload, default=_json_default, separators=(",", ":"), allow_nan=False)


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self._seed()

    def close(self) -> None:
        with self._lock:
            self.con.close()

    def _seed(self) -> None:
        now = iso_utc(datetime.now(timezone.utc))
        with self._lock:
            self.con.execute("BEGIN IMMEDIATE")
            for key, value in defaults().items():
                self.con.execute(
                    "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, encode(value), now),
                )
            self.con.execute("COMMIT")

    def journal_mode(self) -> str:
        with self._lock:
            return self.con.execute("PRAGMA journal_mode").fetchone()[0]

    def get_setting(self, key: str):
        canon = canonical_key(key)
        with self._lock:
            row = self.con.execute("SELECT value FROM settings WHERE key = ?", (canon,)).fetchone()
        if row is None:
            return defaults()[canon]
        return decode(canon, row["value"])

    def settings_map(self) -> dict:
        with self._lock:
            rows = self.con.execute("SELECT key, value FROM settings").fetchall()
        found = {row["key"]: decode(row["key"], row["value"]) for row in rows}
        merged = defaults()
        merged.update(found)
        return merged

    def update_settings(self, changes: dict[str, Any], who: str, ts: datetime) -> list[dict]:
        stamp = iso_utc(ts)
        applied = []
        with self._lock:
            self.con.execute("BEGIN IMMEDIATE")
            try:
                for key, new_value in changes.items():
                    canon = canonical_key(key)
                    old_raw = self.con.execute("SELECT value FROM settings WHERE key = ?", (canon,)).fetchone()
                    old_text = old_raw["value"] if old_raw else None
                    new_text = encode(new_value)
                    self.con.execute(
                        """
                        INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                        """,
                        (canon, new_text, stamp),
                    )
                    self.con.execute(
                        "INSERT INTO change_log (ts, key, old, new, who) VALUES (?, ?, ?, ?, ?)",
                        (stamp, canon, old_text, new_text, who),
                    )
                    applied.append({"key": canon, "old": old_text, "new": new_text, "who": who, "ts": stamp})
                self.con.execute("COMMIT")
            except Exception:
                self.con.execute("ROLLBACK")
                raise
        return applied

    def change_log(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                "SELECT id, ts, key, old, new, who FROM change_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def ensure_ledger(self, cash: Decimal, ts: datetime) -> None:
        with self._lock:
            row = self.con.execute("SELECT cash, reserved FROM ledger WHERE id = 1").fetchone()
            if row is not None:
                return
            self.con.execute("BEGIN IMMEDIATE")
            self.con.execute(
                "INSERT INTO ledger (id, cash, reserved) VALUES (1, ?, ?)",
                (str(cents(cash)), "0.00"),
            )
            self.con.execute(
                "INSERT INTO balance (ts, value, cash, reserved, label) VALUES (?, ?, ?, ?, ?)",
                (iso_utc(ts), str(cents(cash)), str(cents(cash)), "0.00", "seed"),
            )
            self.con.execute("COMMIT")

    def equity(self) -> tuple[Decimal, Decimal, Decimal]:
        with self._lock:
            row = self.con.execute("SELECT cash, reserved FROM ledger WHERE id = 1").fetchone()
        if row is None:
            return Decimal("0"), Decimal("0"), Decimal("0")
        cash = D(row["cash"])
        reserved = D(row["reserved"])
        return cents(cash + reserved), cash, reserved

    def snapshot_balance(self, ts: datetime, label: str) -> dict:
        equity, cash, reserved = self.equity()
        payload = {
            "ts": iso_utc(ts),
            "value": str(equity),
            "cash": str(cash),
            "reserved": str(reserved),
            "label": label,
        }
        with self._lock:
            self.con.execute(
                "INSERT INTO balance (ts, value, cash, reserved, label) VALUES (?, ?, ?, ?, ?)",
                (payload["ts"], payload["value"], payload["cash"], payload["reserved"], label),
            )
            event_id = self._event_locked(ts, "balance", payload)
        payload["event_id"] = event_id
        return payload

    def _event_locked(self, ts: datetime, event_type: str, payload: dict) -> int:
        cur = self.con.execute(
            "INSERT INTO events (ts, type, payload_json) VALUES (?, ?, ?)",
            (iso_utc(ts), event_type, dumps(payload)),
        )
        return int(cur.lastrowid)

    def add_event(self, ts: datetime, event_type: str, payload: dict) -> int:
        with self._lock:
            return self._event_locked(ts, event_type, payload)

    def events_after(self, last_id: int, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                "SELECT id, ts, type, payload_json FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
                (last_id, limit),
            ).fetchall()
        return [
            {"id": row["id"], "ts": row["ts"], "type": row["type"], "payload": json.loads(row["payload_json"])}
            for row in rows
        ]

    def add_decision(self, agent: str, mint: str | None, ts: datetime, payload: dict, night_id: str) -> None:
        with self._lock:
            self.con.execute(
                "INSERT INTO decisions (agent, mint, ts, payload_json, night_id) VALUES (?, ?, ?, ?, ?)",
                (agent, mint, iso_utc(ts), dumps(payload), night_id),
            )

    def decisions(self, agent: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                """
                SELECT id, agent, mint, ts, payload_json, night_id
                FROM decisions WHERE agent = ? ORDER BY id DESC LIMIT ?
                """,
                (agent, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "agent": row["agent"],
                "mint": row["mint"],
                "ts": row["ts"],
                "night_id": row["night_id"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def upsert_coin(self, **fields) -> None:
        mint = fields["mint"]
        columns = [
            "name",
            "symbol",
            "pool",
            "pool_label",
            "migrated_at",
            "migration_signature",
            "creator",
            "creator_balance_at_migration",
            "creator_balance_observed",
            "creator_observed_at",
            "creator_observed_slot",
            "creator_balance_provenance",
            "holders",
            "scanner_liquidity",
            "scanner_price",
            "analyst_price",
            "analyst_score",
            "migration_provenance",
            "testdata",
        ]
        with self._lock:
            existing = self.con.execute("SELECT mint FROM coins WHERE mint = ?", (mint,)).fetchone()
            if existing is None:
                payload = {name: fields.get(name) for name in columns}
                payload["testdata"] = 0 if payload.get("testdata") is None else payload["testdata"]
                payload["mint"] = mint
                payload["created_at"] = fields.get("created_at") or iso_utc(datetime.now(timezone.utc))
                names = ["mint", "created_at", *columns]
                self.con.execute(
                    f"INSERT INTO coins ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                    [payload.get(name) for name in names],
                )
                return
            sets = []
            values = []
            for name in columns:
                if name in fields and fields[name] is not None:
                    sets.append(f"{name} = ?")
                    values.append(fields[name])
            if not sets:
                return
            values.append(mint)
            self.con.execute(f"UPDATE coins SET {', '.join(sets)} WHERE mint = ?", values)

    def coin(self, mint: str) -> dict | None:
        with self._lock:
            row = self.con.execute("SELECT * FROM coins WHERE mint = ?", (mint,)).fetchone()
        return dict(row) if row else None

    def find_coins(self, text: str) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                """
                SELECT * FROM coins
                WHERE mint = ? OR lower(symbol) = lower(?) OR lower(name) = lower(?)
                """,
                (text, text, text),
            ).fetchall()
        return [dict(row) for row in rows]

    def heartbeat(self, agent: str, ts: datetime, status: str, detail: str = "") -> None:
        with self._lock:
            self.con.execute(
                """
                INSERT INTO heartbeats (agent, ts, status, detail) VALUES (?, ?, ?, ?)
                ON CONFLICT(agent) DO UPDATE SET ts = excluded.ts, status = excluded.status, detail = excluded.detail
                """,
                (agent, iso_utc(ts), status, detail),
            )

    def heartbeats(self) -> dict[str, dict]:
        with self._lock:
            rows = self.con.execute("SELECT agent, ts, status, detail FROM heartbeats").fetchall()
        return {row["agent"]: dict(row) for row in rows}

    def ensure_bridge_job(self, **fields) -> dict:
        with self._lock:
            existing = self.con.execute(
                "SELECT * FROM bridge_jobs WHERE idempotency_key = ?",
                (fields["idempotency_key"],),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            self.con.execute(
                """
                INSERT INTO bridge_jobs (
                    id, role, kind, idempotency_key, status, subject, payload_json, created_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    fields["id"],
                    fields["role"],
                    fields["kind"],
                    fields["idempotency_key"],
                    fields.get("subject"),
                    fields["payload_json"],
                    fields["created_at"],
                ),
            )
            row = self.con.execute("SELECT * FROM bridge_jobs WHERE id = ?", (fields["id"],)).fetchone()
        return dict(row)

    def bridge_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self.con.execute("SELECT * FROM bridge_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def bridge_jobs(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                """
                SELECT id, role, kind, status, subject, created_at, dispatched_at, dispatch_status,
                       dispatch_error, claimed_at, acked_at
                FROM bridge_jobs ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_bridge_job(self, job_id: str, now: str) -> dict | None:
        with self._lock:
            row = self.con.execute("SELECT * FROM bridge_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            if row["acked_at"] is None and row["status"] in {"queued", "dispatched", "failed"}:
                self.con.execute(
                    "UPDATE bridge_jobs SET status = 'claimed', claimed_at = COALESCE(claimed_at, ?) WHERE id = ?",
                    (now, job_id),
                )
            updated = self.con.execute("SELECT * FROM bridge_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(updated)

    def ack_bridge_job(self, job_id: str, *, ok: bool, result_json: str, now: str) -> dict | None:
        with self._lock:
            row = self.con.execute("SELECT * FROM bridge_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            self.con.execute(
                """
                UPDATE bridge_jobs
                SET status = ?, result_json = ?, acked_at = ?
                WHERE id = ?
                """,
                ("acked" if ok else "failed", result_json, now, job_id),
            )
            updated = self.con.execute("SELECT * FROM bridge_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(updated)

    def mark_bridge_dispatch(self, job_id: str, *, http_status: int | None, error: str | None, now: str) -> dict | None:
        with self._lock:
            row = self.con.execute("SELECT * FROM bridge_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None or row["acked_at"] is not None:
                return None if row is None else dict(row)
            status = "dispatched" if http_status is not None and 200 <= http_status < 300 else "failed"
            self.con.execute(
                """
                UPDATE bridge_jobs
                SET status = ?, dispatched_at = ?, dispatch_status = ?, dispatch_error = ?
                WHERE id = ? AND acked_at IS NULL
                """,
                (status, now, http_status, None if not error else error[:160], job_id),
            )
            updated = self.con.execute("SELECT * FROM bridge_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(updated)

    def add_scan(self, **fields) -> int:
        with self._lock:
            cur = self.con.execute(
                """
                INSERT INTO scans (mint, ts, liquidity_usd, top10_pct, h1_change_pct, age_min, verdict, reason, night_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fields["mint"],
                    fields["ts"],
                    fields.get("liquidity_usd"),
                    fields.get("top10_pct"),
                    fields.get("h1_change_pct"),
                    fields.get("age_min"),
                    fields["verdict"],
                    fields.get("reason"),
                    fields["night_id"],
                ),
            )
            return int(cur.lastrowid)

    def mark_processed(self, kind: str, mint: str, night_id: str) -> bool:
        with self._lock:
            try:
                self.con.execute(
                    "INSERT INTO processed (kind, mint, night_id) VALUES (?, ?, ?)",
                    (kind, mint, night_id),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def is_processed(self, kind: str, mint: str, night_id: str) -> bool:
        with self._lock:
            row = self.con.execute(
                "SELECT 1 FROM processed WHERE kind = ? AND mint = ? AND night_id = ?",
                (kind, mint, night_id),
            ).fetchone()
        return row is not None

    def add_activity(self, **fields) -> int:
        with self._lock:
            cur = self.con.execute(
                """
                INSERT INTO activity (ts, kind, mint, agent, title, subtitle, value, link)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fields["ts"],
                    fields["kind"],
                    fields.get("mint"),
                    fields.get("agent"),
                    fields.get("title"),
                    fields.get("subtitle"),
                    fields.get("value"),
                    fields.get("link"),
                ),
            )
            return int(cur.lastrowid)

    def activity_page(self, before: int | None, limit: int = 30) -> list[dict]:
        with self._lock:
            if before is None:
                rows = self.con.execute(
                    "SELECT * FROM activity ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self.con.execute(
                    "SELECT * FROM activity WHERE id < ? ORDER BY id DESC LIMIT ?",
                    (before, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def insert_price(
        self,
        *,
        mint: str,
        ts: str,
        price: str,
        volume_m5: str | None,
        volume_m5_delta: str | None,
        liquidity_usd: str | None,
        writer: str,
    ) -> bool:
        with self._lock:
            try:
                self.con.execute(
                    """
                    INSERT INTO prices (mint, ts, price, volume_m5, volume_m5_delta, liquidity_usd, writer)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (mint, ts, price, volume_m5, volume_m5_delta, liquidity_usd, writer),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def latest_price_row(self, mint: str) -> dict | None:
        with self._lock:
            row = self.con.execute(
                "SELECT * FROM prices WHERE mint = ? ORDER BY ts DESC, id DESC LIMIT 1",
                (mint,),
            ).fetchone()
        return dict(row) if row else None

    def prices_for(self, mint: str) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                "SELECT * FROM prices WHERE mint = ? ORDER BY ts ASC, id ASC",
                (mint,),
            ).fetchall()
        return [dict(row) for row in rows]

    def balance_series(self) -> list[dict]:
        with self._lock:
            rows = self.con.execute("SELECT * FROM balance ORDER BY id ASC").fetchall()
        return [dict(row) for row in rows]

    def claim_writer(self, mint: str, writer: str) -> str:
        with self._lock:
            row = self.con.execute("SELECT writer FROM price_claims WHERE mint = ?", (mint,)).fetchone()
            if row is None:
                self.con.execute("INSERT INTO price_claims (mint, writer) VALUES (?, ?)", (mint, writer))
                return writer
            return row["writer"]

    def release_writer(self, mint: str) -> None:
        with self._lock:
            self.con.execute("DELETE FROM price_claims WHERE mint = ?", (mint,))

    def writer_of(self, mint: str) -> str | None:
        with self._lock:
            row = self.con.execute("SELECT writer FROM price_claims WHERE mint = ?", (mint,)).fetchone()
        return row["writer"] if row else None

    def set_metric(self, mint: str, payload: dict, ts: datetime) -> None:
        with self._lock:
            self.con.execute(
                """
                INSERT INTO metrics (mint, payload_json, ts) VALUES (?, ?, ?)
                ON CONFLICT(mint) DO UPDATE SET payload_json = excluded.payload_json, ts = excluded.ts
                """,
                (mint, dumps(payload), iso_utc(ts)),
            )

    def metric(self, mint: str) -> dict | None:
        with self._lock:
            row = self.con.execute("SELECT payload_json, ts FROM metrics WHERE mint = ?", (mint,)).fetchone()
        if row is None:
            return None
        return {"ts": row["ts"], "payload": json.loads(row["payload_json"])}

    def add_runner(self, night_id: str, mint: str, name: str | None, h24: str) -> None:
        with self._lock:
            self.con.execute(
                """
                INSERT INTO runners (night_id, mint, name, h24) VALUES (?, ?, ?, ?)
                ON CONFLICT(night_id, mint) DO UPDATE SET name = excluded.name, h24 = excluded.h24
                """,
                (night_id, mint, name, h24),
            )

    def runners_except(self, night_id: str, mint: str) -> list[tuple[str, str]]:
        with self._lock:
            rows = self.con.execute(
                "SELECT mint, name FROM runners WHERE night_id = ? AND mint != ?",
                (night_id, mint),
            ).fetchall()
        return [(row["mint"], row["name"] or "") for row in rows]

    def outbox_add(self, ts: datetime, kind: str, payload: dict, status: str, error: str | None = None) -> int:
        with self._lock:
            cur = self.con.execute(
                """
                INSERT INTO outbox (ts, channel, kind, payload_json, status, attempts, last_error)
                VALUES (?, 'dry_run', ?, ?, ?, ?, ?)
                """,
                (iso_utc(ts), kind, dumps(payload), status, 1 if status == "failed" else 0, error),
            )
            return int(cur.lastrowid)

    def outbox_all(self) -> list[dict]:
        with self._lock:
            rows = self.con.execute("SELECT * FROM outbox ORDER BY id ASC").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def closed_positions(self) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                "SELECT * FROM positions WHERE status = 'closed' AND pnl_booked = 1 ORDER BY closed_at, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def open_positions(self) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                f"SELECT * FROM positions WHERE status IN ({_exposure_sql()}) ORDER BY id ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def report_rows(self, start: str, end: str) -> dict:
        """One locked snapshot; exits belong to their close day, not entry day."""
        with self._lock:
            result = {}
            for table in ("scans", "audits", "verdicts", "signals"):
                result[table] = [dict(row) for row in self.con.execute(
                    f"SELECT * FROM {table} WHERE ts >= ? AND ts < ? ORDER BY id",
                    (start, end),
                ).fetchall()]
            result["closed"] = [dict(row) for row in self.con.execute(
                "SELECT * FROM positions WHERE closed_at >= ? AND closed_at < ? "
                "AND status = 'closed' AND pnl_booked = 1 ORDER BY closed_at, id",
                (start, end),
            ).fetchall()]
            result["open"] = [dict(row) for row in self.con.execute(
                f"SELECT * FROM positions WHERE status IN ({_exposure_sql()}) ORDER BY id"
            ).fetchall()]
        return result

    def position_by_mint(self, mint: str) -> dict | None:
        with self._lock:
            row = self.con.execute(
                f"""
                SELECT * FROM positions WHERE mint = ? AND status IN ({_exposure_sql()})
                ORDER BY id DESC LIMIT 1
                """,
                (mint,),
            ).fetchone()
        return dict(row) if row else None

    def latest_position(self, mint: str) -> dict | None:
        with self._lock:
            row = self.con.execute(
                "SELECT * FROM positions WHERE mint = ? ORDER BY id DESC LIMIT 1",
                (mint,),
            ).fetchone()
        return dict(row) if row else None

    def signal_for(self, mint: str, night_id: str) -> dict | None:
        with self._lock:
            row = self.con.execute(
                "SELECT * FROM signals WHERE mint = ? AND night_id = ?",
                (mint, night_id),
            ).fetchone()
        return dict(row) if row else None

    def commit_open(self, row: dict) -> str:
        """Atomically reserve cash and insert one signal plus one position."""
        with self._lock:
            self.con.execute("BEGIN IMMEDIATE")
            try:
                ledger = self.con.execute("SELECT cash, reserved FROM ledger WHERE id = 1").fetchone()
                if ledger is None:
                    self.con.execute("ROLLBACK")
                    return "no ledger"
                cash = D(ledger["cash"])
                reserved = D(ledger["reserved"])
                size = cents(row["size_usd"])
                if size > cash:
                    self.con.execute("ROLLBACK")
                    return "exceeds free"
                exists = self.con.execute(
                    "SELECT 1 FROM signals WHERE mint = ? AND night_id = ?",
                    (row["mint"], row["night_id"]),
                ).fetchone()
                if exists:
                    self.con.execute("ROLLBACK")
                    return "duplicate"
                open_row = self.con.execute(
                    f"SELECT 1 FROM positions WHERE mint = ? AND status IN ({_exposure_sql()})",
                    (row["mint"],),
                ).fetchone()
                if open_row:
                    self.con.execute("ROLLBACK")
                    return "duplicate position"
                self.con.execute(
                    """
                    INSERT INTO signals (
                        mint, ts, price, size_usd, stack_pct, link, mode, status, reason, night_id, name, score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["mint"],
                        row["ts"],
                        row["price"],
                        str(size),
                        row["stack_pct"],
                        row["link"],
                        row["mode"],
                        row["status"],
                        row.get("reason"),
                        row["night_id"],
                        row.get("name"),
                        row.get("score"),
                    ),
                )
                self.con.execute(
                    """
                    INSERT INTO positions (
                        mint, opened_at, entry_price, size_usd, peak_price, stop_price, stop_pct, initial_stop_pct,
                        trail_active, mode, status, source, night_id, name, symbol, link, entry_fee_usd, gas_usd,
                        gas_model, testdata, pnl_booked
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'open', ?, ?, ?, ?, ?, ?, '0', ?, ?, 0)
                    """,
                    (
                        row["mint"],
                        row["ts"],
                        row["entry_price"],
                        str(size),
                        row["entry_price"],
                        row["stop_price"],
                        row["stop_pct"],
                        row["stop_pct"],
                        row["mode"],
                        row["source"],
                        row["night_id"],
                        row.get("name"),
                        row.get("symbol"),
                        row["link"],
                        row.get("entry_fee_preview"),
                        row.get("gas_model"),
                        1 if row.get("testdata") else 0,
                    ),
                )
                self.con.execute(
                    "UPDATE ledger SET cash = ?, reserved = ? WHERE id = 1",
                    (str(cents(cash - size)), str(cents(reserved + size))),
                )
                self.con.execute("COMMIT")
                return "ok"
            except sqlite3.IntegrityError:
                self.con.execute("ROLLBACK")
                return "duplicate"
            except Exception:
                self.con.execute("ROLLBACK")
                raise

    def record_signal_only(self, row: dict) -> str:
        with self._lock:
            try:
                self.con.execute(
                    """
                    INSERT INTO signals (
                        mint, ts, price, size_usd, stack_pct, link, mode, status, reason, night_id, name, score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["mint"],
                        row["ts"],
                        row.get("price"),
                        row.get("size_usd"),
                        row.get("stack_pct"),
                        row.get("link"),
                        row.get("mode"),
                        row["status"],
                        row.get("reason"),
                        row["night_id"],
                        row.get("name"),
                        row.get("score"),
                    ),
                )
                return "ok"
            except sqlite3.IntegrityError:
                return "duplicate"

    def update_signal_status(self, mint: str, night_id: str, status: str, reason: str | None) -> None:
        with self._lock:
            self.con.execute(
                "UPDATE signals SET status = ?, reason = ? WHERE mint = ? AND night_id = ?",
                (status, reason, mint, night_id),
            )

    def skip(self, mint: str, night_id: str, until: str, reason: str) -> None:
        with self._lock:
            self.con.execute(
                """
                INSERT INTO skips (mint, until, night_id, reason) VALUES (?, ?, ?, ?)
                ON CONFLICT(mint, night_id) DO UPDATE SET until = excluded.until, reason = excluded.reason
                """,
                (mint, until, night_id, reason),
            )

    def active_skip(self, mint: str, night_id: str) -> bool:
        with self._lock:
            row = self.con.execute(
                "SELECT 1 FROM skips WHERE mint = ? AND night_id = ?",
                (mint, night_id),
            ).fetchone()
        return row is not None

    def save_audit_layers(self, rows: list[dict]) -> None:
        with self._lock:
            self.con.executemany(
                """
                INSERT INTO audits (
                    mint, ts, layer, name, value_text, pass, source, status, night_id, latency_ms, provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["mint"],
                        row["ts"],
                        row["n"],
                        row["name"],
                        row["value"],
                        row["pass_int"],
                        row.get("source"),
                        row["status"],
                        row["night_id"],
                        row.get("latency_ms"),
                        row.get("provenance"),
                    )
                    for row in rows
                ],
            )

    def audits_for(self, mint: str) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                """
                SELECT * FROM audits WHERE mint = ? AND id IN (
                    SELECT MAX(id) FROM audits WHERE mint = ? GROUP BY layer
                ) ORDER BY layer ASC
                """,
                (mint, mint),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_verdict(self, **fields) -> None:
        with self._lock:
            self.con.execute(
                """
                INSERT INTO verdicts (
                    mint, ts, alive_6h, organic, flush, chase, score, pass, reason, input_tokens, model, night_id, provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fields["mint"],
                    fields["ts"],
                    fields.get("alive_6h"),
                    fields.get("organic"),
                    fields.get("flush"),
                    fields.get("chase"),
                    fields.get("score"),
                    fields["passed"],
                    fields.get("reason"),
                    fields.get("input_tokens"),
                    fields.get("model"),
                    fields["night_id"],
                    fields.get("provenance"),
                ),
            )

    def latest_verdict(self, mint: str) -> dict | None:
        with self._lock:
            row = self.con.execute(
                "SELECT * FROM verdicts WHERE mint = ? ORDER BY id DESC LIMIT 1",
                (mint,),
            ).fetchone()
        return dict(row) if row else None

    def update_position_fields(self, position_id: int, **fields) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._lock:
            self.con.execute(
                f"UPDATE positions SET {assignments} WHERE id = ?",
                (*fields.values(), position_id),
            )

    def book_close(self, position_id: int, economics: dict) -> str:
        """Apply exit cash once. Retries see pnl_booked and do not post again."""
        with self._lock:
            self.con.execute("BEGIN IMMEDIATE")
            try:
                row = self.con.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
                if row is None:
                    self.con.execute("ROLLBACK")
                    return "missing"
                if row["pnl_booked"]:
                    self.con.execute("ROLLBACK")
                    return "already"
                ledger = self.con.execute("SELECT cash, reserved FROM ledger WHERE id = 1").fetchone()
                cash = D(ledger["cash"])
                reserved = D(ledger["reserved"])
                size = D(row["size_usd"])
                proceeds = D(economics["exit_notional"])
                fees = D(economics["fees"])
                reserved_next = cents(reserved - size)
                if reserved_next < 0:
                    self.con.execute("ROLLBACK")
                    return "reserve mismatch"
                cash_next = cents(cash + proceeds - fees)
                self.con.execute(
                    "UPDATE ledger SET cash = ?, reserved = ? WHERE id = 1",
                    (str(cash_next), str(reserved_next)),
                )
                self.con.execute(
                    """
                    UPDATE positions SET
                        status = 'closed', closed_at = ?, exit_price = ?, exit_reason = ?, pnl_usd = ?,
                        fees_usd = ?, entry_fee_usd = ?, exit_fee_usd = ?, gas_usd = ?, gas_model = ?,
                        gross_pct = ?, net_pct = ?, pnl_booked = 1, exit_price_provenance = ?, trigger_price = ?
                    WHERE id = ?
                    """,
                    (
                        economics["closed_at"],
                        economics["exit_price"],
                        economics["reason"],
                        economics["net_pnl"],
                        economics["fees"],
                        economics["entry_fee"],
                        economics["exit_fee"],
                        economics["gas"],
                        economics["gas_model"],
                        economics["gross_pct"],
                        economics["net_pct"],
                        economics["provenance"],
                        economics["exit_price"],
                        position_id,
                    ),
                )
                self.con.execute("COMMIT")
                return "ok"
            except Exception:
                self.con.execute("ROLLBACK")
                raise

    def mark_exiting(self, position_id: int, **fields) -> str:
        with self._lock:
            self.con.execute("BEGIN IMMEDIATE")
            row = self.con.execute("SELECT status FROM positions WHERE id = ?", (position_id,)).fetchone()
            if row is None:
                self.con.execute("ROLLBACK")
                return "missing"
            if row["status"] != "open":
                self.con.execute("ROLLBACK")
                return "not-open"
            assignments = ", ".join(f"{key} = ?" for key in fields)
            self.con.execute(
                f"UPDATE positions SET status = 'exiting', {assignments} WHERE id = ?",
                (*fields.values(), position_id),
            )
            self.con.execute("COMMIT")
            return "ok"

    def note_layer8_incident(self, night_id: str, ts: str, detail: str) -> bool:
        with self._lock:
            try:
                self.con.execute(
                    "INSERT INTO layer8_incidents (night_id, ts, detail) VALUES (?, ?, ?)",
                    (night_id, ts, detail),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def add_dev_snapshot(self, **fields) -> None:
        with self._lock:
            self.con.execute(
                """
                INSERT INTO dev_snapshots (mint, ts, slot, creator, balance_raw, provenance)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    fields["mint"],
                    fields["ts"],
                    fields.get("slot"),
                    fields.get("creator"),
                    fields.get("balance_raw"),
                    fields["provenance"],
                ),
            )

    def night(self, night_id: str) -> dict | None:
        with self._lock:
            row = self.con.execute("SELECT * FROM night_runs WHERE night_id = ?", (night_id,)).fetchone()
        return dict(row) if row else None

    def insert_night(self, row: dict) -> None:
        with self._lock:
            self.con.execute(
                """
                INSERT OR IGNORE INTO night_runs (
                    night_id, started_at, ended_at, synthetic, qualifying, source, warning, blocked, start_equity
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    row["night_id"],
                    row["started_at"],
                    row.get("ended_at"),
                    1 if row.get("synthetic", True) else 0,
                    row["source"],
                    row.get("warning"),
                    1 if row.get("blocked") else 0,
                    row.get("start_equity"),
                ),
            )

    def set_night_blocked(self, night_id: str, blocked: bool, warning: str | None) -> None:
        with self._lock:
            self.con.execute(
                "UPDATE night_runs SET blocked = ?, warning = ? WHERE night_id = ?",
                (1 if blocked else 0, warning, night_id),
            )

    def qualifying_nights(self) -> int:
        with self._lock:
            row = self.con.execute(
                "SELECT COUNT(*) AS n FROM night_runs WHERE qualifying = 1 AND synthetic = 0"
            ).fetchone()
        return int(row["n"])

    def seen_callback(self, callback_id: str) -> bool:
        with self._lock:
            row = self.con.execute(
                "SELECT 1 FROM callback_receipts WHERE id = ?",
                (callback_id,),
            ).fetchone()
        return row is not None

    def remember_callback(self, callback_id: str, ts: str, action: str, mint: str) -> bool:
        with self._lock:
            try:
                self.con.execute(
                    "INSERT INTO callback_receipts (id, ts, action, mint) VALUES (?, ?, ?, ?)",
                    (callback_id, ts, action, mint),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def add_push_sub(self, endpoint: str, p256dh: str, auth: str, ts: str) -> None:
        with self._lock:
            self.con.execute(
                """
                INSERT INTO push_subs (endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth
                """,
                (endpoint, p256dh, auth, ts),
            )

    def confirm_sent(self, row: dict) -> str:
        with self._lock:
            self.con.execute("BEGIN IMMEDIATE")
            try:
                signal = self.con.execute(
                    "SELECT status FROM signals WHERE mint = ? AND night_id = ?",
                    (row["mint"], row["night_id"]),
                ).fetchone()
                if signal is None or signal["status"] != "sent":
                    self.con.execute("ROLLBACK")
                    return "not-sent"
                open_row = self.con.execute(
                    f"SELECT 1 FROM positions WHERE mint = ? AND status IN ({_exposure_sql()})",
                    (row["mint"],),
                ).fetchone()
                if open_row:
                    self.con.execute("ROLLBACK")
                    return "duplicate position"
                ledger = self.con.execute("SELECT cash, reserved FROM ledger WHERE id = 1").fetchone()
                cash = D(ledger["cash"])
                reserved = D(ledger["reserved"])
                size = cents(row["size_usd"])
                if size > cash:
                    self.con.execute("ROLLBACK")
                    return "exceeds free"
                self.con.execute(
                    """
                    INSERT INTO positions (
                        mint, opened_at, entry_price, size_usd, peak_price, stop_price, stop_pct, initial_stop_pct,
                        trail_active, mode, status, source, night_id, name, symbol, link, gas_usd, gas_model, testdata,
                        pnl_booked
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'open', ?, ?, ?, ?, ?, '0', ?, ?, 0)
                    """,
                    (
                        row["mint"],
                        row["ts"],
                        row["entry_price"],
                        str(size),
                        row["entry_price"],
                        row["stop_price"],
                        row["stop_pct"],
                        row["stop_pct"],
                        row["mode"],
                        row["source"],
                        row["night_id"],
                        row.get("name"),
                        row.get("symbol"),
                        row["link"],
                        row.get("gas_model"),
                        1 if row.get("testdata") else 0,
                    ),
                )
                self.con.execute(
                    "UPDATE signals SET status = 'bought', reason = NULL WHERE mint = ? AND night_id = ?",
                    (row["mint"], row["night_id"]),
                )
                self.con.execute(
                    "UPDATE ledger SET cash = ?, reserved = ? WHERE id = 1",
                    (str(cents(cash - size)), str(cents(reserved + size))),
                )
                self.con.execute("COMMIT")
                return "ok"
            except sqlite3.IntegrityError:
                self.con.execute("ROLLBACK")
                return "duplicate"
            except Exception:
                self.con.execute("ROLLBACK")
                raise

    def sent_signals(self) -> list[dict]:
        with self._lock:
            rows = self.con.execute("SELECT * FROM signals WHERE status = 'sent'").fetchall()
        return [dict(row) for row in rows]

    def add_check(self, **fields) -> int:
        with self._lock:
            cur = self.con.execute(
                """
                INSERT INTO checks (mint, ts, kind, context, source_label, sized, on_desk, note, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fields["mint"],
                    fields["ts"],
                    fields["kind"],
                    fields["context"],
                    fields["source_label"],
                    1 if fields.get("sized") else 0,
                    1 if fields.get("on_desk") else 0,
                    fields.get("note"),
                    fields.get("error"),
                ),
            )
            return int(cur.lastrowid)

    def checks(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self.con.execute("SELECT * FROM checks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def latest_scan(self, mint: str) -> dict | None:
        with self._lock:
            row = self.con.execute(
                "SELECT * FROM scans WHERE mint = ? ORDER BY id DESC LIMIT 1",
                (mint,),
            ).fetchone()
        return dict(row) if row else None

    def recent_scans(self, limit: int = 12) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                """
                SELECT mint, ts, verdict, reason, liquidity_usd, top10_pct, h1_change_pct, age_min
                FROM scans ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_source_captures(self) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                """
                SELECT source, url, status, error, ts FROM raw_captures
                WHERE id IN (SELECT MAX(id) FROM raw_captures GROUP BY source)
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def add_raw(self, **fields) -> int:
        body = fields.get("body")
        if isinstance(body, str) and len(body) > 2_000_000:
            body = body[:2_000_000] + "\n…truncated"
        with self._lock:
            cur = self.con.execute(
                """
                INSERT INTO raw_captures (ts, source, mint, url, status, error, body)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fields["ts"],
                    fields["source"],
                    fields.get("mint"),
                    fields.get("url"),
                    fields.get("status"),
                    fields.get("error"),
                    body,
                ),
            )
            return int(cur.lastrowid)

    def raw_summaries(self, mint: str) -> list[dict]:
        with self._lock:
            rows = self.con.execute(
                """
                SELECT source, url, status, error, ts FROM raw_captures
                WHERE mint = ? ORDER BY id ASC
                """,
                (mint,),
            ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        tables = ["positions", "signals", "activity", "balance", "events", "outbox", "audits", "prices"]
        result = {}
        with self._lock:
            for name in tables:
                result[name] = int(self.con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        return result
