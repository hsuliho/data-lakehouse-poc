{{ config(meta={'dagster': {'ref': {'name': 'stg_orders'}}}) }}
-- consistency: every order has at least one item
select o.order_id from {{ ref('stg_orders') }} o left join {{ ref('stg_order_items') }} i on i.order_id = o.order_id where i.order_id is null
