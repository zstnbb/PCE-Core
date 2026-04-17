#!/usr/bin/env bash
# Run PCE's P0 smoke suite locally on macOS / Linux.
#
# Mirrors .github/workflows/smoke.yml so "green here" == "green in CI".

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-python3}"
SKIP_INSTALL="${SKIP_INSTALL:-0}"

echo "==> PCE smoke suite"
echo "    repo:   $REPO_ROOT"
echo "    python: $PYTHON"

if [[ "$SKIP_INSTALL" != "1" ]]; then
  echo "==> Ensuring dependencies"
  "$PYTHON" -m pip install --quiet --disable-pip-version-check -r requirements.txt
fi

TMP_DATA="$(mktemp -d -t pce_smoke.XXXXXX)"
trap 'rm -rf "$TMP_DATA"' EXIT
export PCE_DATA_DIR="$TMP_DATA"

echo "==> Running tests/smoke/"
echo "    PCE_DATA_DIR=$TMP_DATA"

"$PYTHON" -m pytest tests/smoke -v --tb=short

echo "==> smoke OK"
