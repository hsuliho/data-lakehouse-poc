"""Compare two evaluation runs question by question (ADR 0020).

Used for a model swap, but it is the same comparison for a prompt change or a data rebuild. The point is not the two
totals: it is WHICH questions moved, and in which direction. A regression hidden behind an unchanged total is the
failure mode this exists to catch.

Everything else must be held still, so the context of both runs is compared too: a different data snapshot or a
different prompt makes the numbers incomparable, and that is reported rather than quietly ignored.
"""
import json
from collections import Counter
from pathlib import Path


def context(records: list[dict]) -> dict:
    """What the run depended on besides the model. Fields that older result files do not have come back as None."""
    return {"models": sorted({m for r in records for m in (r.get("models") or [])}),
            "prompt": sorted({p for r in records if (p := r.get("prompt_hash"))}) or None,
            "fingerprint": sorted({f for r in records if (f := r.get("fingerprint"))}) or None,
            "arms": sorted({r["arm"] for r in records}),
            "questions": len({r["id"] for r in records}), "runs": len(records)}


def load(path: str) -> list[dict]:
    return [json.loads(line) for line in open(path)]


def outcome(records: list[dict], key) -> dict:
    """{(id, arm): True/False} — a question counts as passed when every run of it passed."""
    ok = {}
    for r in records:
        k = key(r)
        ok[k] = ok.get(k, True) and (r.get("verdict") == "correct")
    return ok


def compare(baseline: list[dict], candidate: list[dict], per_arm: bool = True) -> dict:
    key = (lambda r: (r["id"], r["arm"])) if per_arm else (lambda r: (r["id"], "all"))
    a, b = outcome(baseline, key), outcome(candidate, key)
    shared = sorted(set(a) & set(b))
    moved = {"regressed": [k for k in shared if a[k] and not b[k]],      # was right, now wrong: the reason this exists
             "fixed": [k for k in shared if not a[k] and b[k]],
             "still_failing": [k for k in shared if not a[k] and not b[k]],
             "stable_pass": [k for k in shared if a[k] and b[k]]}
    ctx_a, ctx_b = context(baseline), context(candidate)
    warnings = [f"{field} differs: {ctx_a[field]} vs {ctx_b[field]}"
                for field in ("prompt", "fingerprint", "arms")
                if ctx_a[field] != ctx_b[field]]
    if not set(a) ^ set(b):
        pass
    else:
        warnings.append(f"the two runs do not cover the same questions: only in baseline {sorted(set(a) - set(b))[:4]}, "
                        f"only in candidate {sorted(set(b) - set(a))[:4]}")
    if ctx_a["models"] == ctx_b["models"]:
        warnings.append(f"both runs used the same model(s) {ctx_a['models']}: this compares something else, not a model swap")
    return {"baseline": ctx_a, "candidate": ctx_b, "warnings": warnings,
            "score": {"baseline": f"{sum(a[k] for k in shared)}/{len(shared)}",
                      "candidate": f"{sum(b[k] for k in shared)}/{len(shared)}"},
            "moved": {k: [f"{i}/{arm}" for i, arm in v] for k, v in moved.items()},
            "verdict": ("REGRESSED" if moved["regressed"] else
                        "IMPROVED" if moved["fixed"] else "UNCHANGED")}


def to_diagnose(candidate: list[dict], result: dict) -> list[dict]:
    """The candidate's records for the regressed questions only. Questions both runs fail are a problem with the system
    or the question, not with the swap, so they are not what a swap comparison should spend diagnosis on."""
    want = {tuple(k.split("/")) for k in result["moved"]["regressed"]}
    return [r for r in candidate if (r["id"], r["arm"]) in want]


def render(result: dict) -> str:
    m, s = result["moved"], result["score"]
    lines = [f"baseline {s['baseline']}   candidate {s['candidate']}   -> {result['verdict']}", ""]
    for w in result["warnings"]:
        lines.append(f"  WARNING  {w}")
    if result["warnings"]:
        lines.append("")
    for name, label in (("regressed", "退步(原本對,現在錯)"), ("fixed", "修好"),
                        ("still_failing", "兩邊都錯"), ("stable_pass", "兩邊都對")):
        if m[name]:
            lines.append(f"  {label:22} {len(m[name]):3}  {', '.join(m[name][:8])}" + (" ..." if len(m[name]) > 8 else ""))
    return "\n".join(lines)
