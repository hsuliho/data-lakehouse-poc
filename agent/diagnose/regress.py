"""Regression of the diagnosis agent against RECORDED evidence (fixtures written by agent.diagnose.faults). Nothing live is touched:
the reads the diagnosis makes are answered from the fixture, so it is safe to run any time, after a prompt or model change.
  python -m agent.diagnose.regress [--arms rules,diagnose,diagnose_plain]      `rules` needs no model and no quota; the model arms use a little
A read the fixture does not hold answers "not recorded in this fixture": the model arms may ask for more than was recorded (their SQL, for
instance), which the fixtures cannot serve, so a model arm can score lower here than against a live fault."""
import argparse
import json
from pathlib import Path

from agent.diagnose import evidence as E
from agent.diagnose.__main__ import diagnose

FIXTURES = Path(__file__).parent / "fixtures"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--arms", default="rules")
    a, bad, total = ap.parse_args(), 0, 0
    for path in sorted(FIXTURES.glob("*.json")):
        meta = json.loads(path.read_text())["meta"]
        E.use_fixture(path)
        try:
            for arm in a.arms.split(","):
                d = diagnose(meta["dt"], arm)
                ok = d.get("cause_category") == meta["expected"]
                total += 1; bad += not ok
                print(f"{meta['fault']:18} {arm:15} expected {meta['expected']:24} got {str(d.get('cause_category')):24} {'OK' if ok else 'WRONG'} {d.get('error') or ''}"[:190])
        finally:
            E.use_fixture(None)
    print(f"{total - bad}/{total} correct")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
