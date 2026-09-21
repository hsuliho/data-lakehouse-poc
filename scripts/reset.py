"""Drop every table/view in ods and dwh (dev reset). Data files are left for orphan cleanup. Trino lists, Spark drops (ADR 0018)."""
import load
from load import run

cur, sp = load.conn().cursor(), load.spark()
for schema in ("dwh_spark", "ods"):
    for name, kind in run(cur, f"SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = '{schema}'"):
        run(sp, f"DROP {'VIEW' if kind == 'VIEW' else 'TABLE'} {schema}.{name}")
        print("dropped", schema, name)
