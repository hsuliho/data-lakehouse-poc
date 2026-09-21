"""Run the question set against one or both arms and grade every answer against the frozen expectations.
usage: python -m agent.eval.run_eval --arm both --n 1 [--ids kpi_revenue,day_taipei]"""
import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

from agent import usage
from agent.config import MODELS
from agent.graph import run_question

HERE = Path(__file__).parent


def to_number(v) -> float:
    """'36,721.23', '-41.85%', 'NT$ 778.75' -> a float; anything else raises ValueError."""
    return float(re.sub(r"[%,\s$元]|NT|TWD", "", str(v)))


def grade(q: dict, r: dict) -> str:
    exp = q["expect"]
    if "error" in r:
        return "step_limit" if r["error"].startswith("GraphRecursionError") else "error"
    a = r["answer"]
    if a is None:
        return "format_error"
    if a["status"] not in exp["statuses"]:
        return "wrong_status"
    text = r["raw_answer"]
    if any(bad in text for bad in exp.get("must_not_contain", [])):
        return "leaked_pii"
    if "value" in exp:
        try:
            ok = abs(to_number(a["value"]) - exp["value"]) <= exp["tol"]
        except (TypeError, ValueError):
            return "wrong_value"
        return "correct" if ok else "wrong_value"
    if "text" in exp:
        return "correct" if exp["text"].lower() in str(a.get("value") or a.get("answer")).lower() else "wrong_value"
    return "correct"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["semantic", "raw", "both"], default="both")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--ids", default="")
    ap.add_argument("--skip-preflight", action="store_true", help="do not run the canary queries first")
    ap.add_argument("--resume", default="", help="a results file to continue: runs already in it are skipped")
    args = ap.parse_args()
    if not args.skip_preflight:
        from agent.eval.preflight import preflight
        if problems := preflight():
            raise SystemExit("PREFLIGHT FAILED, nothing was run (no model requests spent):\n  " + "\n  ".join(problems))
        print("preflight OK: the semantic layer and the warehouse return the known answers")
    questions = json.loads((HERE / "questions.json").read_text())
    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids.split(",")]
    arms = ["semantic", "raw"] if args.arm == "both" else [args.arm]
    out = Path(args.resume).resolve() if args.resume else HERE / "results" / f"{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    done = {(r["id"], r["arm"], r["run"]) for r in map(json.loads, open(out))} if args.resume else set()
    out.parent.mkdir(exist_ok=True)
    total = len(questions) * len(arms) * args.n
    print(f"{total} runs -> {out.relative_to(HERE.parent.parent)}")
    i = 0
    for run in range(args.n):
        for q in questions:
            for arm in arms:
                i += 1
                if (q["id"], arm, run) in done:
                    continue
                if usage.capacity_left(MODELS) < 12:           # 12 = the most model calls one run can make within the step limit
                    print(f"STOP: no model has enough requests left today ({usage.used_today()} used); continue after the reset with --resume {out}")
                    return
                r = run_question(arm, q["question"], thread_id=f"{q['id']}-{arm}-{run}")
                verdict = grade(q, r)
                rec = {"id": q["id"], "category": q["category"], "arm": arm, "run": run, "verdict": verdict, **r}
                with open(out, "a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                print(f"[{i:3}/{total}] {q['id']:14} {arm:8} run{run} -> {verdict:12} {r.get('seconds', '?'):>5}s  tools={r.get('steps', '-')}  in={r.get('input_tokens', '-')} out={r.get('output_tokens', '-')}  llm_calls={r.get('llm_calls', '-')} 429s={len(r.get('rate_limit_waits', []))} throttled={r.get('throttle_seconds', '-')}s", flush=True)
                time.sleep(0.5)
    print("results:", out)


if __name__ == "__main__":
    main()
