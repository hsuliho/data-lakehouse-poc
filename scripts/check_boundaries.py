"""Time-zone boundary suite (batch5). Rules under test:
  * every stored time is an instant (timestamptz); the database shows UTC; only clients convert to local time
  * the pipeline's day is the UTC day; as-of intervals are half-open [valid_from, valid_to)
  * conversions are defended: input is normalised or rejected, sessions are pinned, and the two conversion
    expressions used inside the pipeline give the same answer in any session zone.
Expected values below are derived by hand from the instants in landing/ (the batch5 rows), NOT from pipeline code."""
import trino
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pyhive import hive
import load
from load import run, normalize_ts

S = "dwh_spark"
q = lambda sql: run(cur, sql)
cur = load.conn().cursor()

# ---- hand-derived truth: order -> (UTC instant, UTC day, category valid then, Taipei date a local client should show)
# product 7 changes toys -> sports at 2026-01-30T00:00:00Z
T = {
    61: ("2026-01-29 23:59:59.999999", "2026-01-29", "toys",   "2026-01-30"),  # last microsecond before the change
    62: ("2026-01-30 00:00:00.000000", "2026-01-30", "sports", "2026-01-30"),  # exactly valid_from: half-open, new version
    63: ("2026-01-30 00:00:00.000001", "2026-01-30", "sports", "2026-01-30"),
    64: ("2026-01-30 00:00:00.000000", "2026-01-30", "sports", "2026-01-30"),  # fed as 08:00:00+08:00, same instant as 62
    65: ("2026-01-29 15:59:59.000000", "2026-01-29", "toys",   "2026-01-29"),  # fed as 23:59:59+08:00
    66: ("2026-01-29 16:00:00.000000", "2026-01-29", "toys",   "2026-01-30"),  # fed with Z; Taipei midnight
    67: ("2026-01-29 15:59:59.999999", "2026-01-29", "toys",   "2026-01-29"),  # 1 microsecond before Taipei midnight
    68: ("2026-01-29 09:00:00.000000", "2026-01-29", "toys",   "2026-01-29"),  # ties the old watermark; the daily-delivery design (ADR 0015) keeps it
}

# 1. no naive timestamp column anywhere in ods / dwh_spark tables
naive = q("""SELECT c.table_schema, c.table_name, c.column_name, c.data_type FROM iceberg.information_schema.columns c
             JOIN iceberg.information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
             WHERE c.table_schema IN ('ods', 'dwh_spark') AND t.table_type = 'BASE TABLE'
               AND c.data_type LIKE 'timestamp%' AND c.data_type NOT LIKE '%with time zone'""")
assert not naive, naive
print("PASS 1  no timestamp column without a time zone in ods / dwh_spark")

# 2. the loader normalises offsets exactly and refuses to guess
assert normalize_ts("2026-01-30T08:00:00+08:00") == normalize_ts("2026-01-30 00:00:00") == "2026-01-30 00:00:00.000000 UTC"
assert normalize_ts("2026-01-29T16:00:00Z") == "2026-01-29 16:00:00.000000 UTC"
for bad in ("2026-01-30 08:00:00 CST", "Jan 30 2026", "30/01/2026", "1969-12-31 23:59:59"):
    try:
        normalize_ts(bad); raise AssertionError(f"accepted {bad!r}")
    except ValueError:
        pass
print("PASS 2  loader: +08:00 and Z converted exactly, naive = UTC by contract, abbreviations / free text / out-of-range rejected")

# 3. stored instant, UTC day and as-of category for every boundary order
rows = q(f"""SELECT f.order_id, CAST(f.ordered_at AT TIME ZONE 'UTC' AS VARCHAR), CAST(date(f.ordered_at AT TIME ZONE 'UTC') AS VARCHAR), p.category
             FROM {S}.fact_orders f JOIN {S}.dim_product p ON p.product_sk = f.product_sk WHERE f.order_id >= 61""")
got = {r[0]: (r[1], r[2], r[3]) for r in rows}
assert set(got) == set(T), sorted(got)
for oid, (inst, day, cat, _) in T.items():
    assert got[oid] == (inst + " UTC", day, cat), (oid, got[oid], (inst + " UTC", day, cat))
print("PASS 3  8 orders: instant (microseconds intact), UTC day and as-of category match; order 62 sits exactly on valid_from -> new version")

# 4. DWS: buckets by the UTC day
exp = {("2026-01-29", "toys"): 5 * 155.75, ("2026-01-30", "sports"): 3 * 155.75}
act = {(d, c): float(r) for d, c, r in q(f"SELECT CAST(revenue_date AS VARCHAR), category, revenue FROM {S}.dws_daily_revenue WHERE revenue_date >= DATE '2026-01-29'")}
assert act == exp, (act, exp)
print("PASS 4  DWS (UTC day, category):", {k: round(v, 2) for k, v in sorted(exp.items())})

# 5. local display happens at the edge, from instants (Python zoneinfo here; a BI tool or an AT TIME ZONE query elsewhere)
tpe = ZoneInfo("Asia/Taipei")
local = {}
for oid, (inst, _, _, tdate) in T.items():
    t = datetime.fromisoformat(inst).replace(tzinfo=timezone.utc).astimezone(tpe)
    assert str(t.date()) == tdate, (oid, t, tdate)
    local[oid] = t
differ = [oid for oid, (_, day, _, tdate) in T.items() if day != tdate]
assert differ == [61, 66], differ
sql_local = {d: float(r) for d, r in q(f"""SELECT CAST(date(ordered_at AT TIME ZONE 'Asia/Taipei') AS VARCHAR), sum(line_amount)
                                            FROM {S}.fact_orders WHERE order_id >= 61 GROUP BY 1""")}
assert sql_local == {"2026-01-29": 3 * 155.75, "2026-01-30": 5 * 155.75}, sql_local
print(f"PASS 5  edge conversion: orders {differ} have a different Taipei date than UTC date; a Taipei-day report "
      f"computed from instants gives {sql_local}, whereas the UTC-day DWS says 778.75 / 467.25 (same total)")

# 6. Trino: a naive literal compared with a timestamptz column is read in the SESSION zone; an explicit-UTC literal is not.
#    (timestamptz -> timestamp casts use the value's own zone, which is UTC for Iceberg, so they are not the risk.)
def trino_session(zone):
    return trino.dbapi.connect(host="localhost", port=8080, user="engineer", catalog="iceberg", schema="ods", timezone=zone).cursor()
lit_safe = "TIMESTAMP '2026-01-30 00:00:00 UTC'"
lit_naive = "TIMESTAMP '2026-01-30 00:00:00'"
res = {}
for zone in ("UTC", "Asia/Taipei"):
    c = trino_session(zone)
    res[zone] = {k: run(c, f"SELECT order_id FROM {S}.fact_orders WHERE order_id >= 61 AND ordered_at >= {lit} ORDER BY 1")
                 for k, lit in (("safe", lit_safe), ("naive", lit_naive))}
    res[zone]["day"] = run(c, f"SELECT order_id, CAST(date(ordered_at AT TIME ZONE 'UTC') AS VARCHAR) FROM {S}.fact_orders WHERE order_id >= 61 ORDER BY 1")
assert res["UTC"]["safe"] == res["Asia/Taipei"]["safe"] and res["UTC"]["day"] == res["Asia/Taipei"]["day"]
assert res["UTC"]["naive"] != res["Asia/Taipei"]["naive"], "expected the naive literal to be read in the session zone"
gained = sorted({r[0] for r in res["Asia/Taipei"]["naive"]} ^ {r[0] for r in res["UTC"]["naive"]})
print(f"PASS 6  Trino: an explicit-UTC literal and AT TIME ZONE 'UTC' give the same rows in UTC and Asia/Taipei sessions; "
      f"the naive literal selects a different set (orders {gained} change side) in the Taipei session")

# 7. Spark: the pipeline's utc_date() expression is session-independent; a bare to_date() is not; the key hash does not use text
h = hive.connect(host="localhost", port=10000, username="spark", database="default").cursor()
def spark(zone, sql):
    h.execute(f"SET spark.sql.session.timeZone={zone}"); h.execute(sql); return h.fetchall()
macro = "to_date(convert_timezone(current_timezone(), 'UTC', cast(ordered_at as timestamp_ntz)))"
out = {}
for zone in ("Etc/UTC", "Asia/Taipei"):
    out[zone] = {
        "macro": spark(zone, f"SELECT order_id, cast({macro} as string) FROM {S}.fact_orders WHERE order_id >= 61 ORDER BY 1"),
        "bare": spark(zone, f"SELECT order_id, cast(to_date(ordered_at) as string) FROM {S}.fact_orders WHERE order_id >= 61 ORDER BY 1"),
        "sk_text": spark(zone, f"SELECT cast(valid_from as string) FROM {S}.dim_product WHERE product_id = 7 ORDER BY valid_from"),
        "sk_micros": spark(zone, f"SELECT cast(unix_micros(valid_from) as string) FROM {S}.dim_product WHERE product_id = 7 ORDER BY valid_from"),
    }
h.execute("SET spark.sql.session.timeZone=Etc/UTC")
assert out["Etc/UTC"]["macro"] == out["Asia/Taipei"]["macro"]
assert out["Etc/UTC"]["sk_micros"] == out["Asia/Taipei"]["sk_micros"]
assert out["Etc/UTC"]["bare"] != out["Asia/Taipei"]["bare"] and out["Etc/UTC"]["sk_text"] != out["Asia/Taipei"]["sk_text"]
moved = [a[0] for a, b in zip(out["Etc/UTC"]["bare"], out["Asia/Taipei"]["bare"]) if a != b]
print(f"PASS 7  Spark: utc_date() and unix_micros() identical in UTC and Asia/Taipei sessions; bare to_date() moves orders {moved}, "
      f"and casting valid_from to text changes ({out['Etc/UTC']['sk_text'][-1][0]} vs {out['Asia/Taipei']['sk_text'][-1][0]}) -> the surrogate key hashes unix_micros")

# 8. the old watermark tie (order 68 dropped, its item orphaned) is gone under daily deliveries (ADR 0015)
present = {r[0] for r in q("SELECT order_id FROM ods.orders WHERE order_id >= 61")}
orphans = q("SELECT count(*) FROM ods.order_items i LEFT JOIN ods.orders o ON o.order_id = i.order_id WHERE o.order_id IS NULL")[0][0]
assert 68 in present and orphans == 0
print("PASS 8  order 68 (updated_at equal to the old watermark) is kept and has its item: no orphan item")
