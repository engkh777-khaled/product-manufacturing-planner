-- M10 · Fix: receipt-change flags follow the BOM upwards. A purchased part is often consumed through a
-- sub-assembly (ACT-DA-400 → AMK-TBV-10 → V-600-TBV-10), so the impact is on the finished good's operation that
-- consumes the sub-assembly, needed earlier by the sub-assembly's standard lead time (same rule as MRP).
create or replace function private.flag_receipt_change_impact()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_base    public.schedule_versions;
  v_po      text;
  v_item    text;
  v_new_at  timestamptz;
  r         record;
begin
  if private.audit_maintenance() then
    return null;
  end if;
  if new.expected_delivery_date is not distinct from old.expected_delivery_date
     or (new.expected_delivery_date is not null and old.expected_delivery_date is not null
         and new.expected_delivery_date <= old.expected_delivery_date) then
    return null;  -- unchanged or earlier: no new risk
  end if;
  select * into v_base from public.schedule_versions where is_baseline;
  if v_base.id is null then
    return null;
  end if;
  select po_number into v_po from public.purchase_orders where id = new.purchase_order_id;
  select product_code into v_item from public.products where id = new.product_id;
  v_new_at := new.expected_delivery_date::timestamp at time zone 'America/Chicago';

  for r in
    with recursive up (product_id, op_ref, lead_days, path, depth) as (
      select bh.product_id, bi.operation_reference, 0::numeric, array[bh.product_id], 1
        from public.bom_items bi
        join public.bom_headers bh on bh.id = bi.bom_header_id and bh.status = 'Approved'
       where bi.component_product_id = new.product_id
      union all
      select bh.product_id, bi.operation_reference, up.lead_days + coalesce(p.standard_lead_time_days, 0),
             up.path || bh.product_id, up.depth + 1
        from up
        join public.products p on p.id = up.product_id
        join public.bom_items bi on bi.component_product_id = up.product_id
        join public.bom_headers bh on bh.id = bi.bom_header_id and bh.status = 'Approved'
       where up.depth < 6 and not bh.product_id = any (up.path)
    )
    select distinct on (po.id) po.id as production_order_id, po.sales_order_item_id, po.production_order_number,
           so.order_number, op.operation_number, op.planned_start, up.lead_days,
           (select product_code from public.products where id = up.path[1]) as via
      from up
      join public.production_orders po on po.product_id = up.product_id and po.schedule_version_id = v_base.id
      join public.production_operations op on op.production_order_id = po.id
           and op.status not in ('Completed', 'Cancelled') and op.planned_start is not null
           and (up.op_ref is null or op.operation_number = up.op_ref)
      left join public.sales_order_items soi on soi.id = po.sales_order_item_id
      left join public.sales_orders so on so.id = soi.sales_order_id
     where new.expected_delivery_date is null
        or op.planned_start - make_interval(days => ceil(up.lead_days)::int) < v_new_at
     order by po.id, op.planned_start
  loop
    insert into public.planning_alerts (planning_run_id, sales_order_item_id, production_order_id, alert_type, severity,
                                        description, recommended_action, details)
    values (v_base.planning_run_id, r.sales_order_item_id, r.production_order_id, 'late_supplier_receipt', 'critical',
            format('%s/%s (%s%s) now expected %s — approved baseline v%s starts %s %s of %s on %s',
                   v_po, new.line_number, v_item,
                   case when r.lead_days > 0 then ' via ' || r.via || ', ' || r.lead_days || ' d build' else '' end,
                   coalesce(to_char(new.expected_delivery_date, 'Mon DD'), 'without a date'),
                   v_base.version_number, r.production_order_number, r.operation_number, coalesce(r.order_number, '—'),
                   to_char(r.planned_start at time zone 'America/Chicago', 'Mon DD HH24:MI')),
            'The approved baseline stays in force. Re-run the solver or build a scenario to see the impact, then submit the new version for approval.',
            jsonb_build_object('source', 'receipt_change', 'po_number', v_po, 'line_number', new.line_number,
                               'item', v_item, 'old_date', old.expected_delivery_date,
                               'new_date', new.expected_delivery_date, 'baseline_version', v_base.version_number,
                               'operation', r.operation_number, 'planned_start', r.planned_start,
                               'order_number', r.order_number, 'lead_days', r.lead_days));
  end loop;
  return null;
end;
$$;
revoke execute on function private.flag_receipt_change_impact() from public, anon;
