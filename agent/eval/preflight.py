"""Canary queries with frozen known answers, run before an evaluation spends any model requests.
A semantic layer that quietly degrades (a stale view, a missing column) would otherwise look like a model failure."""
import json
from pathlib import Path

from agent.tools_raw import run_sql
from agent.tools_semantic import query_metric

QUESTIONS = {q["id"]: q["expect"]["value"] for q in json.loads((Path(__file__).parent / "questions.json").read_text()) if "value" in q["expect"]}


def preflight() -> list[str]:
    problems = []
    checks = {
        "revenue (all data)": (query_metric.invoke({"metrics": ["revenue"]}), "revenue", QUESTIONS["kpi_revenue"]),
        "revenue, one Taipei day": (query_metric.invoke({"metrics": ["revenue"], "start_date": "2026-01-30", "end_date": "2026-01-30", "day_basis": "taipei"}), "revenue", QUESTIONS["day_taipei"]),
        "revenue, one UTC day": (query_metric.invoke({"metrics": ["revenue"], "start_date": "2026-01-30", "end_date": "2026-01-30", "day_basis": "utc"}), "revenue", QUESTIONS["day_utc"]),
    }
    for name, (res, col, want) in checks.items():
        got = (res.get("rows") or [{}])[0].get(col) if "error" not in res else res["error"]
        if not isinstance(got, (int, float)) or abs(got - want) > 0.01:
            problems.append(f"semantic layer: {name}: got {got!r}, expected {want}")
    raw = run_sql.invoke({"sql": "select count(*) from iceberg.dwh_spark.fact_orders"})
    if raw.get("rows") != [[138]]:
        problems.append(f"warehouse: fact_orders row count is {raw.get('rows') or raw.get('error')}, expected 138 (one per ODS order item)")
    return problems


if __name__ == "__main__":
    p = preflight()
    print("preflight OK" if not p else "preflight FAILED:\n  " + "\n  ".join(p))
    raise SystemExit(1 if p else 0)
