"""Facts about one graded run, and the fixed rules over them (ADR 0020).

Everything here reads a STORED result record (agent/eval/results/*.jsonl), never the live system: a rerun of a failed
question may pass by chance, and then the failure cannot be diagnosed at all. The record already carries every tool call
with its arguments and its result, which is what the stage-level verdict is derived from.

The two splits that matter are mechanical, so they cost no model request:
  * the golden value is nowhere in the tool results -> the agent was given the wrong numbers (a platform problem), or it
    asked the wrong question;
  * the golden value IS in a tool result but the submitted value differs -> the agent had the right numbers and still got
    it wrong (a model problem).
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent


def question_set(results_file) -> dict:
    """The questions.json that belongs to a results file: agent/eval/<anything>/results/x.jsonl -> that folder's own set.
    The archived batch-era run has different frozen values, so grading it against the current set invents failures."""
    path = Path(results_file).resolve().parent.parent / "questions.json"
    return {q["id"]: q for q in json.loads(path.read_text())}


QUESTIONS = question_set(HERE.parent / "eval" / "results" / "_")       # the current set, for callers that have no file
CONSTRAINTS = {k: v for k, v in json.loads((HERE / "constraints.json").read_text()).items() if not k.startswith("_")}

CATEGORIES = ["tool_choice", "tool_params", "tool_output", "model_misread",
              "format_error", "flow_error", "env_error", "no_problem_found", "unknown"]

# tools whose result the model dictates (it writes the SQL): a wrong number from them is the model's doing, not the
# platform's. query_metric is the opposite: the model only picks a metric and some parameters.
MODEL_WRITES_THE_QUERY = {"run_sql"}
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_DATE_CN = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")
YEAR = 2026                                    # the mock data and TODAY_NOTE are both 2026


def numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(text or ""):
        try:
            out.append(float(m.group()))
        except ValueError:
            pass
    return out


def near(values, want, tol) -> bool:
    return any(abs(v - want) <= tol for v in values)


def expected_days(question: str) -> list[str]:
    """The calendar days the question names, as YYYY-MM-DD. Empty when it names none ('last week')."""
    return [f"{YEAR}-{int(mth):02d}-{int(day):02d}" for mth, day in _DATE_CN.findall(question)]


def expected_basis(question: str) -> str | None:
    """'utc' only when the user asked for it explicitly; 'taipei' when the question says so; else None (not constrained)."""
    if "UTC" in question.upper():
        return "utc"
    if "台北" in question:
        return "taipei"
    return None


def facts(rec: dict, questions: dict | None = None) -> dict:
    """What a rule (or the model) needs, read out of one stored result record."""
    q = (questions or QUESTIONS)[rec["id"]]
    exp = q["expect"]
    golden, tol = exp.get("value"), exp.get("tol", 0.01)
    answer = rec.get("answer") or {}
    tools = []
    for c in rec.get("tool_calls") or []:
        text = " ".join(str(x) for x in (c.get("results") or []))
        tools.append({"tool": c["tool"], "args": c.get("args") or {}, "result_head": text[:300],
                      "has_golden": golden is not None and near(numbers(text), golden, tol),
                      "error": bool(re.search(r'"error"|Access Denied|Traceback', text))})
    try:
        submitted = float(re.sub(r"[%,\s$元]|NT|TWD", "", str(answer.get("value"))))
    except (TypeError, ValueError):
        submitted = None
    want_basis = expected_basis(rec["question"])
    used_basis = next((t["args"].get("day_basis", "taipei") for t in reversed(tools) if t["tool"] == "query_metric"), None)
    want_days = expected_days(rec["question"])
    used_days = sorted({d for t in tools for k in ("start_date", "end_date") if (d := t["args"].get(k))})
    return {
        "id": rec["id"], "arm": rec["arm"], "verdict": rec.get("verdict"), "question": rec["question"],
        "expected_statuses": exp.get("statuses"), "golden": golden, "golden_text": exp.get("text"), "tol": tol,
        "status": answer.get("status"), "submitted_value": submitted, "basis_text": answer.get("basis"),
        "tools": tools, "steps": rec.get("steps"), "models": rec.get("models"), "error": rec.get("error"),
        "golden_in_tools": [t["tool"] for t in tools if t["has_golden"]],
        "tool_errors": [t["tool"] for t in tools if t["error"]],
        "day_basis": {"expected": want_basis, "used": used_basis},
        "days": {"expected": want_days, "used": used_days},
        "derived": bool(CONSTRAINTS.get(rec["id"], {}).get("derived")),
        # periods named in words: every declared range must actually have been queried
        "missing_ranges": [f"{a}..{b}" for a, b in CONSTRAINTS.get(rec["id"], {}).get("ranges", [])
                           if not any(t["args"].get("start_date") == a and t["args"].get("end_date") == b for t in tools)],
        "model_wrote_the_query": any(t["tool"] in MODEL_WRITES_THE_QUERY for t in tools),
        # a question naming one day must be asked as that one day: start_date alone means "from then on"
        "open_range": [f"{t['tool']}(start_date={t['args'].get('start_date')}, end_date={t['args'].get('end_date')})"
                       for t in tools if len(want_days) == 1
                       and (t["args"].get("start_date") or t["args"].get("end_date"))
                       and (t["args"].get("start_date") != want_days[0] or t["args"].get("end_date") != want_days[0])],
    }


def classify(f: dict) -> dict:
    """Fixed rules, in the order a person would look. {category, reason, confidence}."""
    hi = lambda cat, why: {"category": cat, "reason": why, "confidence": "high"}
    med = lambda cat, why: {"category": cat, "reason": why, "confidence": "medium"}

    if f["verdict"] == "correct":
        return hi("no_problem_found", "the answer matched the frozen expectation")
    if f["verdict"] == "error":
        return hi("env_error", f"the run did not finish: {str(f['error'])[:200]}")
    if f["verdict"] == "step_limit":
        return hi("flow_error", f"the step limit was reached after {f['steps']} tool calls without a submitted answer")
    if f["verdict"] == "format_error":
        return hi("format_error", "the model answered in plain text instead of calling the submit tool")
    if f["verdict"] == "leaked_pii":
        # the same split as for a wrong value: masked at the source, or invented by the model?
        return (hi("tool_output", "personal data reached the agent: the masking in Trino did not hold")
                if any("REDACTED" not in t["result_head"] and t["tool"] in ("run_sql", "describe_table") for t in f["tools"])
                else hi("flow_error", "the model put personal data in an answer it should have refused"))
    if f["verdict"] == "wrong_status":
        return hi("flow_error", f"status {f['status']!r}, expected one of {f['expected_statuses']}")

    if f["verdict"] != "wrong_value":
        return med("unknown", f"no rule covers verdict {f['verdict']!r}")

    # --- wrong value: parameters first (a tool that was told the wrong thing is not a tool that is broken)
    want, used = f["day_basis"]["expected"], f["day_basis"]["used"]
    if want and used and want != used:
        return hi("tool_params", f"the question asks for the {want} day, the query used day_basis={used!r}")
    want_days, used_days = f["days"]["expected"], f["days"]["used"]
    if want_days and used_days and set(want_days) - set(used_days):
        return hi("tool_params", f"the question names {want_days}, the query used {used_days}")
    if f["missing_ranges"]:
        return hi("tool_params", f"the period the question names was never queried: {f['missing_ranges']} missing; "
                                 f"the query used {f['days']['used']}")
    if want_days and f["open_range"]:
        return hi("tool_params", f"the question names {want_days} but the query left the range open: {f['open_range']}")
    if f["tool_errors"]:
        return hi("tool_output", f"a tool returned an error: {f['tool_errors']}")
    if not f["tools"]:
        return hi("tool_choice", "no tool was called at all: the number cannot have come from the warehouse")

    # A derived answer is never in a tool result, so the usual test cannot apply. But that only points at the arithmetic
    # when the model did NOT choose the inputs itself: an arm that writes its own SQL can just as easily have derived
    # correctly from the wrong numbers, and the query is the thing it had full control over.
    if f["derived"] and not f["model_wrote_the_query"]:
        return med("model_misread", "the expected value is derived, so no tool can return it: the parameters are right and "
                                    f"tool data came back, so the arithmetic that produced {f['submitted_value']} is where it went wrong")
    if f["golden_in_tools"]:
        return hi("model_misread", f"{f['golden_in_tools']} returned the expected value, "
                                   f"but {f['submitted_value']} was submitted instead of {f['golden']}")
    if f["model_wrote_the_query"]:
        return hi("tool_choice", "the model wrote the query itself and it returned the wrong number: "
                                 "the query, not the tool, is what is wrong")
    return med("unknown", "no tool returned the expected value and the parameters look right: "
                          "either the metric chosen is the wrong one, or the semantic layer is wrong")


def diagnose_rules(rec: dict, questions: dict | None = None) -> dict:
    f = facts(rec, questions)
    v = classify(f)
    return {"id": f["id"], "arm": f["arm"], "verdict": f["verdict"], "arm_of_verifier": "rules",
            "cause_category": v["category"], "cause": v["reason"], "confidence": v["confidence"],
            "evidence": [f"{t['tool']}({json.dumps(t['args'], ensure_ascii=False)}) -> {t['result_head'][:120]}" for t in f["tools"]][:4],
            "ruled_out": [], "suggested_actions": [], "needs_human": v["category"] == "unknown"}
