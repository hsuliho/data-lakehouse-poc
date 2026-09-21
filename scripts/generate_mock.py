"""Deterministic mock e-commerce batches -> data/batch{1,2}/*.csv (seed fixed)."""
import csv, random
from datetime import datetime, timedelta
from pathlib import Path

rng = random.Random(42)
ROOT = Path(__file__).resolve().parent.parent / "data"
CITIES = ["Taipei", "Taichung", "Kaohsiung", "Tainan", "Hsinchu"]
CATS = ["books", "toys", "home", "sports"]
STATUS = ["paid", "shipped", "completed"]
T = lambda s: datetime.fromisoformat(s)

def write(batch, name, header, rows):
    d = ROOT / batch
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{name}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

C = ["customer_id", "name", "city", "membership_tier", "updated_at"]
P = ["product_id", "name", "category", "price", "updated_at"]
O = ["order_id", "customer_id", "order_status", "ordered_at", "updated_at"]
I = ["order_item_id", "order_id", "product_id", "quantity", "unit_price", "updated_at"]

# ---- batch 1: clean baseline
customers = {i: [i, f"Customer {i}", rng.choice(CITIES), rng.choice(["bronze", "silver"]),
                 T("2026-01-10 08:00") + timedelta(minutes=i)] for i in range(1, 21)}
customers[1][3] = "silver"; customers[2][2] = "Taipei"  # fixed so batch 2 changes are unambiguous
products = {i: [i, f"Product {i}", rng.choice(CATS), round(rng.uniform(5, 200), 2),
                T("2026-01-10 09:00") + timedelta(minutes=i)] for i in range(1, 11)}
orders, items, iid = {}, [], 0
def add_order(oid, cust, status, ts, pool):
    global iid
    orders[oid] = [oid, cust, status, ts, ts]
    for _ in range(rng.randint(1, 3)):
        iid += 1
        p = products[rng.choice(pool)]
        items.append([iid, oid, p[0], rng.randint(1, 4), p[3], ts])
for oid in range(1, 41):
    ts = T("2026-01-11 08:00") + timedelta(hours=rng.randint(0, 9 * 24))
    add_order(oid, rng.randint(1, 20), rng.choice(STATUS), ts, list(range(1, 11)))
orders[1][2:] = ["paid", T("2026-01-15 10:00"), T("2026-01-15 10:00")]  # will be shipped in batch 2

write("batch1", "customers", C, list(customers.values()))
write("batch1", "products", P, list(products.values()))
write("batch1", "orders", O, list(orders.values()))
write("batch1", "order_items", I, items)

# ---- batch 2: changes + new rows + planted dirt
b2c = [[1, "Customer 1", customers[1][2], "gold", T("2026-01-22 09:00")],       # upgrade
       [2, "Customer 2", "Kaohsiung", customers[2][3], T("2026-01-23 09:00")],  # city move
       *[[i, f"Customer {i}", rng.choice(CITIES), "bronze", T("2026-01-24 08:00")] for i in (21, 22, 23)]]
b2p = [[1, "Product 1", products[1][2], round(products[1][3] * 1.1, 2), T("2026-01-22 10:00")],  # price change
       [11, "Product 11", "home", 49.90, T("2026-01-22 10:30")]]
cat3 = products[3][2]  # category change: lets the as-of join show old orders stay in the old category (no rng call, other data unchanged)
b2p.append([3, "Product 3", "home" if cat3 != "home" else "books", products[3][3], T("2026-01-24 09:00")])
products[11] = b2p[1]; products[1] = b2p[0]
orders, items = {}, []
orders[1] = [1, customers[1][0], "shipped", T("2026-01-15 10:00"), T("2026-01-25 09:00")]  # OLD order, new status
for oid in range(41, 61):
    ts = T("2026-01-21 08:00") + timedelta(hours=rng.randint(0, 7 * 24))
    add_order(oid, rng.choice([1, 1, 2, 21, 22, 23, rng.randint(3, 20)]), rng.choice(STATUS), ts, list(range(1, 12)))
o2 = [*orders.values()]
dup_first = list(orders[45]); dup_first[2] = "created"; dup_first[4] = orders[45][3] + timedelta(minutes=5)
orders[45][2] = "paid"; orders[45][4] = orders[45][3] + timedelta(minutes=30)
o2 = [dup_first, *orders.values()]                     # DIRT 1: duplicate order_id (latest wins)
orders[50][2] = "unknown_status"                       # DIRT 2: illegal status
for r in items:
    if r[1] == 55: r[4] = -19.99; break                # DIRT 3: negative unit_price

write("batch2", "customers", C, b2c)
write("batch2", "products", P, b2p)
write("batch2", "orders", O, o2)
write("batch2", "order_items", I, items)
print("ok")
