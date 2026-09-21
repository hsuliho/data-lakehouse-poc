"""Fault-injection evaluation of the diagnosis agent (ADR 0016 decision 8).
Each fault is injected into the real pipeline, the daily job is run for one delivery date, every arm diagnoses the result, and the answer
is compared with the root cause written down here BEFORE any arm ran. The pipeline run is real: the failure text and the data checks are
whatever Dagster recorded.  usage: FAULT_INJECTION_OK=1 python -m agent.diagnose.faults [--arms rules,diagnose,diagnose_plain] [--faults a,b] [--resume FILE]"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

from agent.diagnose import evidence as E
from agent.diagnose import tools as T
from agent.diagnose.__main__ import diagnose

DT = "2026-01-30"                       # the delivery date every fault is injected into
LAND = E.LANDING / f"dt={DT}"
HERE = Path(__file__).parent
BAK = HERE / "results" / ".backup"
FIXTURES = HERE / "fixtures"          # what every read returned during the fault: replayed by agent.diagnose.regress with nothing touched
sh = lambda *c: subprocess.run(c, capture_output=True, text=True, cwd=E.ROOT)


def backup(name):
    BAK.mkdir(parents=True, exist_ok=True); shutil.copy(LAND / name, BAK / name)


def restore(name):
    shutil.copy(BAK / name, LAND / name)


def edit(name, fn):
    backup(name); p = LAND / name; p.write_text(fn(p.read_text()))


def wait(pred, secs=180):
    end = time.time() + secs
    while time.time() < end:
        try:
            if pred(): return
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError("service did not come back")


def container_up(name, ready):
    sh("docker", "start", name); wait(ready)


def settle():
    """Before the next fault: every service answers AND a warehouse table can be read (a container that is 'Up' can still fail its first queries)."""
    wait(lambda: all(v["endpoint"] == "ok" for v in E.service_health().values()), 240)
    def read():
        import load
        load.run(load.conn().cursor(), "SELECT count(*) FROM dwh_spark.fact_orders"); return True
    wait(read, 180)


health = lambda n: (lambda: sh("docker", "inspect", "-f", "{{.State.Health.Status}}", n).stdout.strip() == "healthy")
FAULTS = {}
def fault(name, expect, note, inject, restore_fn):
    FAULTS[name] = dict(expect=expect, note=note, inject=inject, restore=restore_fn)


def _future_changed_at(text):
    """Set changed_at (column 5) of the first order to the start of the next day; ordered_at (column 4) is left alone."""
    lines = text.splitlines()
    cols = lines[1].split(","); cols[4] = (datetime.fromisoformat(DT) + timedelta(days=1)).strftime("%Y-%m-%d") + " 00:00:00"
    lines[1] = ",".join(cols)
    return "\n".join(lines) + "\n"


def _illegal_status(text):
    """The status of the first order becomes 'refunded'. No new key is added, so rerunning the original delivery heals the warehouse."""
    lines = text.splitlines()
    cols = lines[1].split(","); cols[2] = "refunded"
    lines[1] = ",".join(cols)
    return "\n".join(lines) + "\n"


fault("control_healthy", "no_problem_found", "nothing injected; the run is a plain rerun of a completed date", lambda: None, lambda: None)
fault("missing_success", "delivery_incomplete", "the _SUCCESS marker of the day is gone",
      lambda: (LAND / "_SUCCESS").rename(LAND / "_SUCCESS.hidden"), lambda: (LAND / "_SUCCESS.hidden").rename(LAND / "_SUCCESS"))
fault("schema_drift", "schema_drift", "customers.csv: column membership_tier renamed to tier",
      lambda: edit("customers.csv", lambda t: t.replace("membership_tier", "tier", 1)), lambda: restore("customers.csv"))
fault("future_timestamp", "timestamp_out_of_window", "orders.csv: a changed_at at the start of the next day",
      lambda: edit("orders.csv", _future_changed_at), lambda: restore("orders.csv"))
fault("trino_down", "infra_trino", "the Trino container is stopped",
      lambda: sh("docker", "stop", "data-lakehouse-poc-trino-1"), lambda: container_up("data-lakehouse-poc-trino-1", health("data-lakehouse-poc-trino-1")))
fault("catalog_down", "infra_catalog", "the Iceberg REST catalog container is stopped",
      lambda: sh("docker", "stop", "data-lakehouse-poc-iceberg-rest-1"), lambda: container_up("data-lakehouse-poc-iceberg-rest-1", health("data-lakehouse-poc-iceberg-rest-1")))
fault("spark_down", "infra_spark", "the Spark Thrift container is stopped (since ADR 0018 the raw load fails too)",
      lambda: sh("docker", "stop", "data-lakehouse-poc-spark-thrift-1"),
      lambda: (sh("docker", "start", "data-lakehouse-poc-spark-thrift-1"), wait(lambda: __import__("socket").create_connection(("localhost", 10000), 3).close() or True)))
fault("illegal_status", "data_quality_status", "orders.csv: an existing order gets status 'refunded' (a new bad value in a test that already has one known bad row)",
      lambda: edit("orders.csv", _illegal_status), lambda: (restore("orders.csv"), trigger("illegal_status: healing rerun")))     # the original delivery is run again: the warehouse is healed
fault("scd2_overlap", "scd2_overlap", "dim_product: the closed version of product 1 is stretched over its successor",
      lambda: sh(str(E.ROOT / ".venv/bin/python"), "-c", "import sys;sys.path.insert(0,'scripts');import load;c=load.conn().cursor();load.run(c,\"UPDATE dwh_spark.dim_product SET valid_to = valid_to + INTERVAL '10' DAY WHERE product_id = 1 AND NOT is_current\")"),
      lambda: sh(str(E.ROOT / ".venv/bin/python"), "scripts/scd2.py", "run", "2026-01-29"))


def trigger(fault="unnamed"):
    """One real run of the daily job for DT inside the Dagster container, recorded there; a failure is data here, not an error.
    The run is tagged synthetic=true and fault=<name>, so it is not taken for a real failure on the Runs page."""
    r = sh("docker", "exec", E.CONTAINERS["dagster"], "python", "-m", "orchestration.trigger", DT, "--fault", fault)
    return r.returncode == 0


def record_fixture(name, f):
    """Everything the diagnosis reads while this fault is in place, saved so it can be replayed later (regress.py)."""
    E.start_recording()
    diagnose(DT, "rules")                                                       # the checklist's own reads
    E.run_summary(DT, ""); E.run_summary("", ""); E.partition_status()
    T.docker_ps.invoke({}); T.list_landing.invoke({"dt": DT}); T.list_landing.invoke({"dt": ""})
    for t in ("orders", "order_items", "customers", "products"):
        T.read_landing_file.invoke({"dt": DT, "name": f"{t}.csv", "lines": 8})
    E.save_recording(FIXTURES / f"{name}.json", {"fault": name, "expected": f["expect"], "note": f["note"], "dt": DT})


def main():
    if os.environ.get("FAULT_INJECTION_OK") != "1":
        sys.exit("""REFUSING TO RUN. This is a development experiment, not a check. It breaks things on purpose in the ONE warehouse you have:
  - edits landing/dt=%s (removes _SUCCESS, renames a column, sets a future time, changes an order status)
  - stops and restarts the Trino, Iceberg catalog and Spark containers
  - runs UPDATE on dwh_spark.dim_product, and runs the daily job several times (failed runs stay in the Dagster history)
Every fault is undone afterwards, but a crash in the middle can leave the warehouse damaged (scripts/rebuild.sh repairs it).
Never point it at anything you need. To run it: FAULT_INJECTION_OK=1 python -m agent.diagnose.faults ...""" % DT)
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="rules,diagnose,diagnose_plain"); ap.add_argument("--faults", default=",".join(FAULTS)); ap.add_argument("--resume", default="")
    a = ap.parse_args()
    out = Path(a.resume) if a.resume else HERE / "results" / f"faults_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    out.parent.mkdir(exist_ok=True)
    done = {(r["fault"], r["arm"]) for r in map(json.loads, open(out))} if a.resume else set()
    names = a.faults.split(",")
    for n in names:
        f, arms = FAULTS[n], [x for x in a.arms.split(",") if (n, x) not in done]
        if not arms:
            continue
        print(f"== {n}: {f['note']}")
        settle()
        try:
            f["inject"]()
            ok = trigger(n)
            print(f"   pipeline run {'succeeded' if ok else 'failed'}")
            record_fixture(n, f)
            for arm in arms:
                t0 = time.time()
                d = diagnose(DT, arm)
                got = d.get("cause_category")
                rec = {"fault": n, "arm": arm, "expected": f["expect"], "got": got, "correct": got == f["expect"], "pipeline_succeeded": ok, "dt_ok": d.get("dt") == DT,
                       "has_evidence": bool(d.get("evidence")), "confidence": d.get("confidence"), "tool_calls": d.get("tool_calls"), "model": d.get("model"),
                       "error": d.get("error"), "cause": d.get("cause"), "evidence": d.get("evidence"), "seconds": round(time.time() - t0, 1)}
                open(out, "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
                print(f"   {arm:15} expected {f['expect']:24} got {str(got):24} {'OK' if rec['correct'] else 'WRONG'}  tools={d.get('tool_calls')} {d.get('error') or ''}"[:200])
        finally:
            f["restore"]()
            settle()
    print("results:", out)


if __name__ == "__main__":
    main()
