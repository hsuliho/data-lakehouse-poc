#!/bin/bash
# Refresh the catalog from the current warehouse and dbt project, then check the PII columns still agree.
# Order matters: `dbt docs generate` overwrites run_results.json, so the test run and the freshness run come last.
# `dbt test` exits 1 while the planted dirty data still fails, which must not stop the refresh.
set -uo pipefail
cd "$(dirname "$0")/.."
# the refresh runs dbt in dbt_spark/, and so does every Dagster run: at the same time they disturbed each other (a no-fault rerun failed two checks once)
busy=$(docker exec data-lakehouse-poc-dagster-daemon-1 python -m orchestration.state runs 2>/dev/null | .venv/bin/python -c "import json,sys;print(sum(r['status'] in ('STARTED','STARTING','QUEUED') for r in json.load(sys.stdin)))" 2>/dev/null || echo 0)
if [ "$busy" != "0" ]; then echo "REFUSING: $busy Dagster run(s) in progress or queued; refresh after they finish"; exit 1; fi
( cd dbt_spark
  ../.venv/bin/dbt docs generate --profiles-dir . > /tmp/refresh_docs.log 2>&1 || { echo "dbt docs generate failed"; exit 1; }
  ../.venv/bin/dbt test --profiles-dir . > /tmp/refresh_test.log 2>&1
  ../.venv/bin/dbt source freshness --profiles-dir . > /tmp/refresh_fresh.log 2>&1 || { echo "dbt source freshness failed"; exit 1; } )
sed 's/\x1b\[[0-9;]*m//g' /tmp/refresh_test.log | grep -E "Done\." | tail -1
# the semantic layer's own dbt project (Trino adapter); `build` = engineer, needed to read the catalog
( cd dbt_semantic && ../.venv/bin/dbt docs generate --target build --profiles-dir . > /tmp/refresh_sem.log 2>&1 ) || { echo "dbt_semantic docs generate failed"; exit 1; }
datahub/ingest.sh iceberg > /tmp/refresh_ingest_iceberg.log 2>&1 && echo "ingested iceberg: $(grep -o 'Pipeline finished[^.]*' /tmp/refresh_ingest_iceberg.log | tail -1)"
datahub/ingest.sh dbt > /tmp/refresh_ingest_dbt.log 2>&1 && echo "ingested dbt: $(grep -o 'Pipeline finished[^.]*' /tmp/refresh_ingest_dbt.log | tail -1)"
datahub/ingest.sh dbt_semantic > /tmp/refresh_ingest_sem.log 2>&1 && echo "ingested dbt_semantic: $(grep -o 'Pipeline finished[^.]*' /tmp/refresh_ingest_sem.log | tail -1)"
.venv/bin/python scripts/check_pii_consistency.py --datahub | tail -1
