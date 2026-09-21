"""Step 1 acceptance (ADR 0015): landing -> raw -> current state -> SCD2.
Expected results are derived here from the landing files with plain Python (dedupe by event_id, order by (changed_at, event_id),
newest record per key wins, version rules), NOT by the SQL under test.

usage: check_delivery.py   (after replay.sh has loaded every delivery)
"""
import csv, shutil
from datetime import datetime, timezone
from pathlib import Path

import load
from load import run, TABLES, DeliveryError, LANDING, normalize_ts

cur = load.conn().cursor()
UTC = timezone.utc
T0 = datetime(1970, 1, 1, tzinfo=UTC)
inst = lambda v: datetime.fromisoformat(normalize_ts(v)[:-4]).replace(tzinfo=UTC)


def read_landing():
    """{table: {key: [record...]}} sorted by (changed_at, event_id), duplicates dropped."""
    out = {}
    for t, (key, cols) in TABLES.items():
        seen, recs = set(), []
        for d in sorted(LANDING.glob("dt=*")):
            for r in csv.DictReader(open(d / f"{t}.csv")):
                if r["event_id"] not in seen:
                    seen.add(r["event_id"]); recs.append(r)
        by = {}
        for r in sorted(recs, key=lambda r: (inst(r["changed_at"]), r["event_id"])):
            by.setdefault(r[key], []).append(r)
        out[t] = by
    return out


def fail(msg):
    raise SystemExit("FAIL " + msg)


land = read_landing()

# 1. current state: newest record per key, deleted keys absent
for t, (key, cols) in TABLES.items():
    want = {k: rs[-1] for k, rs in land[t].items() if rs[-1]["is_deleted"] == "false"}
    got = {str(r[0]): r for r in run(cur, f"SELECT {key}, {', '.join(c for c, _ in cols if c != key)} FROM ods.{t}")}
    if set(want) != set(got):
        fail(f"ods.{t} keys differ: only expected {sorted(set(want) - set(got))[:5]}, only actual {sorted(set(got) - set(want))[:5]}")
    names = [c for c, _ in cols if c != key]
    for k, w in want.items():
        for i, c in enumerate(names, start=1):
            a, e = got[k][i], w["changed_at" if c == "updated_at" else c]
            typ = dict(cols)[c]
            ok = (a == inst(e)) if typ.startswith("TIMESTAMP") else (float(a) == float(e)) if typ in ("BIGINT", "INT") or typ.startswith("DECIMAL") else a == e
            if not ok:
                fail(f"ods.{t} {key}={k} column {c}: actual {a!r}, expected {e!r}")
    print(f"PASS current state ods.{t}: {len(want)} keys equal the newest Change record per key")

# 2. SCD2 versions from the full history
DIMS = {"dim_product": ("products", "product_id", ["name", "category", "price"]),
        "dim_customer": ("customers", "customer_id", ["name", "city", "membership_tier"])}
for dim, (t, key, attrs) in DIMS.items():
    want = set()
    for k, rs in land[t].items():
        starts, prev = [], None
        for r in rs:
            sig = tuple(r[a] for a in attrs)
            if r["is_deleted"] == "true":
                starts.append((inst(r["changed_at"]), None)); prev = "deleted"
            elif prev is None or prev == "deleted" or sig != prev:
                starts.append((inst(r["changed_at"]), r)); prev = sig
        for i, (at, r) in enumerate(starts):
            if r is None:
                continue
            nxt = starts[i + 1][0] if i + 1 < len(starts) else None
            want.add((int(k), T0 if i == 0 else at, nxt, r["name"], r[attrs[1]], str(float(r[attrs[2]])) if t == "products" else r[attrs[2]]))
    dcol = {"dim_product": "product_name, category, price", "dim_customer": "customer_name, city, membership_tier"}[dim]
    got = {(r[0], r[1], r[2], r[3], r[4], str(float(r[5])) if t == "products" else r[5])
           for r in run(cur, f"SELECT {key}, valid_from, valid_to, {dcol} FROM dwh_spark.{dim}")}
    if want != got:
        fail(f"{dim}: only expected {sorted(want - got, key=str)[:3]}; only actual {sorted(got - want, key=str)[:3]}")
    cur_n = run(cur, f"SELECT count(*) FROM dwh_spark.{dim} WHERE is_current")[0][0]
    live = sum(1 for rs in land[t].values() if rs[-1]["is_deleted"] == "false")
    if cur_n != live:
        fail(f"{dim}: {cur_n} current versions, {live} live keys")
    print(f"PASS {dim}: {len(want)} versions equal the independent derivation; one current version per live key ({cur_n})")

# 3. the planted cases
assert run(cur, "SELECT order_status FROM ods.orders WHERE order_id = 1") == [["completed"]]
print("PASS out-of-order: order 1 'shipped' arrived after 'completed' and did not overwrite it")
assert run(cur, "SELECT count(*) FROM ods.orders_raw WHERE order_id = 68")[0][0] == 2 and run(cur, "SELECT count(*) FROM ods.orders WHERE order_id = 68")[0][0] == 1
print("PASS duplicate delivery: order 68 is in raw twice (two days), once in ODS")
v = run(cur, "SELECT valid_to, is_current FROM dwh_spark.dim_customer WHERE customer_id = 24")
assert len(v) == 1 and v[0][1] is False and v[0][0] == datetime(2026, 1, 27, 10, tzinfo=UTC).astimezone(v[0][0].tzinfo), v
print("PASS delete: customer 24 has one closed version (valid_to 2026-01-27 10:00 UTC), no current version, absent from ODS")

# 4. idempotence: redeliver and rebuild days, nothing may change (values or _loaded_at)
snap = lambda: (run(cur, "SELECT max(_loaded_at), count(*) FROM ods.orders"), run(cur, "SELECT max(_loaded_at), count(*) FROM ods.customers"),
                run(cur, "SELECT max(_loaded_at), count(*) FROM dwh_spark.dim_product"), run(cur, "SELECT max(_loaded_at), count(*) FROM dwh_spark.dim_customer"),
                run(cur, "SELECT count(*) FROM ods.orders_raw"))
before = snap()
import subprocess, sys
for d in ("2026-01-29", "2026-01-30", "2026-01-31"):
    load.deliver(d)
    subprocess.run([sys.executable, str(Path(__file__).parent / "scd2.py"), "run", d], check=True, capture_output=True)
if snap() != before:
    fail(f"redelivery changed state:\n{before}\n{snap()}")
print("PASS idempotent: redelivering and rebuilding 3 days changed no row and no _loaded_at")

# 5. rejections: nothing may be written
TMP = LANDING / "dt=2099-01-01"
def attempt(setup, label):
    shutil.rmtree(TMP, ignore_errors=True); TMP.mkdir()
    for t in TABLES:
        shutil.copy(LANDING / "dt=2026-01-29" / f"{t}.csv", TMP / f"{t}.csv")
    setup()
    try:
        load.deliver("2099-01-01"); fail(f"{label}: accepted")
    except DeliveryError as e:
        print(f"PASS rejected ({label}): {e}"[:150])
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    assert run(cur, "SELECT count(*) FROM ods.customers_raw WHERE dt = DATE '2099-01-01'")[0][0] == 0
attempt(lambda: None, "no _SUCCESS marker")
def drift(): (TMP / "_SUCCESS").write_text(""); p = TMP / "customers.csv"; p.write_text(p.read_text().replace("membership_tier", "tier", 1))
attempt(drift, "schema drift")
def future(): (TMP / "_SUCCESS").write_text(""); p = TMP / "customers.csv"; p.write_text(p.read_text().replace("2026-01-10 08:01:00", "2099-01-02 00:00:00", 1))
attempt(future, "changed_at after the delivery day")

# 6. the delivery day is a half-open window: a change may be stamped up to the last microsecond of dt, never at dt + 1 day 00:00
def stamped(ts):
    shutil.rmtree(TMP, ignore_errors=True); TMP.mkdir(); (TMP / "_SUCCESS").write_text("")
    for t in TABLES:
        shutil.copy(LANDING / "dt=2026-01-29" / f"{t}.csv", TMP / f"{t}.csv")
    p = TMP / "customers.csv"; p.write_text(p.read_text().replace("2026-01-10 08:01:00", ts, 1))
    try:
        load.read_delivery("2099-01-01"); return True                    # validation only, nothing is written
    except DeliveryError:
        return False
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
assert stamped("2099-01-01 00:00:00") and stamped("2099-01-01 23:59:59.999999"), "the delivery day itself must be accepted"
assert not stamped("2099-01-02 00:00:00"), "dt + 1 day 00:00:00 must be rejected (upper bound excluded)"
print("PASS half-open delivery day: dt 00:00:00 and dt 23:59:59.999999 accepted, dt+1 00:00:00 rejected")
print("ALL PASS")
