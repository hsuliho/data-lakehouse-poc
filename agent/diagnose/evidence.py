"""Deterministic checks for the diagnosis agent (ADR 0016): every function reads, none writes.
The checklist order is: locate the run, look at services, look at the delivery, look at the stage and the data checks, look at neighbours,
compare with known incidents. `classify` turns the evidence into a cause by fixed rules, so there is a floor that needs no model."""
import csv
import functools
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "landing"
sys.path.insert(0, str(ROOT / "scripts"))
CONTAINERS = {"dagster": "data-lakehouse-poc-dagster-daemon-1", "trino": "data-lakehouse-poc-trino-1", "catalog": "data-lakehouse-poc-iceberg-rest-1", "spark": "data-lakehouse-poc-spark-thrift-1", "minio": "data-lakehouse-poc-minio-1"}
CATEGORIES = ["delivery_incomplete", "schema_drift", "timestamp_out_of_window", "data_quality_status", "scd2_overlap", "data_quality_other",
              "infra_trino", "infra_catalog", "infra_spark", "infra_minio", "infra_dagster", "no_problem_found", "unknown"]


# ---- record / replay: every read the diagnosis makes can be recorded during a real fault and replayed later without touching any system
_RECORD: dict | None = None      # while recording: {call key: what it returned}
_REPLAY: dict | None = None      # while replaying: the same, loaded from a fixture; a call that was not recorded gets an explicit error


def replayable(fn):
    name = fn.__name__

    @functools.wraps(fn)
    def wrapper(*a, **k):
        key = f"{name}|{json.dumps([a, k], sort_keys=True, default=str)}"
        if _REPLAY is not None:
            return _REPLAY.get(key, {"error": f"not recorded in this fixture: {key}"})
        out = fn(*a, **k)
        if _RECORD is not None:
            _RECORD[key] = out
        return out
    return wrapper


def start_recording():
    global _RECORD
    _RECORD = {}


def save_recording(path, meta):
    global _RECORD
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"meta": meta, "calls": _RECORD}, ensure_ascii=False, indent=1, default=str))
    _RECORD = None


def use_fixture(path):
    """Replay a fixture (None goes back to the live systems)."""
    global _REPLAY
    _REPLAY = None if path is None else json.loads(Path(path).read_text())["calls"]


def _dagster(*args) -> dict:
    """Dagster's records are a SQLite file in a docker volume, so they are read inside the daemon container (orchestration/state.py)."""
    p = subprocess.run(["docker", "exec", CONTAINERS["dagster"], "python", "-m", "orchestration.state", *args], capture_output=True, text=True, timeout=90)
    if p.returncode:
        return {"found": False, "error": "cannot read Dagster (is the dagster-daemon container up?): " + (p.stderr or p.stdout)[-300:]}
    return json.loads(p.stdout)


@replayable
def run_summary(dt: str = "", run_id: str = "") -> dict:
    """The newest run for a delivery date (or the given run): status, the failing step and its error, and the data checks it ran."""
    return _dagster("run", *(["--dt", dt] if dt else []), *(["--run-id", run_id] if run_id else []))


@replayable
def partition_status() -> dict:
    """Which delivery dates have completed, and which landing days exist but have not."""
    done = _dagster("partitions").get("completed_dates", [])
    landed = sorted(p.name[3:] for p in LANDING.glob("dt=*"))
    return {"completed_dates": done, "landing_dates_not_completed": [d for d in landed if d not in done]}


@replayable
def delivery_check(dt: str) -> dict:
    d = LANDING / f"dt={dt}"
    out = {"dt": dt, "folder_exists": d.exists(), "success_marker": (d / "_SUCCESS").exists(), "tables": {}}
    if not d.exists():
        return out
    import load
    day_end = datetime.fromisoformat(dt).replace(tzinfo=timezone.utc) + timedelta(days=1)
    for t, (key, cols) in load.TABLES.items():
        f = d / f"{t}.csv"
        if not f.exists():
            out["tables"][t] = {"file": "missing"}
            continue
        rdr = csv.DictReader(open(f))
        want = [("changed_at" if c == "updated_at" else c) for c, _ in cols] + ["event_id", "is_deleted"]
        rows, ids, late = list(rdr), [], 0
        for r in rows:
            ids.append(r.get("event_id"))
            try:
                if datetime.fromisoformat(load.normalize_ts(r["changed_at"])[:-4]).replace(tzinfo=timezone.utc) >= day_end:
                    late += 1
            except Exception:
                late += 1
        out["tables"][t] = {"rows": len(rows), "columns_match_contract": rdr.fieldnames == want,
                            **({"unexpected_columns": sorted(set(rdr.fieldnames or []) - set(want)), "missing_columns": sorted(set(want) - set(rdr.fieldnames or []))} if rdr.fieldnames != want else {}),
                            "duplicate_event_ids": len(ids) - len(set(ids)), "changed_at_after_delivery_day": late}
    try:
        load.read_delivery(dt)
        out["loader_would_accept"] = True
    except load.DeliveryError as e:
        out["loader_would_accept"], out["loader_reason"] = False, str(e)[:400]
    return out


@replayable
def service_health() -> dict:
    """Containers and endpoints the pipeline depends on. 'ok' or the reason."""
    ps = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"], capture_output=True, text=True, timeout=20).stdout
    status = dict(l.split("|", 1) for l in ps.splitlines() if "|" in l)
    out = {}
    for svc, name in CONTAINERS.items():
        out[svc] = {"container": status.get(name, "not found")}
    def probe(svc, fn):
        try:
            fn(); out[svc]["endpoint"] = "ok"
        except Exception as e:
            out[svc]["endpoint"] = f"{type(e).__name__}: {str(e)[:120]}"
    import urllib.request
    probe("catalog", lambda: urllib.request.urlopen("http://localhost:8181/v1/config", timeout=5).read())
    probe("dagster", lambda: urllib.request.urlopen("http://localhost:3000/server_info", timeout=5).read())
    probe("minio", lambda: urllib.request.urlopen("http://localhost:9000/minio/health/live", timeout=5).read())
    def trino():
        import trino as tr
        c = tr.dbapi.connect(host="localhost", port=8080, user="diagnoser", catalog="iceberg", http_scheme="http", request_timeout=8).cursor(); c.execute("SELECT 1"); c.fetchall()
    probe("trino", trino)
    probe("spark", lambda: socket.create_connection(("localhost", 10000), timeout=5).close())
    return out


def _trino():
    import trino as tr
    return tr.dbapi.connect(host="localhost", port=8080, user="diagnoser", catalog="iceberg", timezone="UTC", request_timeout=30).cursor()


@replayable
def failing_rows(test: str, limit: int = 20) -> dict:
    """Rows dbt stored for a failing test (dbt test --store-failures). Read as `diagnoser`: names and cities are masked."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", test):
        return {"error": "test name must be letters, digits and underscores"}
    try:
        c = _trino(); c.execute(f'SELECT * FROM dwh_spark_dbt_test__audit."{test}" LIMIT {min(int(limit), 50)}')
        return {"columns": [d[0] for d in c.description], "rows": [[str(v) for v in r] for r in c.fetchall()]}
    except Exception as e:
        return {"error": str(e)[:300]}


@replayable
def known_issues(query: str) -> list[str]:
    """Lines of the project's own pitfall list and incident log that share a word with the query."""
    words = {w.lower() for w in re.findall(r"[A-Za-z_]{4,}|[一-鿿]{2,}", query)}
    hits = []
    for f in ("docs/RESEARCH_REPORT.md", "REPORT.md"):
        for line in (ROOT / f).read_text().splitlines():
            if len(line) > 40 and sum(w in line.lower() for w in words) >= 2:
                hits.append((sum(w in line.lower() for w in words), f"{f}: {line.strip()[:260]}"))
    return [h for _, h in sorted(hits, key=lambda x: -x[0])[:5]]


def checklist(dt: str = "", run_id: str = "") -> dict:
    run = run_summary(dt, run_id)
    dt = dt or (run.get("partition") or "")
    ev = {"run": run, "services": service_health()}
    if dt:
        ev["delivery"] = delivery_check(dt)
    ev["partitions"] = partition_status()
    text = json.dumps(run.get("error") or "") + " ".join(c["check"] for c in (run.get("data_checks") or {}).get("failing", []))
    ev["known_issues"] = known_issues(text) if text.strip('"" ') else []
    return ev


def classify(ev: dict) -> dict:
    """Fixed rules over the checklist, in the order a person would look. Returns {category, reason}."""
    sv, dl, run = ev["services"], ev.get("delivery", {}), ev["run"]
    down = [s for s, v in sv.items() if v["endpoint"] != "ok" or not str(v["container"]).startswith("Up")]
    if dl and (not dl["folder_exists"] or not dl["success_marker"]):
        return {"category": "delivery_incomplete", "reason": f"landing dt={dl['dt']} has no _SUCCESS marker (folder exists: {dl['folder_exists']})"}
    if dl and any(not t.get("columns_match_contract", True) for t in dl["tables"].values()):
        bad = {n: (t.get("missing_columns"), t.get("unexpected_columns")) for n, t in dl["tables"].items() if not t.get("columns_match_contract", True)}
        return {"category": "schema_drift", "reason": f"columns differ from the contract: {bad}"}
    if dl and any(t.get("changed_at_after_delivery_day") for t in dl["tables"].values()):
        return {"category": "timestamp_out_of_window", "reason": "changed_at after the end of the delivery day: " + str({n: t["changed_at_after_delivery_day"] for n, t in dl["tables"].items() if t.get("changed_at_after_delivery_day")})}
    if down:
        order = ["catalog", "minio", "trino", "spark", "dagster"]      # a dead catalog also breaks Trino and Spark, so name the root first
        root = next(s for s in order if s in down)
        return {"category": f"infra_{root}", "reason": f"{root} is not healthy: {sv[root]}"}
    failing = (run.get("data_checks") or {}).get("failing", [])
    if failing:
        names = [c["check"] for c in failing]
        cat = "scd2_overlap" if any("dim_versions_chain" in n for n in names) else "data_quality_status" if any("order_status" in n for n in names) else "data_quality_other"
        return {"category": cat, "reason": f"data check failed on: {[(c['asset'], c['check'], c['failed_rows']) for c in failing]}"}
    if run.get("status") == "FAILURE":
        return {"category": "unknown", "reason": f"run failed at {run['error']['step'] if run.get('error') else '?'} and no rule matched: {(run.get('error') or {}).get('message', '')[:200]}"}
    return {"category": "no_problem_found", "reason": "run succeeded, services healthy, delivery complete, data checks as expected"}
