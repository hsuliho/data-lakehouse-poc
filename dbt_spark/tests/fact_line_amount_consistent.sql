-- accuracy: the stored amount is quantity x the frozen price
select order_item_id from {{ ref('fact_orders') }} where line_amount <> quantity * unit_price
