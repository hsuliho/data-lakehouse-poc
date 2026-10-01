"""Stage-level diagnosis of one graded failure (ADR 0020).

`rules` is the floor: it needs no model and it already settles every failure we have recorded. The `verify` arm exists
for what the rules leave as `unknown`, and as a second opinion that is allowed to disagree with them.
"""
from agent.verify.evidence import diagnose_rules, question_set
from agent.verify.tools import brief

ARMS = ("rules", "verify")


def diagnose(rec: dict, arm: str = "rules", questions: dict | None = None, llm=None) -> dict:
    """One record in, one stage verdict out. `questions` must be the set the record was graded against."""
    base = diagnose_rules(rec, questions)
    if arm == "rules":
        return base
    from agent.graph import run_question
    r = run_question("verify", brief(rec, questions), thread_id=f"verify-{rec['id']}-{rec['arm']}-{rec.get('run', 0)}", llm=llm)
    if r.get("error") or not r.get("answer"):
        return {**base, "arm_of_verifier": "verify", "error": r.get("error") or "no verdict submitted",
                "rules_said": base["cause_category"]}
    a = r["answer"]
    for k in ("evidence", "ruled_out"):                   # a model sometimes passes one string where a list is asked for
        if isinstance(a.get(k), str):
            a[k] = [a[k]]
    return {"id": rec["id"], "arm": rec["arm"], "verdict": rec.get("verdict"), "arm_of_verifier": "verify",
            "cause_category": a.get("stage"), "cause": a.get("cause"), "confidence": a.get("confidence"),
            "evidence": a.get("evidence") or [], "ruled_out": a.get("ruled_out") or [],
            "agrees_with_rules": a.get("agrees_with_rules"), "rules_said": base["cause_category"],
            "needs_human": a.get("stage") == "unknown" or a.get("agrees_with_rules") is False,
            "model": ",".join(r.get("models", [])), "seconds": r.get("seconds")}


def load(results_file: str, only_failed: bool = True, ids: list[str] | None = None) -> tuple[list[dict], dict]:
    """Records from one results file, plus the question set it was graded against."""
    import json
    questions = question_set(results_file)
    recs = [json.loads(line) for line in open(results_file)]
    if only_failed:
        recs = [r for r in recs if r.get("verdict") not in (None, "correct")]
    if ids:
        recs = [r for r in recs if r["id"] in ids]
    return recs, questions
