#!/bin/bash
# usage: scripts/dagster.sh status | logs | cli <dagster args...> | clock <ISO time|real>
# Dagster runs as two docker-compose services (dagster-daemon, dagster-webserver, http://localhost:3000); `docker-compose up -d` starts everything.
# Its state is in the named volume dagster-home. `clock` restarts the daemon with a fixed clock (LAKEHOUSE_NOW) for tests, `real` puts the real one back.
set -euo pipefail
cd "$(dirname "$0")/.."
D=data-lakehouse-poc-dagster-daemon-1
case "${1:-status}" in
  status) docker exec $D python -m orchestration.state partitions | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin)['completed_dates'];print(f'completed dates: {len(d)}', d[0] if d else '', '..', d[-1] if d else '')"
          docker exec $D python -m orchestration.state runs | .venv/bin/python -c "import json,sys;[print('  ',r['run_id'],r['status'].ljust(10),r['partition']) for r in json.load(sys.stdin)]" ;;
  logs)   docker logs --tail 60 $D ;;
  cli)    shift; docker exec $D dagster "$@" ;;
  clock)  if [ "$2" = real ]; then LAKEHOUSE_NOW= docker-compose up -d --force-recreate dagster-daemon dagster-webserver
          else LAKEHOUSE_NOW="$2" docker-compose up -d --force-recreate dagster-daemon dagster-webserver; fi ;;
esac
