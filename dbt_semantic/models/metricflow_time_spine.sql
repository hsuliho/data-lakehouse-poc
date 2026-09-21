-- one row per calendar day; MetricFlow needs it for days with no data and for ratio metrics
select cast(d as date) as date_day
from unnest(sequence(date '2024-01-01', date '2028-12-31', interval '1' day)) as t(d)
