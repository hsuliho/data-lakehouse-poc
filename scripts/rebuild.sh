#!/bin/bash
# Rebuild the whole warehouse from landing/, one day at a time, in date order. About 6 minutes.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python scripts/reset.py > /dev/null
.venv/bin/python scripts/load.py init
.venv/bin/python scripts/scd2.py init
scripts/replay.sh $(ls landing | sed 's/dt=//' | sort) | grep -E "FAILED|rror" || true
docker restart data-lakehouse-poc-iceberg-rest-1 > /dev/null      # the catalog serves stale pointers to recreated tables (REPORT stage 12)
until [ "$(docker inspect -f '{{.State.Health.Status}}' data-lakehouse-poc-iceberg-rest-1)" = healthy ]; do sleep 2; done
echo rebuilt
