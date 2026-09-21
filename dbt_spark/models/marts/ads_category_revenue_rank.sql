select category, sum(revenue) as revenue, sum(order_count) as order_count,
       rank() over (order by sum(revenue) desc) as revenue_rank
from {{ ref('dws_daily_revenue') }}
group by category
