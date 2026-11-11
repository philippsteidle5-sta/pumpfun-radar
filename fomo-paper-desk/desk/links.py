"""The only FOMO URL builder. Other modules call these functions."""

from __future__ import annotations

import logging
import re

CHAIN_ID = {
    "solana": 1399811149,
    "robinhood": 4663,
    "base": 8453,
    "bnb": 56,
}
SIGNUP_URL = "https://fomo.family/r/savipww"

# Base58 without 0, O, I, l. Valid addresses stay literal in the query.
_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
# After stripping leading @. Rejects path or query injection.
_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,32}$")

log = logging.getLogger("desk.links")


def coin_url(address: str, chain: str = "solana") -> str:
    if chain not in CHAIN_ID:
        log.info("unknown chain %s; using signup url", chain)
        return SIGNUP_URL
    if not isinstance(address, str) or _ADDRESS.fullmatch(address) is None:
        log.info("invalid coin address; using signup url")
        return SIGNUP_URL
    return (
        f"https://fomo.family/coin?address={address}"
        f"&chainId={CHAIN_ID[chain]}&r=savipww&source=share_link"
    )


def profile_url(handle: str) -> str:
    if not isinstance(handle, str):
        log.info("invalid profile handle; using signup url")
        return SIGNUP_URL
    h = handle.lstrip("@")
    if _HANDLE.fullmatch(h) is None:
        log.info("invalid profile handle; using signup url")
        return SIGNUP_URL
    return f"https://fomo.family/profile/{h}?source=share_link&r=savipww"
