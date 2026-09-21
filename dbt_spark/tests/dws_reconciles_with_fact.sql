{{ config(meta={'dagster': {'ref': {'name': 'dws_daily_revenue'}}}) }}
-- accuracy: the DWS equals a fresh recomputation from fact + dimension (catches a stale incremental)
with expected as (
  select {{ utc_date('f.ordered_at') }} as revenue_date, p.category, sum(f.line_amount) as revenue,
         sum(f.quantity) as item_count, count(distinct f.order_id) as order_count
  from {{ ref('fact_orders') }} f join {{ source('scd2', 'dim_product') }} p on p.product_sk = f.product_sk
  group by 1, 2)
select coalesce(e.revenue_date, w.revenue_date) as revenue_date, coalesce(e.category, w.category) as category
from expected e full join {{ ref('dws_daily_revenue') }} w on e.revenue_date = w.revenue_date and e.category = w.category
where e.revenue is distinct from w.revenue or e.item_count is distinct from w.item_count or e.order_count is distinct from w.order_count
