{#- DWS, grain = day x category. Recomputes every day touched by a changed fact row (delete that day, insert fresh). -#}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='revenue_date',
    properties={'format': "'PARQUET'", 'partitioning': "ARRAY['day(revenue_date)']"}
) }}
with days as (
  select distinct date(ordered_at) as revenue_date
  from {{ ref('fact_orders') }}
  {% if is_incremental() %}where updated_at > (select max(source_updated_at) from {{ this }}){% endif %}
)
select date(f.ordered_at) as revenue_date, p.category,
       count(distinct f.order_id) as order_count, sum(f.quantity) as item_count,
       sum(f.line_amount) as revenue, max(f.updated_at) as source_updated_at
from {{ ref('fact_orders') }} f
join {{ ref('stg_products') }} p on p.product_id = f.product_id   -- current product; SCD2 as-of join comes in stage 5
where date(f.ordered_at) in (select revenue_date from days)
group by 1, 2
