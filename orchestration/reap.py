"""Run when the daemon container starts: every run still marked STARTED or STARTING belongs to a container that no longer exists
(the default run launcher runs the worker inside the daemon container, and a manual run from orchestration.trigger does too), so it is dead.
Left as it is, such a run keeps the queue blocked (limit: one run at a time) until run monitoring times it out."""
from dagster import DagsterInstance, DagsterRunStatus, RunsFilter

inst = DagsterInstance.get()
for r in inst.get_runs(filters=RunsFilter(statuses=[DagsterRunStatus.STARTED, DagsterRunStatus.STARTING])):
    inst.report_run_canceled(r, message="the daemon container was restarted while this run was in progress; its worker is gone")
    print("reaped run", r.run_id[:8], r.tags.get("dagster/partition"))
