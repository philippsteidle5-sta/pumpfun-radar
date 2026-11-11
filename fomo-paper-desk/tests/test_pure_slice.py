"""First offline slice: links, rules gate, ticket sizing, risk ticks."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from desk.config import defaults
from desk.agents.analyst import compute_rules, judge, score_of
from desk.agents.executor import plan_size
from desk.agents.risk import evaluate_tick, stop_price_for, tighten_stop_pct
from desk.links import CHAIN_ID, SIGNUP_URL, coin_url, profile_url

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
MINT = "DKtofLM6bhb54GQy5YFSWQjQmGiZqAkjzkQJ1LTopump"


def settings():
    return defaults()


def test_links_exact_referral_and_fallbacks():
    url = coin_url(MINT, "solana")
    assert url == (
        "https://fomo.family/coin?address="
        f"{MINT}&chainId={CHAIN_ID['solana']}&r=savipww&source=share_link"
    )
    assert coin_url(MINT, "base") == (
        "https://fomo.family/coin?address="
        f"{MINT}&chainId={CHAIN_ID['base']}&r=savipww&source=share_link"
    )
    assert coin_url(MINT, "ethereum") == SIGNUP_URL
    assert coin_url("mint&chainId=1", "solana") == SIGNUP_URL
    assert coin_url("not a mint", "solana") == SIGNUP_URL
    assert profile_url("@Staticroom") == profile_url("Staticroom")
    assert profile_url("Staticroom") == (
        "https://fomo.family/profile/Staticroom?source=share_link&r=savipww"
    )
    assert profile_url("../admin") == SIGNUP_URL
    assert profile_url("a/b") == SIGNUP_URL
    assert "savipww" in url
    assert "bit.ly" not in url
    assert "APP_TOKEN" not in url


def test_gate_just_below_fails_and_threshold_passes():
    cfg = settings()
    fail = judge(Decimal("0.69"), Decimal("0.64"), Decimal("0.74"), Decimal("0.31"), cfg)
    assert fail.score == 69
    assert fail.passed is False
    assert any(item.startswith("alive_6h") for item in fail.failed)
    assert any(item.startswith("organic") for item in fail.failed)
    assert any(item.startswith("chase") for item in fail.failed)
    assert any(item.startswith("score") for item in fail.failed)

    ok = judge(Decimal("0.70"), Decimal("0.65"), Decimal("0.75"), Decimal("0.30"), cfg)
    assert ok.score == 70
    assert ok.passed is True
    assert ok.failed == []
    assert ok.model == "rules"
    assert ok.input_tokens == 0
    assert score_of(Decimal("1"), Decimal("0.5"), Decimal("0.5"), Decimal("0.22")) == 70
    assert score_of(Decimal("1"), Decimal("0.5"), Decimal("0.5"), Decimal("0.26")) == 68


def test_rules_from_fake_dex_labels_and_missing_data():
    cfg = settings()
    passed = compute_rules(
        {
            "volume_h1": "17500",
            "buys_h1": "97.5",
            "buys_m5": "10",
            "sells_m5": "10",
            "price_change_m5": "-5",
            "price_change_h1": "24",
        },
        bundles=0,
        settings=cfg,
    )
    assert passed.passed is True
    assert passed.answers["alive_6h"] == Decimal("0.70")
    assert passed.answers["organic"] == Decimal("0.65")
    assert passed.answers["flush"] == Decimal("1")
    assert passed.answers["chase"] == Decimal("0.30")
    assert passed.model == "rules"
    assert passed.input_tokens == 0

    penalized = compute_rules(
        {
            "volume_h1": "25000",
            "buys_h1": "150",
            "buys_m5": "1",
            "sells_m5": "1",
            "price_change_m5": "-5",
            "price_change_h1": "0",
        },
        bundles=3,
        settings=cfg,
    )
    assert penalized.answers["organic"] == Decimal("0.5")
    assert penalized.passed is False
    assert penalized.failed[0].startswith("organic")

    missing = compute_rules({"volume_h1": None, "buys_h1": "150"}, bundles=0, settings=cfg)
    assert missing.passed is False
    assert missing.score is None
    assert missing.answers is None
    assert missing.failed[0] == "missing:volume.h1"
    assert "0" not in missing.reason


def test_executor_bump_skip_and_hard_cap():
    cfg = settings()
    bumped = plan_size(stack="200", free="200", pool_liquidity="40000", settings=cfg)
    assert bumped.action == "size"
    assert bumped.raw == Decimal("16.00")
    assert bumped.cap == Decimal("800.00")
    assert bumped.size == Decimal("47.50")
    assert bumped.stack_pct == Decimal("23.75")

    cfg_skip = dict(cfg)
    cfg_skip["executor.below_min"] = "skip"
    skipped = plan_size(stack="200", free="200", pool_liquidity="40000", settings=cfg_skip)
    assert skipped.action == "skip"
    assert skipped.reason == "below min ticket"

    capped = plan_size(stack="200", free="200", pool_liquidity="2000", settings=cfg)
    assert capped.cap == Decimal("40.00")
    assert capped.action == "skip"
    assert capped.reason == "bump exceeds pool cap"

    free_short = plan_size(stack="200", free="40", pool_liquidity="40000", settings=cfg)
    assert free_short.action == "skip"
    assert free_short.reason == "bump exceeds free"

    hard = plan_size(stack="100000", free="100000", pool_liquidity="40000", settings=cfg)
    assert hard.action == "size"
    assert hard.raw == Decimal("8000.00")
    assert hard.size == Decimal("800.00")


def test_risk_trail_stop_timeout_and_no_loosen():
    cfg = settings()
    entry = Decimal("100")
    stop = stop_price_for(entry, cfg["risk.stop_pct"])
    assert stop == Decimal("94.00")
    opened = NOW - timedelta(minutes=10)

    up = evaluate_tick(
        entry=entry,
        peak=entry,
        stop_price=stop,
        trail_active=False,
        price=Decimal("125"),
        opened_at=opened,
        now=NOW,
        settings=cfg,
    )
    assert up.action is None
    assert up.trail_active is True
    assert up.peak == Decimal("125")

    trail = evaluate_tick(
        entry=entry,
        peak=up.peak,
        stop_price=stop,
        trail_active=True,
        price=Decimal("108.75"),
        opened_at=opened,
        now=NOW,
        settings=cfg,
    )
    assert trail.action == "trail"

    stopped = evaluate_tick(
        entry=entry,
        peak=entry,
        stop_price=stop,
        trail_active=False,
        price=entry * Decimal("0.939"),
        opened_at=opened,
        now=NOW,
        settings=cfg,
    )
    assert stopped.action == "stop"

    timed = evaluate_tick(
        entry=entry,
        peak=entry,
        stop_price=stop,
        trail_active=False,
        price=Decimal("104"),
        opened_at=NOW - timedelta(hours=3),
        now=NOW,
        settings=cfg,
    )
    assert timed.action == "timeout"
    held = evaluate_tick(
        entry=entry,
        peak=entry,
        stop_price=stop,
        trail_active=False,
        price=Decimal("105"),
        opened_at=NOW - timedelta(hours=3),
        now=NOW,
        settings=cfg,
    )
    assert held.action is None

    assert tighten_stop_pct(Decimal("6"), Decimal("8")) == Decimal("6")
    assert tighten_stop_pct(Decimal("6"), Decimal("4")) == Decimal("4")
    assert stop_price_for(entry, Decimal("8")) == Decimal("92.00")
    assert stop_price_for(entry, Decimal("6")) == Decimal("94.00")
