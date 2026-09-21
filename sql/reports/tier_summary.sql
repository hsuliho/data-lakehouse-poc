-- Orders and average order value by the membership tier the customer had WHEN ordering (as-of dimension version).
SELECT c.membership_tier, count(DISTINCT f.order_id) AS orders, sum(f.line_amount) AS revenue,
       round(sum(f.line_amount) / count(DISTINCT f.order_id), 2) AS avg_order_value
FROM iceberg.dwh_spark.fact_orders f
JOIN iceberg.dwh_spark.dim_customer c ON c.customer_sk = f.customer_sk
GROUP BY 1
