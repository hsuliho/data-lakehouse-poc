"""Tools of the diagnosis agent. All read-only. `diagnose` gets the checklist and the project's own knowledge; `diagnose_plain` gets only
primitive tools (a run, SQL, files, docker), which is the control arm of the fault-injection evaluation."""
import json
import re
import subprocess
from typing import Literal

from langchain_core.tools import tool

from agent.diagnose import evidence as E

CATEGORY = Literal["delivery_incomplete", "schema_drift", "timestamp_out_of_window", "data_quality_status", "scd2_overlap", "data_quality_other",
                   "infra_trino", "infra_catalog", "infra_spark", "infra_minio", "infra_dagster", "no_problem_found", "unknown"]


def _j(x, n=6000):
    s = json.dumps(x, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + f'..."(truncated, {len(s)} chars)"'


@tool
def submit_diagnosis(symptom: str, cause_category: CATEGORY, cause: str, confidence: Literal["high", "medium", "low"], evidence: list[str],
                     ruled_out: list[str], suggested_actions: list[str], needs_human: bool, dt: str = "") -> str:
    """Submit your FINAL diagnosis. Call this exactly once. Do not answer in plain text.
    Args:
      symptom: what was observed, one sentence (which date, which step, what failed).
      cause_category: the closest category; use unknown when nothing fits, and no_problem_found when the pipeline is healthy.
      cause: the root cause in one or two sentences, in Traditional Chinese.
      confidence: high only when a tool result directly shows the cause.
      evidence: each item cites the tool result it comes from, e.g. 'inspect_delivery(2026-01-27): no _SUCCESS marker'. No evidence, no claim.
      ruled_out: what you checked and found fine.
      suggested_actions: what a person should do. You cannot run anything; never suggest deleting data.
      needs_human: true when a person must decide or act.
      dt: the delivery date concerned, if any."""
    return "recorded"


@tool
def get_run(dt: str = "", run_id: str = "") -> str:
    """The newest Dagster run for a delivery date (or a given run id): status, per-step result, the failing step's error text, and the data checks: how many ran and which failed (table, test name, severity, failing row count). Every dbt test is a check on the table it tests. Args: dt: 'YYYY-MM-DD' or empty; run_id: optional."""
    return _j(E.run_summary(dt, run_id))


@tool
def partition_status() -> str:
    """Which delivery dates completed, and which landing days exist but have not completed."""
    return _j(E.partition_status())


@tool
def inspect_delivery(dt: str) -> str:
    """Check one landing delivery against the contract: folder, _SUCCESS marker, per file the columns, row count, duplicate event ids, and changed_at values after the delivery day; and whether the loader would accept it. Args: dt: 'YYYY-MM-DD'."""
    return _j(E.delivery_check(dt))


@tool
def service_health() -> str:
    """Container state and endpoint check of Dagster, Trino, the Iceberg REST catalog, Spark Thrift and MinIO."""
    return _j(E.service_health())


@tool
def failing_rows(test: str, limit: int = 20) -> str:
    """The rows dbt stored for a data test that did not pass (names and cities are masked). Args: test: the test name from get_run's tests_not_passing."""
    return _j(E.failing_rows(test, limit))


@tool
def lookup_known_issues(query: str) -> str:
    """Search this project's own pitfall list and incident log for earlier incidents that look like the symptom. Args: query: error text or keywords."""
    return _j(E.known_issues(query))


@tool
def run_checklist(dt: str = "", run_id: str = "") -> str:
    """Run the whole standard checklist at once (run, services, delivery, data checks, neighbours, known incidents) and the fixed-rule verdict. Start here. Args: dt: 'YYYY-MM-DD' or empty; run_id: optional."""
    ev = E.checklist(dt, run_id)
    return _j({"evidence": ev, "rule_verdict": E.classify(ev)}, 9000)


@tool
def table_snapshots(table: str) -> str:
    """The latest Iceberg snapshots of a table (id, time, operation, rows added). Args: table like 'ods.orders_raw' or 'dwh_spark.fact_orders'."""
    if not re.fullmatch(r"(ods|dwh_spark)\.[a-z_]+", table):
        return _j({"error": "table must look like ods.orders_raw"})
    s, t = table.split(".")
    return _sql(f'SELECT snapshot_id, committed_at, operation, summary[\'added-records\'] AS added FROM {s}."{t}$snapshots" ORDER BY committed_at DESC LIMIT 5')


_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|merge|truncate|grant|revoke|call|set)\b", re.I)


@E.replayable
def _sql(sql: str) -> str:
    sql = sql.strip().rstrip(";")
    if ";" in sql or _FORBIDDEN.search(sql) or not re.match(r"^(select|with|show|describe)\b", sql, re.I):
        return _j({"error": "only a single read-only SELECT / WITH / SHOW / DESCRIBE"})
    try:
        c = E._trino(); c.execute(sql); rows = c.fetchmany(50)
        return _j({"columns": [d[0] for d in c.description], "rows": [[str(v) for v in r] for r in rows]})
    except Exception as e:
        return _j({"error": str(e)[:400]})


@tool
def run_sql(sql: str) -> str:
    """Read-only Trino SQL on ods, dwh_spark, dwh_spark_dbt_test__audit and ops (names and cities are masked; at most 50 rows). Args: sql: one SELECT statement."""
    return _sql(sql)


@E.replayable
def _list_landing(dt: str = "") -> str:
    p = E.LANDING / (f"dt={dt}" if dt else "")
    if not re.fullmatch(r"(dt=)?[0-9-]*", p.name) or not p.exists():
        return _j({"error": f"{p.name} does not exist"})
    return _j(sorted(x.name for x in p.iterdir())[-60:])


@tool
def list_landing(dt: str = "") -> str:
    """List the landing folder (delivery days, or the files of one day). Args: dt: 'YYYY-MM-DD' or empty."""
    return _list_landing(dt)


@E.replayable
def _read_landing_file(dt: str, name: str, lines: int = 8) -> str:
    if not re.fullmatch(r"[0-9-]+", dt) or not re.fullmatch(r"[a-z_]+\.csv", name):
        return _j({"error": "dt like 2026-01-27 and name like orders.csv"})
    f = E.LANDING / f"dt={dt}" / name
    return _j(f.read_text().splitlines()[:min(int(lines), 20)] if f.exists() else {"error": "no such file"})


@tool
def read_landing_file(dt: str, name: str, lines: int = 8) -> str:
    """First lines of a landing file. Args: dt: 'YYYY-MM-DD'; name: e.g. 'orders.csv'."""
    return _read_landing_file(dt, name, lines)


@E.replayable
def _docker_ps() -> str:
    return subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"], capture_output=True, text=True, timeout=20).stdout[-3000:]


@tool
def docker_ps() -> str:
    """Names and status of all containers, stopped ones included."""
    return _docker_ps()


DIAGNOSE_TOOLS = [run_checklist, get_run, partition_status, inspect_delivery, service_health, failing_rows, lookup_known_issues, table_snapshots, run_sql]
DIAGNOSE_PLAIN_TOOLS = [get_run, run_sql, list_landing, read_landing_file, docker_ps]
