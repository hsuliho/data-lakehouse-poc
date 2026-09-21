"""Read-only view of the Dagster instance, run INSIDE the dagster container (the instance is a SQLite file in a docker volume):
    python -m orchestration.state run [--dt YYYY-MM-DD] [--run-id ID]   the newest run for a date (or a run): status, steps, error, data checks
    python -m orchestration.state partitions                            the dates whose last model (ads_category_revenue_rank) has been materialized
    python -m orchestration.state runs                                  the latest runs, one line each
The diagnosis agent and scripts/dagster.sh call this with `docker exec`; output is JSON."""
import argparse
import json
from datetime import datetime, timezone

from dagster import AssetKey, DagsterEventType, DagsterInstance, RunsFilter

from orchestration.keys import DONE_ASSET


def run_summary(dt: str = "", run_id: str = "") -> dict:
    inst = DagsterInstance.get()
    if run_id:
        run = inst.get_run_by_id(run_id)
    else:
        runs = inst.get_runs(filters=RunsFilter(tags={"dagster/partition": dt}), limit=1) if dt else inst.get_runs(limit=1)
        run = runs[0] if runs else None
    if not run:
        return {"found": False, "note": f"no Dagster run for {dt or 'anything'}"}
    stats, steps, error, run_n, failing = inst.get_run_stats(run.run_id), {}, None, 0, []
    for e in inst.all_logs(run.run_id):
        de = e.dagster_event
        if not de:
            continue
        t = de.event_type
        if t == DagsterEventType.STEP_SUCCESS:
            steps[de.step_key] = "success"
        elif t == DagsterEventType.STEP_FAILURE:
            steps[de.step_key] = "failed"
            err = de.step_failure_data.error
            error = {"step": de.step_key, "message": (err.message if err else "")[-1500:]}
        elif t == DagsterEventType.ASSET_CHECK_EVALUATION:
            d = de.event_specific_data
            run_n += 1
            if not d.passed:
                md = {k: str(getattr(v, "value", v))[:300] for k, v in d.metadata.items()}
                failing.append({"asset": d.asset_key.to_user_string(), "check": d.check_name, "severity": str(d.severity.value), "failed_rows": md.get("dagster_dbt/failed_row_count"), "status": md.get("status")})
    fmt = lambda x: datetime.fromtimestamp(x, timezone.utc).isoformat() if x else None
    return {"found": True, "run_id": run.run_id, "status": run.status.value, "partition": run.tags.get("dagster/partition"),
            "started_utc": fmt(stats.start_time), "ended_utc": fmt(stats.end_time), "steps": steps, "error": error,
            "data_checks": {"checks_run": run_n, "failing": failing}}          # a check that matches the planted dirt already passes; "failing" is what is really wrong


def completed_dates() -> list[str]:
    return sorted(DagsterInstance.get().get_materialized_partitions(AssetKey(DONE_ASSET)))


def latest_runs(n: int = 8) -> list[dict]:
    return [{"run_id": r.run_id[:8], "status": r.status.value, "partition": r.tags.get("dagster/partition")} for r in DagsterInstance.get().get_runs(limit=n)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["run", "partitions", "runs"]); ap.add_argument("--dt", default=""); ap.add_argument("--run-id", default="")
    a = ap.parse_args()
    print(json.dumps(run_summary(a.dt, a.run_id) if a.what == "run" else {"completed_dates": completed_dates()} if a.what == "partitions" else latest_runs(), default=str))
