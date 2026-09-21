"""Run the daily job once for one delivery date, recorded in Dagster (used by the fault-injection harness).
    python -m orchestration.trigger 2026-01-27 [--fault NAME]      exit 0 when the run succeeded, 1 when it failed (a failure is data, not an error)
With --fault the run is labelled as a synthetic test run (tags synthetic=true, fault=NAME), so a failure that was put there on purpose is not
mistaken for a real one on the Runs page (filter on the tag `synthetic`)."""
import sys

from dagster import DagsterInstance, DagsterRunStatus, RunsFilter

from orchestration.definitions import JOB_NAME, defs, run_tags

# this path skips the run queue (limit: one run at a time), so it checks by hand: two dbt runs at once in the same project directory disturbed each other
busy = DagsterInstance.get().get_runs_count(RunsFilter(statuses=[DagsterRunStatus.STARTED, DagsterRunStatus.STARTING, DagsterRunStatus.QUEUED]))
if busy:
    sys.exit(f"REFUSING: {busy} run(s) are in progress or queued. Wait for them, then run this again.")
r = defs.get_job_def(JOB_NAME).execute_in_process(partition_key=sys.argv[1], instance=DagsterInstance.get(), raise_on_error=False,
                                                 tags={**run_tags(sys.argv[1], "manual (fault injection)" if "--fault" in sys.argv else "manual"),
                                                       **({"synthetic": "true", "fault": sys.argv[sys.argv.index("--fault") + 1]} if "--fault" in sys.argv else {})})
print("succeeded" if r.success else "failed")
sys.exit(0 if r.success else 1)
