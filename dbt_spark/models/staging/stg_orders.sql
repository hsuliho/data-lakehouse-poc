select order_id, customer_id, lower(trim(order_status)) as order_status, ordered_at, updated_at, _loaded_at
from {{ source('ods', 'orders') }}
