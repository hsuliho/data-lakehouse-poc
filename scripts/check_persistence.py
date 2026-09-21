"""usage: check_persistence.py snapshot | compare
snapshot: record the fingerprint of every stateful thing (rows, snapshots, MinIO objects, Superset objects) in .fingerprint.json.
compare:  after a container recreate or a Colima restart, retry until the services answer, then diff against the snapshot."""
import json, subprocess, sys, time
from pathlib import Path

FP = Path(__file__).resolve().parent.parent / ".fingerprint.json"
ROOT = FP.parent


def fingerprint():
    r = subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/fingerprint.py")], capture_output=True, text=True, cwd=ROOT)
    return json.loads(r.stdout) if r.returncode == 0 else None, r.stderr


if sys.argv[1] == "snapshot":
    fp, err = fingerprint()
    assert fp, err[-300:]
    FP.write_text(json.dumps(fp, indent=1, sort_keys=True)); print(f"saved {len(fp)} facts to {FP.name}")
else:
    before = json.loads(FP.read_text())
    for i in range(40):
        after, err = fingerprint()
        if after: break
        time.sleep(6)
    else:
        sys.exit(f"services not ready: {err[-300:]}")
    diff = {k: (before[k], after.get(k)) for k in before if before[k] != after.get(k)}
    print(f"compared {len(before)} facts, differences: {diff or 'none'}")
    sys.exit(1 if diff else 0)
