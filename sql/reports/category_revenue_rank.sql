-- All-time revenue per category (the ADS table); no day bucketing, so no time-zone question.
SELECT revenue_rank, category, revenue, order_count FROM iceberg.dwh_spark.ads_category_revenue_rank
