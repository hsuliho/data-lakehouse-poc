"""Summarise one or more result files: accuracy per arm and category, failure classes, tokens and time.
usage: python -m agent.eval.report [results/*.jsonl]   (default: every file in results/)"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

files = [Path(a) for a in sys.argv[1:]] or sorted((Path(__file__).parent / "results").glob("*.jsonl"))
from agent.eval.run_eval import grade

QUESTIONS = {q["id"]: q for q in json.loads((Path(__file__).parent / "questions.json").read_text())}
rows = [json.loads(l) for f in files for l in open(f)]
for r in rows:                       # grade again from the stored answer, so a corrected grading rule applies to old runs too
    r["verdict"] = grade(QUESTIONS[r["id"]], r)
arms = sorted({r["arm"] for r in rows})
print(f"{len(rows)} runs from {len(files)} file(s)")
_models = Counter(m for r in rows for m in (r.get("models") or ["(not recorded)"]))
print("models:", dict(_models), "" if len(_models) == 1 else "  <- MIXED: the comparison between arms is not clean unless both arms ran on the same model")
print()
print(f"{'':16}" + "".join(f"{a:>22}" for a in arms))
def acc(rs): return f"{sum(r['verdict'] == 'correct' for r in rs)}/{len(rs)} ({100 * sum(r['verdict'] == 'correct' for r in rs) / max(len(rs), 1):.0f}%)"
print(f"{'ALL':16}" + "".join(f"{acc([r for r in rows if r['arm'] == a]):>22}" for a in arms))
for cat in sorted({r["category"] for r in rows}):
    print(f"{cat:16}" + "".join(f"{acc([r for r in rows if r['arm'] == a and r['category'] == cat]):>22}" for a in arms))
print("\nfailure classes:")
for a in arms:
    print(f"  {a:9}", dict(Counter(r["verdict"] for r in rows if r["arm"] == a and r["verdict"] != "correct")) or "none")
print("\nper-question (correct / runs):")
per = defaultdict(lambda: defaultdict(list))
for r in rows: per[r["id"]][r["arm"]].append(r["verdict"] == "correct")
for qid, d in per.items():
    print(f"  {qid:14}" + "".join(f"{a}: {sum(d[a])}/{len(d[a])}   " for a in arms))
print("\ncost and time (mean per run):")
for a in arms:
    rs = [r for r in rows if r["arm"] == a]
    print(f"  {a:9} input tokens {sum(r.get('input_tokens', 0) for r in rs) / len(rs):8.0f}   output tokens {sum(r.get('output_tokens', 0) for r in rs) / len(rs):6.0f}   seconds {sum(r.get('seconds', 0) for r in rs) / len(rs):5.1f} (throttle {sum(r.get('throttle_seconds', 0) for r in rs) / len(rs):4.1f})   LLM calls {sum(r.get('llm_calls', 0) for r in rs) / len(rs):4.1f}   tool calls {sum(r.get('steps', 0) for r in rs) / len(rs):4.1f}")
