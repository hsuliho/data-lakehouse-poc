select customer_id, trim(name) as customer_name, trim(city) as city,
       lower(trim(membership_tier)) as membership_tier, updated_at, _loaded_at
from {{ source('ods', 'customers') }}
