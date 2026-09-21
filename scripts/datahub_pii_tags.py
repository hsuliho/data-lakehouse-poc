"""Print {schema.table: [columns tagged PII]} as DataHub holds it. The tags sit on the dbt-platform entity (the primary of each sibling pair)."""
import json
from datahub_query import datasets, gql, short

out = {}
for urn in datasets():
    plat, name = short(urn).split(":", 1)
    if plat != "dbt":
        continue
    f = gql("query($u:String!){ dataset(urn:$u){ schemaMetadata{ fields{ fieldPath globalTags{ tags{ tag{ urn } } } } } } }", u=urn)["dataset"]["schemaMetadata"]
    cols = sorted(x["fieldPath"] for x in (f or {}).get("fields", []) if any(t["tag"]["urn"].endswith(":PII") for t in (x.get("globalTags") or {}).get("tags", [])))
    if cols:
        out[name] = cols
print(json.dumps(out))
