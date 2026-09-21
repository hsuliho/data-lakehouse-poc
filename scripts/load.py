"""Daily delivery -> ods.<t>_raw (partition dt replaced) -> current state ods.<t> for the keys that delivery touched. ADR 0015.
All ingestion and transformation writes go through Spark (ADR 0018); conn()/run() below are the Trino side, for queries only.

usage: load.py init | deliver <YYYY-MM-DD> [--table T] [--stage raw|current|both]
"""
import csv, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import trino
from pyhive import hive

LANDING = Path(__file__).resolve().parent.parent / "landing"
TS = "TIMESTAMP"  # Spark TIMESTAMP is an Iceberg timestamptz (microseconds): every stored time is an absolute instant; the database shows UTC, clients convert to local
# name -> (key, [(col, type)])
TABLES = {
    "customers": ("customer_id", [("customer_id", "BIGINT"), ("name", "STRING"), ("city", "STRING"),
                                  ("membership_tier", "STRING"), ("updated_at", TS)]),
    "products": ("product_id", [("product_id", "BIGINT"), ("name", "STRING"), ("category", "STRING"),
                                ("price", "DECIMAL(12,2)"), ("updated_at", TS)]),
    "orders": ("order_id", [("order_id", "BIGINT"), ("customer_id", "BIGINT"), ("order_status", "STRING"),
                            ("ordered_at", TS), ("updated_at", TS)]),
    "order_items": ("order_item_id", [("order_item_id", "BIGINT"), ("order_id", "BIGINT"), ("product_id", "BIGINT"),
                                      ("quantity", "INT"), ("unit_price", "DECIMAL(12,2)"), ("updated_at", TS)]),
}
NOW = "current_timestamp()"  # an instant, independent of the session zone


def conn():
    """Trino connection, for queries only. Session zone pinned to UTC, and verified: a client left on the machine zone
    (UTC+8) is how literals and displayed values silently drift."""
    c = trino.dbapi.connect(host=os.environ.get("TRINO_HOST", "localhost"), port=8080, user=os.environ.get("TRINO_USER", "engineer"), catalog="iceberg", schema="ods", timezone="UTC")
    zone = run(c.cursor(), "SELECT current_timezone()")[0][0]
    if zone != "UTC":
        raise RuntimeError(f"Trino session zone is {zone!r}, expected 'UTC'")
    return c


def spark():
    """Spark Thrift cursor, for every write. Session zone verified: literals and as-of joins must not depend on it."""
    c = hive.connect(host=os.environ.get("SPARK_HOST", "localhost"), port=10000, username="spark", database="default").cursor()
    zone = run(c, "SELECT current_timezone()")[0][0]
    if zone not in ("Etc/UTC", "UTC"):
        raise RuntimeError(f"Spark session zone is {zone!r}, expected UTC")
    return c


def normalize_ts(v):
    """Any source timestamp -> 'YYYY-MM-DD HH:MM:SS.ffffff UTC'.
    Offset-aware input (+08:00, Z) is converted exactly. A naive string is UTC by contract (the feed carries no zone).
    Zone abbreviations and free-form text are rejected instead of guessed."""
    try:
        t = datetime.fromisoformat(v.strip())
    except ValueError as e:
        raise ValueError(f"unparseable timestamp {v!r}: use ISO 8601, optionally with an offset or Z") from e
    t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
    if not 1970 <= t.year <= 9999:
        raise ValueError(f"timestamp out of range: {v!r}")
    return t.strftime("%Y-%m-%d %H:%M:%S.%f") + " UTC"


def run(cur, sql):
    cur.execute(sql)
    try:
        return cur.fetchall()
    except TypeError:                                       # pyhive: a Spark DDL / INSERT / MERGE has no result set
        return []


def count(cur, t):
    return run(cur, f"SELECT count(*) FROM ods.{t}")[0][0]


class DeliveryError(Exception):
    """The delivery cannot be accepted; nothing was written for it."""


def lit(v, typ):
    if v == "":
        return "NULL"
    if typ.startswith("TIMESTAMP"):
        return f"TIMESTAMP '{normalize_ts(v)}'"
    if typ == "STRING":
        return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"   # Spark string literal: backslash escapes
    return f"CAST('{v}' AS {typ})"  # numerics; input is our own CSV


def raw_cols(cols):
    """Raw = the entity columns (updated_at is called changed_at) + the Change record envelope + the delivery date."""
    return [(("changed_at" if c == "updated_at" else c), ty) for c, ty in cols] + [("event_id", "STRING"), ("is_deleted", "BOOLEAN"), ("dt", "DATE")]


def init(cur):
    run(cur, "CREATE NAMESPACE IF NOT EXISTS ods")
    for t, (_, cols) in TABLES.items():
        part = " PARTITIONED BY (days(ordered_at))" if t == "orders" else ""
        run(cur, f"CREATE TABLE IF NOT EXISTS ods.{t}_raw ({', '.join(f'{c} {ty}' for c, ty in raw_cols(cols))}, _loaded_at {TS}) "
                 "USING iceberg PARTITIONED BY (dt) TBLPROPERTIES ('write.format.default'='parquet')")
        run(cur, f"CREATE TABLE IF NOT EXISTS ods.{t} ({', '.join(f'{c} {ty}' for c, ty in cols)}, _loaded_at {TS}) USING iceberg{part} "
                 "TBLPROPERTIES ('write.format.default'='parquet')")


def read_delivery(dt):
    """Validate one delivery day and return {table: rows}. Raises DeliveryError before anything is written."""
    d = LANDING / f"dt={dt}"
    if not (d / "_SUCCESS").exists():
        raise DeliveryError(f"dt={dt}: no _SUCCESS marker, the delivery is not complete (or not there)")
    end = datetime.fromisoformat(dt).replace(tzinfo=timezone.utc) + timedelta(days=1)
    out = {}
    for t, (key, cols) in TABLES.items():
        want = [("changed_at" if c == "updated_at" else c) for c, _ in cols] + ["event_id", "is_deleted"]
        with open(d / f"{t}.csv") as f:
            rdr = csv.DictReader(f)
            got = rdr.fieldnames
            rows = list(rdr)
        if got != want:
            raise DeliveryError(f"dt={dt} {t}: columns {got} differ from the contract {want}")
        for r in rows:
            if not r["event_id"] or r["is_deleted"] not in ("true", "false") or not r[key]:
                raise DeliveryError(f"dt={dt} {t}: bad event_id / is_deleted / key in {r}")
            at = datetime.fromisoformat(normalize_ts(r["changed_at"])[:-4]).replace(tzinfo=timezone.utc)
            if at >= end:
                raise DeliveryError(f"dt={dt} {t}: changed_at {r['changed_at']} is after the delivery day (event {r['event_id']})")
        out[t] = rows
    return out


def load_raw(cur, dt, t, rows):
    """Raw partition dt of one table = exactly this delivery: one atomic dynamic-partition overwrite (an empty delivery deletes the partition)."""
    key, cols = TABLES[t]
    rc = raw_cols(cols)
    if not rows:
        run(cur, f"DELETE FROM ods.{t}_raw WHERE dt = DATE '{dt}'")
    else:
        typ = dict(rc)
        values = ", ".join("(" + ", ".join([lit(r[c], typ[c]) if c not in ("is_deleted", "dt") else
                                            (r[c].upper() if c == "is_deleted" else f"DATE '{dt}'") for c, _ in rc] + [NOW]) + ")" for r in rows)
        run(cur, f"INSERT OVERWRITE ods.{t}_raw VALUES {values}")
    n = run(cur, f"SELECT count(*) FROM ods.{t}_raw WHERE dt = DATE '{dt}'")[0][0]
    assert n == len(rows), f"{t}: {n} rows in raw dt={dt}, delivery has {len(rows)}"
    print(f"dt={dt} {t:12} raw records={len(rows):3}")


def derive_current(cur, dt, t):
    """Current state of the keys delivery dt touched = their newest Change record (order: changed_at, then event_id)."""
    key, cols = TABLES[t]
    names = [c for c, _ in cols]
    others = [c for c in names if c != key]
    diff = " OR ".join(f"NOT (tgt.{c} <=> s.{c})" for c in others)
    sets = ", ".join(f"{c} = s.{c}" for c in others)
    before = count(cur, t)
    run(cur, f"""
            MERGE INTO ods.{t} tgt
            USING (SELECT {', '.join(n if n != 'updated_at' else 'changed_at AS updated_at' for n in names)}, is_deleted FROM (
                     SELECT r.*, row_number() OVER (PARTITION BY {key} ORDER BY changed_at DESC, event_id DESC) AS rn
                     FROM ods.{t}_raw r WHERE {key} IN (SELECT {key} FROM ods.{t}_raw WHERE dt = DATE '{dt}')) WHERE rn = 1) s
            ON tgt.{key} = s.{key}
            WHEN MATCHED AND s.is_deleted THEN DELETE
            WHEN MATCHED AND NOT s.is_deleted AND ({diff}) THEN UPDATE SET {sets}, _loaded_at = {NOW}
            WHEN NOT MATCHED AND NOT s.is_deleted THEN INSERT ({', '.join(names + ['_loaded_at'])})
                 VALUES ({', '.join([f's.{c}' for c in names] + [NOW])})""")  # ponytail: no partition predicate, a big orders table is scanned whole
    print(f"dt={dt} {t:12} current ods {before} -> {count(cur, t)}")


def deliver(dt, tables=None, stage="both"):
    """Idempotent: replaces raw partition dt from the landing file, then re-derives the current state of the touched keys.
    `tables` and `stage` (raw | current | both) let the orchestrator run one table and one step as its own asset."""
    delivery = read_delivery(dt)                      # validates the whole delivery, whichever table is asked for
    cur = spark()
    for t in tables or TABLES:
        if stage in ("raw", "both"):
            load_raw(cur, dt, t, delivery[t])
        if stage in ("current", "both"):
            derive_current(cur, dt, t)


if __name__ == "__main__":
    if sys.argv[1] == "init":
        init(spark())
    else:
        args = sys.argv[3:]
        opt = lambda name, default=None: args[args.index(name) + 1] if name in args else default
        try:
            deliver(sys.argv[2], [opt("--table")] if opt("--table") else None, opt("--stage", "both"))
        except DeliveryError as e:
            print(f"REJECTED: {e}"); sys.exit(2)
