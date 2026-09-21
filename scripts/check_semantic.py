"""Semantic layer acceptance: every `mf query` answer equals a number derived by a DIFFERENT route
(ODS items and orders joined to the dimension version intervals by hand, not through the fact's surrogate keys)."""
import csv, subprocess, tempfile
from pathlib import Path
import load
from load import run

ROOT = Path(__file__).resolve().parent.parent
cur = load.conn().cursor()                      # engineer: the independent route may read ODS
q = lambda sql: run(cur, sql)
COUNTS = "o.order_status IN ('paid', 'shipped', 'completed')"
FAR = "TIMESTAMP '9999-12-31 00:00:00 UTC'"


def mf(metrics, group_by=None):
    out = Path(tempfile.mkdtemp()) / "r.csv"
    cmd = [str(ROOT / ".venv/bin/mf"), "query", "--metrics", metrics, "--csv", str(out)]
    if group_by:
        cmd += ["--group-by", group_by]
    r = subprocess.run(cmd, cwd=ROOT / "dbt_semantic", capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0 and out.exists(), (r.stdout + r.stderr)[-500:]
    return list(csv.DictReader(open(out)))


def close(a, b):
    return abs(float(a) - float(b)) < 0.005

# 1. revenue = the status filter is applied, and matches the hand-derived total
exp = float(q(f"SELECT sum(i.quantity * i.unit_price) FROM ods.order_items i JOIN ods.orders o ON o.order_id = i.order_id WHERE {COUNTS}")[0][0])
all_statuses = float(q("SELECT sum(i.quantity * i.unit_price) FROM ods.order_items i JOIN ods.orders o ON o.order_id = i.order_id")[0][0])
got = float(mf("revenue")[0]["revenue"])
assert close(got, exp) and not close(got, all_statuses), (got, exp, all_statuses)
print(f"PASS revenue {got:.2f} = ODS-derived {exp:.2f} (all statuses would be {all_statuses:.2f}: order 50, status unknown_status, is excluded)")

# 2. the two "days": Taipei day for the boundary orders, UTC day for the same orders
by_tpe = {r["order_item__ordered_date_taipei__day"][:10]: float(r["revenue"]) for r in mf("revenue", "order_item__ordered_date_taipei__day")}
by_utc = {r["order_item__ordered_date_utc__day"][:10]: float(r["revenue"]) for r in mf("revenue", "order_item__ordered_date_utc__day")}
assert close(by_tpe["2026-01-29"], 467.25) and close(by_tpe["2026-01-30"], 778.75), by_tpe
assert close(by_utc["2026-01-29"], 778.75) and close(by_utc["2026-01-30"], 467.25), by_utc
assert close(sum(by_tpe.values()), got) and close(sum(by_utc.values()), got)
print(f"PASS Taipei day 01-29={by_tpe['2026-01-29']:.2f} 01-30={by_tpe['2026-01-30']:.2f}; UTC day 01-29={by_utc['2026-01-29']:.2f} 01-30={by_utc['2026-01-30']:.2f}; both sum to the total")

# 3. category and tier as of the order time, derived through the dimension version intervals
def expected(dim, key, col):
    rows = q(f"""SELECT d.{col}, sum(i.quantity * i.unit_price), count(DISTINCT o.order_id) FROM ods.order_items i
                 JOIN ods.orders o ON o.order_id = i.order_id
                 JOIN dwh_spark.{dim} d ON d.{key} = {'i.product_id' if dim == 'dim_product' else 'o.customer_id'}
                  AND o.ordered_at >= d.valid_from AND o.ordered_at < coalesce(d.valid_to, {FAR})
                 WHERE {COUNTS} GROUP BY 1""")
    return {r[0]: (float(r[1]), r[2]) for r in rows}
for dim, key, col, sem in (("dim_product", "product_id", "category", "order_item__product_category"),
                           ("dim_customer", "customer_id", "membership_tier", "order_item__customer_membership_tier")):
    e = expected(dim, key, col)
    g = {r[sem]: (float(r["revenue"]), int(r["orders"])) for r in mf("revenue,orders", sem)}
    assert set(e) == set(g) and all(close(e[k][0], g[k][0]) and e[k][1] == g[k][1] for k in e), (e, g)
    print(f"PASS {col} as of order time: {', '.join(f'{k}={v[0]:.2f}' for k, v in sorted(e.items()))}")

# 4. the ratio metric is revenue / orders
orders = int(mf("orders")[0]["orders"]); aov = float(mf("average_order_value")[0]["average_order_value"])
assert close(aov, got / orders), (aov, got, orders)
print(f"PASS average_order_value {aov:.2f} = revenue {got:.2f} / orders {orders}")

# 5. no PII is reachable through the semantic layer
dims = subprocess.run([str(ROOT / ".venv/bin/mf"), "list", "dimensions", "--metrics", "revenue"], cwd=ROOT / "dbt_semantic",
                      capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120).stdout
assert not any(w in dims for w in ("customer_name", "city")), dims
print("PASS no PII dimension is exposed")
