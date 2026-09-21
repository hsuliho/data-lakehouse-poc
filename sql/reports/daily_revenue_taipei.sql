-- Revenue per Taipei day and category. The zone conversion happens here, at the edge; nothing stored is in local time.
SELECT date(f.ordered_at AT TIME ZONE 'Asia/Taipei') AS taipei_day, p.category,
       sum(f.line_amount) AS revenue, count(DISTINCT f.order_id) AS orders
FROM iceberg.dwh_spark.fact_orders f
JOIN iceberg.dwh_spark.dim_product p ON p.product_sk = f.product_sk
GROUP BY 1, 2
