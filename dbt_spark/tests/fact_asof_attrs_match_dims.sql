{{ config(meta={'dagster': {'ref': {'name': 'fact_orders'}}}) }}
-- consistency: the as-of attributes copied onto the fact equal the dimension version its surrogate key points at
select f.order_item_id
from {{ ref('fact_orders') }} f
join {{ source('scd2', 'dim_product') }} p on p.product_sk = f.product_sk
join {{ source('scd2', 'dim_customer') }} c on c.customer_sk = f.customer_sk
where f.product_category is distinct from p.category or f.customer_membership_tier is distinct from c.membership_tier
