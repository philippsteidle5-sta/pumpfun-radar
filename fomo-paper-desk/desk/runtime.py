"""One process, five roles. Steps are callable so tests inject the clock."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from desk import CHUNK_ALLOWS_LIVE
from desk.agents import AGENTS
from desk.agents.analyst import ask_jev, build_state, compute_rules, state_hash
from desk.agents.auditor import LAYER_NAMES, AuditEvidence, evaluate_audit
from desk.agents.executor import plan_size, recheck
from desk.agents.history import evaluate_history
from desk.agents.risk import (
    evaluate_tick,
    gross_pct,
    live_pending_text,
    paper_exit_text,
    stop_price_for,
    tighten_stop_pct,
)
from desk.agents.scanner import evaluate_waiting, next_backoff_seconds, transport_needs_reconnect
from desk.bus import Bus, RateCoordinator, night_end_utc, night_id
from desk.clock import SystemClock
from desk.config import (
    CONFIRM_AUDITOR_OFF,
    CONFIRM_REFERRAL,
    TICKET_KEYS,
    SettingsError,
    canonical_key,
    normalize_value,
    ticket_warning,
)
from desk.db import Database
from desk.fees import GAS_MODEL, FeeModelError, close_economics
from desk.fields import (
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    Top10,
    creator_from_report,
    parse_lp,
    parse_migration,
    parse_mint_info,
    parse_token_accounts,
    program_account_request,
    select_pumpswap_pair,
    volume_m5_delta,
)
from desk.links import SIGNUP_URL, coin_url, profile_url
from desk.logging_setup import configure_logging
from desk.money import D, cents, fmt_money, fmt_pct, iso_utc
from desk.notifier import DryRunNotifier, buy_text, message
from desk.sources import OfflineSources, RateLimitError

log = logging.getLogger("desk.desk")


def _num(value):
    if value is None:
        return None
    return str(D(value))


class Desk:
    def __init__(
        self,
        db: Database,
        *,
        clock=None,
        sources=None,
        notifier=None,
        sleeper=None,
        secrets=None,
        log_dir=None,
        jev_transport=None,
        top10_provider=None,
        evidence_override=None,
    ):
        self.db = db
        self.clock = clock or SystemClock()
        self.sources = sources or OfflineSources()
        self.notifier = notifier or DryRunNotifier(db, self.clock)
        self.sleeper = sleeper or _remembering_sleeper(self)
        self.secrets = secrets if secrets is not None else []
        self.jev_transport = jev_transport
        self.top10_provider = top10_provider or _no_top10
        self.evidence_override = evidence_override or {}
        self.bus = Bus()
        self.rate = RateCoordinator(self.clock)
        self.waiting: dict[str, dict] = {}
        self.api_watch: set[str] = set()
        self.held_audit: list[str] = []
        self.held_analyst: list[str] = []
        self.held_executor: list[str] = []
        self.paused = {name: False for name in AGENTS}
        self.last_poll = {"scanner": None, "risk": None, "api": None}
        self.feed = {
            "closed": False,
            "open_seen": False,
            "last_rx": None,
            "attempt": 0,
            "error": None,
            "detail": "nicht verbunden",
        }
        self.no_size: set[str] = set()
        self.source_label = "off"
        self.market_data = "off"
        self._probe_deadline = None
        self.sleeps: list[float] = []
        self.last_audit = {}
        self.finite_stream = True
        if log_dir is not None:
            configure_logging(log_dir, self.clock, self.secrets)

    def settings(self) -> dict:
        return self.db.settings_map()

    def now(self) -> datetime:
        return self.clock.now()

    def night(self) -> str:
        return night_id(self.now(), int(self.settings()["desk.owner_utc_offset_hours"]))

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        await self.sleeper(seconds)

    def open_night(self, *, source: str = "offline") -> dict:
        settings = self.settings()
        self.db.ensure_ledger(D(settings["desk.paper_start_stack"]), self.now())
        nid = self.night()
        existing = self.db.night(nid)
        equity, cash, _reserved = self.db.equity()
        warning = self._warning_for(equity)
        blocked = bool(warning and settings["executor.below_min"] == "skip" and not settings["desk.empty_night_ack"])
        if existing is None:
            self.db.insert_night(
                {
                    "night_id": nid,
                    "started_at": iso_utc(self.now()),
                    "synthetic": True,
                    "source": source,
                    "warning": warning,
                    "blocked": blocked,
                    "start_equity": str(equity),
                }
            )
            self.db.snapshot_balance(self.now(), "night-start")
            if warning:
                self.notifier.send({"kind": "ticket_warning", "text": warning, "mint": None, "link": None, "buttons": []})
        elif warning:
            self.db.set_night_blocked(nid, blocked, warning)
        row = self.db.night(nid)
        assert row is not None
        for agent in AGENTS:
            if self.db.heartbeats().get(agent) is None:
                self.db.heartbeat(agent, self.now(), "starting", "")
        return row

    def _warning_for(self, stack: Decimal) -> str | None:
        settings = self.settings()
        base = stack if settings["desk.mode"] == "PAPER" else D(settings["desk.declared_stack"])
        return ticket_warning(
            stack=base,
            stack_pct=D(settings["executor.stack_pct"]),
            min_ticket=D(settings["executor.min_ticket_usd"]),
            below_min=settings["executor.below_min"],
        )

    def apply_settings(self, changes: dict, who: str, confirm: str | None = None, confirm_again: str | None = None) -> dict:
        normalized = {}
        for key, value in changes.items():
            canon = canonical_key(key)
            if canon == "desk.mode":
                raise SettingsError("mode changes go through POST /api/mode", key=canon)
            if canon == "desk.notify_transport" and value == "live":
                raise SettingsError("live notifier is refused in this chunk", key=canon)
            normalized[canon] = normalize_value(canon, value)
        if normalized.get("auditor.enabled") is False:
            if confirm != CONFIRM_AUDITOR_OFF or confirm_again != CONFIRM_AUDITOR_OFF:
                raise SettingsError(
                    "disabling the auditor requires two confirmations: DISABLE AUDITOR",
                    key="auditor.enabled",
                )
        if normalized.get("desk.referral_discount_verified") is True:
            if confirm != CONFIRM_REFERRAL or confirm_again != CONFIRM_REFERRAL:
                raise SettingsError(
                    "verifying the referral discount requires two confirmations: VERIFY REFERRAL",
                    key="desk.referral_discount_verified",
                )
        old_stop = D(self.settings()["risk.stop_pct"])
        applied = self.db.update_settings(normalized, who, self.now())
        new_stop = D(self.settings()["risk.stop_pct"])
        if new_stop != old_stop:
            self.retighten_stops(new_stop)
        warning = None
        if TICKET_KEYS.intersection(normalized):
            equity, _cash, _reserved = self.db.equity()
            stack = equity if self.db.equity() else D(self.settings()["desk.paper_start_stack"])
            if self.db.equity()[0] == 0 and self.db.night(self.night()) is None:
                stack = D(self.settings()["desk.paper_start_stack"])
            warning = self._warning_for(stack if stack > 0 else D(self.settings()["desk.paper_start_stack"]))
            if warning:
                self.notifier.send({"kind": "ticket_warning", "text": warning, "mint": None, "link": None, "buttons": []})
            self._sync_night_block(warning)
        return {"applied": applied, "warning": warning, "change_log": self.db.change_log(20)}

    def _sync_night_block(self, warning: str | None) -> None:
        row = self.db.night(self.night())
        if row is None:
            return
        settings = self.settings()
        blocked = bool(warning and settings["executor.below_min"] == "skip" and not settings["desk.empty_night_ack"])
        self.db.set_night_blocked(self.night(), blocked, warning)

    def retighten_stops(self, proposed: Decimal) -> None:
        for position in self.db.open_positions():
            tightened = tighten_stop_pct(position["stop_pct"], proposed)
            if tightened == D(position["stop_pct"]):
                continue
            price = stop_price_for(position["entry_price"], tightened)
            self.db.update_position_fields(position["id"], stop_pct=str(tightened), stop_price=str(price))

    def set_mode(self, mode: str, confirm: str | None) -> dict:
        if mode == "PAPER":
            self.db.update_settings({"desk.mode": "PAPER"}, "owner", self.now())
            return {"mode": "PAPER", "live": False}
        if mode != "LIVE":
            raise SettingsError("mode must be PAPER or LIVE", key="desk.mode")
        if confirm != "LIVE":
            raise SettingsError("LIVE requires body confirm LIVE", key="desk.mode")
        if not CHUNK_ALLOWS_LIVE or self.db.qualifying_nights() < 1:
            raise SettingsError("LIVE blocked: no real qualifying PAPER night in this chunk", key="desk.mode")
        raise SettingsError("LIVE blocked this chunk", key="desk.mode")

    def pause(self, name: str) -> dict:
        if name not in AGENTS:
            raise KeyError(name)
        if name == "risk" and self.db.open_positions():
            raise SettingsError("risk cannot pause with open positions", key="risk")
        self.paused[name] = True
        self.db.heartbeat(name, self.now(), "paused", "")
        return {"agent": name, "paused": True}

    def resume(self, name: str) -> dict:
        if name not in AGENTS:
            raise KeyError(name)
        self.paused[name] = False
        self.db.heartbeat(name, self.now(), "running", "")
        return {"agent": name, "paused": False}

    def note_feed(self, *, opened: bool = False, message_seen: bool = False, closed: bool = False) -> dict:
        if opened:
            self.feed["open_seen"] = True
            self.feed["closed"] = False
            self.feed["last_rx"] = self.now()
            self.feed["attempt"] = 0
        if message_seen:
            self.feed["last_rx"] = self.now()
            self.feed["closed"] = False
        if closed:
            self.feed["closed"] = True
        silent = 10**9
        if self.feed["last_rx"] is not None:
            silent = (self.now() - self.feed["last_rx"]).total_seconds()
        needs = transport_needs_reconnect(
            closed=self.feed["closed"],
            silent_seconds=silent,
            open_seen=self.feed["open_seen"],
        )
        delay = None
        if needs:
            delay = next_backoff_seconds(self.feed["attempt"])
            self.feed["attempt"] += 1
            logging.getLogger("desk.scanner").info(
                "%s reconnect backoff %ss closed=%s silent=%s",
                iso_utc(self.now()),
                delay,
                self.feed["closed"],
                silent if silent < 10**8 else "never",
            )
        return {"reconnect": needs, "backoff_s": delay, "silent_s": None if silent > 10**8 else silent}

    def ingest_migration(self, message: dict, *, testdata: bool = False) -> dict | None:
        event = parse_migration(message)
        if event.tx_type != "migrate" or not event.mint:
            return None
        stamp = iso_utc(self.now())
        self.db.upsert_coin(
            mint=event.mint,
            pool_label=event.pool_label,
            migrated_at=stamp,
            migration_signature=event.signature,
            creator=event.creator,
            migration_provenance="testdata-receipt" if testdata else "pumpportal_receipt",
            testdata=1 if testdata else 0,
            created_at=stamp,
        )
        self.waiting[event.mint] = {"seen_at": self.now(), "testdata": testdata}
        if self.db.writer_of(event.mint) is None:
            self.db.claim_writer(event.mint, "scanner")
        logging.getLogger("desk.scanner").info("%s waiting %s label=%s", stamp, event.mint, event.pool_label)
        return {"mint": event.mint, "pool_label": event.pool_label, "creator": event.creator}

    async def step(self) -> None:
        self.open_night()
        await self.poll_scanner()
        await self.drain()
        await self.poll_risk()
        await self.poll_api()
        self.expire_confirmations()
        self.remind_exits()

    async def poll_scanner(self) -> None:
        settings = self.settings()
        if self.paused["scanner"]:
            self.db.heartbeat("scanner", self.now(), "paused", "")
            return
        mints = list(self.waiting)
        if not mints:
            self.db.heartbeat("scanner", self.now(), "idle", "warte auf Migration")
            return
        if not self._due("scanner", int(settings["scanner.poll_s"])):
            return
        self.last_poll["scanner"] = self.now()
        payload = await self._dex_batch(mints)
        if payload is None:
            self.db.heartbeat("scanner", self.now(), "backoff", "dex 429 or rate limit; room kept")
            return
        nid = self.night()
        for mint in mints:
            item = self.waiting.get(mint)
            if item is None:
                continue
            pair = select_pumpswap_pair(payload, mint)
            if pair is not None:
                self._remember_pair(mint, pair, "scanner")
            top10 = self.top10_provider(mint, pair)
            runners = self.db.runners_except(nid, mint)
            decision = evaluate_waiting(
                pair=pair,
                top10=top10,
                now=self.now(),
                seen_at=item["seen_at"],
                runners=runners,
                settings=settings,
                own_name=pair.name if pair else None,
            )
            output = {
                "agent": "scanner",
                "mint": mint,
                "name": pair.name if pair else None,
                "symbol": pair.symbol if pair else None,
                "pool": pair.pair_address if pair else None,
                "liquidityUsd": _num(decision.liquidity_usd),
                "top10Pct": _num(decision.top10_pct),
                "h1ChangePct": _num(decision.h1_change_pct),
                "ageMin": _num(decision.age_min),
                "verdict": decision.verdict,
                "reason": decision.reason,
                "ts": iso_utc(self.now()),
            }
            self.db.add_scan(
                mint=mint,
                ts=output["ts"],
                liquidity_usd=output["liquidityUsd"],
                top10_pct=output["top10Pct"],
                h1_change_pct=output["h1ChangePct"],
                age_min=output["ageMin"],
                verdict=decision.verdict if decision.verdict != "wait" else "wait",
                reason=decision.reason,
                night_id=nid,
            )
            self.db.add_decision("scanner", mint, self.now(), output, nid)
            self.db.add_event(self.now(), "scan", output)
            logging.getLogger("desk.scanner").info(
                "%s %s %s %s liq=%s top10=%s h1=%s age=%s",
                output["ts"],
                mint,
                decision.verdict,
                decision.reason,
                output["liquidityUsd"],
                output["top10Pct"],
                output["h1ChangePct"],
                output["ageMin"],
            )
            if decision.verdict == "wait":
                continue
            self.db.add_check(
                mint=mint,
                ts=output["ts"],
                kind="migration",
                context="testdata" if item.get("testdata") else "live_migration",
                source_label=self.source_label,
                sized=0,
                on_desk=0,
                note=decision.reason,
                error=None,
            )
            self.waiting.pop(mint, None)
            self._hand_off_writer(mint)
            if decision.verdict == "pass" and self.db.mark_processed("scan_pass", mint, nid):
                if pair is not None:
                    self.db.upsert_coin(
                        mint=mint,
                        name=pair.name,
                        symbol=pair.symbol,
                        pool=pair.pair_address,
                        scanner_liquidity=str(pair.liquidity_usd) if pair.liquidity_usd is not None else None,
                        scanner_price=str(pair.price_usd) if pair.price_usd is not None else None,
                    )
                self.bus.put_audit(mint)
        self.db.heartbeat("scanner", self.now(), "running", f"room {len(self.waiting)}")

    async def drain(self) -> None:
        audit_batch = _drain_queue(self.bus.audit)
        if self.paused["auditor"] or not self.settings()["auditor.enabled"]:
            self.held_audit.extend(audit_batch)
            self.db.heartbeat("auditor", self.now(), "held", "auditor off holds coins; it does not pass them")
        else:
            for mint in self.held_audit + audit_batch:
                await self.audit_mint(mint)
            self.held_audit.clear()
            if audit_batch or self.last_audit:
                self.db.heartbeat("auditor", self.now(), "running", "")
        analyst_batch = _drain_queue(self.bus.analyst)
        if self.paused["analyst"]:
            self.held_analyst.extend(analyst_batch)
            self.db.heartbeat("analyst", self.now(), "paused", "")
        else:
            for mint in self.held_analyst + analyst_batch:
                await self.analyze_mint(mint)
            self.held_analyst.clear()
        exec_batch = _drain_queue(self.bus.executor)
        if self.paused["executor"]:
            self.held_executor.extend(exec_batch)
            self.db.heartbeat("executor", self.now(), "paused", "")
        else:
            for mint in self.held_executor + exec_batch:
                await self.execute_mint(mint)
            self.held_executor.clear()

    async def audit_mint(self, mint: str) -> dict | None:
        nid = self.night()
        if self.db.is_processed("audit_reject", mint, nid) or self.db.is_processed("audit_pass", mint, nid):
            return None
        evidence = self.evidence_override.get(mint)
        if evidence is None:
            evidence = await self.gather_evidence(mint)
        result = evaluate_audit(evidence, self.settings())
        self.last_audit[mint] = result
        stamp = iso_utc(self.now())
        rows = []
        for layer in result.layers:
            payload = {
                "agent": "auditor",
                "mint": mint,
                "ts": stamp,
                "n": layer.n,
                "name": layer.name,
                "pass": layer.passed,
                "value": layer.value,
                "source": layer.source,
                "status": layer.status,
            }
            self.db.add_event(self.now(), "audit_layer", payload)
            rows.append(
                {
                    "mint": mint,
                    "ts": stamp,
                    "n": layer.n,
                    "name": layer.name,
                    "value": layer.value,
                    "pass_int": None if layer.passed is None else int(layer.passed),
                    "source": layer.source,
                    "status": layer.status,
                    "night_id": nid,
                    "latency_ms": 0,
                    "provenance": layer.provenance,
                }
            )
        self.db.save_audit_layers(rows)
        if result.holders is not None:
            self.db.upsert_coin(mint=mint, holders=result.holders)
        output = {
            "agent": "auditor",
            "mint": mint,
            "ts": stamp,
            "passed": result.passed,
            "layersChecked": result.layers_checked,
            "layers": [
                {"n": layer.n, "name": layer.name, "pass": layer.passed, "value": layer.value, "source": layer.source}
                for layer in result.layers
            ],
            "holders": result.holders,
            "reason": result.reason,
            "testdata": result.testdata,
        }
        self.db.add_decision("auditor", mint, self.now(), output, nid)
        logging.getLogger("desk.auditor").info(
            "%s %s passed=%s checked=%s reason=%s",
            stamp,
            mint,
            result.passed,
            result.layers_checked,
            result.reason,
        )
        if evidence.rpc_error and any(layer.n == 8 and layer.status == "red" for layer in result.layers):
            if self.db.note_layer8_incident(nid, stamp, evidence.rpc_error):
                self.notifier.send(
                    {
                        "kind": "incident",
                        "text": "layer 8 down, every coin is red until rpc_url changes",
                        "mint": mint,
                        "link": coin_url(mint),
                        "buttons": [{"label": "Buy on FOMO", "url": coin_url(mint)}],
                    }
                )
        if result.passed:
            self.db.mark_processed("audit_pass", mint, nid)
            self.bus.put_analyst(mint)
        else:
            self.db.mark_processed("audit_reject", mint, nid)
            coin = self.db.coin(mint) or {}
            link = coin_url(mint)
            self.db.add_activity(
                ts=stamp,
                kind="rejected",
                mint=mint,
                agent="auditor",
                title=f"Rejected {coin.get('name') or mint}",
                subtitle=f"Auditor · {result.reason}",
                value="Skipped",
                link=link,
            )
        return output

    async def gather_evidence(self, mint: str) -> AuditEvidence:
        report = await self._attempt(lambda: self.sources.rugcheck_report(mint))
        graph = await self._attempt(lambda: self.sources.rugcheck_graph(mint))
        account = await self._attempt(
            lambda: self.sources.rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        )
        from desk.fields import program_account_request

        classic_raw = await self._attempt(
            lambda: self.sources.rpc("getProgramAccounts", program_account_request(TOKEN_PROGRAM, mint)["params"])
        )
        token2022_raw = await self._attempt(
            lambda: self.sources.rpc(
                "getProgramAccounts",
                program_account_request(TOKEN_2022_PROGRAM, mint)["params"],
            )
        )
        rpc_error = None
        classic_accounts = None
        token2022_accounts = None
        if isinstance(classic_raw, Exception) or isinstance(token2022_raw, Exception):
            rpc_error = "getProgramAccounts refused"
        else:
            try:
                classic_accounts = parse_token_accounts(classic_raw)
                token2022_accounts = parse_token_accounts(token2022_raw)
            except Exception as exc:
                rpc_error = str(exc)
        mint_info = parse_mint_info(account if isinstance(account, dict) else {})
        creator, present = creator_from_report(report if isinstance(report, dict) else {})
        if isinstance(report, dict) and creator:
            observed = report.get("creatorBalance") if "creatorBalance" in report else None
            self.db.upsert_coin(
                mint=mint,
                creator=creator,
                creator_balance_observed=None if observed is None else str(observed),
                creator_observed_at=iso_utc(self.now()),
                creator_balance_provenance="rugcheck_current_not_migration_baseline",
            )
            self.db.add_dev_snapshot(
                mint=mint,
                ts=iso_utc(self.now()),
                slot=None,
                creator=creator,
                balance_raw=None if observed is None else str(observed),
                provenance="rugcheck_current_not_migration_baseline",
            )
        coin = self.db.coin(mint) or {}
        pool = coin.get("pool")
        lp = parse_lp(report if isinstance(report, dict) else {}, pool)
        metric = self.db.metric(mint)
        fresh = None
        if metric and metric["payload"].get("liquidity_usd") is not None:
            fresh = D(metric["payload"]["liquidity_usd"])
        scanned = D(coin["scanner_liquidity"]) if coin.get("scanner_liquidity") else None
        migrated = _parse_ts(coin["migrated_at"]) if coin.get("migrated_at") else None
        return AuditEvidence(
            mint_info=mint_info,
            creator=creator,
            creator_key_present=present,
            history=None,
            current_balance_raw=None,
            top10=self.top10_provider(mint, None),
            lp=lp,
            scanner_liquidity=scanned,
            fresh_liquidity=fresh if fresh is not None else scanned,
            migrated_at=migrated,
            now=self.now(),
            graph=graph if not isinstance(graph, Exception) else [],
            classic_accounts=classic_accounts,
            token2022_accounts=token2022_accounts,
            rpc_error=rpc_error,
            provenance="observed",
        )

    async def _attempt(self, fn):
        import time

        attempts = 1 if self._probe_deadline is not None else int(self.settings()["auditor.retries"])
        last = None
        for index in range(attempts):
            if self._probe_deadline is not None and time.monotonic() > self._probe_deadline:
                return TimeoutError("source budget elapsed")
            try:
                return await fn()
            except Exception as exc:
                last = exc
                if index + 1 < attempts:
                    await self._sleep(2)
        return last

    async def analyze_mint(self, mint: str) -> dict | None:
        settings = self.settings()
        nid = self.night()
        if self.db.is_processed("verdict", mint, nid):
            return None
        payload = await self._dex_batch([mint])
        pair = None if payload is None else select_pumpswap_pair(payload, mint)
        stamp = iso_utc(self.now())
        coin = self.db.coin(mint) or {}
        link = coin_url(mint)
        if pair is None:
            gate_reason = "missing:dex"
            failed = [gate_reason]
            answers = None
            score = None
            model = settings["analyst.mode"]
            tokens = 0
            passed = False
        else:
            self._remember_pair(mint, pair, self._writer_for(mint) or "api")
            audit = self.last_audit.get(mint)
            bundles = None if audit is None else audit.bundles
            metrics = {
                "volume_h1": pair.volume_h1,
                "buys_h1": pair.buys_h1,
                "buys_m5": pair.buys_m5,
                "sells_m5": pair.sells_m5,
                "price_change_m5": pair.price_change_m5,
                "price_change_h1": pair.price_change_h1,
            }
            if settings["analyst.mode"] == "rules":
                gate = compute_rules(metrics, bundles, settings)
            elif self.jev_transport is None:
                from desk.agents.analyst import GateResult

                gate = GateResult(False, None, None, ["no answer"], "jev", 0, "no answer")
            else:
                facts = {
                    "name": pair.name,
                    "symbol": pair.symbol,
                    "age_min": coin.get("migrated_at"),
                    "liq_usd": pair.liquidity_usd,
                    "mcap": pair.market_cap,
                    "m5_buys": pair.buys_m5,
                    "m5_sells": pair.sells_m5,
                    "m5_vol": pair.volume_m5,
                    "m5_change": pair.price_change_m5,
                    "h1_buys": pair.buys_h1,
                    "h1_sells": pair.sells_h1,
                    "h1_vol": pair.volume_h1,
                    "h1_change": pair.price_change_h1,
                    "top10_pct": None,
                    "dev_pct": None,
                    "sold_proven": False,
                    "lp_locked_pct": None,
                    "bundles": bundles,
                    "delegations": None,
                }
                gate = await ask_jev(
                    self.jev_transport,
                    build_state(facts),
                    "test-key-not-live",
                    self._sleep,
                    settings,
                )
            answers = None if gate.answers is None else {key: str(value) for key, value in gate.answers.items()}
            score = gate.score
            failed = gate.failed
            model = gate.model
            tokens = gate.input_tokens
            passed = gate.passed
            gate_reason = gate.reason
            if pair.price_usd is not None and passed:
                self.db.upsert_coin(mint=mint, analyst_price=str(pair.price_usd), analyst_score=str(score))
        self.db.add_verdict(
            mint=mint,
            ts=stamp,
            alive_6h=None if not answers else answers["alive_6h"],
            organic=None if not answers else answers["organic"],
            flush=None if not answers else answers["flush"],
            chase=None if not answers else answers["chase"],
            score=None if score is None else str(score),
            passed=int(passed),
            reason=gate_reason,
            input_tokens=tokens,
            model=model,
            night_id=nid,
            provenance="testdata" if coin.get("testdata") else "rules",
        )
        output = {
            "agent": "analyst",
            "mint": mint,
            "answers": answers,
            "score": score,
            "passed": passed,
            "failed": failed,
            "model": model,
            "inputTokens": tokens,
            "ts": stamp,
        }
        self.db.add_decision("analyst", mint, self.now(), output, nid)
        self.db.add_event(self.now(), "verdict", output)
        self.db.mark_processed("verdict", mint, nid)
        hashed = state_hash(str(answers))
        logging.getLogger("desk.analyst").info(
            "%s %s model=%s score=%s passed=%s tokens=%s state_hash=%s",
            stamp,
            mint,
            model,
            score,
            passed,
            tokens,
            hashed,
        )
        if passed and mint in self.no_size:
            self.db.add_activity(
                ts=stamp,
                kind="rejected",
                mint=mint,
                agent="analyst",
                title=f"Rejected {coin.get('name') or mint}",
                subtitle="nicht dimensioniert, nicht auf dem Desk",
                value="Skipped",
                link=link,
            )
            self.db.heartbeat("analyst", self.now(), "running", "not sized")
        elif passed:
            self.bus.put_executor(mint)
            self.db.heartbeat("analyst", self.now(), "running", "")
        else:
            label = "Analyst (rules)" if model == "rules" else "Analyst (jev)"
            first = failed[0] if failed else gate_reason
            self.db.add_activity(
                ts=stamp,
                kind="rejected",
                mint=mint,
                agent="analyst",
                title=f"Rejected {coin.get('name') or mint}",
                subtitle=f"{label} · {first}",
                value="Skipped",
                link=link,
            )
            self.db.heartbeat("analyst", self.now(), "running", first or "")
        return output

    async def execute_mint(self, mint: str) -> dict | None:
        settings = self.settings()
        nid = self.night()
        coin = self.db.coin(mint) or {}
        link = coin_url(mint)
        night_row = self.db.night(nid)
        if night_row and night_row["blocked"]:
            self.db.heartbeat("executor", self.now(), "blocked", night_row.get("warning") or "empty night blocked")
            return None
        if mint in self.no_size:
            return {"agent": "executor", "mint": mint, "status": "skipped", "reason": "not sized, not on the desk"}
        if self.db.signal_for(mint, nid) or self.db.active_skip(mint, nid):
            return None
        if self.db.position_by_mint(mint):
            return {"agent": "executor", "mint": mint, "status": "skipped", "reason": "already open"}
        payload = await self._dex_batch([mint])
        pair = None if payload is None else select_pumpswap_pair(payload, mint)
        fresh_price = None if pair is None else pair.price_usd
        fresh_liq = None if pair is None else pair.liquidity_usd
        if pair is not None:
            self._remember_pair(mint, pair, self._writer_for(mint) or "api")
        reason = recheck(
            scanner_liquidity=coin.get("scanner_liquidity"),
            fresh_liquidity=fresh_liq,
            analyst_price=coin.get("analyst_price"),
            fresh_price=fresh_price,
            settings=settings,
        )
        equity, cash, _reserved = self.db.equity()
        mode = settings["desk.mode"]
        stack = equity if mode == "PAPER" else D(settings["desk.declared_stack"])
        free = cash if mode == "PAPER" else D(settings["desk.declared_stack"])
        pool_liq = fresh_liq if fresh_liq is not None else Decimal(0)
        plan = None if reason else plan_size(stack=stack, free=free, pool_liquidity=pool_liq, settings=settings)
        if reason is None and plan is not None and plan.action == "skip":
            reason = plan.reason
        stamp = iso_utc(self.now())
        score = coin.get("analyst_score") or "0"
        if reason is not None:
            self.db.record_signal_only(
                {
                    "mint": mint,
                    "ts": stamp,
                    "price": None if fresh_price is None else str(fresh_price),
                    "size_usd": None if plan is None or plan.size is None else str(plan.size),
                    "stack_pct": None if plan is None or plan.stack_pct is None else str(plan.stack_pct),
                    "link": link,
                    "mode": mode,
                    "status": "skipped",
                    "reason": reason,
                    "night_id": nid,
                    "name": coin.get("name"),
                    "score": score,
                }
            )
            self.db.skip(mint, nid, iso_utc(night_end_utc(self.now(), int(settings["desk.owner_utc_offset_hours"]))), reason)
            output = self._exec_output(mint, fresh_price, None, None, link, mode, "skipped", reason)
            self._finish_exec(mint, output, nid)
            self.db.add_activity(
                ts=stamp,
                kind="rejected",
                mint=mint,
                agent="executor",
                title=f"Rejected {coin.get('name') or mint}",
                subtitle=f"Executor · {reason}",
                value="Skipped",
                link=link,
            )
            return output
        assert plan is not None and plan.size is not None and fresh_price is not None
        text = buy_text(
            mode=mode,
            name=coin.get("name") or mint,
            size=plan.size,
            stack_pct=plan.stack_pct,
            score=score,
            stop_pct=settings["risk.stop_pct"],
            trail_pct=settings["risk.trail_pct"],
        )
        push = message("buy", mint, text)
        delivered = False
        last_error = None
        for _attempt in range(3):
            if self.notifier.send(push):
                delivered = True
                break
            last_error = "push failed"
        status = "bought" if mode == "PAPER" else ("sent" if delivered else "missed")
        reason_out = None if delivered else last_error
        if mode == "PAPER":
            opened = self.db.commit_open(
                {
                    "mint": mint,
                    "ts": stamp,
                    "price": str(fresh_price),
                    "entry_price": str(fresh_price),
                    "size_usd": str(plan.size),
                    "stack_pct": str(plan.stack_pct),
                    "link": link,
                    "mode": "PAPER",
                    "status": "bought",
                    "reason": reason_out,
                    "night_id": nid,
                    "name": coin.get("name"),
                    "symbol": coin.get("symbol"),
                    "score": score,
                    "stop_price": str(stop_price_for(fresh_price, settings["risk.stop_pct"])),
                    "stop_pct": str(D(settings["risk.stop_pct"])),
                    "source": "simulated",
                    "gas_model": GAS_MODEL,
                    "testdata": bool(coin.get("testdata")),
                }
            )
            if opened != "ok":
                output = self._exec_output(mint, fresh_price, plan.size, plan.stack_pct, link, mode, "skipped", opened)
                self._finish_exec(mint, output, nid)
                return output
            self._claim_risk(mint)
            self.db.snapshot_balance(self.now(), "fill")
            self.db.add_activity(
                ts=stamp,
                kind="bought",
                mint=mint,
                agent="executor",
                title=f"Bought {coin.get('name') or mint}",
                subtitle=f"Executor · {stamp}",
                value=f"${fmt_money(plan.size)} · {fmt_pct(plan.stack_pct)}% stack",
                link=link,
            )
            self.db.add_event(self.now(), "fill", {"mint": mint, "mode": "PAPER", "source": "simulated", "size": str(plan.size)})
            if reason_out:
                logging.getLogger("desk.executor").info("%s %s push failed; paper fill kept", stamp, mint)
        else:
            recorded = self.db.record_signal_only(
                {
                    "mint": mint,
                    "ts": stamp,
                    "price": str(fresh_price),
                    "size_usd": str(plan.size),
                    "stack_pct": str(plan.stack_pct),
                    "link": link,
                    "mode": "LIVE",
                    "status": status,
                    "reason": reason_out,
                    "night_id": nid,
                    "name": coin.get("name"),
                    "score": score,
                }
            )
            if recorded != "ok":
                status = "skipped"
                reason_out = recorded
        output = self._exec_output(mint, fresh_price, plan.size, plan.stack_pct, link, mode, status, reason_out)
        self._finish_exec(mint, output, nid)
        self.db.add_event(self.now(), "signal", output)
        logging.getLogger("desk.executor").info(
            "%s %s stack=%s raw=%s cap=%s final=%s mode=%s status=%s link=%s",
            stamp,
            mint,
            stack,
            plan.raw,
            plan.cap,
            plan.size,
            mode,
            status,
            link,
        )
        return output

    def _exec_output(self, mint, price, size, pct, link, mode, status, reason) -> dict:
        return {
            "agent": "executor",
            "mint": mint,
            "signalPrice": None if price is None else str(price),
            "sizeUsd": None if size is None else str(size),
            "stackPct": None if pct is None else str(pct),
            "link": link,
            "mode": mode,
            "status": status,
            "reason": reason,
            "ts": iso_utc(self.now()),
        }

    def _finish_exec(self, mint, output, nid) -> None:
        self.db.add_decision("executor", mint, self.now(), output, nid)
        self.db.heartbeat("executor", self.now(), "running", output["status"])

    async def poll_risk(self) -> None:
        settings = self.settings()
        if not self._due("risk", int(settings["risk.poll_s"])):
            return
        positions = self.db.open_positions()
        if self.paused["risk"]:
            self.db.heartbeat("risk", self.now(), "paused", "")
            return
        self.last_poll["risk"] = self.now()
        if not positions:
            self.db.heartbeat("risk", self.now(), "idle", "no open positions")
            return
        blocked = [row for row in positions if row["status"] == "close_blocked"]
        actionable = [row for row in positions if row["status"] != "close_blocked"]
        if not actionable:
            self.db.heartbeat("risk", self.now(), "running", f"exposure close_blocked {len(blocked)}")
            return
        positions = actionable
        mints = [row["mint"] for row in positions]
        payload = await self._dex_batch(mints)
        if payload is None:
            self.db.heartbeat("risk", self.now(), "backoff", "dex 429; missed poll, strikes unchanged")
            logging.getLogger("desk.risk").info("%s missed poll dex429", iso_utc(self.now()))
            return
        for position in positions:
            await self._risk_one(position, select_pumpswap_pair(payload, position["mint"]), settings)
        self.db.heartbeat("risk", self.now(), "running", f"{len(positions)} open")

    async def _risk_one(self, position: dict, pair, settings: dict) -> None:
        current = self.db.position_by_mint(position["mint"]) or position
        if current["status"] not in {"open", "exiting"}:
            return
        if pair is None or pair.price_usd is None:
            strikes = int(current["no_price_polls"]) + 1
            self.db.update_position_fields(current["id"], no_price_polls=strikes)
            logging.getLogger("desk.risk").info("%s %s no price strike %s", iso_utc(self.now()), current["mint"], strikes)
            if strikes >= 4 and not current["missing_price_alerted"]:
                self.db.update_position_fields(current["id"], missing_price_alerted=1)
                text = f"no price for 1 min · {current.get('name') or current['mint']}"
                self.notifier.send(message("sell", current["mint"], f"PAPER · {text}" if current["mode"] == "PAPER" else text))
            return
        if pair.liquidity_usd is not None and D(pair.liquidity_usd) == 0:
            self._pull(current, settings)
            return
        self._remember_pair(current["mint"], pair, "risk")
        self.db.update_position_fields(
            current["id"],
            no_price_polls=0,
            missing_price_alerted=0,
            last_price=str(pair.price_usd),
            last_price_at=iso_utc(self.now()),
            liquidity_usd=None if pair.liquidity_usd is None else str(pair.liquidity_usd),
        )
        if current["status"] != "open":
            return
        opened = _parse_ts(current["opened_at"])
        decision = evaluate_tick(
            entry=current["entry_price"],
            peak=current["peak_price"],
            stop_price=current["stop_price"],
            trail_active=bool(current["trail_active"]),
            price=pair.price_usd,
            opened_at=opened,
            now=self.now(),
            settings=settings,
        )
        fields = {"peak_price": str(decision.peak)}
        if decision.trail_active:
            fields["trail_active"] = 1
            if decision.trail_just_activated:
                fields["trail_activated_at"] = iso_utc(self.now())
        self.db.update_position_fields(current["id"], **fields)
        logging.getLogger("desk.risk").info(
            "%s %s price=%s peak=%s stop=%s trail=%s action=%s",
            iso_utc(self.now()),
            current["mint"],
            pair.price_usd,
            decision.peak,
            current["stop_price"],
            decision.trail_active,
            decision.action,
        )
        if decision.action:
            self._exit(current, decision.action, pair.price_usd, "polled-price", settings)

    def _pull(self, position: dict, settings: dict) -> None:
        if not position.get("last_price"):
            if not position["missing_price_alerted"]:
                self.db.update_position_fields(position["id"], missing_price_alerted=1, liquidity_usd="0")
                self.notifier.send(
                    {
                        "kind": "incident",
                        "text": f"{position.get('name') or position['mint']} liquidity 0 with no genuine price; no pnl booked",
                        "mint": position["mint"],
                        "link": coin_url(position["mint"]),
                        "buttons": [],
                    }
                )
            return
        self._exit(
            position,
            "pulled",
            D(position["last_price"]),
            "stale_last_price_liquidity_0_no_executable_guarantee",
            settings,
        )

    def _exit(self, position: dict, reason: str, price, provenance: str, settings: dict, *, confirmed: bool = False) -> None:
        fresh = self.db.latest_position(position["mint"])
        if fresh is None or fresh["status"] not in {"open", "exiting"}:
            return
        position = fresh
        name = position.get("name") or position["mint"]
        link = position.get("link") or coin_url(position["mint"])
        if position["mode"] == "LIVE" and not confirmed:
            if position["status"] == "exiting":
                return
            marked = self.db.mark_exiting(
                position["id"],
                exiting_since=iso_utc(self.now()),
                trigger_price=str(price),
                exit_price_provenance=provenance,
                exit_reason=reason,
            )
            if marked != "ok":
                return
            text = live_pending_text(name=name, reason=reason, stop_pct=position["stop_pct"])
            self.notifier.send(message("sell", position["mint"], text))
            self.db.add_event(
                self.now(),
                "exit",
                {"mint": position["mint"], "reason": reason, "status": "exiting", "mode": "LIVE", "link": link},
            )
            self.db.add_decision("risk", position["mint"], self.now(), {"reason": reason, "status": "exiting"}, self.night())
            return
        try:
            economics = close_economics(
                position["size_usd"],
                position["entry_price"],
                price,
                nominal_discount=settings["desk.fees_ref_discount"],
                verified=bool(settings["desk.referral_discount_verified"]),
            )
        except FeeModelError as exc:
            self.db.update_position_fields(position["id"], status="close_blocked", exit_reason="fee-model-blocked")
            self.notifier.send(
                {
                    "kind": "incident",
                    "text": f"fee model refused close for {name}: {exc}",
                    "mint": position["mint"],
                    "link": link,
                    "buttons": [],
                }
            )
            return
        gross = gross_pct(position["entry_price"], price)
        net_pct = cents(economics.net_pnl / D(position["size_usd"]) * Decimal(100))
        booked = self.db.book_close(
            position["id"],
            {
                "closed_at": iso_utc(self.now()),
                "exit_price": str(price),
                "reason": reason,
                "net_pnl": str(economics.net_pnl),
                "fees": str(economics.fees),
                "entry_fee": str(economics.entry_fee),
                "exit_fee": str(economics.exit_fee),
                "gas": str(economics.gas),
                "gas_model": economics.gas_model,
                "gross_pct": str(gross),
                "net_pct": str(net_pct),
                "exit_notional": str(economics.exit_notional),
                "provenance": provenance,
            },
        )
        if booked != "ok":
            return
        self._release_writer(position["mint"])
        text = paper_exit_text(
            mode="PAPER",
            name=name,
            reason=reason,
            stop_pct=position["stop_pct"],
            net_pnl=economics.net_pnl,
            gross=gross,
            timeout_h=settings["risk.timeout_h"],
        )
        self.notifier.send(message("sell", position["mint"], text))
        kind = "stopped" if reason == "stop" else "sold"
        if reason == "stop":
            title = f"Stopped {name}"
            subtitle = f"Risk · -{fmt_pct(position['stop_pct'])}% stop"
            value = f"-${fmt_money(abs(economics.net_pnl))}"
        elif reason == "trail":
            title = f"Sold {name}"
            subtitle = "Risk · trailing stop"
            value = f"+${fmt_money(economics.net_pnl)} / {fmt_pct(gross)}%"
        elif reason == "timeout":
            title = f"Sold {name}"
            subtitle = "Risk · timeout"
            value = f"{fmt_money(economics.net_pnl)} / {fmt_pct(gross)}%"
        else:
            title = f"Sold {name}"
            subtitle = "Risk · pulled · stale last price, no executable guarantee"
            value = f"{fmt_money(economics.net_pnl)}"
        self.db.add_activity(
            ts=iso_utc(self.now()),
            kind=kind,
            mint=position["mint"],
            agent="risk",
            title=title,
            subtitle=subtitle,
            value=value,
            link=link,
        )
        output = {
            "agent": "risk",
            "mint": position["mint"],
            "name": name,
            "ts": iso_utc(self.now()),
            "reason": reason,
            "entryPrice": position["entry_price"],
            "exitPrice": str(price),
            "peakPrice": position["peak_price"],
            "pnlUsd": str(economics.net_pnl),
            "pct": str(gross),
            "netPct": str(net_pct),
            "feesUsd": str(economics.fees),
            "stopPct": position["stop_pct"],
            "mode": "PAPER",
            "status": "closed",
            "link": link,
            "pnlBasis": "net of both fees; pct is gross price change",
        }
        self.db.add_event(self.now(), "exit", output)
        self.db.add_decision("risk", position["mint"], self.now(), output, self.night())
        self.db.snapshot_balance(self.now(), "exit")

    def expire_confirmations(self) -> None:
        window = D(self.settings()["executor.confirm_window_min"])
        for signal in self.db.sent_signals():
            opened = _parse_ts(signal["ts"])
            age = Decimal(str((self.now() - opened).total_seconds())) / Decimal(60)
            if age >= window and self.db.position_by_mint(signal["mint"]) is None:
                self.db.update_signal_status(signal["mint"], signal["night_id"], "missed", "no tap")
                self.db.skip(
                    signal["mint"],
                    signal["night_id"],
                    iso_utc(night_end_utc(self.now(), int(self.settings()["desk.owner_utc_offset_hours"]))),
                    "no tap",
                )

    def remind_exits(self) -> None:
        window = D(self.settings()["risk.confirm_window_min"])
        for position in self.db.open_positions():
            if position["status"] != "exiting" or position["reminder_sent"] or not position["exiting_since"]:
                continue
            age = Decimal(str((self.now() - _parse_ts(position["exiting_since"])).total_seconds())) / Decimal(60)
            if age >= window:
                self.db.update_position_fields(position["id"], reminder_sent=1)
                text = f"{position.get('name') or position['mint']} still open"
                self.notifier.send(message("sell", position["mint"], text))

    def confirm_bought(self, mint: str, night: str | None = None) -> str:
        nid = night or self.night()
        signal = self.db.signal_for(mint, nid)
        if signal is None or signal["status"] != "sent":
            return "not-sent"
        settings = self.settings()
        result = self.db.confirm_sent(
            {
                "mint": mint,
                "night_id": nid,
                "ts": iso_utc(self.now()),
                "entry_price": signal["price"],
                "size_usd": signal["size_usd"],
                "stop_price": str(stop_price_for(signal["price"], settings["risk.stop_pct"])),
                "stop_pct": str(D(settings["risk.stop_pct"])),
                "mode": "LIVE",
                "source": "owner_confirmed",
                "name": signal.get("name"),
                "symbol": None,
                "link": signal["link"],
                "gas_model": GAS_MODEL,
                "testdata": False,
            }
        )
        if result == "ok":
            self._claim_risk(mint)
            self.db.add_activity(
                ts=iso_utc(self.now()),
                kind="bought",
                mint=mint,
                agent="executor",
                title=f"Bought {signal.get('name') or mint}",
                subtitle="Executor · owner confirmed · signal price",
                value=f"${signal['size_usd']}",
                link=signal["link"],
            )
            self.db.snapshot_balance(self.now(), "fill")
        return result

    def confirm_sold(self, mint: str) -> str:
        position = self.db.position_by_mint(mint)
        if position is None:
            return "no position"
        if position["status"] == "closed":
            return "already"
        price = position.get("trigger_price") or position.get("last_price")
        if not price:
            return "no price"
        if position["mode"] != "LIVE":
            return "not-live"
        settings = self.settings()
        self._exit(
            position,
            position.get("exit_reason") or "stop",
            D(price),
            "owner_sold_confirmation",
            settings,
            confirmed=True,
        )
        return "ok"

    def handle_update(self, update: dict, owner_chat_id: str | None) -> dict:
        from desk.telegram import parse_update

        inbound = parse_update(update, owner_chat_id)
        if inbound.status != "accept" or inbound.action is None:
            return {"status": inbound.status, "reason": inbound.reason}
        if inbound.callback_id and not self.db.remember_callback(
            inbound.callback_id, iso_utc(self.now()), inbound.action, inbound.mint or ""
        ):
            return {"status": "reject", "reason": "duplicate callback"}
        mint = inbound.mint or ""
        if inbound.action == "bought":
            result = self.confirm_bought(mint)
            return {"status": "accept", "result": result, "mint": mint}
        if inbound.action == "skip":
            signal = self.db.signal_for(mint, self.night())
            if signal and signal["status"] == "sent":
                self.db.update_signal_status(mint, self.night(), "skipped", "owner skip")
                self.db.skip(mint, self.night(), iso_utc(night_end_utc(self.now(), int(self.settings()["desk.owner_utc_offset_hours"]))), "owner skip")
                return {"status": "accept", "result": "skipped", "mint": mint}
            return {"status": "accept", "result": "ignored", "mint": mint}
        if inbound.action == "sold":
            return {"status": "accept", "result": self.confirm_sold(mint), "mint": mint}
        return {"status": "reject", "reason": "callback unbound"}

    async def poll_api(self) -> None:
        if not self._due("api", 60):
            return
        self.last_poll["api"] = self.now()
        mints = [mint for mint in self.api_watch if self._writer_for(mint) == "api"]
        if not mints:
            return
        payload = await self._dex_batch(mints)
        if payload is None:
            return
        for mint in mints:
            pair = select_pumpswap_pair(payload, mint)
            if pair is not None:
                self._remember_pair(mint, pair, "api")

    def watch(self, mint: str) -> None:
        if self._writer_for(mint) is None:
            self.api_watch.add(mint)
            self.db.claim_writer(mint, "api")

    async def _dex_batch(self, mints: list[str]):
        rows = []
        try:
            for offset in range(0, len(mints), 30):
                chunk = mints[offset : offset + 30]
                if not self.rate.acquire():
                    return None
                try:
                    part = await self.sources.dex_tokens(chunk)
                except RateLimitError:
                    self.rate.note_429()
                    logging.getLogger("desk.scanner").info("%s dex 429 backoff 30s", iso_utc(self.now()))
                    return None
                if isinstance(part, list):
                    rows.extend(part)
                elif isinstance(part, dict):
                    rows.append(part)
            return rows
        except Exception as exc:
            logging.getLogger("desk.desk").info("dex read failed %s", type(exc).__name__)
            return None

    def _remember_pair(self, mint: str, pair, writer: str) -> None:
        owner = self.db.writer_of(mint)
        if owner is None:
            self.db.claim_writer(mint, writer)
            owner = writer
        if owner != writer:
            self.db.set_metric(mint, _pair_metric(pair), self.now())
            return
        previous = self.db.latest_price_row(mint)
        previous_volume = D(previous["volume_m5"]) if previous and previous["volume_m5"] is not None else None
        delta = volume_m5_delta(previous_volume, pair.volume_m5)
        if pair.price_usd is not None:
            self.db.insert_price(
                mint=mint,
                ts=iso_utc(self.now()),
                price=str(pair.price_usd),
                volume_m5=None if pair.volume_m5 is None else str(pair.volume_m5),
                volume_m5_delta=None if delta is None else str(delta),
                liquidity_usd=None if pair.liquidity_usd is None else str(pair.liquidity_usd),
                writer=writer,
            )
        self.db.set_metric(mint, _pair_metric(pair), self.now())
        h24 = pair.price_change_h24
        if h24 is not None and h24 >= D(self.settings()["scanner.runner_h24_pct"]) and pair.name:
            self.db.add_runner(self.night(), mint, pair.name, str(h24))
        if pair.name or pair.symbol or pair.pair_address:
            self.db.upsert_coin(
                mint=mint,
                name=pair.name,
                symbol=pair.symbol,
                pool=pair.pair_address,
            )

    def _writer_for(self, mint: str) -> str | None:
        if self.db.position_by_mint(mint):
            return "risk"
        if mint in self.waiting:
            return "scanner"
        if mint in self.api_watch:
            return "api"
        return self.db.writer_of(mint)

    def _hand_off_writer(self, mint: str) -> None:
        if self.db.position_by_mint(mint):
            self._claim_risk(mint)
            return
        if self.db.writer_of(mint) == "scanner":
            self.db.release_writer(mint)
        self.api_watch.add(mint)
        if self.db.writer_of(mint) is None:
            self.db.claim_writer(mint, "api")

    def _claim_risk(self, mint: str) -> None:
        self.waiting.pop(mint, None)
        self.api_watch.discard(mint)
        current = self.db.writer_of(mint)
        if current != "risk":
            if current is not None:
                self.db.release_writer(mint)
            self.db.claim_writer(mint, "risk")

    def _release_writer(self, mint: str) -> None:
        if self.db.writer_of(mint) == "risk":
            self.db.release_writer(mint)

    def _due(self, name: str, seconds: int) -> bool:
        last = self.last_poll[name]
        if last is None:
            return True
        return (self.now() - last).total_seconds() >= seconds

    async def probe_historical(self, mint: str, budget_s: float = 30.0) -> dict:
        """Read-only check of a chosen mint. It does not size or open a position."""
        import time

        self.open_night()
        if self.settings()["desk.mode"] != "PAPER":
            self.db.update_settings({"desk.mode": "PAPER"}, "probe-guard", self.now())
        self.no_size.add(mint)
        self._probe_deadline = time.monotonic() + float(budget_s)
        error = None
        pair = None
        age = None
        try:
            payload = await self._dex_batch([mint])
            if isinstance(payload, list):
                pair = select_pumpswap_pair(payload, mint)
                if pair is None:
                    error = "kein Dex-Paar"
            else:
                error = "Dex ohne Antwort"
        except Exception as exc:
            error = type(exc).__name__
        stamp = iso_utc(self.now())
        if pair is not None and pair.pair_created_at is not None:
            age = (Decimal(str((self.now() - pair.pair_created_at).total_seconds())) / Decimal(60)).quantize(
                Decimal("0.01")
            )
            self._remember_pair(mint, pair, self._writer_for(mint) or "api")
        self.db.add_scan(
            mint=mint,
            ts=stamp,
            liquidity_usd=None if pair is None or pair.liquidity_usd is None else str(pair.liquidity_usd),
            top10_pct=None,
            h1_change_pct=None if pair is None or pair.price_change_h1 is None else str(pair.price_change_h1),
            age_min=None if age is None else str(age),
            verdict="context",
            reason="historische Prüfung, nicht frisch migriert",
            night_id=self.night(),
        )
        self.db.add_event(
            self.now(),
            "scan",
            {"agent": "scanner", "mint": mint, "verdict": "context", "reason": "historische Prüfung", "ts": stamp},
        )
        nid = self.night()
        try:
            if not self.db.is_processed("audit_reject", mint, nid) and not self.db.is_processed("audit_pass", mint, nid):
                await self.audit_mint(mint)
                await self.drain()
        except Exception as exc:
            error = type(exc).__name__
        finally:
            self._probe_deadline = None
        if error is None:
            for raw in self.db.raw_summaries(mint):
                if raw.get("error") or (raw.get("status") not in (None, 200)):
                    error = raw.get("error") or f"{raw.get('source')} http {raw.get('status')}"
                    break
        self.db.add_check(
            mint=mint,
            ts=stamp,
            kind="on_demand",
            context="historical_on_demand",
            source_label=self.source_label,
            sized=0,
            on_desk=0,
            note="nicht dimensioniert, nicht auf dem Desk",
            error=error,
        )
        self.db.add_event(self.now(), "probe", {"mint": mint, "context": "historical_on_demand", "paper": True, "ts": stamp})
        return {"mint": mint, "error": error, "ts": stamp, "sized": False, "on_desk": False}

    def state(self) -> dict:
        equity, cash, reserved = self.db.equity()
        night = self.db.night(self.night())
        start = D(night["start_equity"]) if night and night.get("start_equity") else equity or Decimal(1)
        today = Decimal(0) if start == 0 else cents((equity - start) / start * Decimal(100))
        initial = D(self.settings()["desk.paper_start_stack"]) or Decimal(1)
        multiplier = cents(equity / initial) if initial else Decimal(0)
        cards = []
        beats = self.db.heartbeats()
        for name in AGENTS:
            beat = beats.get(name)
            cards.append(
                {
                    "name": name,
                    "paused": self.paused[name],
                    "status": self._agent_status(name, beat),
                    "last_beat": None if beat is None else beat["ts"],
                    "detail": None if beat is None else beat["detail"],
                }
            )
        return {
            "balance": str(equity),
            "cash": str(cash),
            "reserved": str(reserved),
            "today_pct": str(today),
            "multiplier": str(multiplier),
            "mode": self.settings()["desk.mode"],
            "night_id": self.night(),
            "ticket_warning": None if night is None else night.get("warning"),
            "night_blocked": bool(night and night["blocked"]),
            "agents": cards,
            "exposure": len(self.db.open_positions()),
            "live_allowed": False,
        }

    def chart(self, range_name: str) -> dict:
        """LIVE is the current owner-night, not a fixed 15-minute window."""
        allowed = ("LIVE", "1H", "4H", "1D", "1W", "ALL")
        if range_name not in allowed:
            raise SettingsError("range must be LIVE, 1H, 4H, 1D, 1W, or ALL", key="range")
        now = self.now()
        start = None
        if range_name == "LIVE":
            row = self.db.night(self.night())
            if row and row.get("started_at"):
                start = _parse_ts(row["started_at"])
            else:
                offset = int(self.settings()["desk.owner_utc_offset_hours"])
                local = now.astimezone(timezone.utc) + timedelta(hours=offset)
                midnight = datetime.combine(local.date(), time.min, tzinfo=timezone.utc)
                start = midnight - timedelta(hours=offset)
        elif range_name != "ALL":
            hours = {"1H": 1, "4H": 4, "1D": 24, "1W": 168}[range_name]
            start = now - timedelta(hours=hours)
        points = []
        for row in self.db.balance_series():
            moment = _parse_ts(row["ts"])
            if start is not None and moment < start:
                continue
            points.append({"t": row["ts"], "value": row["value"]})
        marks = []
        for row in reversed(self.db.activity_page(None, 500)):
            moment = _parse_ts(row["ts"])
            if start is not None and moment < start:
                continue
            if row["kind"] in {"bought", "sold", "stopped"}:
                marks.append({"t": row["ts"], "kind": row["kind"], "mint": row["mint"], "link": row["link"]})
        return {
            "range": range_name,
            "start": None if start is None else iso_utc(start),
            "points": points,
            "marks": marks,
        }

    def _agent_status(self, name: str, beat: dict | None) -> str:
        if self.paused[name]:
            return "paused"
        if name == "auditor" and not self.settings()["auditor.enabled"]:
            return "held"
        if beat is None:
            return "stalled"
        age = (self.now() - _parse_ts(beat["ts"])).total_seconds()
        if age > 120 and beat["status"] not in {"paused", "held", "idle"}:
            return "stalled"
        return beat["status"]

    def account(self) -> dict:
        settings = self.settings()
        equity, _cash, _reserved = self.db.equity()
        verified = bool(settings["desk.referral_discount_verified"])
        return {
            "mode": settings["desk.mode"],
            "stack": str(equity),
            "paper_start_stack": str(D(settings["desk.paper_start_stack"])),
            "declared_stack": str(D(settings["desk.declared_stack"])),
            "handle": settings["desk.fomo_handle"],
            "profile_url": profile_url(settings["desk.fomo_handle"]),
            "signup_url": SIGNUP_URL,
            "referral_discount_nominal": str(D(settings["desk.fees_ref_discount"])),
            "referral_discount_verified": verified,
            "effective_fee_discount": "0" if not verified else str(D(settings["desk.fees_ref_discount"])),
            "notifier": settings["desk.notifier"],
            "notify_transport": "dry_run",
        }


def _pair_metric(pair) -> dict:
    return {
        "price_usd": None if pair.price_usd is None else str(pair.price_usd),
        "liquidity_usd": None if pair.liquidity_usd is None else str(pair.liquidity_usd),
        "volume_m5": None if pair.volume_m5 is None else str(pair.volume_m5),
        "volume_h1": None if pair.volume_h1 is None else str(pair.volume_h1),
        "price_change_m5": None if pair.price_change_m5 is None else str(pair.price_change_m5),
        "price_change_h1": None if pair.price_change_h1 is None else str(pair.price_change_h1),
        "buys_m5": pair.buys_m5,
        "sells_m5": pair.sells_m5,
        "buys_h1": pair.buys_h1,
        "pair_address": pair.pair_address,
        "name": pair.name,
        "symbol": pair.symbol,
    }


def _no_top10(mint, pair) -> Top10:
    return Top10("incomplete", None, "no data:top10", provenance="missing-exclusion-proof")


def _remembering_sleeper(desk: Desk):
    async def _sleep(_seconds: float) -> None:
        return None

    return _sleep


def _drain_queue(queue) -> list[str]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def _parse_ts(value: str | None) -> datetime:
    if not value:
        raise ValueError("missing timestamp")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("naive timestamp")
    return moment.astimezone(timezone.utc)


def history_is_not_operational(evidence) -> str:
    verdict = evaluate_history(evidence)
    if verdict.operational_proof:
        return verdict.reason
    return verdict.reason
