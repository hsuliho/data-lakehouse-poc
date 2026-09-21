select order_item_id, order_id, product_id, quantity, unit_price, updated_at
from {{ source('ods', 'order_items') }}
