{#- DWS, grain = day x category. The category is the one valid at order time (fact.product_sk -> dim_product).
    Every day that contains a fact row rebuilt since the last run is recomputed and its partition replaced
    (INSERT OVERWRITE, dynamic mode: one atomic commit). Trigger = fact._built_at, so late-arriving dimension
    changes (which rebuild facts without changing their source updated_at) are picked up too. -#}
{{ config(
    materialized='incremental',
    incremental_strategy='insert_overwrite',
    partition_by=['revenue_date']
) }}
with days as (
  select distinct {{ utc_date('ordered_at') }} as revenue_date
  from {{ ref('fact_orders') }}
  {% if is_incremental() %}where _built_at > (select coalesce(max(source_built_at), timestamp'1970-01-01 00:00:00+00:00') from {{ this }}){% endif %}
)
select p.category, count(distinct f.order_id) as order_count, sum(f.quantity) as item_count,
       sum(f.line_amount) as revenue, max(f._built_at) as source_built_at,
       {{ utc_date('f.ordered_at') }} as revenue_date
from {{ ref('fact_orders') }} f
join {{ source('scd2', 'dim_product') }} p on p.product_sk = f.product_sk
where {{ utc_date('f.ordered_at') }} in (select revenue_date from days)
group by {{ utc_date('f.ordered_at') }}, p.category
