"""Turn the mock batches into THREE daily deliveries of Change records: landing/dt=YYYY-MM-DD/<table>.csv + _SUCCESS.

  dt=2026-01-29  everything that changed up to 01-29 (the first, large load)
  dt=2026-01-30  what changed on 01-30, plus two LATE records (a dimension change from 01-18, the delete of customer 24 from 01-27)
  dt=2026-01-31  an OUT-OF-ORDER record (order 1 'shipped' from 01-25 arrives after 'completed' from 01-29) and DUPLICATE deliveries

The change records and their instants are the same as before (only the delivery grouping changed), so the final tables and every
hand-derived expectation are unchanged: the result depends on the set of records, not on the days they arrived. Output is deterministic;
landing/ is rebuilt from scratch.
"""
import csv, hashlib, shutil
from datetime import datetime, timedelta
from pathlib import Path

from load import TABLES, normalize_ts

ROOT = Path(__file__).resolve().parent.parent
DATA, LANDING = ROOT / "data", ROOT / "landing"
utc = lambda v: datetime.strptime(normalize_ts(v)[:-4], "%Y-%m-%d %H:%M:%S.%f")  # naive UTC, for comparing
header = lambda t: [("changed_at" if c == "updated_at" else c) for c, _ in TABLES[t][1]] + ["event_id", "is_deleted"]
event_id = lambda t, row: hashlib.md5("|".join([t, *row]).encode()).hexdigest()[:16]

D1, D2, D3 = "2026-01-29", "2026-01-30", "2026-01-31"
# a delivery may only hold changes that happened before the end of its day (changed_at < dt + 1 day)
delivery_of = lambda ts: D1 if utc(ts) < datetime(2026, 1, 30) else D2
# (table, key, changed_at as written in the batch) -> the delivery that is NOT the natural one: it arrived late / out of order
LATE = {("products", "5", "2026-01-18 12:00:00"): D2,   # dimension change found 12 days late (old batch4)
        ("orders", "1", "2026-01-25 09:00:00"): D3}      # 'shipped' arrives after 'completed' (01-29, in D1)

records = []  # (dt, table, csv row as list)
for b in sorted(DATA.glob("batch*")):
    for t, (key, cols) in TABLES.items():
        with open(b / f"{t}.csv") as f:
            for r in csv.DictReader(f):
                ts = r["updated_at"]
                dt = LATE.get((t, r[key], ts)) or delivery_of(ts)
                row = [r[c] for c, _ in cols]
                records.append((dt, t, row + [event_id(t, row), "false"]))

def add(dt, t, values, deleted=False):
    row = [values.get(c, "") for c, _ in TABLES[t][1]]
    records.append((dt, t, row + [event_id(t, row + ["deleted" if deleted else ""]), "true" if deleted else "false"]))

for oid_row in [r for r in records if (r[1], r[2][0]) in (("orders", "68"), ("order_items", "138")) and r[0] == D1]:
    records.append((D3, oid_row[1], list(oid_row[2])))                      # the same records delivered again (duplicate delivery)
cust = {"customer_id": "24", "name": "Customer 24", "city": "Taipei", "membership_tier": "bronze"}
add(D1, "customers", {**cust, "updated_at": "2026-01-25 10:00:00"})                         # created ...
add(D2, "customers", {"customer_id": "24", "updated_at": "2026-01-27 10:00:00"}, deleted=True)  # ... and deleted, reported late

days = sorted({d for d, _, _ in records})
first, last = datetime.fromisoformat(days[0]), datetime.fromisoformat(days[-1])
shutil.rmtree(LANDING, ignore_errors=True)
for i in range((last - first).days + 1):
    dt = (first + timedelta(days=i)).strftime("%Y-%m-%d")
    d = LANDING / f"dt={dt}"; d.mkdir(parents=True)
    for t in TABLES:
        rows = sorted((r for x, tt, r in records if x == dt and tt == t), key=lambda r: utc(r[len(TABLES[t][1]) - 1]) if r[len(TABLES[t][1]) - 1] else datetime.min)
        with open(d / f"{t}.csv", "w", newline="") as f:
            w = csv.writer(f); w.writerow(header(t)); w.writerows(rows)
    (d / "_SUCCESS").write_text("")
print(f"landing: {len(days)} days with records, {(last - first).days + 1} deliveries {days[0]} .. {days[-1]}, {len(records)} change records")
