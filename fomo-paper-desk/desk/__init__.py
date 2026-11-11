"""FOMO paper desk. Simulated coordination only. No orders, no live send."""

__version__ = "0.1.0"

# This chunk cannot activate LIVE, even if a database row is spoofed.
CHUNK_ALLOWS_LIVE = False
