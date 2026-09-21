-- consistency: at most one current version per key (none = the key was deleted), a version that is not current is closed, and versions never overlap (a gap is legal only after a delete)
{% for dim, key in [('dim_product', 'product_id'), ('dim_customer', 'customer_id')] %}
select '{{ dim }}' as dim, {{ key }} as key from {{ source('scd2', dim) }} where is_current group by {{ key }} having count(*) > 1
union all
select '{{ dim }}', {{ key }} from (
  select {{ key }}, is_current, valid_to, lead(valid_from) over (partition by {{ key }} order by valid_from) as nxt from {{ source('scd2', dim) }})
where (is_current and valid_to is not null) or (not is_current and (valid_to is null or valid_to > nxt))
{{ 'union all' if not loop.last }}
{% endfor %}
