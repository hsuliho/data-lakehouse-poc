#!/bin/bash
# usage: replay.sh <YYYY-MM-DD>...
# per delivery day: raw + current state (Spark) -> SCD2 rebuild of the touched keys (Spark SQL) -> dbt-spark models. Stops at the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."
for d in "$@"; do
  echo "=== dt=$d"
  .venv/bin/python scripts/load.py deliver "$d" | tail -4 | awk '{print "  " $0}'
  .venv/bin/python scripts/scd2.py run "$d"
  ( cd dbt_spark && ../.venv/bin/dbt run --profiles-dir . > /tmp/dbt_spark.out 2>&1 ) || { sed 's/\x1b\[[0-9;]*m//g' /tmp/dbt_spark.out | grep -E "rror|failed" | cut -c1-300 | head -8; echo "FAILED: dbt-spark"; exit 1; }
  sed 's/\x1b\[[0-9;]*m//g' /tmp/dbt_spark.out | grep -E " OK created sql (incremental|table)|PASS=" | sed -E 's/^[0-9:]+ +[0-9]+ of [0-9]+ OK created sql //'
done
