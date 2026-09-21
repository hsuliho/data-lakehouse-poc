"""Equivalence of run order (ADR 0015 decision 11): a backfill must leave the same tables as a run in date order.

usage: check_equivalence.py export <file> | compare <fileA> <fileB> [--ignore-column dt]
export writes every table (all columns except the load / build timestamps, which record WHEN, not WHAT) as sorted rows."""
import json, sys
from decimal import Decimal
import load
from load import run

TABLES = [f"ods.{t}{s}" for t in load.TABLES for s in ("_raw", "")] + \
         [f"dwh_spark.{t}" for t in ("dim_product", "dim_customer", "fact_orders", "dws_daily_revenue", "ads_category_revenue_rank")]
VOLATILE = {"_loaded_at", "_built_at", "source_built_at"}


def export(path):
    cur, out = load.conn().cursor(), {}
    for t in TABLES:
        schema, name = t.split(".")
        cols = [c for (c,) in run(cur, f"SELECT column_name FROM information_schema.columns WHERE table_schema = '{schema}' AND table_name = '{name}' ORDER BY ordinal_position") if c not in VOLATILE]
        rows = run(cur, f"SELECT {', '.join(cols)} FROM {t}")
        fix = lambda v: str(v) if v is not None else None
        out[t] = {"columns": cols, "rows": sorted([[fix(v) for v in r] for r in rows], key=lambda r: [x or "" for x in r])}
    json.dump(out, open(path, "w"))
    print(f"exported {len(out)} tables, {sum(len(v['rows']) for v in out.values())} rows -> {path}")


def drop(tbl, cols):
    """The table without the named columns (e.g. the delivery date `dt`, which differs when the same records are delivered in other groups)."""
    keep = [i for i, c in enumerate(tbl["columns"]) if c not in cols]
    return {"columns": [tbl["columns"][i] for i in keep], "rows": [[r[i] for i in keep] for r in tbl["rows"]]}


def compare(a, b, ignore=()):
    A, B, bad = json.load(open(a)), json.load(open(b)), 0
    for t in TABLES:
        A[t], B[t] = drop(A[t], ignore), drop(B[t], ignore)
        ra, rb = {tuple(r) for r in A[t]["rows"]}, {tuple(r) for r in B[t]["rows"]}
        if A[t]["columns"] != B[t]["columns"] or len(A[t]["rows"]) != len(B[t]["rows"]) or ra != rb:
            bad += 1
            print(f"DIFF {t}: {len(A[t]['rows'])} vs {len(B[t]['rows'])} rows; only in A {sorted(ra - rb, key=str)[:2]}; only in B {sorted(rb - ra, key=str)[:2]}")
        else:
            print(f"same {t}: {len(ra)} rows")
    print("EQUIVALENT" if not bad else f"NOT EQUIVALENT: {bad} table(s) differ")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    ig = sys.argv[sys.argv.index("--ignore-column") + 1].split(",") if "--ignore-column" in sys.argv else []
    export(sys.argv[2]) if sys.argv[1] == "export" else compare(sys.argv[2], sys.argv[3], ig)
