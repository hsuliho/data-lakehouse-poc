"""Arm B tools: the governed semantic layer. The model never writes SQL and never writes a `mf` filter:
it names metrics and dimensions, and this module builds the query from validated pieces."""
import csv
import json
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from langchain_core.tools import tool

from agent.config import MF, SEMANTIC_DIR

# friendly name -> (MetricFlow name, kind)
DIMENSIONS = {
    "day_taipei": ("order_item__ordered_date_taipei__day", "time"),
    "day_utc": ("order_item__ordered_date_utc__day", "time"),
    "product_category": ("order_item__product_category", "categorical"),
    "customer_membership_tier": ("order_item__customer_membership_tier", "categorical"),
    "order_status": ("order_item__order_status", "categorical"),
}
DIM_HELP = {
    "day_taipei": "Calendar day in Taipei time (UTC+8). Use for 'yesterday', 'last week' and days as a Taiwan user means them.",
    "day_utc": "Calendar day in UTC. Use only when the user explicitly asks for UTC.",
    "product_category": "Product category as of the order time (a later reclassification does not rewrite history).",
    "customer_membership_tier": "The customer's membership tier as of the order time.",
    "order_status": "Order status.",
}
_BACK = {v[0]: k for k, v in DIMENSIONS.items()}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALUE = re.compile(r"^[A-Za-z0-9_ -]{1,40}$")


@lru_cache(maxsize=1)
def _manifest():
    path = SEMANTIC_DIR / "target" / "semantic_manifest.json"
    if not path.exists():
        raise RuntimeError("semantic_manifest.json is missing: run `dbt parse` in dbt_semantic/ first")
    return json.loads(path.read_text())


def _metrics():
    return {m["name"]: m for m in _manifest()["metrics"]}


@tool
def list_metrics() -> list[dict]:
    """List the governed business metrics you can ask for, with their exact definitions. Always start here."""
    return [{"name": n, "label": m.get("label"), "definition": m.get("description")} for n, m in _metrics().items()]


@tool
def describe_metric(metric: str) -> dict:
    """Describe one metric: its definition and the dimensions you may group or filter it by.
    Args: metric: a name returned by list_metrics."""
    m = _metrics().get(metric)
    if not m:
        return {"error": f"unknown metric {metric!r}", "valid_metrics": sorted(_metrics())}
    return {"name": metric, "definition": m.get("description"), "type": m["type"],
            "dimensions": {k: DIM_HELP[k] for k in DIMENSIONS},
            "notes": ["Only orders with status paid, shipped or completed count toward any metric.",
                      "Dates are calendar days; pick day_taipei or day_utc explicitly and say which one you used."]}


@tool
def query_metric(metrics: list[str], group_by: list[str] | None = None, start_date: str | None = None,
                 end_date: str | None = None, day_basis: str = "taipei", filters: dict[str, str] | None = None,
                 order_by: str | None = None, limit: int = 50) -> dict:
    """Query one or more governed metrics.
    Args:
      metrics: metric names from list_metrics, e.g. ["revenue"].
      group_by: dimension names from describe_metric, e.g. ["product_category"] or ["day_taipei"].
      start_date / end_date: inclusive calendar days 'YYYY-MM-DD' (both optional).
      day_basis: which calendar the start/end dates refer to, "taipei" (default) or "utc".
      filters: equality filters, e.g. {"product_category": "sports"}; keys: product_category, customer_membership_tier, order_status.
      order_by: a group_by name or a metric name, prefix with '-' for descending.
      limit: maximum rows (at most 200).
    Returns rows plus the definitions used. Values are exact; do not round or estimate."""
    group_by = group_by or []
    metrics_ok = _metrics()
    bad = [m for m in metrics if m not in metrics_ok]
    if not metrics or bad:
        return {"error": f"unknown metric(s) {bad or metrics!r}", "valid_metrics": sorted(metrics_ok)}
    bad = [g for g in group_by if g not in DIMENSIONS]
    if bad:
        return {"error": f"unknown dimension(s) {bad}", "valid_dimensions": sorted(DIMENSIONS)}
    if day_basis not in ("taipei", "utc"):
        return {"error": "day_basis must be 'taipei' or 'utc'"}
    where = []
    time_dim = "order_item__ordered_date_taipei" if day_basis == "taipei" else "order_item__ordered_date_utc"
    for op, value in ((">=", start_date), ("<=", end_date)):
        if value is not None:
            if not _DATE.match(value):
                return {"error": f"dates must look like 2026-01-19, got {value!r}"}
            where.append("{{ TimeDimension('%s', 'day') }} %s DATE '%s'" % (time_dim, op, value))
    for key, value in (filters or {}).items():
        if key not in DIMENSIONS or DIMENSIONS[key][1] != "categorical":
            return {"error": f"cannot filter on {key!r}", "valid_filters": [k for k, v in DIMENSIONS.items() if v[1] == "categorical"]}
        if not _VALUE.match(str(value)):
            return {"error": f"invalid filter value {value!r}"}
        where.append("{{ Dimension('%s') }} = '%s'" % (DIMENSIONS[key][0], value))
    cmd = [str(MF), "query", "--metrics", ",".join(metrics), "--limit", str(max(1, min(int(limit), 200)))]
    if group_by:
        cmd += ["--group-by", ",".join(DIMENSIONS[g][0] for g in group_by)]
    if where:
        cmd += ["--where", " AND ".join(where)]
    if order_by:
        name = order_by.lstrip("-")
        mapped = DIMENSIONS[name][0] if name in DIMENSIONS else name
        if name not in DIMENSIONS and name not in metrics_ok:
            return {"error": f"cannot order by {order_by!r}"}
        cmd += ["--order", ("-" if order_by.startswith("-") else "") + mapped]
    out = Path(tempfile.mkdtemp()) / "r.csv"
    cmd += ["--csv", str(out)]
    try:
        r = subprocess.run(cmd, cwd=SEMANTIC_DIR, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
    except subprocess.TimeoutExpired:
        return {"error": "the query timed out"}
    if r.returncode != 0 or not out.exists():
        msg = re.sub(r"\x1b\[[0-9;]*[mKJH]", "", r.stdout + r.stderr)
        m = re.search(r'message="([^"]+)"', msg)
        return {"error": (m.group(1) if m else msg.strip().splitlines()[0])[:300]}
    rows = []
    for row in csv.DictReader(open(out)):
        item = {}
        for col, val in row.items():
            key = _BACK.get(col, col)
            if key.startswith("day_"):
                item[key] = val[:10]
            elif key in metrics_ok:
                item[key] = round(float(val), 2) if val not in ("", None) else None      # a day with no orders comes back empty
            else:
                item[key] = val
        rows.append(item)
    empty = bool(rows) and all(v is None for row in rows for k, v in row.items() if k in metrics_ok)
    return {"rows": rows, "row_count": len(rows),
            **({"warning": "the metric value is null: no order matched these filters, which is not the same as an error"} if empty else {}),
            "definitions": {m: metrics_ok[m].get("description") for m in metrics},
            "day_basis": day_basis if (start_date or end_date or any(g.startswith("day_") for g in group_by)) else None}


SEMANTIC_TOOLS = [list_metrics, describe_metric, query_metric]
