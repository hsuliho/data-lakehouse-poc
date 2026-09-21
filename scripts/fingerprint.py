"""Snapshot of every stateful thing, to compare before/after a restart or a container recreate."""
import json, subprocess
import requests
import load
from load import run

cur = load.conn().cursor()
q = lambda sql: run(cur, sql)
fp = {}
for t in ("ods.orders", "ods.order_items", "ods.customers", "ods.products", "dwh_spark.fact_orders", "dwh_spark.dim_product",
          "dwh_spark.dim_customer", "dwh_spark.dws_daily_revenue", "dwh_spark.ads_category_revenue_rank"):
    fp[f"rows {t}"] = q(f"SELECT count(*) FROM {t}")[0][0]
fp["rows semantic.orders_semantic"] = q("SELECT count(*) FROM semantic.orders_semantic")[0][0]
fp["revenue total"] = str(q("SELECT sum(line_amount) FROM dwh_spark.fact_orders")[0][0])
schema, name = "dwh_spark", "fact_orders"
fp["snapshots fact_orders"] = q(f'SELECT count(*) FROM {schema}."{name}$snapshots"')[0][0]
fp["snapshots dim_product"] = q('SELECT count(*) FROM dwh_spark."dim_product$snapshots"')[0][0]
fp["catalog tables"] = q("SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('ods','dwh_spark')")[0][0]
mc = ("docker run --rm --network data-lakehouse-poc_default --entrypoint /bin/sh quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z "
      "-c 'mc alias set m http://minio:9000 admin password >/dev/null; mc ls -r m/warehouse | wc -l'")
fp["minio objects"] = int(subprocess.run(mc, shell=True, capture_output=True, text=True).stdout.strip())
s = requests.Session()
tok = s.post("http://localhost:8088/api/v1/security/login", json={"username": "admin", "password": "admin", "provider": "db"}).json()["access_token"]
s.headers["Authorization"] = f"Bearer {tok}"
for k in ("dashboard", "chart", "dataset", "database"):
    fp[f"superset {k}s"] = s.get(f"http://localhost:8088/api/v1/{k}/").json()["count"]
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from datahub_query import datasets, gql, short          # DataHub (catalog, lineage, tags, test results)
ds = datasets()
fp["datahub datasets"] = len(ds)
pii = fails = ok = 0
for urn in ds:
    if short(urn).startswith("dbt:"):
        d = gql("""query($u:String!){ dataset(urn:$u){ schemaMetadata{ fields{ globalTags{ tags{ tag{ urn } } } } }
                   assertions(start:0,count:100){ assertions{ runEvents(status:COMPLETE,limit:1){ runEvents{ result{ type } } } } } } }""", u=urn)["dataset"]
        pii += sum(1 for f in (d["schemaMetadata"] or {}).get("fields", []) if (f.get("globalTags") or {}).get("tags"))
        for a in d["assertions"]["assertions"]:
            ev = a["runEvents"]["runEvents"]
            if ev: ok += ev[0]["result"]["type"] == "SUCCESS"; fails += ev[0]["result"]["type"] != "SUCCESS"
fp["datahub tagged columns"], fp["datahub assertions passing"], fp["datahub assertions failing"] = pii, ok, fails
up = gql("query($u:String!){ dataset(urn:$u){ lineage(input:{direction:UPSTREAM,start:0,count:50}){ total } } }",
         u="urn:li:dataset:(urn:li:dataPlatform:iceberg,dwh_spark.fact_orders,DEV)")["dataset"]["lineage"]["total"]
fp["datahub fact_orders upstreams"] = up
# Dagster: the dates it has completed live in the docker volume `dagster-home`, so this is what shows that the volume survived
try:
    dg = subprocess.run(["docker", "exec", "data-lakehouse-poc-dagster-daemon-1", "python", "-m", "orchestration.state", "partitions"], capture_output=True, text=True, timeout=60)
    fp["dagster completed dates"] = json.loads(dg.stdout)["completed_dates"] if dg.returncode == 0 else "unreadable"
except Exception as e:
    fp["dagster completed dates"] = f"unreadable: {type(e).__name__}"
print(json.dumps(fp, indent=1, sort_keys=True))
