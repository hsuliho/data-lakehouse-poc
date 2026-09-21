"""Diagnose an alert: `python -m agent.diagnose --dt 2026-01-27 [--arm diagnose|diagnose_plain|rules] [--no-store]`
`rules` is the fixed-rule verdict only (no model, no quota). Results go to ops.diagnosis so a diagnosis can be traced."""
import argparse
import json
import sys
from datetime import datetime, timezone

from agent.diagnose import evidence as E

DDL = """CREATE TABLE IF NOT EXISTS ops.diagnosis (diagnosed_at TIMESTAMP(6) WITH TIME ZONE, dt VARCHAR, run_id VARCHAR, arm VARCHAR, model VARCHAR,
  symptom VARCHAR, cause_category VARCHAR, cause VARCHAR, confidence VARCHAR, evidence VARCHAR, ruled_out VARCHAR, suggested_actions VARCHAR,
  needs_human BOOLEAN, tool_calls INTEGER) WITH (format = 'PARQUET')"""


def store(rec: dict) -> None:
    sys.path.insert(0, str(E.ROOT / "scripts"))
    import load
    cur = load.conn().cursor()
    load.run(cur, "CREATE SCHEMA IF NOT EXISTS ops"); load.run(cur, DDL)
    q = lambda v: "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"
    js = lambda v: q(json.dumps(v, ensure_ascii=False))
    load.run(cur, f"""INSERT INTO ops.diagnosis VALUES (CAST(current_timestamp AS TIMESTAMP(6) WITH TIME ZONE), {q(rec['dt'])}, {q(rec.get('run_id'))}, {q(rec['arm'])}, {q(rec.get('model'))},
        {q(rec['symptom'])}, {q(rec['cause_category'])}, {q(rec['cause'])}, {q(rec['confidence'])}, {js(rec['evidence'])}, {js(rec['ruled_out'])}, {js(rec['suggested_actions'])},
        {'TRUE' if rec['needs_human'] else 'FALSE'}, {int(rec.get('tool_calls', 0))})""")


def diagnose(dt: str, arm: str = "diagnose", run_id: str = "") -> dict:
    if arm == "rules":
        ev = E.checklist(dt, run_id); v = E.classify(ev)
        return {"dt": dt, "run_id": ev["run"].get("run_id"), "arm": arm, "model": None, "symptom": f"run {ev['run'].get('status')} for {dt}", "cause_category": v["category"],
                "cause": v["reason"], "confidence": "medium", "evidence": [f"checklist: {v['reason']}"], "ruled_out": [], "suggested_actions": [], "needs_human": v["category"] != "no_problem_found", "tool_calls": 0}
    from agent.graph import run_question
    alert = f"告警:交付日 {dt} 的每日 pipeline 出現異常(sensor 或資料檢查發出告警)。" + (f"執行編號 {run_id}。" if run_id else "") + "請診斷根因。"
    r = run_question(arm, alert, thread_id=f"diag-{dt}-{arm}")
    if r.get("error") or not r.get("answer"):
        return {"dt": dt, "arm": arm, "error": r.get("error") or "no diagnosis submitted", "trace": r.get("tool_calls")}
    a = r["answer"]
    for k in ("evidence", "ruled_out", "suggested_actions"):              # a model sometimes passes one string where a list is asked for
        if isinstance(a.get(k), str):
            a[k] = [a[k]]
    return {**{k: a.get(k) for k in ("symptom", "cause_category", "cause", "confidence", "evidence", "ruled_out", "suggested_actions", "needs_human")},
            "dt": a.get("dt") or dt, "run_id": run_id or None, "arm": arm, "model": ",".join(r.get("models", [])), "tool_calls": len(r.get("tool_calls", [])), "trace": r.get("tool_calls")}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dt", required=True); ap.add_argument("--arm", default="diagnose", choices=["diagnose", "diagnose_plain", "rules"])
    ap.add_argument("--run-id", default=""); ap.add_argument("--no-store", action="store_true")
    a = ap.parse_args()
    d = diagnose(a.dt, a.arm, a.run_id)
    print(json.dumps({k: v for k, v in d.items() if k != "trace"}, ensure_ascii=False, indent=2))
    if "error" not in d and not a.no_store:
        store(d); print("stored in ops.diagnosis")
