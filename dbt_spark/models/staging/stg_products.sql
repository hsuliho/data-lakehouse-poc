select product_id, trim(name) as product_name, lower(trim(category)) as category, price, updated_at, _loaded_at
from {{ source('ods', 'products') }}
