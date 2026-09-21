"""One truth for "which columns are PII": the dbt `meta: {pii: true}` flags must equal what the Trino access rules mask
for the analyst. Optional third leg: the tags DataHub actually holds (pass --datahub to include it)."""
import json, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
rules = json.loads((ROOT / "trino/rules.json").read_text())
manifest = json.loads((ROOT / "dbt_spark/target/manifest.json").read_text())

# dbt: (schema.table -> {pii columns}) from sources and models
dbt_pii = {}
for n in list(manifest["nodes"].values()) + list(manifest["sources"].values()):
    if n.get("resource_type") in ("test", "seed"):
        continue
    name = n.get("identifier") or n.get("alias") or n["name"]
    cols = {c for c, v in n.get("columns", {}).items() if str(v.get("meta", {}).get("pii")).lower() == "true"}
    if cols:
        dbt_pii[f"{n['schema']}.{name}"] = cols

# rules: what the analyst may read, and which columns are masked / hidden on those tables
analyst = [t for t in rules["tables"] if t.get("user") == "analyst"]
readable, masked = set(), {}
for t in analyst:
    for tbl in t["table"].split("|"):
        key = f"{t['schema']}.{tbl}"
        readable.add(key)
        cols = {c["name"] for c in t.get("columns", []) if "mask" in c or c.get("allow") is False}
        if cols:
            masked[key] = cols

problems = []
for key in sorted(readable):                       # every PII column of a readable table must be masked
    missing = dbt_pii.get(key, set()) - masked.get(key, set())
    if missing:
        problems.append(f"{key}: PII in dbt but NOT masked for the analyst: {sorted(missing)}")
for key, cols in masked.items():                   # every masked column must be tagged PII
    extra = cols - dbt_pii.get(key, set())
    if extra:
        problems.append(f"{key}: masked in rules but not tagged PII in dbt: {sorted(extra)}")
ods_rules = [t for t in analyst if t["schema"].startswith("ods")]
if ods_rules:
    problems.append("analyst has a rule on the ods schema (raw PII)")
print("dbt PII columns  :", {k: sorted(v) for k, v in sorted(dbt_pii.items())})
print("analyst masks    :", {k: sorted(v) for k, v in sorted(masked.items())})

if "--datahub" in sys.argv:
    dh = subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/datahub_pii_tags.py")], capture_output=True, text=True, cwd=ROOT)
    if dh.returncode != 0:
        problems.append("could not read DataHub: " + dh.stderr[-200:])
    else:
        held = {k: set(v) for k, v in json.loads(dh.stdout).items()}
        print("DataHub pii tags :", {k: sorted(v) for k, v in sorted(held.items())})
        for key in sorted(set(held) | set(dbt_pii)):
            if held.get(key, set()) != dbt_pii.get(key, set()):
                problems.append(f"{key}: DataHub has {sorted(held.get(key, set()))}, dbt has {sorted(dbt_pii.get(key, set()))}")
# the semantic layer's contract view must not carry any column that is PII anywhere in the project
import load
pii_names = {c for cols in dbt_pii.values() for c in cols}
sem_cols = {r[0] for r in load.run(load.conn().cursor(), "SELECT column_name FROM iceberg.information_schema.columns WHERE table_schema = 'semantic' AND table_name = 'orders_semantic'")}
leak = sem_cols & pii_names
if leak:
    problems.append(f"semantic.orders_semantic exposes PII columns: {sorted(leak)}")
print(f"semantic view columns: {len(sem_cols)}, PII among them: {sorted(leak) or 'none'}")
if problems:
    print("FAIL"); [print(" -", p) for p in problems]; sys.exit(1)
print("PASS: PII columns agree" + (" across dbt, Trino rules and DataHub" if "--datahub" in sys.argv else " between dbt and Trino rules"))
