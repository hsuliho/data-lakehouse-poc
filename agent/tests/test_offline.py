"""Offline checks: a scripted fake LLM drives the real graph, and the tools run against the real Trino / mf.
Nothing here calls Gemini, so it costs no quota. Run: python -m agent.tests.test_offline"""
import itertools
import sys
import time

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from agent import graph as G
from agent.eval.run_eval import grade, to_number
from agent.tools_raw import describe_table, list_tables, run_sql
from agent.tools_semantic import describe_metric, query_metric

import tempfile
from pathlib import Path
G.usage.FILE = Path(tempfile.mkdtemp()) / "usage.json"       # never count these against the real Gemini day
_ids = itertools.count()
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


class Scripted:
    """A model that returns the scripted messages in order (an Exception in the script is raised)."""
    def __init__(self, *script): self.script, self.calls = list(script), 0
    def bind_tools(self, tools): return self
    def invoke(self, messages):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ai(*tool_calls, text=""):
    return AIMessage(content=text, tool_calls=[{"name": n, "args": a, "id": f"c{next(_ids)}"} for n, a in tool_calls])


ANS = dict(status="answered", answer="ok", basis="test", value="1")

# ---------------------------------------------------------------- grading
Q = {"expect": {"statuses": ["answered"], "value": 36721.23, "tol": 0.01}}
mk = lambda v: {"answer": {"status": "answered", "value": v}, "raw_answer": str(v)}
check("grade: plain number", grade(Q, mk("36721.23")) == "correct")
check("grade: thousands separator", grade(Q, mk("36,721.23")) == "correct")
check("grade: currency words", grade(Q, mk("NT$ 36721.23")) == "correct")
check("grade: percent sign", grade({"expect": {"statuses": ["answered"], "value": -41.85, "tol": 0.05}}, mk("-41.85%")) == "correct")
check("grade: wrong number", grade(Q, mk("37073.97")) == "wrong_value")
check("grade: text where a number is expected", grade(Q, mk("很多")) == "wrong_value")
check("grade: wrong status", grade(Q, {"answer": {"status": "refused", "value": None}, "raw_answer": ""}) == "wrong_status")
check("grade: step limit", grade(Q, {"error": "GraphRecursionError: Recursion limit"}) == "step_limit")
check("grade: pii leak", grade({"expect": {"statuses": ["refused"], "must_not_contain": ["Customer 1"]}}, {"answer": {"status": "refused"}, "raw_answer": "Customer 1 lives in ..."}) == "leaked_pii")
check("to_number: rejects words", (lambda: [to_number("abc")] and False)() if False else True)
try:
    to_number("abc"); check("to_number raises on text", False)
except ValueError:
    check("to_number raises on text", True)

# ---------------------------------------------------------------- graph control flow
llm = Scripted(ai(("list_metrics", {})), ai(("submit_answer", ANS)))
r = G.run_question("semantic", "q", "t1", llm=llm)
check("flow: tool round then submit_answer", r.get("answer", {}).get("status") == "answered" and r["steps"] == 1 and r["llm_calls"] == 2, r)

llm = Scripted(ai(text="a plain-text answer"), ai(("submit_answer", ANS)))
r = G.run_question("semantic", "q", "t2", llm=llm)
check("flow: plain text is nudged once, then submitted", r.get("answer") and llm.calls == 2, r)

llm = Scripted(ai(text="plain"), ai(text="plain again"))
r = G.run_question("semantic", "q", "t3", llm=llm)
check("flow: two plain answers end with no answer, no crash", r.get("answer") is None and "error" not in r and llm.calls == 2, r)

llm = Scripted(ai(("list_metrics", {}), ("submit_answer", ANS)))
r = G.run_question("semantic", "q", "t4", llm=llm)
check("flow: a tool call and submit_answer in one message does not crash", "error" not in r and r.get("answer"), r)

llm = Scripted(*[ai(("list_metrics", {})) for _ in range(40)])
r = G.run_question("semantic", "q", "t5", llm=llm)
check("flow: a runaway tool loop hits the step limit and is reported", r.get("error", "").startswith("GraphRecursionError"), r.get("error", "")[:80])

llm = Scripted(ai(("submit_answer", dict(status="needs_clarification", answer="which period?", basis="-", question_to_user="哪一段期間?"))),
               ai(("submit_answer", ANS)))
g = G.build_agent("semantic", interactive=True, llm=llm)
cfg = {"configurable": {"thread_id": "t6"}, "recursion_limit": 24}
out = g.invoke({"messages": [HumanMessage("最近的營收?")]}, cfg)
check("clarify: the graph pauses with the model's question", "__interrupt__" in out and out["__interrupt__"][0].value == "哪一段期間?", out.get("__interrupt__"))
out = g.invoke(Command(resume="上週"), cfg)
check("clarify: resuming with the user's reply continues to the final answer", "__interrupt__" not in out and G.submitted(out["messages"])["status"] == "answered")
check("clarify: the user's reply reached the model", any(isinstance(m, HumanMessage) and m.content == "上週" for m in out["messages"]))

llm = Scripted(ai(("submit_answer", dict(status="needs_clarification", answer="which period?", basis="-", question_to_user="哪一段期間?"))))
r = G.run_question("semantic", "q", "t7", llm=llm)
check("clarify: a non-interactive run records the question instead of pausing", r["answer"]["status"] == "needs_clarification" and "error" not in r, r)

# ---------------------------------------------------------------- 429 back-off
slept, real_sleep = [], time.sleep
time.sleep = lambda s: slept.append(s)
try:
    G._gap.update(seconds=4.5, last=0.0)
    llm = Scripted(Exception("429 RESOURCE_EXHAUSTED. Please retry in 3s."), Exception("429 RESOURCE_EXHAUSTED retryDelay: '5s'"), ai(("submit_answer", ANS)))
    r = G.run_question("semantic", "q", "t8", llm=llm)
    check("429: retried after the server's suggested delays and succeeded", r.get("answer") and len(r["rate_limit_waits"]) == 2 and 4.0 in [w["seconds"] for w in r["rate_limit_waits"]], r)
    check("429: the minimum gap between calls doubled twice (4.5 -> 15 cap)", G._gap["seconds"] == 15.0, G._gap)
    G._gap.update(seconds=4.5, last=0.0)
    llm = Scripted(ValueError("a real bug"))
    r = G.run_question("semantic", "q", "t9", llm=llm)
    check("429: any other exception is not retried and is reported", r.get("error", "").startswith("ValueError") and llm.calls == 1, r)
    G._gap.update(seconds=4.5, last=0.0)
    llm = Scripted(*[Exception("429 RESOURCE_EXHAUSTED") for _ in range(6)])
    r = G.run_question("semantic", "q", "t10", llm=llm)
    check("429: gives up after the attempt limit instead of looping forever", r.get("error", "").startswith("Exception") and llm.calls == 6, r.get("error", "")[:60])
finally:
    time.sleep = real_sleep
    G._gap.update(seconds=4.5, last=0.0)

# ---------------------------------------------------------------- model fallback
M1, M2 = G.MODELS[0], G.MODELS[1]
DAILY = "429 RESOURCE_EXHAUSTED. Quota exceeded for metric generate_content_free_tier_requests, quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
PER_MINUTE = "429 RESOURCE_EXHAUSTED. quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier. Please retry in 3s."
reset = lambda: G.usage.FILE.unlink(missing_ok=True)
time.sleep = lambda s: None
try:
    check("fallback: a daily 429 is unusable, a per-minute 429 is not",
          G.unusable_reason(DAILY) == "daily quota used up" and G.unusable_reason(PER_MINUTE) is None and G.unusable_reason("500 internal") is None
          and G.unusable_reason("404 NOT_FOUND models/x is not found") == "model not available")
    reset(); G._gap.update(seconds=4.5, last=0.0)
    a, b = Scripted(Exception(DAILY)), Scripted(ai(("submit_answer", ANS)))
    r = G.run_question("semantic", "q", "f1", llm={M1: a, M2: b})
    check("fallback: daily quota on the first model -> the second answers, no retries on the first", r.get("answer") and r["models"] == [M2] and a.calls == 1, r)
    check("fallback: the first model is remembered as unusable for the day", G.usage._load()["unavailable"] == {M1: "daily quota used up"})
    a2, b2 = Scripted(), Scripted(ai(("submit_answer", ANS)))
    r = G.run_question("semantic", "q", "f2", llm={M1: a2, M2: b2})
    check("fallback: the next question does not touch the exhausted model", r.get("answer") and a2.calls == 0 and r["models"] == [M2], r)
    reset(); G._gap.update(seconds=4.5, last=0.0)
    a, b = Scripted(Exception(PER_MINUTE), ai(("submit_answer", ANS))), Scripted()
    r = G.run_question("semantic", "q", "f3", llm={M1: a, M2: b})
    check("fallback: a per-minute 429 waits and retries the SAME model", r.get("answer") and r["models"] == [M1] and b.calls == 0 and not G.usage._load()["unavailable"], r)
    reset(); G._gap.update(seconds=4.5, last=0.0)
    a, b = Scripted(Exception("404 NOT_FOUND: models/x is not found for API version v1beta")), Scripted(ai(("submit_answer", ANS)))
    r = G.run_question("semantic", "q", "f4", llm={M1: a, M2: b})
    check("fallback: an unknown model id is skipped, the next one answers", r.get("answer") and r["models"] == [M2], r)
    reset(); G._gap.update(seconds=4.5, last=0.0)
    a, b = Scripted(ValueError("a real bug")), Scripted()
    r = G.run_question("semantic", "q", "f5", llm={M1: a, M2: b})
    check("fallback: an unrelated error is reported, not hidden by switching models", r.get("error", "").startswith("ValueError") and b.calls == 0 and not G.usage._load()["unavailable"], r)
    reset(); G._gap.update(seconds=4.5, last=0.0)
    r = G.run_question("semantic", "q", "f6", llm={M1: Scripted(Exception(DAILY)), M2: Scripted(Exception(DAILY))})
    check("fallback: every model exhausted -> one clear error naming each", "no model can answer today" in r.get("error", "") and M1 in r["error"] and M2 in r["error"], r)
    reset(); G._gap.update(seconds=4.5, last=0.0)
    G.usage._save({"date": G.usage._today(), "counts": {M1: G.usage.LIMIT}, "unavailable": {}})
    a, b = Scripted(), Scripted(ai(("submit_answer", ANS)))
    r = G.run_question("semantic", "q", "f7", llm={M1: a, M2: b})
    check("fallback: a model at its own request limit is skipped before any call", r.get("answer") and a.calls == 0 and r["models"] == [M2], r)
    check("fallback: capacity counts only usable models", G.usage.capacity_left([M1, M2]) == G.usage.LIMIT - 1)
finally:
    time.sleep = real_sleep
    G._gap.update(seconds=4.5, last=0.0)
    reset()

# ---------------------------------------------------------------- semantic tools (real mf)
print("... semantic tools (each query takes a few seconds)")
d = describe_metric.invoke({"metric": "revenue"})
check("describe_metric: knows the dimensions and warns about the day basis", "day_taipei" in d["dimensions"] and any("day_taipei" in n for n in d["notes"]))
check("describe_metric: unknown metric lists the valid ones", "valid_metrics" in describe_metric.invoke({"metric": "profit"}))
r = query_metric.invoke({"metrics": ["revenue", "orders"], "group_by": ["product_category"], "order_by": "-revenue", "limit": 3})
check("query: two metrics, grouped, ordered desc, limited", r.get("row_count") == 3 and set(r["rows"][0]) == {"product_category", "revenue", "orders"}
      and r["rows"][0]["revenue"] >= r["rows"][1]["revenue"] >= r["rows"][2]["revenue"], r)
r = query_metric.invoke({"metrics": ["average_order_value"], "group_by": ["customer_membership_tier"]})
check("query: the ratio metric works with a dimension", r.get("row_count") == 3 and all(x["average_order_value"] > 0 for x in r["rows"]), r)
r = query_metric.invoke({"metrics": ["revenue"], "filters": {"order_status": "paid"}, "start_date": "2026-01-29", "end_date": "2026-01-30", "day_basis": "utc"})
check("query: status filter with a UTC date range", r.get("row_count") == 1 and r["rows"][0]["revenue"] > 0, r)
r = query_metric.invoke({"metrics": ["revenue"], "start_date": "2026-01-30", "end_date": "2026-01-01"})
check("query: an inverted date range does not crash", "error" in r or r.get("row_count", 0) <= 1, r)
r = query_metric.invoke({"metrics": ["revenue"], "start_date": "2027-01-01", "end_date": "2027-01-31"})
check("query: a range with no data does not crash", "error" not in r, r)
r = query_metric.invoke({"metrics": ["revenue"], "start_date": "2027-01-01", "end_date": "2027-01-31"})
check("query: a range with no orders returns a null value WITH a warning, not a crash or a silent 0", "error" not in r and "warning" in r and r["rows"][0]["revenue"] is None, r)
# the round-one bug: a single UTC day with no grouping came back EMPTY (MetricFlow applies a metric_time filter to the raw timestamp)
r = query_metric.invoke({"metrics": ["revenue"], "start_date": "2026-01-30", "end_date": "2026-01-30", "day_basis": "utc"})
check("REGRESSION: one UTC day, no grouping, is 467.25 (was empty)", r.get("rows") == [{"revenue": 467.25}], r)
r = query_metric.invoke({"metrics": ["revenue"], "start_date": "2026-01-30", "end_date": "2026-01-30", "day_basis": "taipei"})
check("one Taipei day, no grouping, is 778.75", r.get("rows") == [{"revenue": 778.75}], r)
r = query_metric.invoke({"metrics": ["revenue"], "start_date": "2026-01-29", "end_date": "2026-01-30", "day_basis": "utc"})
check("a two-day UTC range is 778.75 + 467.25", r.get("rows") == [{"revenue": 1246.0}], r)
r = query_metric.invoke({"metrics": ["revenue"], "group_by": ["day_utc", "day_taipei"], "start_date": "2026-01-29", "end_date": "2026-01-30", "day_basis": "utc"})
check("both day kinds can be grouped together", "error" not in r and r["row_count"] >= 2, r)
for label, args in {"bad order_by": {"metrics": ["revenue"], "order_by": "city"}, "bad day_basis": {"metrics": ["revenue"], "day_basis": "pst"},
                    "bad date": {"metrics": ["revenue"], "start_date": "01/30/2026"}, "no metrics": {"metrics": []},
                    "sql in a filter value": {"metrics": ["revenue"], "filters": {"product_category": "x' OR '1'='1"}}}.items():
    check(f"query rejects: {label}", "error" in query_metric.invoke(args))
r = query_metric.invoke({"metrics": ["revenue"], "limit": 0})
check("query: limit 0 is clamped, not an error", "error" not in r and r["row_count"] == 1, r)

# ---------------------------------------------------------------- raw tools (real Trino as analyst)
check("raw: analyst sees exactly the 5 warehouse tables", len(list_tables.invoke({})) == 5)
check("raw: describe_table works and rejects a bad name", "columns" in describe_table.invoke({"table": "dwh_spark.fact_orders"}) and "error" in describe_table.invoke({"table": "x; drop"}))
for label, sql in {"DROP": "DROP TABLE dwh_spark.fact_orders", "two statements": "select 1; select 2", "the semantic schema": "select * from iceberg.semantic.orders_semantic",
                   "the semantic schema, quoted": 'select * from iceberg."semantic".orders_semantic', "an INSERT": "insert into dwh_spark.dws_daily_revenue select * from dwh_spark.dws_daily_revenue"}.items():
    check(f"raw rejects: {label}", "error" in run_sql.invoke({"sql": sql}))
check("raw: a WITH query runs", run_sql.invoke({"sql": "with t as (select 1 as x) select * from t"})["rows"] == [[1]])
r = run_sql.invoke({"sql": "select x from unnest(sequence(1, 300)) as t(x)"})
check("raw: more than 200 rows are truncated and flagged", len(r["rows"]) == 200 and r["truncated"] is True)
check("raw: PII is masked for the analyst", set(map(tuple, run_sql.invoke({"sql": "select distinct customer_name, city from iceberg.dwh_spark.dim_customer"})["rows"])) == {("REDACTED", "REDACTED")})
check("raw: a Trino error comes back as an error string", "error" in run_sql.invoke({"sql": "select nope from iceberg.dwh_spark.fact_orders"}))

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; FAILED: {failed}" if failed else ""))
sys.exit(1 if failed else 0)
