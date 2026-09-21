#!/bin/bash
# Build the semantic layer's contract views and make sure every reader sees them.
# Why the restart: after `CREATE OR REPLACE VIEW`, the Iceberg REST catalog kept returning the OLD view definition
# (SQLite held the new pointer; the REST server answered with the old one) until it was restarted. Tables are not affected.
# The mechanism is not understood; the restart is the verified workaround, and `agent.eval.preflight` catches a regression.
set -euo pipefail
cd "$(dirname "$0")/.."
( cd dbt_semantic && ../.venv/bin/dbt run --target build --profiles-dir . )
docker-compose restart iceberg-rest > /dev/null
for i in $(seq 1 40); do curl -sf -m 3 http://localhost:8181/v1/config > /dev/null && break; sleep 2; done
sleep 3
( cd dbt_semantic && ../.venv/bin/dbt parse --profiles-dir . > /dev/null && ../.venv/bin/mf validate-configs 2>&1 | grep -E "Successfully validated metrics|ERROR" | head -3 )
.venv/bin/python -m agent.eval.preflight
