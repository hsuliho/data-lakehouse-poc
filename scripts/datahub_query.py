"""Small GraphQL helper for DataHub's GMS (port 8082): what does the catalog actually hold?"""
import json, requests

GMS = "http://localhost:8082/api/graphql"


def gql(query, **variables):
    r = requests.post(GMS, json={"query": query, "variables": variables}, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("errors"):
        raise RuntimeError(json.dumps(j["errors"])[:400])
    return j["data"]


def datasets():
    out, start = [], 0
    while True:
        d = gql("""query($s:Int!){ search(input:{type:DATASET, query:"*", start:$s, count:100}){ total searchResults{ entity{ urn } } } }""", s=start)["search"]
        out += [r["entity"]["urn"] for r in d["searchResults"]]
        start += 100
        if start >= d["total"]:
            return out


def short(urn):            # urn:li:dataset:(urn:li:dataPlatform:iceberg,dwh_spark.fact_orders,DEV) -> iceberg:dwh_spark.fact_orders
    p, name, _ = urn[len("urn:li:dataset:("):-1].rsplit(",", 2)[0], urn.split(",")[1], urn.split(",")[2]
    return f"{p.split(':')[-1]}:{name}"
