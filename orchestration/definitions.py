"""Daily pipeline in Dagster (ADR 0015 / 0016 / 0017). One partition = one UTC delivery date (dt). The lineage is per table:

  ods/<t>_raw  ->  ods/<t>   ->  stg_<t>  ->  fact_orders  ->  dws_daily_revenue  ->  ads_category_revenue_rank      (t = orders, order_items, customers, products)
  ods/products_raw  ->  scd2/dim_product   ->  fact_orders, dws_daily_revenue         ods/customers_raw  ->  scd2/dim_customer  ->  fact_orders

The ods and scd2 assets are ours (each runs the existing script for one table). The dbt models and their tests come from the dbt manifest through
dagster-dbt; a dbt source such as `ods.orders` or `scd2.dim_product` has the asset key ods/orders, scd2/dim_product, which is the key of OUR asset,
so the two halves join on the table name. Every dbt test is an asset check on the table it tests; a test that reads several tables (a
singular test) says which table it is about in its dbt `meta.dagster.asset_key`, which is what makes dagster-dbt load it as a check.

Partitions are independent: there is no dependency between one date and the next, so a single date can be run or backfilled alone (the equivalence
test, scripts/equivalence.sh, is what makes that safe). The sensor is the "schedule": it requests the missing dates, oldest first, as soon as their
_SUCCESS marker exists, and fails loudly once a date is overdue.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dagster import (AssetCheckResult, AssetCheckSeverity, AssetCheckSpec, AssetExecutionContext, AssetKey, AssetSelection, DailyPartitionsDefinition, DefaultSensorStatus, Definitions,
                     Failure, MaterializeResult, RunRequest, SkipReason, asset, define_asset_job, in_process_executor, multi_asset_check, sensor)
from dagster_dbt import DagsterDbtTranslator, DagsterDbtTranslatorSettings, DbtCliResource, dbt_assets

from orchestration.keys import DONE_ASSET

ROOT = Path(__file__).resolve().parent.parent
LANDING = ROOT / "landing"
PY = sys.executable                                # the interpreter that runs Dagster: the host .venv, or /opt/venv in the container
DBT_DIR = ROOT / "dbt_spark"
MANIFEST = DBT_DIR / "target" / "manifest.json"
TABLES = ["orders", "order_items", "customers", "products"]
DIMS = {"dim_product": "products", "dim_customer": "customers"}          # dimension -> the ODS table its versions come from
# the mock data is three deliveries, 2026-01-29 .. 2026-01-31; drop end_date for real deliveries
DAILY = DailyPartitionsDefinition(start_date="2026-01-29", end_date="2026-02-01", timezone="UTC")
OVERDUE_AFTER = timedelta(days=1, hours=3)       # a date is overdue 3 hours after its day closed (UTC 03:00), ADR 0015 amendment 2
# dbt tests that fail on purpose because of dirty rows planted in the mock data (REPORT stage 6), and how many rows each must fail.
# A test outside this list, or failing more rows than listed (a NEW illegal value), is a real failure.
KNOWN_DIRT = {
    "accepted_range_stg_order_items_unit_price__0": 1,
    "accepted_values_stg_orders_order_status__created__paid__shipped__completed__cancelled": 1,
    "accepted_range_fact_orders_line_amount__0": 1,
    "source_unique_ods_orders_raw_event_id": 1,
}


def sh(context, cmd, cwd=ROOT):
    """Run a command, keep its output in the Dagster log, and turn a failure into a Failure that carries the last lines."""
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = (p.stdout + p.stderr).strip()
    context.log.info(out[-4000:])
    if p.returncode:
        raise Failure(description=f"{Path(cmd[1]).name if len(cmd) > 1 else cmd[0]} exited {p.returncode}: " + "\n".join(out.splitlines()[-12:]))
    return out


def snapshot(table):
    """Newest Iceberg snapshot id of a table (the link from a Dagster run to the data it wrote)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import load
    cur = load.conn().cursor()
    rows = load.run(cur, f'SELECT snapshot_id FROM {table.split(".")[0]}."{table.split(".")[1]}$snapshots" ORDER BY committed_at DESC LIMIT 1')
    return str(rows[0][0]) if rows else None


def ensure_manifest():
    """dagster-dbt builds the model assets from dbt's manifest: (re)parse when a model, test, macro or yml file is newer than it."""
    files = [f for f in DBT_DIR.rglob("*") if f.suffix in (".sql", ".yml") and not {"target", "logs", "dbt_packages"} & set(f.relative_to(DBT_DIR).parts)]
    if not MANIFEST.exists() or max(f.stat().st_mtime for f in files) > MANIFEST.stat().st_mtime:
        subprocess.run([str(Path(sys.executable).parent / "dbt"), "parse", "--profiles-dir", "."], cwd=DBT_DIR, check=True, capture_output=True)


def parse_metric(out, pattern):
    return {m[0]: int(m[1]) for m in re.findall(pattern, out, re.M)}


def make_ods(table, stage):
    key = AssetKey(["ods", f"{table}_raw" if stage == "raw" else table])
    deps = [AssetKey(["ods", f"{table}_raw"])] if stage == "current" else []

    @asset(key=key, deps=deps, partitions_def=DAILY, group_name="ods", description=f"ods.{table}" + ("_raw: the delivery of the day, append-only per date" if stage == "raw" else ": the current state of the keys the delivery touched"))
    def _ods(context) -> MaterializeResult:
        dt = context.partition_key
        out = sh(context, [PY, "scripts/load.py", "deliver", dt, "--table", table, "--stage", stage])
        if "REJECTED" in out:
            raise Failure(description=out)
        return MaterializeResult(metadata={"dt": dt, "table": key.to_user_string(), "records": parse_metric(out, r"^dt=\S+ (\w+)\s+raw records=\s*(\d+)") or None,
                                           "snapshot": snapshot(f"ods.{key.path[-1]}")})
    return _ods


def make_dim(dim, table):
    @asset(key=AssetKey(["scd2", dim]), deps=[AssetKey(["ods", f"{table}_raw"])], partitions_def=DAILY, group_name="scd2",
           description=f"{dim}: SCD2 versions rebuilt from the whole history of ods.{table}_raw for the keys delivery dt touched")
    def _dim(context) -> MaterializeResult:
        dt = context.partition_key
        out = sh(context, [PY, "scripts/scd2.py", "run", dt, "--dim", dim])
        return MaterializeResult(metadata={"dt": dt, "versions": (re.findall(r"versions: (\d+)", out) or [None])[-1], "snapshot": snapshot(f"dwh_spark.{dim}")})
    return _dim


def tolerate(ev: AssetCheckResult) -> AssetCheckResult:
    """A test that fails exactly as many rows as the planted dirt is expected: it passes here, with a note. Anything else stays failed."""
    n = ev.metadata.get("dagster_dbt/failed_row_count")
    n = getattr(n, "value", n)
    if not ev.passed and KNOWN_DIRT.get(ev.check_name) == n:
        return AssetCheckResult(passed=True, asset_key=ev.asset_key, check_name=ev.check_name, severity=ev.severity,
                                metadata={**ev.metadata, "expected_dirt": f"planted in the mock data: {n} row(s)"})
    return ev


ensure_manifest()


@dbt_assets(manifest=MANIFEST, partitions_def=DAILY, name="dbt_models",
            dagster_dbt_translator=DagsterDbtTranslator(settings=DagsterDbtTranslatorSettings(enable_source_tests_as_checks=True)))
def dbt_models(context: AssetExecutionContext, dbt: DbtCliResource):
    """Models first, then the tests. `dbt build` is not used: it skips the models below a failing test, and two tests fail on purpose."""
    yield from dbt.cli(["run"], context=context).stream()
    for ev in dbt.cli(["test", "--store-failures", "--exclude", " ".join(SOURCE_TESTS)], context=context, raise_on_error=False).stream():
        yield tolerate(ev) if isinstance(ev, AssetCheckResult) else ev


# The dbt tests dagster-dbt cannot carry as checks: it loads a test only when it hangs on one model (or on a table it can name), and these read
# only sources or nothing. They are declared here on the table they are about (the test name is the check name), BLOCKING like the others, and left
# out of the `dbt test` inside dbt_models so that they run once. They all sit on assets BEFORE the dbt models, so they run before the models and a
# failure (an overlapping SCD2 chain, say) stops the models from running at all.
# Two steps, not one: a blocking check holds back everything below its asset, so a step that also held a check on an asset below (ods/orders is
# below ods/orders_raw) would wait for itself.
RAW_TESTS = {"source_unique_ods_orders_raw_event_id": AssetKey(["ods", "orders_raw"])}        # a source test on a table no model reads
UPSTREAM_TESTS = {
    "loaded_at_not_in_future": AssetKey(["ods", "orders"]),                    # the load time of the four ODS tables
    "dim_versions_chain": AssetKey(["scd2", "dim_product"]),                   # the version chains of both dimensions
    "session_timezone_is_utc": AssetKey(["scd2", "dim_product"]),              # the Spark session zone every dbt model depends on
}
SOURCE_TESTS = {**RAW_TESTS, **UPSTREAM_TESTS}


def make_source_checks(tests, step_name):
    @multi_asset_check(specs=[AssetCheckSpec(name=n, asset=k, blocking=True) for n, k in tests.items()], can_subset=False, name=step_name)
    def _checks(context, dbt: DbtCliResource):
        inv = dbt.cli(["test", "--select", " ".join(tests), "--store-failures"], raise_on_error=False).wait()
        results = {r["unique_id"].split(".")[2]: r for r in inv.get_artifact("run_results.json")["results"]}
        for name, key in tests.items():
            r = results.get(name)
            n = (r or {}).get("failures") or 0
            ok = r is not None and (r["status"] == "pass" or KNOWN_DIRT.get(name) == n)                   # the planted dirt, same rule as `tolerate`
            yield AssetCheckResult(passed=ok, asset_key=key, check_name=name, severity=AssetCheckSeverity.ERROR,
                                   metadata={"status": (r or {}).get("status", "not run"), "dagster_dbt/failed_row_count": n})
    return _checks


source_checks = [make_source_checks(RAW_TESTS, "raw_table_checks"), make_source_checks(UPSTREAM_TESTS, "upstream_table_checks")]


ods_assets = [make_ods(t, st) for t in TABLES for st in ("raw", "current")]
dim_assets = [make_dim(d, t) for d, t in DIMS.items()]
JOB_NAME = "ecommerce_daily_sync"      # this version of Dagster has no run title: the job name is what the Runs page shows first, the tags carry the rest


def run_tags(dt: str, trigger: str) -> dict:
    """Tags of a run, shown as labels on the Runs page and usable as filters: what data, which delivery date, which day of how many."""
    keys = DAILY.get_partition_keys()
    return {"data": "ecommerce", "delivery_date": dt, "day": f"{keys.index(dt) + 1} of {len(keys)}", "trigger": trigger}


daily_job = define_asset_job(JOB_NAME, selection=AssetSelection.all() | AssetSelection.all_asset_checks(), partitions_def=DAILY,
                             tags={"data": "ecommerce"}, executor_def=in_process_executor)   # one step at a time: the catalog's SQLite does not like parallel commits


def now_utc() -> datetime:
    """LAKEHOUSE_NOW (ISO, UTC) replaces the clock, so a stop-and-resume can be tested with the mock dates."""
    return datetime.fromisoformat(os.environ["LAKEHOUSE_NOW"]).replace(tzinfo=timezone.utc) if os.environ.get("LAKEHOUSE_NOW") else datetime.now(timezone.utc)


def plan(closed_keys: list[str], done: set[str], marker_exists, now: datetime) -> tuple[list[str], str | None]:
    """(dates to request oldest first, overdue message). Stops at the first date whose delivery has not arrived."""
    todo = []
    for dt in (k for k in closed_keys if k not in done):
        if not marker_exists(dt):
            if now > datetime.fromisoformat(dt).replace(tzinfo=timezone.utc) + OVERDUE_AFTER:
                return todo, f"delivery dt={dt} is overdue: no _SUCCESS marker {now - datetime.fromisoformat(dt).replace(tzinfo=timezone.utc) - timedelta(days=1)} after its day closed; later dates are not processed"
            return todo, None
        todo.append(dt)
    return todo, None


SENSOR_INTERVAL = int(os.environ.get("SENSOR_INTERVAL") or 5)     # seconds between checks; a check is a directory listing and one query, so 5 s is cheap
ALERT_EVERY = 300                                               # an overdue delivery raises (a failed tick) at most once per this many seconds


def should_alert(now_ts: float, interval: int = SENSOR_INTERVAL, every: int = ALERT_EVERY) -> bool:
    """True for about one check per `every` seconds. Stateless on purpose: a failed tick cannot save a cursor, and a failure per check
    (17,000 a day at 5 s) would bury the history. ponytail: needs interval < every; a per-date cursor would be exact."""
    return int(now_ts) % every < max(interval, 1)


@sensor(job=daily_job, minimum_interval_seconds=SENSOR_INTERVAL, default_status=DefaultSensorStatus.RUNNING)
def delivery_sensor(context):
    now = now_utc()
    closed = [k for k in DAILY.get_partition_keys() if date.fromisoformat(k) < now.date()]      # days that have ended in UTC
    done = set(context.instance.get_materialized_partitions(AssetKey(DONE_ASSET)))
    todo, overdue = plan(closed, done, lambda dt: (LANDING / f"dt={dt}" / "_SUCCESS").exists(), now)
    for dt in todo:
        yield RunRequest(run_key=dt, partition_key=dt, tags=run_tags(dt, "sensor"))
    if overdue and should_alert(time.time()):
        raise Exception(overdue)                                                                   # a failed tick is the alert
    if not todo:                                                                                   # a tick may end with one SkipReason, and only when it asked for no run
        if overdue:
            yield SkipReason("OVERDUE: " + overdue)                                                # the checks in between only say so
        else:
            waiting = next((k for k in closed if k not in done and not (LANDING / f"dt={k}" / "_SUCCESS").exists()), None)
            yield SkipReason(f"waiting for the delivery of dt={waiting} (no _SUCCESS yet, not overdue)" if waiting else "nothing missing")


defs = Definitions(assets=[*ods_assets, *dim_assets, dbt_models], asset_checks=source_checks,
                    resources={"dbt": DbtCliResource(project_dir=str(DBT_DIR), profiles_dir=str(DBT_DIR), dbt_executable=str(Path(sys.executable).parent / "dbt"))},
                   jobs=[daily_job], sensors=[delivery_sensor])
