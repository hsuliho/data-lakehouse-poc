"""Stage 7: every report query ties out to numbers derived from ODS / the CSV feed, not from the layers being reported."""
import csv
from datetime import datetime, timezone
from pathlib import Path
import load
from load import run, normalize_ts

ROOT = Path(__file__).resolve().parent.parent
cur = load.conn().cursor()
q = lambda sql: run(cur, sql)
rep = lambda name: q((ROOT / "sql" / "reports" / f"{name}.sql").read_text())

# independent total: straight from ODS (items that have an order), not from fact / DWS / ADS
total = float(q("SELECT sum(i.quantity * i.unit_price) FROM ods.order_items i JOIN ods.orders o ON o.order_id = i.order_id")[0][0])

daily = rep("daily_revenue_taipei")
assert abs(sum(float(r[2]) for r in daily) - total) < 0.005, "daily trend does not tie to ODS"
by_day = {}
for d, c, rev, n in daily:
    by_day[str(d)] = by_day.get(str(d), 0) + float(rev)
assert round(by_day["2026-01-29"], 2) == 467.25 and round(by_day["2026-01-30"], 2) == 778.75, by_day   # hand-derived Taipei days (order 68 adds 155.75 to 01-29)
print(f"PASS daily trend: {len(daily)} (Taipei day, category) rows, total {total:.2f} ties to ODS; Taipei 01-29={by_day['2026-01-29']:.2f} 01-30={by_day['2026-01-30']:.2f}")

rank = rep("category_revenue_rank")
assert abs(sum(float(r[2]) for r in rank) - total) < 0.005 and sorted(r[0] for r in rank) == [1, 2, 3, 4]
print("PASS category rank:", [(r[0], r[1], float(r[2])) for r in sorted(rank)])

tier = rep("tier_summary")
assert abs(sum(float(r[2]) for r in tier) - total) < 0.005
assert sum(r[1] for r in tier) == q("SELECT count(DISTINCT o.order_id) FROM ods.orders o JOIN ods.order_items i ON i.order_id = o.order_id")[0][0]
print("PASS tier summary:", [(r[0], r[1], float(r[2]), float(r[3])) for r in sorted(tier)])

# customer 1: expected split derived from the CSV feed with the loader's own time parsing, upgrade instant hard-coded
cut = datetime(2026, 1, 22, 9, 0, tzinfo=timezone.utc)
before = after = 0
orders = {}
for b in sorted((ROOT / "data").glob("batch*")):
    for r in csv.DictReader(open(b / "orders.csv")):
        orders[r["order_id"]] = r
has_items = {r["order_id"] for b in (ROOT / "data").glob("batch*") for r in csv.DictReader(open(b / "order_items.csv"))}
for oid, r in orders.items():
    if r["customer_id"] != "1" or oid not in has_items:
        continue
    t = datetime.strptime(normalize_ts(r["ordered_at"]), "%Y-%m-%d %H:%M:%S.%f UTC").replace(tzinfo=timezone.utc)
    if t < cut: before += 1
    else: after += 1
up = {r[0]: r[1] for r in rep("customer_upgrade_before_after")}
assert up == {"silver": before, "gold": after}, (up, before, after)
print(f"PASS customer 1 upgrade: {before} orders as silver before 2026-01-22 09:00Z, {after} as gold after (matches the CSV feed)")
