-- uniqueness: one DWS row per day x category
select revenue_date, category, count(*) as n from {{ ref('dws_daily_revenue') }} group by 1, 2 having count(*) > 1
