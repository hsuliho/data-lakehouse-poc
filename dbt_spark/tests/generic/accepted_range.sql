{% test accepted_range(model, column_name, min_value=none, max_value=none) %}
{#- rows outside [min_value, max_value]; NULLs are not_null's job -#}
select * from {{ model }}
where {{ column_name }} is not null
  and ({{ 'false' if min_value is none else column_name ~ ' < ' ~ min_value }}
    or {{ 'false' if max_value is none else column_name ~ ' > ' ~ max_value }})
{% endtest %}
