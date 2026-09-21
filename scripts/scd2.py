"""Hand-written SCD2 for dim_product / dim_customer on Spark. Versions are rebuilt from the whole history in ods.*_raw
for the keys a delivery touched (sql/scd2/rebuild.sql). The old batch-driven variants are in archive/scd2_batch.

usage: scd2.py init | run <YYYY-MM-DD> [--dim dim_product|dim_customer]
"""
import os
import sys
from pathlib import Path
from pyhive import hive

SQL = Path(__file__).resolve().parent.parent / "sql" / "scd2"
DIMS = {
    "dim_product": dict(raw="ods.products_raw", key="product_id", sk="product_sk",
                        attrs=[("product_name", "name", "STRING"), ("category", "category", "STRING"),
                               ("price", "price", "DECIMAL(12,2)")]),
    "dim_customer": dict(raw="ods.customers_raw", key="customer_id", sk="customer_sk",
                         attrs=[("customer_name", "name", "STRING"), ("city", "city", "STRING"),
                                ("membership_tier", "membership_tier", "STRING")]),
}


def params(dim, d, dt=""):
    a = d["attrs"]
    return dict(
        dim=dim, key=d["key"], sk=d["sk"], raw=d["raw"], dt=dt,
        attr_ddl=", ".join(f"{n} {t}" for n, _, t in a),
        attr_names=", ".join(n for n, _, _ in a),
        attr_from_s=", ".join(f"s.{n}" for n, _, _ in a),
        attr_cols=", ".join(f"{src} AS {n}" for n, src, _ in a),
        attr_lags=", ".join(f"lag({src}) OVER w AS p_{src}" for _, src, _ in a),
        changed_expr=" OR ".join(f"NOT ({src} <=> p_{src})" for _, src, _ in a),
        attr_diff=" OR ".join(f"NOT (t.{n} <=> s.{n})" for n, _, _ in a),
        attr_set=", ".join(f"t.{n} = s.{n}" for n, _, _ in a),
    )


def statements(template, p):
    text = (SQL / template).read_text()
    for k, v in p.items():
        text = text.replace("{" + k + "}", v)
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))  # full-line comments
    return [s.strip() for s in text.split(";") if s.strip()]


def execute(cur, template, p):
    for stmt in statements(template, p):
        cur.execute(stmt)


def main():
    cur = hive.connect(host=os.environ.get("SPARK_HOST", "localhost"), port=10000, username="spark", database="default").cursor()
    cur.execute("SELECT current_timezone()")
    zone = cur.fetchall()[0][0]
    if zone not in ("Etc/UTC", "UTC"):  # defensive: the surrogate key and as-of joins must not depend on a session zone
        raise RuntimeError(f"Spark session zone is {zone!r}, expected UTC")
    if sys.argv[1] == "init":
        for dim, d in DIMS.items():
            execute(cur, "create_dim.sql", params(dim, d))
        return
    dt = sys.argv[2]
    only = sys.argv[sys.argv.index("--dim") + 1] if "--dim" in sys.argv else None          # one dimension, for the orchestrator
    for dim, d in DIMS.items():
        if only and dim != only:
            continue
        execute(cur, "rebuild.sql", params(dim, d, dt))
        cur.execute(f"SELECT count(*) FROM dwh_spark.{dim}"); print(f"scd2 dt={dt} {dim:13} versions: {cur.fetchall()[0][0]}")


if __name__ == "__main__":
    main()
