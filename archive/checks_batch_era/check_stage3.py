"""Stage 3 acceptance: idempotent MERGE, watermark on updated_at, dedupe, dirt survives into ODS."""
import load
from load import run, count, TABLES

cur = load.conn().cursor()
snap = lambda: {t: count(cur, t) for t in TABLES}

load.load(cur, "batch2")
after = snap(); print("after batch2:", after)

load.load(cur, "batch2")  # same batch again, watermark on
assert snap() == after, "rerun with watermark changed counts"
load.load(cur, "batch2", use_watermark=False)  # rerun with NO watermark: MERGE alone must be idempotent
load.load(cur, "batch2", use_watermark=False)
assert snap() == after, "MERGE without watermark is not idempotent"
print("PASS idempotent: counts unchanged after 3 reruns")

# old order changed status: ordered_at is 01-15, only updated_at can see it
st = run(cur, "SELECT order_status FROM ods.orders WHERE order_id = 1")[0][0]
assert st == "shipped", st
print("PASS watermark on updated_at picks up old order 1 ->", st)

# duplicate order_id in batch: latest updated_at wins, still one row
rows = run(cur, "SELECT order_status FROM ods.orders WHERE order_id = 45")
assert rows == [["paid"]], rows
print("PASS dedupe: order 45 has one row ->", rows[0][0])

# dirt was NOT filtered (ODS keeps source truth; stage 6 must catch it)
assert run(cur, "SELECT count(*) FROM ods.orders WHERE order_status = 'unknown_status'")[0][0] == 1
assert run(cur, "SELECT count(*) FROM ods.order_items WHERE unit_price < 0")[0][0] == 1
print("PASS dirt kept in ODS: 1 illegal status, 1 negative unit_price")

# contrast: blind INSERT twice doubles
run(cur, "DROP TABLE IF EXISTS ods.orders_blind")
run(cur, "CREATE TABLE ods.orders_blind AS SELECT * FROM ods.orders_batch")
n = count(cur, "orders_blind")
run(cur, "INSERT INTO ods.orders_blind SELECT * FROM ods.orders_batch")
assert count(cur, "orders_blind") == 2 * n
print(f"CONTRAST blind INSERT x2: {n} -> {2 * n} rows"); run(cur, "DROP TABLE ods.orders_blind")

# copy-on-write vs merge-on-read: which file kinds did MERGE leave behind? (content 0=data, 1=position deletes)
print("orders files by content:", run(cur, 'SELECT content, count(*) FROM "orders$files" GROUP BY content'))
