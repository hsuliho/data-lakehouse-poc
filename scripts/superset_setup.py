"""Create (or update) the Superset connection, datasets, charts and dashboard from sql/reports/*.sql. Idempotent.
Superset only ever talks to Trino; the report SQL does the local-time conversion, so nothing stored changes zone."""
import json
from pathlib import Path
import requests

BASE = "http://localhost:8088"
ROOT = Path(__file__).resolve().parent.parent
s = requests.Session()
tok = s.post(f"{BASE}/api/v1/security/login", json={"username": "admin", "password": "admin", "provider": "db", "refresh": True}).json()["access_token"]
s.headers["Authorization"] = f"Bearer {tok}"
s.headers["X-CSRFToken"] = s.get(f"{BASE}/api/v1/security/csrf_token/").json()["result"]
s.headers["Referer"] = BASE


def find(kind, col, value):
    q = json.dumps({"filters": [{"col": col, "opr": "eq", "value": value}]})
    res = s.get(f"{BASE}/api/v1/{kind}/", params={"q": q}).json()["result"]
    return res[0]["id"] if res else None


def upsert(kind, col, value, body):
    i = find(kind, col, value)
    r = s.put(f"{BASE}/api/v1/{kind}/{i}", json=body) if i else s.post(f"{BASE}/api/v1/{kind}/", json=body)
    assert r.status_code in (200, 201), (kind, value, r.status_code, r.text[:400])
    return i or r.json()["id"]


db = upsert("database", "database_name", "Lakehouse (Trino)",
            {"database_name": "Lakehouse (Trino)", "sqlalchemy_uri": "trino://analyst@trino:8080/iceberg", "expose_in_sqllab": True})

ds = {}
for name in ("daily_revenue_taipei", "category_revenue_rank", "tier_summary", "customer_upgrade_before_after"):
    sql = (ROOT / "sql" / "reports" / f"{name}.sql").read_text().strip()
    i = find("dataset", "table_name", name)
    body = {"database": db, "schema": "dwh_spark", "table_name": name, "sql": sql}
    r = s.put(f"{BASE}/api/v1/dataset/{i}", json={"sql": sql}) if i else s.post(f"{BASE}/api/v1/dataset/", json=body)
    assert r.status_code in (200, 201), (name, r.status_code, r.text[:400])
    ds[name] = i or r.json()["id"]
    s.put(f"{BASE}/api/v1/dataset/{ds[name]}/refresh")                 # re-read the columns after the SQL changed

dash = upsert("dashboard", "slug", "lakehouse-poc", {"dashboard_title": "Lakehouse POC: revenue", "slug": "lakehouse-poc", "published": True})
metric = lambda col, agg="SUM", label=None: {"expressionType": "SIMPLE", "column": {"column_name": col}, "aggregate": agg, "label": label or f"{agg}({col})"}
charts = [
    ("Total revenue", "daily_revenue_taipei", "big_number_total", {"metric": metric("revenue", label="revenue")}, 4),
    ("Revenue per Taipei day, by category", "daily_revenue_taipei", "echarts_timeseries_bar",
     {"x_axis": "taipei_day", "time_grain_sqla": "P1D", "metrics": [metric("revenue", label="revenue")], "groupby": ["category"], "stack": True, "row_limit": 10000}, 8),
    ("Revenue share by category", "category_revenue_rank", "pie", {"groupby": ["category"], "metric": metric("revenue", label="revenue"), "row_limit": 100}, 4),
    ("Average order value by membership tier (tier at order time)", "tier_summary", "echarts_timeseries_bar",
     {"x_axis": "membership_tier", "metrics": [metric("avg_order_value", label="avg_order_value")], "groupby": [], "row_limit": 100}, 4),
    ("Customer 1: orders and revenue before / after the upgrade", "customer_upgrade_before_after", "table",
     {"query_mode": "raw", "all_columns": ["membership_tier", "orders", "revenue", "first_order_taipei", "last_order_taipei"], "row_limit": 100}, 4),
]
ids = []
for title, dataset, viz, extra, width in charts:
    params = {"viz_type": viz, "datasource": f"{ds[dataset]}__table", **extra}
    ids.append((upsert("chart", "slice_name", title, {"slice_name": title, "viz_type": viz, "datasource_id": ds[dataset], "datasource_type": "table",
                                                    "params": json.dumps(params), "dashboards": [dash]}), title, width))

# layout: a row of 12 grid columns per band
pos = {"DASHBOARD_VERSION_KEY": "v2", "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
       "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
       "HEADER_ID": {"id": "HEADER_ID", "type": "HEADER", "meta": {"text": "Lakehouse POC: revenue (day = Taipei, converted at query time; storage is UTC)"}}}
row, used, n = None, 12, 0
for cid, title, w in ids:
    if used + w > 12:
        n += 1
        row = f"ROW-{n}"
        pos[row] = {"type": "ROW", "id": row, "children": [], "parents": ["ROOT_ID", "GRID_ID"], "meta": {"background": "BACKGROUND_TRANSPARENT"}}
        pos["GRID_ID"]["children"].append(row)
        used = 0
    node = f"CHART-{cid}"
    pos[node] = {"type": "CHART", "id": node, "children": [], "parents": ["ROOT_ID", "GRID_ID", row], "meta": {"width": w, "height": 50, "chartId": cid, "sliceName": title}}
    pos[row]["children"].append(node)
    used += w
r = s.put(f"{BASE}/api/v1/dashboard/{dash}", json={"position_json": json.dumps(pos)})
assert r.status_code == 200, r.text[:300]
print(f"database={db} datasets={ds} dashboard={dash} charts={[c[0] for c in ids]}")
print(f"open: {BASE}/superset/dashboard/lakehouse-poc/   login admin / admin (local POC only)")
