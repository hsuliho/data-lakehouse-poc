"""Do the three lineage sources agree? For the dbt models (the part all three know about): dbt's manifest, the Dagster asset graph, and DataHub.
The load and SCD2 edges (ods/*_raw -> ods/*, ods/*_raw -> scd2/dim_*) exist only in Dagster, by hand, and are listed as the expected difference.
Nothing keeps the three in step by itself: the manifest changes when dbt runs, Dagster when its code location is reloaded, DataHub when
datahub/refresh.sh runs. This shows whether they have drifted. usage: check_lineage_consistency.py"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from datahub_query import gql

m = json.load(open(ROOT / "dbt_spark" / "target" / "manifest.json"))
models = {u: n for u, n in m["nodes"].items() if n["resource_type"] == "model"}
src = m["sources"]
rel = lambda u: (f"{m['nodes'][u]['schema']}.{m['nodes'][u]['name']}" if u in m["nodes"] else f"{src[u]['schema']}.{src[u]['identifier']}")   # the table name in the catalog
akey = lambda u: m["nodes"][u]["name"] if u in m["nodes"] else f"{src[u]['source_name']}/{src[u]['name']}"                                        # the Dagster asset key

manifest = {rel(u): sorted(rel(p) for p in m["parent_map"][u] if p in models or p in src) for u in models}
manifest_keys = {akey(u): sorted(akey(p) for p in m["parent_map"][u] if p in models or p in src) for u in models}

nodes = json.loads(subprocess.run(["curl", "-s", "-X", "POST", "localhost:3000/graphql", "-H", "Content-Type: application/json", "-d",
                                   '{"query":"{ assetNodes { assetKey{path} dependencyKeys{path} } }"}'], capture_output=True, text=True).stdout)["data"]["assetNodes"]
dagster = {"/".join(n["assetKey"]["path"]): sorted("/".join(x["path"]) for x in n["dependencyKeys"]) for n in nodes}


def datahub_upstreams(table):
    r = gql("query($u:String!){ dataset(urn:$u){ lineage(input:{direction:UPSTREAM,start:0,count:50}){ relationships{ entity{ urn } } } } }",
            u=f"urn:li:dataset:(urn:li:dataPlatform:iceberg,{table},DEV)")
    return sorted(x["entity"]["urn"].split(",")[1] for x in r["dataset"]["lineage"]["relationships"])


bad = 0
for u in models:
    t, k = rel(u), akey(u)
    d, g = datahub_upstreams(t), dagster.get(k)
    ok = d == manifest[t] and g == manifest_keys[k]
    bad += not ok
    print(("same " if ok else "DIFF ") + t.ljust(38) + ("" if ok else f" manifest {manifest[t]} | dagster {g} | datahub {d}"))
extra = sorted(k for k, v in dagster.items() if v and "/" in k)
print("only in Dagster (declared by hand, expected):", {k: dagster[k] for k in extra})
print("CONSISTENT" if not bad else f"DRIFT: {bad} model(s) differ")
sys.exit(1 if bad else 0)
