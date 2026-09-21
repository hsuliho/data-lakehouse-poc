{{ config(meta={'dagster': {'ref': {'name': 'ads_category_revenue_rank'}}}) }}
-- accuracy: totals tie out across layers (fact -> DWS -> ADS)
select f.total as fact_total, a.total as ads_total
from (select sum(line_amount) as total from {{ ref('fact_orders') }}) f
cross join (select sum(revenue) as total from {{ ref('ads_category_revenue_rank') }}) a
where f.total is distinct from a.total
