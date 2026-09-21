"""Offline check of the sensor's decision: python -m orchestration.test_plan"""
from datetime import datetime, timezone
from orchestration.definitions import DAILY, plan

U = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
keys = DAILY.get_partition_keys()                                   # 2026-01-29, 2026-01-30, 2026-01-31
d1, d2, d3 = keys
have = lambda s: (lambda dt: dt in s)
assert plan([d1, d2], set(), have(set(keys)), U("2026-01-31 00:00")) == ([d1, d2], None), "catch-up: the missing days, oldest first"
assert plan([d1, d2], {d1}, have(set(keys)), U("2026-01-31 00:00")) == ([d2], None), "only the day that is not done yet"
assert plan([d1, d2], set(keys), have(set(keys)), U("2026-01-31 00:00")) == ([], None), "nothing missing"
todo, msg = plan([d1, d2], set(), have({d1}), U("2026-01-31 01:00"))
assert todo == [d1] and msg is None, "d2 closed one hour ago and is not delivered yet: wait, before the deadline"
todo, msg = plan([d1, d2], set(), have({d1}), U("2026-01-31 03:00:01"))
assert todo == [d1] and f"dt={d2} is overdue" in msg, "after UTC 03:00 the missing day is overdue"
todo, msg = plan([d1, d2, d3], set(), have({d1, d3}), U("2026-02-01 00:00"))
assert todo == [d1] and msg, "a missing day blocks the days after it even though they were delivered"
print("PASS orchestration plan: catch-up, nothing missing, wait, overdue alert, blocking")

from dagster import AssetCheckResult, AssetKey
from orchestration.definitions import tolerate
mk = lambda name, n, passed=False: AssetCheckResult(passed=passed, asset_key=AssetKey("stg_orders"), check_name=name, metadata={"dagster_dbt/failed_row_count": n})
KNOWN = "accepted_values_stg_orders_order_status__created__paid__shipped__completed__cancelled"
assert tolerate(mk(KNOWN, 1)).passed, "the planted dirt (1 row) is tolerated"
assert not tolerate(mk(KNOWN, 2)).passed, "a NEW bad row in a known test (2 rows, expected 1) is not"
assert not tolerate(mk("dim_versions_chain", 1)).passed, "an unknown failing test is not"
assert tolerate(mk("not_null_x", 0, passed=True)).passed
print("PASS dbt checks: expected dirt is tolerated, a NEW bad row in a known test and an unknown failing test are not")

from orchestration.definitions import should_alert
hits = sum(should_alert(t, 5, 300) for t in range(1_000_000, 1_000_000 + 3000, 5))            # a check every 5 s for 50 minutes
assert hits == 10, hits
print("PASS overdue alert is rate-limited: 1 failed tick per 5 minutes at 5 s checks, not 60")

from orchestration.definitions import parse_metric
assert parse_metric("dt=2026-01-27 orders       raw records=  2\ndt=2026-01-27 customers    raw records=  0", r"^dt=\S+ (\w+)\s+raw records=\s*(\d+)") == {"orders": 2, "customers": 0}
print("PASS asset metadata parsing reads the loader's own output")
