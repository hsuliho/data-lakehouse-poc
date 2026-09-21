{#- The contract between the warehouse and the semantic layer: one row per order item, only the columns a metric or a
    dimension may use. No customer name or city (PII). The Taipei day is computed here, at query time; nothing stored is
    in local time (ADR 0010). -#}
select order_item_id, order_id, customer_id, ordered_at,
       cast(date(ordered_at at time zone 'Asia/Taipei') as date) as ordered_date_taipei,
       cast(date(ordered_at at time zone 'UTC') as date) as ordered_date_utc,   -- a DATE column like the Taipei day: filtering metric_time returns empty for a single day
       order_status,
       order_status in ('paid', 'shipped', 'completed') as is_revenue_order,   -- the ONE definition of which orders count (CONTEXT.md: Revenue)
       quantity, unit_price, line_amount, product_category, customer_membership_tier
from {{ source('published', 'fact_orders') }}
