"""Replay the frozen failures in fixtures/cases.jsonl against the current rules: a change to classify() that silently
relabels an old failure shows up here. Reads nothing but the fixture file, so it needs no services and no model quota.

usage: python -m agent.verify.regress [--verbose]
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from agent.verify.evidence import diagnose_rules, question_set

FIXTURES = Path(__file__).parent / "fixtures" / "cases.jsonl"


def replay() -> list[dict]:
    out, sets = [], {}
    for line in open(FIXTURES):
        c = json.loads(line)
        if c["source"] not in sets:                       # each results folder has its own frozen expectations
            sets[c["source"]] = question_set(c["source"])
        got = diagnose_rules(c["record"], sets[c["source"]])
        out.append({"id": c["id"], "arm": c["arm"], "verdict": c["verdict"],
                    "expected": c["expected_category"], "got": got["cause_category"],
                    "ok": got["cause_category"] == c["expected_category"], "reason": got["cause"]})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    verbose = ap.parse_args().verbose
    rows = replay()
    bad = [r for r in rows if not r["ok"]]
    for r in rows:
        if verbose or not r["ok"]:
            flag = "OK " if r["ok"] else "DRIFT"
            print(f"{flag} {r['id']:14} {r['arm']:8} {r['verdict']:12} expected {r['expected']:14} got {r['got']:14} {r['reason'][:70]}")
    print(f"\n{len(rows) - len(bad)}/{len(rows)} stable", dict(Counter(r["expected"] for r in rows)))
    raise SystemExit(1 if bad else 0)
