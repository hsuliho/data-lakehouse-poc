"""Freeze every graded failure we already have as a fixture for `verify regress`.
The label is the fixed-rule verdict, spot-checked by a person once (REPORT stage 17); after that it is the standard the
rules must keep reproducing, so a change to classify() that silently relabels an old failure shows up as a regression.

usage: python -m agent.verify.build_fixtures [--write]
"""
import argparse
import glob
import json
from pathlib import Path

from agent.verify.evidence import diagnose_rules, facts, question_set

HERE = Path(__file__).parent
SOURCES = ["agent/eval/results/*.jsonl", "agent/eval/archive_batch_era/results/*.jsonl"]


def collect():
    out = []
    for pat in SOURCES:
        files = sorted(f for f in glob.glob(pat) if "invalidated" not in f)
        if not files:
            continue
        qs = question_set(files[0])                      # each results folder has its own frozen expectations
        seen = set()
        for f in files:
            for line in open(f):
                r = json.loads(line)
                key = (r["id"], r["arm"], r.get("run"), r.get("seconds"))
                if key in seen or r.get("verdict") in (None, "correct"):
                    continue
                seen.add(key)
                d = diagnose_rules(r, qs)
                out.append({"source": f, "id": r["id"], "arm": r["arm"], "run": r.get("run"),
                            "verdict": r["verdict"], "expected_category": d["cause_category"],
                            "reason_at_capture": d["cause"], "record": r})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    cases = collect()
    print(f"{len(cases)} graded failures")
    if ap.parse_args().write:
        path = HERE / "fixtures" / "cases.jsonl"
        with open(path, "w") as fh:
            for c in cases:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        print("wrote", path)
