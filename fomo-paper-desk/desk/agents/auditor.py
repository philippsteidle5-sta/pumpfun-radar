"""Eight ordered layers. The first red stops the rest. The tail is unchecked."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from desk.agents.history import HistoryEvidence, evaluate_history
from desk.fields import (
    LpView,
    MintInfo,
    TokenAccount,
    Top10,
    count_holders,
    graph_compatible,
    biggest_holder_component,
    MISSING,
)
from desk.money import D

LAYER_NAMES = {
    1: "mintAuthority",
    2: "freezeAuthority",
    3: "dev",
    4: "top10",
    5: "lp",
    6: "age",
    7: "bundles",
    8: "delegations",
}


@dataclass
class LayerResult:
    n: int
    name: str
    status: str  # pass | red | unchecked
    value: str
    source: str | None
    provenance: str | None = None

    @property
    def passed(self) -> bool | None:
        if self.status == "unchecked":
            return None
        return self.status == "pass"


@dataclass
class AuditResult:
    passed: bool
    layers_checked: int
    layers: list[LayerResult]
    holders: int | None
    reason: str | None
    testdata: bool = False
    bundles: int | None = None


@dataclass
class AuditEvidence:
    mint_info: MintInfo
    creator: str | None
    creator_key_present: bool
    history: HistoryEvidence | None
    current_balance_raw: int | None
    top10: Top10
    lp: LpView
    scanner_liquidity: Decimal | None
    fresh_liquidity: Decimal | None
    migrated_at: datetime | None
    now: datetime
    graph: Any
    classic_accounts: list[TokenAccount] | None
    token2022_accounts: list[TokenAccount] | None
    rpc_error: str | None = None
    provenance: str = "observed"


def evaluate_audit(evidence: AuditEvidence, settings: dict) -> AuditResult:
    layers: list[LayerResult] = []
    failed = False
    holders: int | None = None
    reason: str | None = None
    testdata = evidence.provenance == "testdata"
    for number in range(1, 9):
        if failed:
            layers.append(
                LayerResult(number, LAYER_NAMES[number], "unchecked", "not checked", None, evidence.provenance)
            )
            continue
        layer, extra_holders = _check(number, evidence, settings)
        layers.append(layer)
        if extra_holders is not None:
            holders = extra_holders
        if layer.status != "pass":
            failed = True
            reason = layer.value
    checked = 8
    for layer in layers:
        if layer.status == "unchecked":
            checked = layer.n - 1
            break
    bundles = biggest_holder_component(evidence.graph) if not failed and graph_compatible(evidence.graph) else None
    return AuditResult(not failed, checked, layers, holders, reason, testdata, bundles)


def _check(number: int, evidence: AuditEvidence, settings: dict) -> tuple[LayerResult, int | None]:
    name = LAYER_NAMES[number]
    if number == 1:
        return _authority("mint", evidence.mint_info), None
    if number == 2:
        return _authority("freeze", evidence.mint_info), None
    if number == 3:
        return _dev(evidence, settings), None
    if number == 4:
        return _top10(evidence, settings), None
    if number == 5:
        return _lp(evidence), None
    if number == 6:
        return _age(evidence, settings), None
    if number == 7:
        return _bundles(evidence, settings), None
    return _delegations(evidence, settings)


def _layer(number: int, status: str, value: str, source: str | None, provenance: str | None = None) -> LayerResult:
    return LayerResult(number, LAYER_NAMES[number], status, value, source, provenance)


def _authority(kind: str, info: MintInfo) -> LayerResult:
    number = 1 if kind == "mint" else 2
    present = info.mint_authority_present if kind == "mint" else info.freeze_authority_present
    value = info.mint_authority if kind == "mint" else info.freeze_authority
    label = "mintAuthority" if kind == "mint" else "freezeAuthority"
    if not info.structure_present or not present or value is MISSING:
        return _layer(number, "red", f"no data:{label}", "rpc")
    if value is None:
        return _layer(number, "pass", "Revoked", "rpc")
    if kind == "mint":
        return _layer(number, "red", "mint not revoked", "rpc")
    return _layer(number, "red", "freeze not revoked", "rpc")


def _dev(evidence: AuditEvidence, settings: dict) -> LayerResult:
    info = evidence.mint_info
    if not info.structure_present or info.supply_raw is None or info.supply_raw <= 0:
        return _layer(3, "red", "no supply", "rpc")
    if not evidence.creator_key_present or not evidence.creator:
        return _layer(3, "red", "no data:dev", "rugcheck")
    history = evaluate_history(evidence.history)
    if not history.proven:
        return _layer(3, "red", history.reason, "rpc", "testdata" if history.testdata else evidence.provenance)
    if evidence.current_balance_raw is None:
        return _layer(3, "red", "no data:dev", "rpc")
    pct = (Decimal(evidence.current_balance_raw) / Decimal(info.supply_raw)) * Decimal(100)
    if history.reason == "dev sold":
        return _layer(3, "red", "dev sold", "rpc")
    if pct >= D(settings["auditor.max_dev_pct"]):
        shown = pct.quantize(Decimal("0.01"))
        return _layer(3, "red", f"dev holds {shown}%", "rpc")
    marker = " TESTDATA" if history.testdata else ""
    return _layer(3, "pass", f"dev {pct.quantize(Decimal('0.01'))}%{marker}", "rpc", evidence.provenance)


def _top10(evidence: AuditEvidence, settings: dict) -> LayerResult:
    top = evidence.top10
    if top.status != "ok" or top.pct is None:
        return _layer(4, "red", "no data:top10", "rpc", top.provenance)
    limit = D(settings["auditor.max_top10_pct"])
    shown = top.pct.quantize(Decimal("0.01"))
    marker = " TESTDATA" if top.provenance == "testdata" else ""
    if top.pct < limit:
        return _layer(4, "pass", f"top10 {shown}%{marker}", "rpc", top.provenance)
    return _layer(4, "red", f"top10 {shown}%", "rpc", top.provenance)


def _lp(evidence: AuditEvidence) -> LayerResult:
    lp = evidence.lp
    if not lp.market_found or lp.lp_locked_pct is None:
        return _layer(5, "red", "no data:liquidity", "rugcheck")
    if evidence.scanner_liquidity is None or evidence.fresh_liquidity is None:
        return _layer(5, "red", "no data:liquidity", "dex")
    if evidence.scanner_liquidity <= 0:
        return _layer(5, "red", "no data:liquidity", "dex")
    drop = (evidence.scanner_liquidity - evidence.fresh_liquidity) / evidence.scanner_liquidity * Decimal(100)
    if drop > Decimal(50):
        return _layer(5, "red", "liquidity pulled", "dex")
    if lp.lp_locked_pct < D("99"):
        return _layer(5, "red", "liquidity unlocked", "rugcheck")
    return _layer(
        5,
        "pass",
        f"lpLockedPct {lp.lp_locked_pct} burned=null",
        "rugcheck",
        evidence.provenance,
    )


def _age(evidence: AuditEvidence, settings: dict) -> LayerResult:
    if evidence.migrated_at is None:
        return _layer(6, "red", "no data:age", "pumpportal")
    minutes = Decimal(str((evidence.now - evidence.migrated_at).total_seconds())) / Decimal(60)
    if minutes < D(settings["auditor.min_pool_age_min"]):
        return _layer(6, "red", f"migration age {minutes.quantize(Decimal('0.01'))} min", "pumpportal")
    return _layer(6, "pass", f"migration age {minutes.quantize(Decimal('0.01'))} min", "pumpportal")


def _bundles(evidence: AuditEvidence, settings: dict) -> LayerResult:
    if not graph_compatible(evidence.graph):
        return _layer(7, "red", "no data:bundles", "rugcheck", evidence.provenance)
    size = biggest_holder_component(evidence.graph)
    limit = int(settings["auditor.max_bundled"])
    marker = " TESTDATA" if evidence.provenance == "testdata" else ""
    if size < limit:
        return _layer(7, "pass", f"bundles {size}{marker}", "rugcheck", evidence.provenance)
    return _layer(7, "red", f"bundles {size}", "rugcheck", evidence.provenance)


def _delegations(evidence: AuditEvidence, settings: dict) -> tuple[LayerResult, int | None]:
    if evidence.rpc_error:
        return _layer(8, "red", "no data:delegations", "rpc"), None
    if evidence.classic_accounts is None or evidence.token2022_accounts is None:
        return _layer(8, "red", "no data:delegations", "rpc"), None
    combined = list(evidence.classic_accounts) + list(evidence.token2022_accounts)
    summary = count_holders(combined)
    if not summary["ok"]:
        return _layer(8, "red", "no data:delegations", "rpc"), None
    holders = int(summary["holders"])
    maximum = int(summary["max_delegate"])
    limit = int(settings["auditor.max_delegated_accounts"])
    if maximum > limit:
        return _layer(8, "red", f"delegate covers {maximum} holder accounts", "rpc"), holders
    marker = " TESTDATA" if evidence.provenance == "testdata" else ""
    return _layer(8, "pass", f"holders {holders} delegated_max {maximum}{marker}", "rpc"), holders
