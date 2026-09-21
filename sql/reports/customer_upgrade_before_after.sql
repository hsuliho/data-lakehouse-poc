-- Customer 1 was upgraded silver -> gold on 2026-01-22 09:00 UTC. Each order belongs to the tier valid at order time.
SELECT c.membership_tier, count(DISTINCT f.order_id) AS orders, sum(f.line_amount) AS revenue,
       min(f.ordered_at AT TIME ZONE 'Asia/Taipei') AS first_order_taipei, max(f.ordered_at AT TIME ZONE 'Asia/Taipei') AS last_order_taipei
FROM iceberg.dwh_spark.fact_orders f
JOIN iceberg.dwh_spark.dim_customer c ON c.customer_sk = f.customer_sk
WHERE f.customer_id = 1
GROUP BY 1
