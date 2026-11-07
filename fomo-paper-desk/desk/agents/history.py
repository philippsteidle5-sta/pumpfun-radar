"""Creator-history interface. Sparse snapshots are not a never-sold proof.

Equal balances do not mean sold 0. This module does not read a chain.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HistoryEvidence:
    case: str
    provenance: str
    coverage_complete: bool = False
    migration_boundary_included: bool = False
    inner_instructions_covered: bool = False
    creator_token_accounts_complete: bool = False
    no_pagination_gap: bool = False
    no_null_transaction: bool = False
    programs_known: bool = False
    closed_accounts_resolved: bool = False
    delegated_transfers_visible: bool = False
    decreased: bool = False
    sale_proven: bool = False
    baseline_raw: int | None = None
    current_raw: int | None = None
    proof_ref: str | None = None


@dataclass(frozen=True)
class HistoryVerdict:
    proven: bool
    reason: str
    testdata: bool
    operational_proof: bool


_ALWAYS_RED = {
    "equal_balances_only": "no data:dev",
    "intra_tx_out_and_back": "no data:dev",
    "delegated_transfer_wallet_not_signer": "no data:dev",
    "closed_account": "no data:dev",
    "paginated_gap": "no data:dev",
    "null_transaction": "no data:dev",
    "unknown_program": "no data:dev",
    "missing": "no data:dev",
}


def evaluate_history(evidence: HistoryEvidence | None) -> HistoryVerdict:
    if evidence is None:
        return HistoryVerdict(False, "no data:dev", False, False)
    forced = _ALWAYS_RED.get(evidence.case)
    if forced is not None:
        return HistoryVerdict(False, forced, evidence.provenance == "testdata", False)
    flags = (
        evidence.coverage_complete,
        evidence.migration_boundary_included,
        evidence.inner_instructions_covered,
        evidence.creator_token_accounts_complete,
        evidence.no_pagination_gap,
        evidence.no_null_transaction,
        evidence.programs_known,
        evidence.closed_accounts_resolved,
        evidence.delegated_transfers_visible,
    )
    if evidence.case != "complete" or not all(flags):
        return HistoryVerdict(False, "no data:dev", evidence.provenance == "testdata", False)
    if evidence.decreased and evidence.sale_proven:
        return HistoryVerdict(False, "dev sold", evidence.provenance == "testdata", False)
    if evidence.decreased:
        return HistoryVerdict(False, "balance decreased; sale not proven", evidence.provenance == "testdata", False)
    if evidence.provenance == "testdata":
        return HistoryVerdict(True, "history proven TESTDATA", True, False)
    if evidence.provenance == "complete-chain-evidence" and evidence.proof_ref:
        return HistoryVerdict(True, "history proven", False, True)
    return HistoryVerdict(False, "no data:dev", False, False)
