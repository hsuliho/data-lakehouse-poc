-- One row per key that needs a NEW version:
--   * brand-new key (no version at all)         -> first version, valid_from = "beginning of time"
--   * key with no current version (interrupted) -> heal: open the version the batch describes
--   * newer batch row whose tracked attributes differ from the current version
-- Rows not newer than the current version are skipped (out-of-order, see report).
-- Times are instants (TIMESTAMP with the session zone only for display); the surrogate key hashes unix_micros so it
-- cannot depend on the session zone.
-- Lazy on purpose (a TEMP VIEW): the two-step variant re-evaluates it after closing the old version, so "new key" must
-- mean "no version at all", NOT "no current version", or the new version would start at 1970 and overlap the closed one.
CREATE OR REPLACE TEMP VIEW scd2_stage AS
SELECT b.{key}, {attr_select}, b.updated_at AS source_updated_at,
       CASE WHEN a.{key} IS NULL THEN CAST('1970-01-01 00:00:00+00:00' AS TIMESTAMP) ELSE b.updated_at END AS valid_from
FROM (SELECT *, row_number() OVER (PARTITION BY {key} ORDER BY updated_at DESC) AS rn FROM {batch}) b
LEFT JOIN (SELECT DISTINCT {key} FROM dwh_spark.{dim}) a ON a.{key} = b.{key}
LEFT JOIN dwh_spark.{dim} d ON d.{key} = b.{key} AND d.is_current
WHERE b.rn = 1
  AND (a.{key} IS NULL OR d.{key} IS NULL OR (b.updated_at > d.valid_from AND ({changed})))
