#!/bin/bash
# DataHub v1.7.0.1 (pinned in .env), run as its own compose project so it can be stopped separately from the lakehouse.
# usage: datahub/dh.sh up | down | ps | logs <service> | pull   (down keeps the volumes; add -v yourself to wipe them)
cd "$(dirname "$0")"
exec docker-compose -p datahub -f docker-compose.quickstart.yml -f restart.override.yml --env-file .env --profile quickstart "$@"
