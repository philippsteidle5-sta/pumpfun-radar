#!/bin/sh
# Offline checks for a unpacked desk bundle. No network and no token.
set -eu
cd "$(dirname "$0")/.."
export PYTHONPATH=.
python3 -m desk.main --check
python3 -m pytest tests -o addopts= --tb=line -q
echo "verify ok"
