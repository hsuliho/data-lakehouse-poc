CREATE NAMESPACE IF NOT EXISTS dwh_spark;
CREATE TABLE IF NOT EXISTS dwh_spark.{dim} (
  {sk} STRING,
  {key} BIGINT,
  {attr_ddl},
  valid_from TIMESTAMP,
  valid_to TIMESTAMP,
  is_current BOOLEAN,
  _loaded_at TIMESTAMP
) USING iceberg
