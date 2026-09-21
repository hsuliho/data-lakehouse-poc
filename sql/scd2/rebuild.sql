-- Rebuild the versions of every key touched by delivery {dt} from that key's WHOLE history in raw (ADR 0015).
-- The result depends only on raw, never on the order deliveries were run in. Versions:
--   * a new version starts at the first record of a key, at a record whose tracked attributes differ from the previous
--     record, and right after a delete;  valid_to = the start of the next version or of a delete;
--   * the first version of a key starts at "beginning of time" (1970) so orders older than the first record still bind;
--   * duplicates (same event_id) are dropped; ties in changed_at are ordered by event_id.
-- Times are instants; the surrogate key hashes unix_micros so it cannot depend on a session zone.
CREATE OR REPLACE TEMP VIEW scd2_touched AS
SELECT DISTINCT {key} FROM {raw} WHERE dt = DATE '{dt}';
CREATE OR REPLACE TEMP VIEW scd2_rebuilt AS
WITH e AS (
  SELECT * FROM (SELECT r.*, row_number() OVER (PARTITION BY event_id ORDER BY changed_at) AS dup
                 FROM {raw} r WHERE {key} IN (SELECT {key} FROM scd2_touched)) WHERE dup = 1),
f AS (
  SELECT e.*, row_number() OVER w AS rn, lag(is_deleted) OVER w AS p_deleted, {attr_lags}
  FROM e WINDOW w AS (PARTITION BY {key} ORDER BY changed_at, event_id)),
c AS (
  SELECT * FROM f WHERE rn = 1 OR is_deleted OR coalesce(p_deleted, false) OR {changed_expr}),
v AS (
  SELECT c.*, lead(changed_at) OVER w2 AS next_at, row_number() OVER w2 AS vn
  FROM c WINDOW w2 AS (PARTITION BY {key} ORDER BY changed_at, event_id))
SELECT md5(concat_ws('|', cast({key} AS string),
           cast(unix_micros(CASE WHEN vn = 1 THEN CAST('1970-01-01 00:00:00+00:00' AS TIMESTAMP) ELSE changed_at END) AS string))) AS {sk},
       {key}, {attr_cols},
       CASE WHEN vn = 1 THEN CAST('1970-01-01 00:00:00+00:00' AS TIMESTAMP) ELSE changed_at END AS valid_from,
       next_at AS valid_to, next_at IS NULL AS is_current
FROM v WHERE NOT is_deleted;
-- versions of touched keys that the rebuild no longer produces (a late record moved a boundary)
DELETE FROM dwh_spark.{dim}
WHERE {key} IN (SELECT {key} FROM scd2_touched) AND {sk} NOT IN (SELECT {sk} FROM scd2_rebuilt);
-- unchanged versions are left alone (same _loaded_at), so rerunning a day changes nothing downstream
MERGE INTO dwh_spark.{dim} t USING scd2_rebuilt s ON t.{sk} = s.{sk}
WHEN MATCHED AND (NOT (t.valid_to <=> s.valid_to) OR t.is_current <> s.is_current OR {attr_diff}) THEN UPDATE SET
  t.valid_to = s.valid_to, t.is_current = s.is_current, {attr_set}, t._loaded_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT ({sk}, {key}, {attr_names}, valid_from, valid_to, is_current, _loaded_at)
  VALUES (s.{sk}, s.{key}, {attr_from_s}, s.valid_from, s.valid_to, s.is_current, current_timestamp())
