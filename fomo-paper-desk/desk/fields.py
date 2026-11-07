"""Parsers for observed public payloads. Absent keys stay absent. Null stays null.

Nothing in a fixture is an instruction. Names and metadata are untrusted strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from desk.money import D, BadNumber

MISSING = object()

PUMP_WS_URL = "wss://pumpportal.fun/api/data"
SUBSCRIBE_MIGRATION = {"method": "subscribeMigration"}
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
DEX_BATCH_LIMIT = 30
DEX_URL_PREFIX = "https://api.dexscreener.com/tokens/v1/solana/"
RUGCHECK_REPORT = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
RUGCHECK_GRAPH = "https://api.rugcheck.xyz/v1/tokens/{mint}/insiders/graph"


class SourceShapeError(ValueError):
    pass


@dataclass
class MigrationEvent:
    mint: str | None
    signature: str | None
    tx_type: str | None
    pool_label: str | None
    creator: str | None
    creator_key_present: bool
    pool_address: None = None

    @property
    def pool_label_is_address(self) -> bool:
        return False


@dataclass
class DexPair:
    pair_address: str
    mint: str
    name: str | None
    symbol: str | None
    dex_id: str
    price_usd: Decimal | None
    liquidity_usd: Decimal | None
    market_cap: Decimal | None
    volume_m5: Decimal | None
    volume_h1: Decimal | None
    volume_h24: Decimal | None
    price_change_m5: Decimal | None
    price_change_h1: Decimal | None
    price_change_h24: Decimal | None
    buys_m5: int | None
    sells_m5: int | None
    buys_h1: int | None
    sells_h1: int | None
    pair_created_at: datetime | None
    raw_keys_present: dict[str, bool] = field(default_factory=dict)


@dataclass
class MintInfo:
    structure_present: bool
    mint_authority_present: bool
    mint_authority: Any
    freeze_authority_present: bool
    freeze_authority: Any
    supply_raw: int | None
    decimals: int | None


@dataclass
class TokenAccount:
    pubkey: str
    owner: str | None
    mint: str | None
    amount_raw: int | None
    delegate_present: bool
    delegate: str | None
    parsed_ok: bool
    space: int | None
    reason: str | None = None


@dataclass
class LpView:
    market_found: bool
    lp_locked_pct: Decimal | None
    lp_locked: int | None
    lp_total_supply: int | None
    burned_pct: None
    keys_present: dict[str, bool]


@dataclass
class Top10:
    status: str  # ok | incomplete | no_supply
    pct: Decimal | None
    reason: str
    excluded_accounts: tuple[str, ...] = ()
    provenance: str = "unproven"


@dataclass(frozen=True)
class ExclusionProof:
    """Pool and curve token accounts that may be removed from the top-10 sum.

    A pair-address label, or an owner string that merely equals that label, is
    not proof. `proven` is true only for an explicit account list with a method
    other than a label guess.
    """

    pool_token_accounts: tuple[str, ...]
    curve_token_accounts: tuple[str, ...]
    method: str
    provenance: str

    @property
    def proven(self) -> bool:
        return self.method in {"testdata-explicit", "rpc-account-owner"} and self.provenance in {
            "testdata",
            "observed",
        }


def parse_migration(message: dict) -> MigrationEvent:
    if not isinstance(message, dict):
        raise SourceShapeError("migration message is not an object")
    return MigrationEvent(
        mint=_optional_str(message, "mint"),
        signature=_optional_str(message, "signature"),
        tx_type=_optional_str(message, "txType"),
        pool_label=_optional_str(message, "pool"),
        creator=_optional_str(message, "creator"),
        creator_key_present="creator" in message,
        pool_address=None,
    )


def _optional_str(message: dict, key: str) -> str | None:
    if key not in message:
        return None
    value = message[key]
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return value


def _dex_decimal(container: dict, key: str) -> Decimal | None:
    if key not in container or container[key] is None:
        return None
    try:
        return D(container[key])
    except BadNumber:
        return None


def _dex_int(container: dict, key: str) -> int | None:
    if not isinstance(container, dict) or key not in container or container[key] is None:
        return None
    value = container[key]
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def parse_pair(row: dict) -> DexPair | None:
    if not isinstance(row, dict):
        return None
    base = row.get("baseToken") if isinstance(row.get("baseToken"), dict) else {}
    mint = base.get("address") if isinstance(base.get("address"), str) else None
    pair_address = row.get("pairAddress") if isinstance(row.get("pairAddress"), str) else None
    dex_id = row.get("dexId") if isinstance(row.get("dexId"), str) else None
    if not mint or not pair_address or not dex_id:
        return None
    liquidity = row.get("liquidity") if isinstance(row.get("liquidity"), dict) else {}
    volume = row.get("volume") if isinstance(row.get("volume"), dict) else {}
    change = row.get("priceChange") if isinstance(row.get("priceChange"), dict) else {}
    txns = row.get("txns") if isinstance(row.get("txns"), dict) else {}
    m5 = txns.get("m5") if isinstance(txns.get("m5"), dict) else {}
    h1 = txns.get("h1") if isinstance(txns.get("h1"), dict) else {}
    created = row.get("pairCreatedAt") if "pairCreatedAt" in row else None
    created_at = None
    if isinstance(created, (int, float)) and not isinstance(created, bool):
        created_at = datetime.fromtimestamp(created / 1000, tz=timezone.utc)
    return DexPair(
        pair_address=pair_address,
        mint=mint,
        name=base.get("name") if isinstance(base.get("name"), str) else None,
        symbol=base.get("symbol") if isinstance(base.get("symbol"), str) else None,
        dex_id=dex_id,
        price_usd=_dex_decimal(row, "priceUsd"),
        liquidity_usd=_dex_decimal(liquidity, "usd"),
        market_cap=_dex_decimal(row, "marketCap"),
        volume_m5=_dex_decimal(volume, "m5"),
        volume_h1=_dex_decimal(volume, "h1"),
        volume_h24=_dex_decimal(volume, "h24"),
        price_change_m5=_dex_decimal(change, "m5"),
        price_change_h1=_dex_decimal(change, "h1"),
        price_change_h24=_dex_decimal(change, "h24"),
        buys_m5=_dex_int(m5, "buys"),
        sells_m5=_dex_int(m5, "sells"),
        buys_h1=_dex_int(h1, "buys"),
        sells_h1=_dex_int(h1, "sells"),
        pair_created_at=created_at,
    )


def iter_pairs(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("pairs"), list):
        return [row for row in payload["pairs"] if isinstance(row, dict)]
    return []


def select_pumpswap_pair(payload: Any, mint: str) -> DexPair | None:
    best: DexPair | None = None
    for row in iter_pairs(payload):
        pair = parse_pair(row)
        if pair is None or pair.mint != mint or pair.dex_id != "pumpswap":
            continue
        if pair.liquidity_usd is None:
            continue
        if best is None or pair.liquidity_usd > best.liquidity_usd:
            best = pair
    return best


def parse_mint_info(payload: Any) -> MintInfo:
    try:
        info = payload["result"]["value"]["data"]["parsed"]["info"]
    except (KeyError, TypeError, IndexError):
        return MintInfo(False, False, MISSING, False, MISSING, None, None)
    if not isinstance(info, dict) or payload["result"]["value"]["data"]["parsed"].get("type") != "mint":
        return MintInfo(False, False, MISSING, False, MISSING, None, None)
    supply = None
    if "supply" in info and info["supply"] is not None:
        try:
            supply = int(info["supply"])
        except (TypeError, ValueError):
            supply = None
    decimals = info.get("decimals") if isinstance(info.get("decimals"), int) else None
    return MintInfo(
        structure_present=True,
        mint_authority_present="mintAuthority" in info,
        mint_authority=info["mintAuthority"] if "mintAuthority" in info else MISSING,
        freeze_authority_present="freezeAuthority" in info,
        freeze_authority=info["freezeAuthority"] if "freezeAuthority" in info else MISSING,
        supply_raw=supply,
        decimals=decimals,
    )


def parse_token_accounts(payload: Any) -> list[TokenAccount]:
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        raise SourceShapeError("getProgramAccounts result is not a list")
    accounts: list[TokenAccount] = []
    for row in payload["result"]:
        if not isinstance(row, dict):
            accounts.append(_bad_account("", "row is not an object"))
            continue
        pubkey = row.get("pubkey") if isinstance(row.get("pubkey"), str) else ""
        account = row.get("account") if isinstance(row.get("account"), dict) else {}
        data = account.get("data")
        space = account.get("space") if isinstance(account.get("space"), int) else None
        if not isinstance(data, dict) or "parsed" not in data:
            accounts.append(_bad_account(pubkey, "base64 or unparsed data is not token-account proof", space))
            continue
        parsed = data.get("parsed")
        if not isinstance(parsed, dict) or parsed.get("type") != "account" or not isinstance(parsed.get("info"), dict):
            accounts.append(_bad_account(pubkey, "parsed type is not a token account", space))
            continue
        info = parsed["info"]
        amount = None
        token_amount = info.get("tokenAmount") if isinstance(info.get("tokenAmount"), dict) else None
        if token_amount and "amount" in token_amount:
            try:
                amount = int(token_amount["amount"])
            except (TypeError, ValueError):
                amount = None
        delegate_present = "delegate" in info
        delegate = info.get("delegate") if isinstance(info.get("delegate"), str) else None
        accounts.append(
            TokenAccount(
                pubkey=pubkey,
                owner=info.get("owner") if isinstance(info.get("owner"), str) else None,
                mint=info.get("mint") if isinstance(info.get("mint"), str) else None,
                amount_raw=amount,
                delegate_present=delegate_present,
                delegate=delegate,
                parsed_ok=True,
                space=space,
            )
        )
    return accounts


def _bad_account(pubkey: str, reason: str, space: int | None = None) -> TokenAccount:
    return TokenAccount(pubkey, None, None, None, False, None, False, space, reason)


def count_holders(accounts: list[TokenAccount], mint: str | None = None) -> dict:
    """Positive token accounts, not unique wallets. dataSize is not a filter."""
    if any(not account.parsed_ok for account in accounts):
        return {"ok": False, "reason": "no data:delegations", "holders": None, "max_delegate": None}
    positive = []
    for account in accounts:
        if mint is not None and account.mint != mint:
            return {"ok": False, "reason": "no data:delegations", "holders": None, "max_delegate": None}
        if account.amount_raw is None:
            return {"ok": False, "reason": "no data:delegations", "holders": None, "max_delegate": None}
        if account.amount_raw > 0:
            positive.append(account)
    groups: dict[str, int] = {}
    for account in positive:
        if account.delegate_present and account.delegate:
            groups[account.delegate] = groups.get(account.delegate, 0) + 1
    max_delegate = max(groups.values()) if groups else 0
    spaces: dict[int, int] = {}
    for account in accounts:
        if account.space is not None:
            spaces[account.space] = spaces.get(account.space, 0) + 1
    return {
        "ok": True,
        "holders": len(positive),
        "accounts": len(accounts),
        "max_delegate": max_delegate,
        "delegate_groups": groups,
        "spaces": spaces,
        "unique_owners": len({a.owner for a in positive if a.owner}),
    }


def parse_lp(report: Any, pool_address: str | None) -> LpView:
    empty = LpView(False, None, None, None, None, {"markets": False, "lpLockedPct": False, "burned": False})
    if not isinstance(report, dict) or not isinstance(report.get("markets"), list):
        return empty
    for market in report["markets"]:
        if not isinstance(market, dict):
            continue
        if pool_address is None or market.get("pubkey") != pool_address:
            continue
        lp = market.get("lp") if isinstance(market.get("lp"), dict) else {}
        locked_pct = None
        if "lpLockedPct" in lp and lp["lpLockedPct"] is not None:
            try:
                locked_pct = D(lp["lpLockedPct"])
            except BadNumber:
                locked_pct = None
        locked = lp.get("lpLocked") if isinstance(lp.get("lpLocked"), int) else None
        supply = lp.get("lpTotalSupply") if isinstance(lp.get("lpTotalSupply"), int) else None
        return LpView(
            market_found=True,
            lp_locked_pct=locked_pct,
            lp_locked=locked,
            lp_total_supply=supply,
            burned_pct=None,
            keys_present={
                "markets": True,
                "lpLockedPct": "lpLockedPct" in lp,
                "lpLocked": "lpLocked" in lp,
                "lpTotalSupply": "lpTotalSupply" in lp,
                "burned": False,
            },
        )
    return LpView(False, None, None, None, None, {"markets": True, "matched": False, "burned": False})


def creator_from_report(report: Any) -> tuple[str | None, bool]:
    if not isinstance(report, dict) or "creator" not in report:
        return None, False
    creator = report.get("creator")
    if not isinstance(creator, str) or not creator:
        return None, True
    return creator, True


def graph_compatible(payload: Any) -> bool:
    """Only an explicit holder-edges object with a proven complete edge set.

    Observed payloads are either [] or [{nodes, links}]. Those have no edges
    key. links are not rewritten into edges. An empty edges list does not prove
    the absence of a bundle.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("schema") != "holder-edges-v1":
        return False
    if "edges" not in payload or not isinstance(payload.get("edges"), list):
        return False
    if not payload.get("completeness_proven"):
        return False
    if not isinstance(payload.get("holders"), list):
        return False
    return True


def biggest_holder_component(payload: dict) -> int:
    holders = [item for item in payload.get("holders", []) if isinstance(item, str)]
    parent = {holder: holder for holder in holders}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        if left not in parent or right not in parent:
            return
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    for edge in payload.get("edges", []):
        if not isinstance(edge, dict):
            continue
        union(edge.get("source"), edge.get("target"))
    sizes: dict[str, int] = {}
    for holder in holders:
        root = find(holder)
        sizes[root] = sizes.get(root, 0) + 1
    return max(sizes.values()) if sizes else 0


_EMOJI_AND_NOISE = re.compile(r"[^a-z]+")


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    lowered = name.lower()
    lowered = re.sub(r"\s+", "", lowered)
    lowered = re.sub(r"\d+", "", lowered)
    return _EMOJI_AND_NOISE.sub("", lowered)


def copycat_of(name: str | None, runners: list[tuple[str, str]]) -> str | None:
    """runners are (mint, display name) already known as h24 runners, other mints.

    Empty normalized names match nothing. Match is exact or either substring.
    """
    norm = normalize_name(name)
    if not norm:
        return None
    for _mint, runner_name in runners:
        other = normalize_name(runner_name)
        if not other:
            continue
        if norm == other or norm in other or other in norm:
            return runner_name
    return None


def top10_pct(accounts: list[TokenAccount] | None, supply_raw: int | None, proof: ExclusionProof | None) -> Top10:
    if supply_raw is None:
        return Top10("no_supply", None, "no supply")
    if supply_raw <= 0:
        return Top10("no_supply", None, "no supply")
    if proof is None or not proof.proven:
        return Top10("incomplete", None, "no data:top10", provenance="missing-exclusion-proof")
    if accounts is None:
        return Top10("incomplete", None, "no data:top10", provenance="missing-accounts")
    if any(not account.parsed_ok or account.amount_raw is None for account in accounts):
        return Top10("incomplete", None, "no data:top10", provenance="unparsed-accounts")
    excluded = set(proof.pool_token_accounts) | set(proof.curve_token_accounts)
    ranked = sorted(
        (account for account in accounts if account.pubkey not in excluded and (account.amount_raw or 0) > 0),
        key=lambda account: account.amount_raw or 0,
        reverse=True,
    )
    chosen = ranked[:10]
    total = sum(account.amount_raw or 0 for account in chosen)
    pct = (Decimal(total) / Decimal(supply_raw)) * Decimal(100)
    return Top10("ok", pct, "ok", tuple(sorted(excluded)), provenance=proof.provenance)


def volume_m5_delta(previous: Decimal | None, current: Decimal | None) -> Decimal | None:
    """Dex volume.m5 is a rolling window, not a candle. The delta may be negative."""
    if previous is None or current is None:
        return None
    return current - previous


def aggregate_candles(rows: list[dict]) -> list[dict]:
    """1-minute OHLC from our own price rows.

    volume_m5_last is the last Dex snapshot in the minute. It is not trade
    volume. volume_m5_delta is the last stored snapshot delta, which can be
    negative when the Dex window rolls.
    """
    buckets: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        moment = _parse_iso(row["ts"])
        key = moment.strftime("%Y-%m-%dT%H:%M:00Z")
        price = D(row["price"])
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "t": key,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume_m5_last": _maybe_decimal(row.get("volume_m5")),
                "volume_m5_delta": _maybe_decimal(row.get("volume_m5_delta")),
                "n": 1,
            }
            buckets[key] = bucket
            order.append(key)
            continue
        bucket["high"] = max(bucket["high"], price)
        bucket["low"] = min(bucket["low"], price)
        bucket["close"] = price
        bucket["n"] += 1
        if row.get("volume_m5") is not None:
            bucket["volume_m5_last"] = _maybe_decimal(row.get("volume_m5"))
        if "volume_m5_delta" in row:
            bucket["volume_m5_delta"] = _maybe_decimal(row.get("volume_m5_delta"))
    rendered = []
    for key in order:
        bucket = buckets[key]
        rendered.append(
            {
                "t": bucket["t"],
                "open": format(bucket["open"], "f"),
                "high": format(bucket["high"], "f"),
                "low": format(bucket["low"], "f"),
                "close": format(bucket["close"], "f"),
                "volume_m5_last": None if bucket["volume_m5_last"] is None else format(bucket["volume_m5_last"], "f"),
                "volume_m5_delta": None
                if bucket["volume_m5_delta"] is None
                else format(bucket["volume_m5_delta"], "f"),
                "trades": None,
                "note": "volume_m5 is a Dex snapshot, not summed trade volume",
            }
        )
    return rendered


def _maybe_decimal(value):
    if value is None:
        return None
    return D(value)


def _parse_iso(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError("naive timestamp")
    return moment.astimezone(timezone.utc)


def fmt_liq_reason(liquidity: Decimal) -> str:
    if liquidity >= 1000:
        return f"liq ${liquidity / Decimal(1000):.1f}K"
    return f"liq ${liquidity:.0f}"


def program_account_request(program: str, mint: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getProgramAccounts",
        "params": [
            program,
            {
                "encoding": "jsonParsed",
                "filters": [{"memcmp": {"offset": 0, "bytes": mint}}],
            },
        ],
    }
