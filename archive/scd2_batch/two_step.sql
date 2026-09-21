-- Step 1: close the current version of every changed key
MERGE INTO dwh_spark.{dim} t USING scd2_stage s
ON t.{key} = s.{key} AND t.is_current
WHEN MATCHED THEN UPDATE SET t.valid_to = s.valid_from, t.is_current = false;
-- Step 2: open the new version (brand-new keys included). Two commits: a failure in between leaves keys with no
-- current version, and re-running heals it (scd2_stage opens the version the batch describes).
INSERT INTO dwh_spark.{dim}
SELECT md5(concat_ws('|', cast(s.{key} AS string), cast(unix_micros(s.valid_from) AS string))), s.{key}, {attr_from_s},
       s.valid_from, CAST(NULL AS TIMESTAMP), true, current_timestamp()
FROM scd2_stage s
