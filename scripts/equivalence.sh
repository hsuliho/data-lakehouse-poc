#!/bin/bash
# Rebuild the warehouse twice from scratch and compare every table:
#   sequential = every delivery day in date order
#   permuted   = the same three days in reverse order (the out-of-order 'shipped' of 01-31 arrives before the 'completed' of 01-29)
# dbt runs after every day in both, so the incremental logic is what is being compared. About 6 minutes.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .equivalence
days=$(ls landing | sed 's/dt=//' | sort)
rebuild() {
  .venv/bin/python scripts/reset.py > /dev/null
  .venv/bin/python scripts/load.py init
  .venv/bin/python scripts/scd2.py init
  scripts/replay.sh "$@" | grep -E "FAILED|rror" || true
  docker restart data-lakehouse-poc-iceberg-rest-1 > /dev/null   # the catalog serves stale pointers to recreated tables (REPORT stage 12)
  until [ "$(docker inspect -f '{{.State.Health.Status}}' data-lakehouse-poc-iceberg-rest-1)" = healthy ]; do sleep 2; done
}
echo "== sequential"; rebuild $days
.venv/bin/python scripts/check_equivalence.py export .equivalence/sequential.json
echo "== permuted"; rebuild 2026-01-31 2026-01-30 2026-01-29
.venv/bin/python scripts/check_equivalence.py export .equivalence/permuted.json
.venv/bin/python scripts/check_equivalence.py compare .equivalence/sequential.json .equivalence/permuted.json
