"""Stage 5 acceptance: SCD2 integrity, as-of binding, DWS reconciliation. Reads via Trino."""
import load
from load import run

cur = load.conn().cursor()
q = lambda sql: run(cur, sql)
S = "dwh_spark"

# 1. integrity: at most one current version per key (customer 24 was deleted: none), versions never overlap (a gap is legal only after a delete)
for dim, key in (("dim_product", "product_id"), ("dim_customer", "customer_id")):
    bad = q(f"SELECT count(*) FROM (SELECT {key} FROM {S}.{dim} WHERE is_current GROUP BY 1 HAVING count(*) > 1)")[0][0]
    keys = q(f"SELECT count(DISTINCT {key}) FROM {S}.{dim}")[0][0]
    cur_keys = q(f"SELECT count(DISTINCT {key}) FROM {S}.{dim} WHERE is_current")[0][0]
    chain = q(f"""SELECT count(*) FROM (SELECT valid_to, lead(valid_from) OVER (PARTITION BY {key} ORDER BY valid_from) nxt,
                  is_current FROM {S}.{dim}) WHERE (is_current AND valid_to IS NOT NULL) OR (NOT is_current AND (valid_to IS NULL OR valid_to > nxt))""")[0][0]
    deleted = 1 if dim == "dim_customer" else 0   # customer 24 is created and deleted in the landing data
    assert bad == 0 and keys - cur_keys == deleted and chain == 0, (dim, bad, keys, cur_keys, chain)
    print(f"PASS {dim}: {keys} keys, {cur_keys} current ({deleted} deleted), no overlaps")

# 2. expected versions (from the mock: upgrade, city move, price change, category change, new keys)
v = lambda dim, key, k: q(f"SELECT count(*) FROM {S}.{dim} WHERE {key} = {k}")[0][0]
assert (v("dim_product", "product_id", 1), v("dim_product", "product_id", 3), v("dim_product", "product_id", 11), v("dim_product", "product_id", 2)) == (2, 2, 1, 1)
assert (v("dim_customer", "customer_id", 1), v("dim_customer", "customer_id", 2), v("dim_customer", "customer_id", 21)) == (2, 2, 1)
print("PASS expected version counts (product 1,3 -> 2; customer 1,2 -> 2; new keys -> 1)")

# 3. as-of binding, checked against hard-coded business dates, not against the join being tested
assert q(f"SELECT count(*) FROM {S}.fact_orders WHERE product_sk IS NULL OR customer_sk IS NULL")[0][0] == 0
n = q(f"SELECT count(*) FROM {S}.fact_orders")[0][0]
wrong_p = q(f"""SELECT count(*) FROM {S}.fact_orders f JOIN {S}.dim_product p ON p.product_sk = f.product_sk
                WHERE f.product_id = 3 AND p.category <> CASE WHEN f.ordered_at < TIMESTAMP '2026-01-24 09:00:00 UTC' THEN 'sports' ELSE 'home' END""")[0][0]
wrong_c = q(f"""SELECT count(*) FROM {S}.fact_orders f JOIN {S}.dim_customer c ON c.customer_sk = f.customer_sk
                WHERE f.customer_id = 1 AND c.membership_tier <> CASE WHEN f.ordered_at < TIMESTAMP '2026-01-22 09:00:00 UTC' THEN 'silver' ELSE 'gold' END""")[0][0]
assert wrong_p == 0 and wrong_c == 0, (wrong_p, wrong_c)
split = q(f"""SELECT p.category, count(*) FROM {S}.fact_orders f JOIN {S}.dim_product p ON p.product_sk = f.product_sk
              WHERE f.product_id = 3 GROUP BY 1 ORDER BY 1""")
tiers = q(f"""SELECT c.membership_tier, count(*) FROM {S}.fact_orders f JOIN {S}.dim_customer c ON c.customer_sk = f.customer_sk
              WHERE f.customer_id = 1 GROUP BY 1 ORDER BY 1""")
print(f"PASS as-of: all {n} fact rows bound; product 3 items by category {split}; customer 1 items by tier {tiers}")

# 4. DWS == recomputed from fact + dim, and totals tie out to the fact
d = q(f"""SELECT count(*) FROM (
            SELECT date(f.ordered_at AT TIME ZONE 'UTC') d, p.category c, sum(f.line_amount) r, sum(f.quantity) i, count(DISTINCT f.order_id) o
            FROM {S}.fact_orders f JOIN {S}.dim_product p ON p.product_sk = f.product_sk GROUP BY 1, 2) x
          FULL JOIN {S}.dws_daily_revenue w ON x.d = w.revenue_date AND x.c = w.category
          WHERE x.r IS DISTINCT FROM w.revenue OR x.i IS DISTINCT FROM w.item_count OR x.o IS DISTINCT FROM w.order_count""")[0][0]
tot = q(f"SELECT (SELECT sum(line_amount) FROM {S}.fact_orders), (SELECT sum(revenue) FROM {S}.dws_daily_revenue)")[0]
assert d == 0 and tot[0] == tot[1], (d, tot)
print(f"PASS DWS reconciles with fact+dim (0 differing rows), total revenue {tot[0]} on both sides")
