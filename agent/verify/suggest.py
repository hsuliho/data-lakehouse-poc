"""Turn a batch of stage verdicts into prompt proposals (ADR 0020).

Deliberately batch-level: changing the prompt for one failed question overfits to that question. Only a pattern across
several failures is worth a prompt change, and every proposal must name what it might break, because a prompt rule is
zero-sum -- the sentence that fixes the calendar questions is the sentence that can ruin the rest.

Nothing here applies anything. The output is a proposal for a person to accept, like the diagnosis agent's suggestions.
"""
import json
from collections import Counter, defaultdict

MIN_CASES = 2                  # one case is an anecdote, not a pattern


def patterns(verdicts: list[dict]) -> list[dict]:
    """Group the stage verdicts into candidate patterns. Fixed rules, no model: this is what the proposal must explain."""
    by_stage = defaultdict(list)
    for v in verdicts:
        if v.get("cause_category") in (None, "no_problem_found", "env_error"):
            continue                                      # nothing to fix in the prompt
        by_stage[v["cause_category"]].append(v)
    out = []
    for stage, group in sorted(by_stage.items(), key=lambda kv: -len(kv[1])):
        if len(group) < MIN_CASES:
            continue
        out.append({"stage": stage, "count": len(group),
                    "cases": [f"{v['id']}/{v['arm']}" for v in group],
                    "questions": dict(Counter(v["id"] for v in group)),
                    "arms": dict(Counter(v["arm"] for v in group)),
                    "reasons": [v["cause"] for v in group[:3]]})
    return out


def brief(verdicts: list[dict], pats: list[dict], total_runs: int | None = None) -> str:
    lines = [f"這一批共 {len(verdicts)} 筆失敗的環節判定"
             + (f"(總執行 {total_runs} 筆)" if total_runs else "") + ":", ""]
    for p in pats:
        lines += [f"[{p['stage']}] {p['count']} 筆   題目 {p['questions']}   組別 {p['arms']}"]
        lines += [f"    理由:{r[:160]}" for r in p["reasons"]]
    singles = [v for v in verdicts if v.get("cause_category") not in {p["stage"] for p in pats}]
    if singles:
        lines += ["", f"未成形的個案({len(singles)} 筆,少於 {MIN_CASES} 筆不構成模式):"]
        lines += [f"    {v['id']}/{v['arm']} {v.get('cause_category')} — {str(v.get('cause'))[:120]}" for v in singles[:6]]
    return "\n".join(lines)


def suggest(verdicts: list[dict], total_runs: int | None = None, llm=None, arm: str = "suggest") -> dict:
    """{patterns, proposals}. `arm='patterns'` stops at the fixed grouping and spends no model request."""
    pats = patterns(verdicts)
    text = brief(verdicts, pats, total_runs)
    if arm == "patterns" or not pats:
        return {"patterns": pats, "brief": text, "proposals": [], "model": None}
    from agent.graph import run_question
    r = run_question("suggest", text, thread_id="suggest", llm=llm)
    if r.get("error") or not r.get("answer"):
        return {"patterns": pats, "brief": text, "proposals": [], "model": None,
                "error": r.get("error") or "no proposals submitted"}
    a = r["answer"]
    props = a.get("proposals")
    if isinstance(props, str):                            # a model sometimes sends the list as one JSON string
        try:
            props = json.loads(props)
        except ValueError:
            props = [{"pattern": props, "cases": [], "proposal": "", "expected_effect": "", "risk": ""}]
    return {"patterns": pats, "brief": text, "proposals": props or [],
            "model": ",".join(r.get("models", [])), "seconds": r.get("seconds")}
