-- M06 · Read API for the app and the BOM approval transition.

-- One consistent, RLS-filtered snapshot of plant data (spec §4: "every planning run must use a consistent
-- snapshot"). SECURITY INVOKER: the caller only receives rows their policies allow. A single statement means a
-- single MVCC snapshot, so the pieces always agree with each other.
create or replace function public.planning_snapshot()
returns json
language sql
stable
security invoker
set search_path = ''
as $$
  select json_build_object(
    'generated_at',          now(),
    'work_centers',          (select coalesce(json_agg(t order by t.work_center_code), '[]') from public.work_centers t),
    'machines',              (select coalesce(json_agg(t order by t.machine_code), '[]') from public.machines t),
    'machine_downtime',      (select coalesce(json_agg(t order by t.start_datetime), '[]') from public.machine_downtime t),
    'products',              (select coalesce(json_agg(t order by t.product_code), '[]') from public.products t),
    'bom_headers',           (select coalesce(json_agg(t), '[]') from public.bom_headers t),
    'bom_items',             (select coalesce(json_agg(t order by t.line_number), '[]') from public.bom_items t),
    'routing_headers',       (select coalesce(json_agg(t), '[]') from public.routing_headers t),
    'routing_operations',    (select coalesce(json_agg(t order by t.sequence), '[]') from public.routing_operations t),
    'routing_dependencies',  (select coalesce(json_agg(t), '[]') from public.routing_operation_dependencies t),
    'warehouses',            (select coalesce(json_agg(t), '[]') from public.warehouses t),
    'inventory_balances',    (select coalesce(json_agg(t), '[]') from public.inventory_balances t),
    'purchase_orders',       (select coalesce(json_agg(t order by t.po_number), '[]') from public.purchase_orders t),
    'purchase_order_items',  (select coalesce(json_agg(t order by t.line_number), '[]') from public.purchase_order_items t),
    'sales_orders',          (select coalesce(json_agg(t order by t.order_number), '[]') from public.sales_orders t),
    'sales_order_items',     (select coalesce(json_agg(t order by t.line_number), '[]') from public.sales_order_items t),
    'planning_runs',         (select coalesce(json_agg(t order by t.run_number), '[]') from public.planning_runs t),
    'schedule_versions',     (select coalesce(json_agg(t order by t.version_number), '[]') from public.schedule_versions t),
    'production_orders',     (select coalesce(json_agg(t), '[]') from public.production_orders t),
    'production_operations', (select coalesce(json_agg(t order by t.planned_start), '[]') from public.production_operations t),
    'planning_alerts',       (select coalesce(json_agg(t order by t.created_at), '[]') from public.planning_alerts t),
    'approval_requests',     (select coalesce(json_agg(t order by t.requested_at), '[]') from public.approval_requests t),
    'profiles',              (select coalesce(json_agg(json_build_object('id', t.id, 'full_name', t.full_name, 'job_title', t.job_title)), '[]') from public.profiles t)
  );
$$;
revoke execute on function public.planning_snapshot() from public, anon;
grant execute on function public.planning_snapshot() to authenticated;
comment on function public.planning_snapshot() is 'All plant data the caller may see, from one MVCC snapshot.';

-- Approve an Under Review BOM revision and supersede the currently approved one, atomically.
-- SECURITY INVOKER: RLS lets only approvers change approved/superseded revisions (M05 policies).
create or replace function public.approve_bom_revision(p_bom_header_id uuid)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
  target public.bom_headers;
begin
  select * into target from public.bom_headers where id = p_bom_header_id for update;
  if not found then
    raise exception 'BOM revision not found or not visible' using errcode = 'no_data_found';
  end if;
  if target.status <> 'Under Review' then
    raise exception 'Only revisions Under Review can be approved (this one is %)', target.status
      using errcode = 'check_violation';
  end if;

  update public.bom_headers
     set status = 'Superseded', effective_to = greatest(target.effective_from, effective_from + 1)
   where product_id = target.product_id and status = 'Approved';

  update public.bom_headers
     set status = 'Approved', approved_by = (select auth.uid()), approved_at = now()
   where id = p_bom_header_id;

  if not found then
    raise exception 'Not allowed to approve BOM revisions' using errcode = 'insufficient_privilege';
  end if;
end;
$$;
revoke execute on function public.approve_bom_revision(uuid) from public, anon;
grant execute on function public.approve_bom_revision(uuid) to authenticated;
comment on function public.approve_bom_revision(uuid) is 'Approver-only (via RLS): Under Review → Approved, previous Approved → Superseded.';
