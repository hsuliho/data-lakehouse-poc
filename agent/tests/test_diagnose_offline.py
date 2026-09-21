"""Offline checks of the diagnosis agent: fixed rules on made-up evidence, tool guards, and the graph driven by a scripted model.
Costs no Gemini quota. Run: python -m agent.tests.test_diagnose_offline   (the graph part reads the real Dagster instance and Trino)"""
import json
import sys
import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage

from agent import graph as G
from agent.diagnose import evidence as E
from agent.diagnose import tools as T

G.usage.FILE = Path(tempfile.mkdtemp()) / "usage.json"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


up = {"container": "Up 3 hours", "endpoint": "ok"}
healthy = {"run": {"status": "SUCCESS", "data_checks": {"checks_run": 47, "failing": []}}, "services": {s: dict(up) for s in ("dagster", "trino", "catalog", "spark", "minio")},
           "delivery": {"dt": "d", "folder_exists": True, "success_marker": True, "tables": {"orders": {"columns_match_contract": True, "changed_at_after_delivery_day": 0}}}}
mod = lambda **kw: {**healthy, **kw}
cat = lambda ev: E.classify(ev)["category"]
check("rules: healthy -> no_problem_found", cat(healthy) == "no_problem_found")
check("rules: no _SUCCESS -> delivery_incomplete", cat(mod(delivery={**healthy["delivery"], "success_marker": False})) == "delivery_incomplete")
check("rules: renamed column -> schema_drift", cat(mod(delivery={**healthy["delivery"], "tables": {"orders": {"columns_match_contract": False, "missing_columns": ["a"], "unexpected_columns": ["b"]}}})) == "schema_drift")
check("rules: changed_at after the day -> timestamp_out_of_window", cat(mod(delivery={**healthy["delivery"], "tables": {"orders": {"columns_match_contract": True, "changed_at_after_delivery_day": 1}}})) == "timestamp_out_of_window")
down = lambda *names: {s: ({"container": "Exited (0) 1 minute ago", "endpoint": "URLError"} if s in names else dict(up)) for s in ("dagster", "trino", "catalog", "spark", "minio")}
check("rules: Trino down -> infra_trino", cat(mod(services=down("trino"))) == "infra_trino")
check("rules: catalog and Trino both failing -> the upstream one, infra_catalog", cat(mod(services=down("trino", "catalog"))) == "infra_catalog")
check("rules: Spark down -> infra_spark", cat(mod(services=down("spark"))) == "infra_spark")
chk = lambda names: {"data_checks": {"checks_run": 47, "failing": [{"asset": "x", "check": n, "severity": "ERROR", "failed_rows": "2", "status": "fail"} for n in names]}, "status": "SUCCESS"}
check("rules: status test -> data_quality_status", cat(mod(run=chk(["accepted_values_stg_orders_order_status__x"]))) == "data_quality_status")
check("rules: version chain test -> scd2_overlap", cat(mod(run=chk(["dim_versions_chain"]))) == "scd2_overlap")
check("rules: another test -> data_quality_other", cat(mod(run=chk(["unique_fact_orders_order_item_id"]))) == "data_quality_other")
check("rules: failed run nothing explains -> unknown, not a guess", cat(mod(run={"status": "FAILURE", "error": {"step": "ods__orders_raw", "message": "??"}, "data_checks": {"checks_run": 0, "failing": []}})) == "unknown")

# ---- recorded fixtures (agent/diagnose/fixtures): the fixed rules must give the recorded fault's answer, replayed with nothing live touched
import glob
fx = sorted(glob.glob(str(Path(__file__).resolve().parents[1] / "diagnose" / "fixtures" / "*.json")))
for path in fx:
    meta = json.loads(Path(path).read_text())["meta"]
    E.use_fixture(path)
    try:
        got = E.classify(E.checklist(meta["dt"]))["category"]
    finally:
        E.use_fixture(None)
    check(f"fixture {meta['fault']}: rules say {meta['expected']}", got == meta["expected"], got)
if not fx:
    print("(no fixtures yet: run agent.diagnose.faults once to record them)")

# ---- tool guards
for label, sql in {"DROP": "drop table ods.orders", "two statements": "select 1; select 2", "INSERT": "insert into ods.orders select * from ods.orders", "a CTE that deletes": "with t as (select 1) delete from ods.orders"}.items():
    check(f"run_sql rejects: {label}", "error" in json.loads(T.run_sql.invoke({"sql": sql})))
check("run_sql: PII is masked", json.loads(T.run_sql.invoke({"sql": "select distinct name, city from ods.customers"}))["rows"] == [["REDACTED", "REDACTED"]])
check("failing_rows rejects a name with quotes", "error" in E.failing_rows('x"; drop table y; --'))
check("read_landing_file rejects a path", "error" in json.loads(T.read_landing_file.invoke({"dt": "../..", "name": "orders.csv"})))
check("arms: the plain arm has no checklist and no runbook, the full arm has both",
      "run_checklist" not in [t.name for t in G.ARMS["diagnose_plain"][0]] and {"run_checklist", "lookup_known_issues"} <= {t.name for t in G.ARMS["diagnose"][0]})
check("arms: diagnosis arms end with submit_diagnosis, the question arms still with submit_answer", G.FINAL_TOOL["diagnose"].name == "submit_diagnosis" and "semantic" not in G.FINAL_TOOL)


class Scripted:
    def __init__(self, *script): self.script, self.calls = list(script), 0
    def bind_tools(self, tools): return self
    def invoke(self, messages):
        self.calls += 1
        return self.script.pop(0)


DIAG = dict(symptom="s", cause_category="no_problem_found", cause="c", confidence="high", evidence=["run_checklist: ok"], ruled_out=["x"], suggested_actions=[], needs_human=False, dt="2026-01-27")
ai = lambda *calls: AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"c{i}"} for i, (n, a) in enumerate(calls)])
llm = Scripted(ai(("run_checklist", {"dt": "2026-01-27"})), ai(("submit_diagnosis", DIAG)))
r = G.run_question("diagnose", "告警:交付日 2026-01-27", "d1", llm=llm)
check("graph: checklist, then submit_diagnosis ends the run with the diagnosis", r.get("answer", {}).get("cause_category") == "no_problem_found" and r["steps"] == 1, r)
llm = Scripted(AIMessage(content="looks fine"), ai(("submit_diagnosis", DIAG)))
r = G.run_question("diagnose", "告警", "d2", llm=llm)
check("graph: a plain-text reply is nudged to submit_diagnosis", r.get("answer") and llm.calls == 2, r)
llm = Scripted(ai(("submit_answer", {"status": "answered", "answer": "a", "basis": "b"})), AIMessage(content="x"))
r = G.run_question("diagnose", "告警", "d3", llm=llm)
check("graph: the diagnosis arm cannot end with submit_answer (it is not one of its tools)", "error" in r or not r.get("answer") or "cause_category" not in r["answer"], r)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
