{#- Time rules for this project: every stored time is an instant (timestamptz); the database shows UTC; conversion to
    a local zone happens only in the client. These macros are the only places time is converted inside the pipeline. -#}

{% macro utc_date(col) -%}
{#- The UTC calendar date of an instant, independent of the Spark session zone: read the instant as wall-clock time in
    the session zone, then move that wall-clock from the session zone to UTC. A bare to_date(col) uses the session zone. -#}
to_date(convert_timezone(current_timezone(), 'UTC', cast({{ col }} as timestamp_ntz)))
{%- endmacro %}

{% macro far_future() -%}
cast('9999-12-31 00:00:00+00:00' as timestamp)
{%- endmacro %}

{% macro assert_utc_session() %}
{#- fail the run, before anything is written, if the session zone is not UTC -#}
{% if execute %}
  {% set r = run_query("select current_timezone()") %}
  {% if r.columns[0][0] not in ['Etc/UTC', 'UTC'] %}
    {{ exceptions.raise_compiler_error("Spark session time zone is '" ~ r.columns[0][0] ~ "', expected UTC. Refusing to run.") }}
  {% endif %}
{% endif %}
{% endmacro %}
