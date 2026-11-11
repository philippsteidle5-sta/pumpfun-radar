"""Real public fixtures stay red. Counts are observations, not a passing audit."""

import json
from datetime import datetime, timezone
from pathlib import Path

from desk.agents.auditor import AuditEvidence, evaluate_audit
from desk.config import defaults
from desk.fields import (
    LpView,
    Top10,
    count_holders,
    graph_compatible,
    normalize_name,
    parse_lp,
    parse_migration,
    parse_mint_info,
    parse_token_accounts,
    program_account_request,
    select_pumpswap_pair,
    copycat_of,
)
from desk.fields import TOKEN_2022_PROGRAM, TOKEN_PROGRAM

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "public-market-sources"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
MINT = "DKtofLM6bhb54GQy5YFSWQjQmGiZqAkjzkQJ1LTopump"


def load(name: str):
    return json.loads((ROOT / name).read_text())


def test_saved_market_files_do_not_become_a_pass():
    migration = parse_migration(load("pumpportal_migration.json"))
    assert migration.mint == "FHCntFVzzGmbcUNmf1jBGaBsi4duboB1cwjg4yrcpump"
    assert migration.pool_label == "pump-amm"
    assert migration.creator_key_present is False
    assert migration.pool_address is None

    pair = select_pumpswap_pair(load("dex_tokens.json"), MINT)
    assert pair is not None
    assert pair.pair_address == "ANS69UDTbMCWbW7ZuCYXLZ4HMCQkvzPMtCW9zjPZQGKR"
    assert pair.name == "Diamond Hands"
    assert pair.symbol == "HODL"
    assert str(pair.price_usd) == "0.004010"
    assert pair.liquidity_usd == __import__("decimal").Decimal("165922.11")

    report = load("rugcheck_report.json")
    graph = load("rugcheck_graph.json")
    assert graph == []
    assert graph_compatible(graph) is False
    probe = load("gap-investigation/probe2_graph.json")
    assert isinstance(probe, list)
    assert graph_compatible(probe) is False

    info = parse_mint_info(load("rpc_getAccountInfo.json"))
    assert info.mint_authority_present is True
    assert info.mint_authority is None
    assert info.freeze_authority is None
    assert info.supply_raw == 995525164373836

    classic = parse_token_accounts(load("rpc_program_classic.json"))
    token2022 = parse_token_accounts(load("rpc_program_2022.json"))
    summary = count_holders(classic + token2022, MINT)
    assert summary["ok"] is True
    assert len(classic) == 0
    assert summary["accounts"] == 9420
    assert summary["holders"] == 9113
    assert summary["unique_owners"] == 9113
    assert summary["max_delegate"] == 0
    assert summary["spaces"][170] == 9417
    assert summary["spaces"][165] == 2
    request = program_account_request(TOKEN_2022_PROGRAM, MINT)
    assert "dataSize" not in json.dumps(request)
    assert request["params"][1]["filters"] == [{"memcmp": {"offset": 0, "bytes": MINT}}]
    assert request["params"][0] == TOKEN_2022_PROGRAM
    classic_request = program_account_request(TOKEN_PROGRAM, MINT)
    assert classic_request["params"][0] == TOKEN_PROGRAM

    lp = parse_lp(report, pair.pair_address)
    assert lp.market_found is True
    assert lp.lp_locked_pct == 100
    assert lp.burned_pct is None
    evidence = AuditEvidence(
        mint_info=info,
        creator=report.get("creator"),
        creator_key_present="creator" in report,
        history=None,
        current_balance_raw=None,
        top10=Top10("incomplete", None, "no data:top10", provenance="missing-exclusion-proof"),
        lp=lp,
        scanner_liquidity=pair.liquidity_usd,
        fresh_liquidity=pair.liquidity_usd,
        migrated_at=None,
        now=NOW,
        graph=graph,
        classic_accounts=classic,
        token2022_accounts=token2022,
        provenance="fixture",
    )
    result = evaluate_audit(evidence, defaults())
    assert result.passed is False
    assert result.testdata is False
    assert result.layers[0].value == "Revoked"
    assert result.layers[1].value == "Revoked"
    assert result.layers[2].value == "no data:dev"
    assert result.layers[2].status == "red"
    assert result.layers[7].status == "unchecked"
    assert "sold 0" not in (result.reason or "")
    assert "sold0" not in (result.reason or "")
    assert normalize_name("Diamond Hands 2") == "diamondhands"
    assert copycat_of("diamond hands", [(MINT, "Diamond Hands")]) == "Diamond Hands"
    assert copycat_of("", [(MINT, "Diamond Hands")]) is None
    assert isinstance(lp, LpView)
