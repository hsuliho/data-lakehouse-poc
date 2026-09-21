-- validity / timeliness guard: a load timestamp in the future means a clock or time-zone bug, and it makes the
-- freshness check pass vacuously (negative age). Naive timestamps are read as UTC.
{% for t in ['customers', 'products', 'orders', 'order_items'] %}
select '{{ t }}' as source_table, max(_loaded_at) as max_loaded_at
from {{ source('ods', t) }}
group by 1 having max(_loaded_at) > current_timestamp()
{{ 'union all' if not loop.last }}
{% endfor %}
