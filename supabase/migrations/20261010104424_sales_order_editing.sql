-- M06 · Atomic sales-order editing for planners (spec §5 Planner: create and edit draft sales orders).
-- SECURITY INVOKER: every statement runs under the caller's RLS policies (planner/admin only).

-- Create a Draft order, or replace the header and lines of an existing Draft order, in one transaction.
-- Lines get the product's approved BOM and routing; products without them are accepted but flagged
-- "needs data" by planning (spec acceptance test 8). Only Active finished goods can be ordered (spec §6.1).
create or replace function public.save_draft_sales_order(p jsonb)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_id     uuid;
  v_status text;
  v_line   jsonb;
  n        integer := 0;
begin
  if coalesce(trim(p ->> 'order_number'), '') = '' or coalesce(trim(p ->> 'customer_name'), '') = '' then
    raise exception 'Order number and customer are required' using errcode = 'check_violation';
  end if;

  select id, status into v_id, v_status from public.sales_orders where order_number = p ->> 'order_number' for update;

  if v_id is null then
    insert into public.sales_orders (order_number, customer_name, customer_reference, order_date,
                                     requested_delivery_date, priority, status, notes, created_by)
    values (p ->> 'order_number', trim(p ->> 'customer_name'), nullif(trim(p ->> 'customer_reference'), ''),
            (p ->> 'order_date')::date, (p ->> 'requested_delivery_date')::date, p ->> 'priority', 'Draft',
            nullif(trim(p ->> 'notes'), ''), (select auth.uid()))
    returning id into v_id;
  else
    if v_status <> 'Draft' then
      raise exception 'Only Draft orders can be edited (this order is %)', v_status using errcode = 'check_violation';
    end if;
    update public.sales_orders
       set customer_name = trim(p ->> 'customer_name'), customer_reference = nullif(trim(p ->> 'customer_reference'), ''),
           order_date = (p ->> 'order_date')::date, requested_delivery_date = (p ->> 'requested_delivery_date')::date,
           priority = p ->> 'priority', notes = nullif(trim(p ->> 'notes'), '')
     where id = v_id;
    if not found then
      raise exception 'Not allowed to edit sales orders' using errcode = 'insufficient_privilege';
    end if;
    delete from public.sales_order_items where sales_order_id = v_id;
  end if;

  for v_line in select * from jsonb_array_elements(coalesce(p -> 'lines', '[]'::jsonb)) loop
    n := n + 1;
    insert into public.sales_order_items (sales_order_id, line_number, product_id, product_configuration,
                                          bom_revision_id, routing_revision_id, quantity, unit_price,
                                          requested_delivery_date, status, notes)
    select v_id, n, pr.id, jsonb_build_object('tags', coalesce(v_line ->> 'tags', '')), bh.id, rh.id,
           (v_line ->> 'quantity')::numeric, nullif(v_line ->> 'unit_price', '')::numeric,
           (p ->> 'requested_delivery_date')::date, 'Draft', nullif(trim(v_line ->> 'notes'), '')
    from public.products pr
    left join public.bom_headers bh on bh.product_id = pr.id and bh.status = 'Approved'
    left join public.routing_headers rh on rh.product_id = pr.id and rh.status = 'Approved'
    where pr.product_code = v_line ->> 'product_code' and pr.item_type = 'finished_good' and pr.product_status = 'Active';
    if not found then
      raise exception 'Product % is not an active finished good', v_line ->> 'product_code' using errcode = 'check_violation';
    end if;
  end loop;

  if n = 0 then
    raise exception 'An order needs at least one line' using errcode = 'check_violation';
  end if;
  return p ->> 'order_number';
end;
$$;

-- Draft → Confirmed for the order and all its lines.
create or replace function public.confirm_sales_order(p_order_number text)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_id uuid;
begin
  update public.sales_orders set status = 'Confirmed'
   where order_number = p_order_number and status = 'Draft'
  returning id into v_id;
  if v_id is null then
    raise exception 'Order % is not a Draft you may confirm', p_order_number using errcode = 'check_violation';
  end if;
  update public.sales_order_items set status = 'Confirmed' where sales_order_id = v_id and status = 'Draft';
end;
$$;

revoke execute on function public.save_draft_sales_order(jsonb), public.confirm_sales_order(text) from public, anon;
grant execute on function public.save_draft_sales_order(jsonb), public.confirm_sales_order(text) to authenticated;
