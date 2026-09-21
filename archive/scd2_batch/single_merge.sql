-- One atomic MERGE: every staged row appears twice. The copy keyed on the real key closes the current version;
-- the copy with a NULL key never matches, so it is inserted as the new version.
MERGE INTO dwh_spark.{dim} t
USING (
  SELECT s.{key} AS merge_key, s.* FROM scd2_stage s
  UNION ALL
  SELECT CAST(NULL AS BIGINT) AS merge_key, s.* FROM scd2_stage s
) u
ON t.{key} = u.merge_key AND t.is_current
WHEN MATCHED THEN UPDATE SET t.valid_to = u.valid_from, t.is_current = false
WHEN NOT MATCHED AND u.merge_key IS NULL THEN INSERT ({sk}, {key}, {attr_names}, valid_from, valid_to, is_current, _loaded_at)
  VALUES (md5(concat_ws('|', cast(u.{key} AS string), cast(unix_micros(u.valid_from) AS string))), u.{key}, {attr_from_u},
          u.valid_from, CAST(NULL AS TIMESTAMP), true, current_timestamp())
