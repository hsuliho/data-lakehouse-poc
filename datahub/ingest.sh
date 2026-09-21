#!/bin/bash
# usage: datahub/ingest.sh iceberg|dbt|dbt_semantic    (runs the pinned ingestion image on the host network so localhost:8181/9000/8082 work)
set -euo pipefail
cd "$(dirname "$0")/.."
exec docker run --rm --network host --entrypoint datahub \
  -v "$PWD/datahub/recipes:/recipes:ro" -v "$PWD/dbt_spark/target:/dbt:ro" -v "$PWD/dbt_semantic/target:/dbt_semantic:ro" \
  acryldata/datahub-ingestion:v1.7.0.1 ingest -c "/recipes/$1.yml"
