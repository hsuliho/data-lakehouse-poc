{#- DWD, grain = one order item. unit_price is the price charged at order time (frozen).
    updated_at = newest change of the item OR its order, so an old order changing status is picked up.
    The extra ON predicate is valid only because ordered_at never changes for an item; it lets Trino
    prune target partitions by dynamic filtering instead of scanning every file. -#}
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='order_item_id',
    incremental_predicates=["DBT_INTERNAL_DEST.ordered_at = DBT_INTERNAL_SOURCE.ordered_at"],
    properties={'format': "'PARQUET'", 'partitioning': "ARRAY['day(ordered_at)']"}
) }}
select i.order_item_id, i.order_id, o.customer_id, i.product_id, o.ordered_at, o.order_status,
       i.quantity, i.unit_price, i.quantity * i.unit_price as line_amount,
       greatest(i.updated_at, o.updated_at) as updated_at
from {{ ref('stg_order_items') }} i
join {{ ref('stg_orders') }} o on o.order_id = i.order_id
{% if is_incremental() %}where greatest(i.updated_at, o.updated_at) > (select max(updated_at) from {{ this }}){% endif %}
