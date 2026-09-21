"""Stage 8: Trino file-based access control (trino/rules.json). Identity is the user name the client sends (not verified)."""
import time
import trino
from trino.exceptions import TrinoUserError

def cur(user):
    return trino.dbapi.connect(host="localhost", port=8080, user=user, catalog="iceberg", schema="dwh_spark", timezone="UTC").cursor()

def q(c, sql):
    c.execute(sql)
    return c.fetchall()

def denied(c, sql):
    try:
        q(c, sql)
    except TrinoUserError as e:
        return e.message.splitlines()[0][:110]
    raise AssertionError(f"was allowed: {sql}")

for _ in range(30):                       # wait for the recreated coordinator
    try:
        q(cur("engineer"), "SELECT 1"); break
    except Exception:
        time.sleep(4)

ALLOWED = {"fact_orders", "dim_product", "dim_customer", "dws_daily_revenue", "ads_category_revenue_rank"}
SEMANTIC = {"orders_semantic", "metricflow_time_spine"}     # the semantic-layer contract views (schema `semantic`)
eng, ana = cur("engineer"), cur("analyst")

# ---- engineer: everything, PII unmasked, may write
assert q(eng, "SELECT count(*) FROM ods.orders")[0][0] > 0
raw = q(eng, "SELECT customer_name, city FROM dwh_spark.dim_customer WHERE customer_id = 1 AND is_current")[0]
assert "REDACTED" not in raw, raw
q(eng, "CREATE TABLE ops.access_probe AS SELECT 1 AS x"); q(eng, "DROP TABLE ops.access_probe")
print(f"PASS engineer: reads ODS, sees real PII {tuple(raw)}, can create/drop tables")

# ---- analyst: curated layers only, PII masked, read-only
for t in ALLOWED - {"dim_customer"}:
    assert q(ana, f"SELECT count(*) FROM dwh_spark.{t}")[0][0] > 0
pii = q(ana, "SELECT DISTINCT customer_name, city FROM dwh_spark.dim_customer")
assert pii == [["REDACTED", "REDACTED"]], pii
tiers = {r[0] for r in q(ana, "SELECT DISTINCT membership_tier FROM dwh_spark.dim_customer")}
assert tiers == {"bronze", "silver", "gold"}, tiers
grouped = q(ana, "SELECT city, count(*) FROM dwh_spark.dim_customer GROUP BY 1")
assert len(grouped) == 1, grouped
print(f"PASS analyst reads {sorted(ALLOWED)}; PII columns come back as {pii[0]}; a GROUP BY city yields one group, so location cannot be inferred")

msgs = {}
for label, sql in {
    "ODS orders": "SELECT * FROM ods.orders",
    "ODS customers (real names)": "SELECT name FROM ods.customers",
    "file paths via $files": 'SELECT * FROM dwh_spark."fact_orders$files"',
    "history via $snapshots": 'SELECT * FROM dwh_spark."fact_orders$snapshots"',
    "other schema (ops)": "SELECT * FROM ops.diagnosis",
}.items():
    msgs[label] = denied(ana, sql)
print("PASS analyst denied on:"); [print(f"   {k:28} -> {v}") for k, v in msgs.items()]

for label, sql in {
    "INSERT": "INSERT INTO dwh_spark.dws_daily_revenue SELECT * FROM dwh_spark.dws_daily_revenue WHERE false",
    "DELETE": "DELETE FROM dwh_spark.fact_orders WHERE order_id = 1",
    "CREATE TABLE": "CREATE TABLE dwh_spark.analyst_probe AS SELECT 1 AS x",
    "DROP TABLE": "DROP TABLE dwh_spark.dim_product",
}.items():
    denied(ana, sql)
print("PASS analyst is read-only: INSERT / DELETE / CREATE TABLE / DROP TABLE all denied")

schemas = {r[0] for r in q(ana, "SHOW SCHEMAS FROM iceberg")}
tables = {r[0] for r in q(ana, "SHOW TABLES FROM iceberg.dwh_spark")}
assert schemas == {"dwh_spark", "semantic"}, schemas   # information_schema is not listed, but stays queryable (Trino exempts it from the rules)
assert tables == ALLOWED, tables      # also proves the stg_* views are hidden from the analyst
cols = {r[0] for r in q(ana, "SELECT DISTINCT table_schema FROM iceberg.information_schema.columns")}
assert cols <= {"dwh_spark", "semantic", "information_schema"}, cols
assert {r[0] for r in q(ana, "SHOW TABLES FROM iceberg.semantic")} == SEMANTIC
assert not {"customer_name", "city", "name"} & {r[0] for r in q(ana, "DESCRIBE iceberg.semantic.orders_semantic")}   # no PII column in the contract view
print(f"PASS analyst sees only schemas {sorted(schemas)} and tables {sorted(tables)}; information_schema leaks nothing about ods")

# ---- unknown users, including Trino's own default CLI user
for u in ("nobody", "trino"):
    c = cur(u)
    m = denied(c, "SELECT count(*) FROM dwh_spark.fact_orders")
    assert q(c, "SHOW CATALOGS") is not None
    print(f"PASS user {u!r}: no rule -> denied ({m})")

# ---- diagnoser (the diagnosis agent): read-only on ods / dwh_spark / dbt failure tables / ops, PII masked, nothing else
dg = cur("diagnoser")
assert q(dg, "SELECT DISTINCT name, city FROM ods.customers") == [["REDACTED", "REDACTED"]] and q(dg, "SELECT DISTINCT name, city FROM ods.customers_raw") == [["REDACTED", "REDACTED"]]
assert q(dg, "SELECT DISTINCT customer_name, city FROM dwh_spark.dim_customer") == [["REDACTED", "REDACTED"]]
assert q(dg, "SELECT count(*) FROM ods.orders_raw")[0][0] > 0 and q(dg, "SELECT count(*) FROM dwh_spark_dbt_test__audit.dim_versions_chain") is not None
m = [denied(dg, s) for s in ("INSERT INTO ods.orders_raw SELECT * FROM ods.orders_raw LIMIT 0", "DELETE FROM ods.orders WHERE order_id = -1",
                             "CREATE TABLE ods.x (a int)", "SELECT * FROM semantic.orders_semantic",
                             "SELECT * FROM dwh_spark_dbt_test__audit.accepted_values_stg_customers_c4492bdabced0b994959014b64af90a7")]
print(f"PASS user 'diagnoser': reads ods / dwh_spark / non-customer dbt failure tables with PII masked (names and cities REDACTED); every write, the semantic schema and customer failure tables denied ({m[0][:40]}...)")
