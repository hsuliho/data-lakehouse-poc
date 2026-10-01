"""Offline checks for the verification agent: the rules, the fixture replay, and the mutation test that proves the
replay would notice a drift. Nothing here calls a model or needs a service, so it costs no quota.
Run: python -m agent.tests.test_verify_offline
"""
import json

from agent.verify import evidence as E
from agent.verify import regress

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def rec(**kw):
    """A graded result record with sane defaults; `tools` is [(name, args, result_text)]."""
    base = {"id": "kpi_revenue", "arm": "semantic", "question": "總營收是多少?", "verdict": "wrong_value",
            "answer": {"status": "answered", "value": "1.0", "basis": "-"}, "steps": 1, "tool_calls": []}
    tools = kw.pop("tools", None)
    if tools is not None:
        base["tool_calls"] = [{"tool": t, "args": a, "results": [r]} for t, a, r in tools]
    return {**base, **kw}


def cat(r):
    return E.classify(E.facts(r))["category"]


# ---------------------------------------------------------------- fact extraction
f = E.facts(rec(tools=[("query_metric", {"metrics": ["revenue"], "day_basis": "utc"}, '{"rows": [{"revenue": 36876.98}]}')]))
check("facts: the golden value is found in a tool result", f["golden_in_tools"] == ["query_metric"], f["golden_in_tools"])
check("facts: the day basis actually used is read from the last query", f["day_basis"]["used"] == "utc", f["day_basis"])
check("facts: a question naming no day constrains no day", f["days"]["expected"] == [], f["days"])

f = E.facts(rec(id="day_taipei", question="1 月 30 日(台北時間)的營收是多少?",
                tools=[("query_metric", {"start_date": "2026-01-30", "end_date": "2026-01-30"}, '{"rows": []}')]))
check("facts: a Chinese date becomes an ISO day", f["days"]["expected"] == ["2026-01-30"], f["days"])
check("facts: '台北' means the Taipei day is expected", f["day_basis"]["expected"] == "taipei", f["day_basis"])
check("facts: a closed range on the named day is not an open range", f["open_range"] == [], f["open_range"])

f = E.facts(rec(id="day_utc", question="UTC 時間 1 月 30 日的營收是多少?", tools=[]))
check("facts: 'UTC' means the UTC day is expected", f["day_basis"]["expected"] == "utc", f["day_basis"])

# ---------------------------------------------------------------- one case per category
check("rule: a correct run is no_problem_found", cat(rec(verdict="correct")) == "no_problem_found")
check("rule: an unfinished run is env_error", cat(rec(verdict="error", error="ResourceExhausted: 429")) == "env_error")
check("rule: the step limit is a flow error", cat(rec(verdict="step_limit")) == "flow_error")
check("rule: plain text instead of submit is a format error", cat(rec(verdict="format_error")) == "format_error")
check("rule: the wrong status is a flow error", cat(rec(verdict="wrong_status", id="clarify", question="最近的營收表現如何?")) == "flow_error")

check("rule: the wrong day basis is a parameter error, not a tool error",
      cat(rec(id="day_taipei", question="1 月 30 日(台北時間)的營收是多少?",
              tools=[("query_metric", {"day_basis": "utc", "start_date": "2026-01-30", "end_date": "2026-01-30"},
                      '{"rows": [{"revenue": 467.25}]}')])) == "tool_params")

check("rule: a range left open on a single-day question is a parameter error",
      cat(rec(id="asof_toys_day", question="1 月 19 日(台北時間),toys 類別的營收是多少?",
              tools=[("query_metric", {"start_date": "2026-01-19"}, '{"rows": [{"revenue": 3318.13}]}')])) == "tool_params")

check("rule: a period named in words that was never queried is a parameter error",
      cat(rec(id="day_last_week", question="上週的營收是多少?",
              tools=[("query_metric", {"start_date": "2026-01-25", "end_date": "2026-01-31"}, '{"rows": [{"revenue": 1.0}]}')])) == "tool_params")

# the platform / model split, which is the whole point of the stage verdict
check("rule: the tool returned the expected value and the model submitted another -> model_misread",
      cat(rec(answer={"status": "answered", "value": "37229.72", "basis": "-"},
              tools=[("query_metric", {"metrics": ["revenue"]}, '{"rows": [{"revenue": 36876.98}]}')])) == "model_misread")

check("rule: a tool that returned an error is a tool_output problem",
      cat(rec(tools=[("query_metric", {"metrics": ["revenue"]}, '{"error": "the query timed out"}')])) == "tool_output")

check("rule: no tool called at all is a tool_choice problem", cat(rec(tools=[])) == "tool_choice")

check("rule: the model wrote the query and it returned the wrong number -> tool_choice",
      cat(rec(arm="raw", tools=[("run_sql", {"sql": "select sum(line_amount) from dwh_spark.fact_orders"},
                                 '{"rows": [[37229.72]]}')])) == "tool_choice")

check("rule: a derived answer cannot be found in a tool result, so it is not blamed on the tool",
      cat(rec(id="day_wow", question="上週的營收比前一週成長了多少百分比?",
              tools=[("query_metric", {"start_date": "2026-01-19", "end_date": "2026-01-25"}, '{"rows": [{"revenue": 12128.39}]}'),
                     ("query_metric", {"start_date": "2026-01-12", "end_date": "2026-01-18"}, '{"rows": [{"revenue": 20857.72}]}')])) == "model_misread")

check("rule: an unexplained wrong value stays unknown instead of being guessed",
      cat(rec(tools=[("query_metric", {"metrics": ["orders"]}, '{"rows": [{"orders": 67}]}')])) == "unknown")

# ---------------------------------------------------------------- the question set follows the results file
cur = E.question_set("agent/eval/results/x.jsonl")
old = E.question_set("agent/eval/archive_batch_era/results/x.jsonl")
check("question set: a results file is graded against its own frozen expectations",
      cur["kpi_revenue"]["expect"]["value"] != old["kpi_revenue"]["expect"]["value"],
      f'{cur["kpi_revenue"]["expect"]["value"]} vs {old["kpi_revenue"]["expect"]["value"]}')

# ---------------------------------------------------------------- replay, and the mutation that must break it
rows = regress.replay()
check("regress: every frozen failure still gets its recorded stage", all(r["ok"] for r in rows),
      [f"{r['id']}:{r['expected']}->{r['got']}" for r in rows if not r["ok"]][:3])
check("regress: the fixtures cover more than one stage", len({r["expected"] for r in rows}) >= 5)

_real = E.classify
try:                                                   # a rule that mislabels one stage must be caught by the replay
    E.classify = lambda f: (_real(f) if _real(f)["category"] != "tool_params"
                            else {"category": "model_misread", "reason": "mutated", "confidence": "high"})
    mutated = regress.replay()
finally:
    E.classify = _real
check("regress: a mutated rule is detected (the replay is not vacuous)",
      any(not r["ok"] for r in mutated), f"{sum(not r['ok'] for r in mutated)} drifted")
check("regress: the mutation is detected on exactly the relabelled stage",
      {r["expected"] for r in mutated if not r["ok"]} == {"tool_params"})

# ---------------------------------------------------------------- the verify arm (scripted model, no quota)
import itertools, tempfile
from pathlib import Path
from langchain_core.messages import AIMessage

from agent import graph as G
from agent.verify.diagnose import diagnose, load
from agent.verify.tools import brief

G.usage.FILE = Path(tempfile.mkdtemp()) / "usage.json"          # never count these against the real Gemini day
_ids = itertools.count()


class Scripted:
    def __init__(self, *script): self.script, self.seen = list(script), None
    def bind_tools(self, tools): return self
    def invoke(self, messages):
        self.seen = messages
        return self.script.pop(0)


def ai(*tool_calls, text=""):
    return AIMessage(content=text, tool_calls=[{"name": n, "args": a, "id": f"c{next(_ids)}", "type": "tool_call"} for n, a in tool_calls])


def verdict(**kw):
    return ai(("submit_verdict", {"stage": "tool_params", "cause": "c", "confidence": "high",
                                  "evidence": ["query_metric(start_date=2026-01-19, end_date=None)"],
                                  "ruled_out": [], "agrees_with_rules": True, **kw}))


recs, qs = load("agent/eval/results/20260921_143227.jsonl")
one = next(r for r in recs if r["id"] == "asof_toys_day")

text = brief(one, qs)
check("brief: the frozen expectation is in the prompt", "1055.15" in text)
check("brief: every tool call and its arguments are in the prompt", "start_date" in text and "query_metric" in text)
check("brief: the fixed-rule reading is offered to the model", "固定規則的判定:tool_params" in text)

d = diagnose(one, arm="verify", questions=qs, llm=Scripted(verdict()))
check("verify: a submitted verdict becomes the stage", d["cause_category"] == "tool_params" and d["arm_of_verifier"] == "verify", d)
check("verify: what the rules said is kept for comparison", d["rules_said"] == "tool_params")
check("verify: agreeing with the rules needs no human", d["needs_human"] is False)

d = diagnose(one, arm="verify", questions=qs, llm=Scripted(verdict(stage="model_misread", agrees_with_rules=False)))
check("verify: disagreeing with the rules is flagged for a human", d["needs_human"] is True and d["rules_said"] == "tool_params", d)

d = diagnose(one, arm="verify", questions=qs, llm=Scripted(verdict(stage="unknown", confidence="low")))
check("verify: an unknown verdict is flagged for a human", d["needs_human"] is True)

d = diagnose(one, arm="verify", questions=qs, llm=Scripted(ai(text="錯在參數"), ai(text="還是參數")))
check("verify: plain text twice ends the run and falls back to the rules",
      d.get("error") and d["cause_category"] == "tool_params", d)

d = diagnose(one, arm="rules", questions=qs)
check("verify: the rules arm needs no model at all", d["arm_of_verifier"] == "rules" and d["cause_category"] == "tool_params")

# ---------------------------------------------------------------- suggest (batch level)
from agent.verify.suggest import patterns, suggest, brief as batch_brief

vs = [diagnose(r, "rules", qs) for r in recs]
pats = patterns(vs)
check("suggest: patterns are grouped by stage, biggest first", [p["stage"] for p in pats][0] == "tool_choice", pats)
check("suggest: a stage seen once is not a pattern", all(p["count"] >= 2 for p in pats))
check("suggest: env_error is excluded (a prompt cannot fix the API)",
      "env_error" not in {p["stage"] for p in patterns(vs + [{"id": "x", "arm": "raw", "cause_category": "env_error", "cause": "429"}] * 3)})
check("suggest: the batch summary carries the counts the proposal must explain",
      "[tool_choice] 7 筆" in batch_brief(vs, pats, 36))

out = suggest(vs, total_runs=36, arm="patterns")
check("suggest: the patterns arm spends no model request", out["model"] is None and out["proposals"] == [])

prop = [{"pattern": "4 筆參數錯都是日期區間沒有收斂", "cases": ["asof_toys_day/semantic"],
         "proposal": "查詢單一天時,start_date 與 end_date 都要填那一天。",
         "expected_effect": "asof_toys_day, day_last_week", "risk": "可能讓跨期間的題目也被收窄"}]
out = suggest(vs, llm=Scripted(ai(("submit_proposals", {"proposals": prop}))))
check("suggest: proposals come back with the risk field", out["proposals"][0]["risk"], out["proposals"])

out = suggest(vs, llm=Scripted(ai(("submit_proposals", {"proposals": json.dumps(prop, ensure_ascii=False)}))))
check("suggest: a list sent as a JSON string is still parsed", isinstance(out["proposals"], list) and out["proposals"][0]["cases"])

out = suggest(vs, llm=Scripted(ai(("submit_proposals", {"proposals": [], "no_change_needed": True}))))
check("suggest: 'no change needed' is a valid answer, not an error", out["proposals"] == [] and "error" not in out)

# ---------------------------------------------------------------- compare (two evaluation runs)
from agent.verify import compare as C

def run(**pass_fail):
    """A tiny evaluation run: {question_id: passed}."""
    ctx = pass_fail.pop("_ctx", {})
    return [{"id": k, "arm": "semantic", "verdict": "correct" if v else "wrong_value",
             "models": ctx.get("models", ["m1"]), "prompt_hash": ctx.get("prompt", "p1"),
             "fingerprint": ctx.get("fp", "f1")} for k, v in pass_fail.items()]

base = run(a=True, b=True, c=False, _ctx={"models": ["old"]})
cand = run(a=True, b=False, c=True, _ctx={"models": ["new"]})
res = C.compare(base, cand)
check("compare: a question that was right and is now wrong is a regression", res["moved"]["regressed"] == ["b/semantic"], res["moved"])
check("compare: a question that was wrong and is now right is a fix", res["moved"]["fixed"] == ["c/semantic"])
check("compare: the verdict is REGRESSED even when the total is unchanged",
      res["verdict"] == "REGRESSED" and res["score"]["baseline"] == res["score"]["candidate"], res["score"])
check("compare: only the regressed records are handed to diagnosis",
      [r["id"] for r in C.to_diagnose(cand, res)] == ["b"])

same = C.compare(run(a=True, _ctx={"models": ["m"]}), run(a=True, _ctx={"models": ["m"]}))
check("compare: comparing two runs on the same model is called out", any("same model" in w for w in same["warnings"]), same["warnings"])

drift = C.compare(run(a=True, _ctx={"models": ["old"], "fp": "f1"}), run(a=True, _ctx={"models": ["new"], "fp": "f2"}))
check("compare: a different data snapshot makes the comparison unsound and is reported",
      any("fingerprint differs" in w for w in drift["warnings"]), drift["warnings"])

drift = C.compare(run(a=True, _ctx={"models": ["old"], "prompt": "p1"}), run(a=True, _ctx={"models": ["new"], "prompt": "p2"}))
check("compare: a different prompt is reported too", any("prompt differs" in w for w in drift["warnings"]))

partial = C.compare(run(a=True, b=True, _ctx={"models": ["old"]}), run(a=True, _ctx={"models": ["new"]}))
check("compare: runs covering different questions are reported",
      any("do not cover the same questions" in w for w in partial["warnings"]), partial["warnings"])

from agent.graph import prompt_hash
check("prompt hash: each arm has its own, and it is stable",
      prompt_hash("semantic") != prompt_hash("raw") and prompt_hash("semantic") == prompt_hash("semantic"))

n = sum(ok for _, ok in results)
print(f"\n{n}/{len(results)} passed")
raise SystemExit(0 if n == len(results) else 1)
