-- M08 · Persist one MRP run and its alerts atomically (spec §6.18, §6.20, §7.4).
-- SECURITY INVOKER: planner/admin via RLS. Material alerts of earlier runs are resolved, so the open alerts always
-- describe the latest MRP run; schedule-owned alerts (capacity, delivery risk) are left untouched.
create or replace function public.record_mrp_run(p jsonb)
returns bigint
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_run    uuid;
  v_number bigint;
begin
  if not (select private.has_any_role(array['planner', 'admin'])) then
    raise exception 'Only planners can record MRP runs' using errcode = 'insufficient_privilege';
  end if;

  insert into public.planning_runs (run_type, planning_horizon_start, planning_horizon_end, input_snapshot,
                                    algorithm_version, solver_status, solve_seconds, started_at, completed_at, created_by)
  values ('mrp', (p ->> 'horizon_start')::date, (p ->> 'horizon_end')::date, coalesce(p -> 'input', '{}'::jsonb),
          p ->> 'algorithm_version', p ->> 'status', (p ->> 'seconds')::numeric, (p ->> 'started_at')::timestamptz,
          now(), (select auth.uid()))
  returning id, run_number into v_run, v_number;

  update public.planning_alerts set status = 'Resolved'
   where status in ('Open', 'Acknowledged')
     and alert_type in ('material_shortage', 'late_supplier_receipt', 'quality_hold', 'missing_bom', 'missing_routing');

  insert into public.planning_alerts (planning_run_id, sales_order_item_id, alert_type, severity, description,
                                      recommended_action, details)
  select v_run, soi.id, a ->> 'alert_type', a ->> 'severity', a ->> 'description', a ->> 'recommended_action',
         coalesce(a -> 'details', '{}'::jsonb)
  from jsonb_array_elements(coalesce(p -> 'alerts', '[]'::jsonb)) a
  left join public.sales_orders so on so.order_number = a ->> 'order_number'
  left join public.sales_order_items soi on soi.sales_order_id = so.id and soi.line_number = (a ->> 'line_number')::int;

  return v_number;
end;
$$;
revoke execute on function public.record_mrp_run(jsonb) from public, anon;
grant execute on function public.record_mrp_run(jsonb) to authenticated;
