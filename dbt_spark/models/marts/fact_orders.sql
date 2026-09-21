{#- DWD, grain = one order item. product_category / customer_membership_tier are the values valid at ordered_at (copied from the bound dimension versions). unit_price is the price charged at order time (frozen).
    customer_sk / product_sk = the dimension VERSION valid at ordered_at (as-of join).
    A row is rebuilt when (1) its order/item was rewritten in ODS since the last build (ods._loaded_at, NOT updated_at: a late
    or out-of-order change carries an old updated_at), or (2) a dimension version loaded since the last build
    now applies to it (late-arriving dimension change). _built_at records when the row was last (re)built.
    The extra ON predicate is valid only because ordered_at never changes for an item; it lets the MERGE prune files. -#}
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='order_item_id',
    incremental_predicates=["DBT_INTERNAL_DEST.ordered_at = DBT_INTERNAL_SOURCE.ordered_at"],
    partition_by=['days(ordered_at)']
) }}
select i.order_item_id, i.order_id, o.customer_id, c.customer_sk, i.product_id, p.product_sk,
       p.category as product_category, c.membership_tier as customer_membership_tier,   -- as-of attributes for the semantic layer
       o.ordered_at, o.order_status, i.quantity, i.unit_price, i.quantity * i.unit_price as line_amount,
       greatest(i.updated_at, o.updated_at) as updated_at, current_timestamp() as _built_at
from {{ ref('stg_order_items') }} i
join {{ ref('stg_orders') }} o on o.order_id = i.order_id
left join {{ source('scd2', 'dim_product') }} p
  on p.product_id = i.product_id and o.ordered_at >= p.valid_from and o.ordered_at < coalesce(p.valid_to, {{ far_future() }})
left join {{ source('scd2', 'dim_customer') }} c
  on c.customer_id = o.customer_id and o.ordered_at >= c.valid_from and o.ordered_at < coalesce(c.valid_to, {{ far_future() }})
{#- coalesce: a first build on an empty delivery day leaves the table empty, and max() of nothing is NULL, which would make every later run select nothing -#}
{% if is_incremental() %}
where greatest(i._loaded_at, o._loaded_at) > (select coalesce(max(_built_at), timestamp'1970-01-01 00:00:00+00:00') from {{ this }})
   or exists (select 1 from {{ source('scd2', 'dim_product') }} np
              where np.product_id = i.product_id and np._loaded_at > (select coalesce(max(_built_at), timestamp'1970-01-01 00:00:00+00:00') from {{ this }}) and o.ordered_at >= np.valid_from)
   or exists (select 1 from {{ source('scd2', 'dim_customer') }} nc
              where nc.customer_id = o.customer_id and nc._loaded_at > (select coalesce(max(_built_at), timestamp'1970-01-01 00:00:00+00:00') from {{ this }}) and o.ordered_at >= nc.valid_from)
{% endif %}
