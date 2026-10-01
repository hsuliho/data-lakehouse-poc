"""Verification agent (ADR 0020): why a graded evaluation failed, and whether a change made things worse.

  diagnose  one stage verdict per failed run      (--arm rules costs nothing; --arm verify asks a model)
  suggest   prompt proposals from a batch         (--arm patterns costs nothing)
  compare   two evaluation runs, question by question
  regress   replay the frozen fixtures            (never costs anything)

Stage one -- running the questions and grading the answers -- stays in `agent.eval.run_eval`. This is stage two: it
reads what that produced and never re-runs a question, because a rerun of a failed question may pass by chance.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent


def _write(rows: list[dict], label: str) -> Path:
    out = HERE / "results" / f"{label}_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


def cmd_diagnose(a):
    from agent.verify.diagnose import diagnose, load
    recs, questions = load(a.source, only_failed=not a.include_passed, ids=a.ids.split(",") if a.ids else None)
    if not recs:
        print("nothing to diagnose in", a.source)
        return
    print(f"{len(recs)} record(s), arm={a.arm}")
    rows = []
    for r in recs:
        d = diagnose(r, arm=a.arm, questions=questions)
        rows.append(d)
        flag = "  <- needs a human" if d.get("needs_human") else ""
        print(f"  {d['id']:14} {d['arm']:8} {str(d['verdict']):12} -> {str(d['cause_category']):14} "
              f"{str(d.get('cause'))[:70]}{flag}")
    print("\nsaved to", _write(rows, "diagnose"))


def cmd_suggest(a):
    rows = [json.loads(line) for line in open(a.source)]
    from agent.verify.suggest import suggest
    out = suggest(rows, arm=a.arm)
    print(out["brief"])
    if not out["proposals"]:
        print("\n(no proposals" + (": the patterns arm does not ask a model)" if a.arm == "patterns" else ")"))
        return
    for p in out["proposals"]:
        print(f"\n模式   {p.get('pattern')}")
        print(f"案例   {p.get('cases')}")
        print(f"提案   {p.get('proposal')}")
        print(f"預期   {p.get('expected_effect')}")
        print(f"風險   {p.get('risk')}")
    print("\nNOTHING WAS APPLIED. Edit agent/prompts.py yourself, then re-run the FULL question set:")
    print("  a proposal validated only on the questions it was derived from proves nothing.")
    print("\nsaved to", _write(out["proposals"], "suggest"))


def cmd_compare(a):
    from agent.verify import compare as C
    base, cand = C.load(a.baseline), C.load(a.candidate)
    result = C.compare(base, cand)
    print(C.render(result))
    regressed = C.to_diagnose(cand, result)
    if regressed:
        print(f"\n{len(regressed)} regressed record(s). Diagnose only those:")
        print(f"  python -m agent.verify diagnose --source {a.candidate} "
              f"--ids {','.join(sorted({r['id'] for r in regressed}))}")
    raise SystemExit(1 if result["verdict"] == "REGRESSED" else 0)


def cmd_regress(a):
    from agent.verify import regress
    rows = regress.replay()
    bad = [r for r in rows if not r["ok"]]
    for r in bad:
        print(f"DRIFT {r['id']:14} {r['arm']:8} expected {r['expected']:14} got {r['got']:14}")
    print(f"{len(rows) - len(bad)}/{len(rows)} stable")
    raise SystemExit(1 if bad else 0)


p = argparse.ArgumentParser(prog="agent.verify", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = p.add_subparsers(dest="cmd", required=True)

d = sub.add_parser("diagnose", help="stage verdict for each failed run in a results file")
d.add_argument("--source", required=True, help="an agent/eval/results/*.jsonl file")
d.add_argument("--arm", default="rules", choices=["rules", "verify"])
d.add_argument("--ids", default="", help="only these question ids")
d.add_argument("--include-passed", action="store_true", help="also diagnose the runs that passed")
d.set_defaults(func=cmd_diagnose)

s = sub.add_parser("suggest", help="prompt proposals from a batch of stage verdicts")
s.add_argument("--source", required=True, help="an agent/verify/results/diagnose_*.jsonl file")
s.add_argument("--arm", default="patterns", choices=["patterns", "suggest"])
s.set_defaults(func=cmd_suggest)

c = sub.add_parser("compare", help="two evaluation runs, question by question")
c.add_argument("--baseline", required=True)
c.add_argument("--candidate", required=True)
c.set_defaults(func=cmd_compare)

r = sub.add_parser("regress", help="replay the frozen fixtures (no model, no services)")
r.set_defaults(func=cmd_regress)

a = p.parse_args()
a.func(a)
