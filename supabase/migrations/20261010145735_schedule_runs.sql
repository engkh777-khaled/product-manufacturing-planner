-- M09 · Finite-capacity schedule runs (spec §6.18–6.19, §8.7, §8.8).
-- 1. planning_snapshot() also returns shift calendars and calendar exceptions (scheduler inputs).
-- 2. private.schedule_hard_violations(): a database re-check of stored operations (machine overlap, precedence,
--    confirmed downtime, machine qualification). It runs when a run is recorded and on every transition to
--    Proposed/Approved, so no client can get an invalid schedule approved, whatever it claims.
-- 3. validation_passed can only be set to true inside record_schedule_run(); editing planned times or machines of
--    a Draft/Proposed version afterwards clears it.
-- 4. record_schedule_run(p jsonb): stores the run, a Draft version and its production orders/operations atomically.

-- ------------------------------------------------------------------ 1. snapshot
create or replace function public.planning_snapshot()
returns json
language sql
stable
set search_path = ''
as $$
  select json_build_object(
    'generated_at',          now(),
    'work_centers',          (select coalesce(json_agg(t order by t.work_center_code), '[]') from public.work_centers t),
    'machines',              (select coalesce(json_agg(t order by t.machine_code), '[]') from public.machines t),
    'shift_calendars',       (select coalesce(json_agg(t order by t.start_time), '[]') from public.shift_calendars t),
    'calendar_exceptions',   (select coalesce(json_agg(t order by t.starts_at), '[]') from public.calendar_exceptions t),
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

-- ------------------------------------------------------------------ 2. database re-check
-- SECURITY DEFINER so the check sees every operation and downtime row regardless of the caller's RLS view.
create or replace function private.schedule_hard_violations(p_version uuid)
returns setof text
language sql
stable
security definer
set search_path = ''
as $$
  with ops as (
    select po.production_order_number as pon, op.*
    from public.production_operations op
    join public.production_orders po on po.id = op.production_order_id
    where po.schedule_version_id = p_version and op.status not in ('Cancelled', 'Completed')
      and op.planned_start is not null and op.planned_finish is not null
  )
  select 'machine overlap: ' || a.pon || ' ' || a.operation_number || ' and ' || b.pon || ' ' || b.operation_number
         || ' on ' || m.machine_code
  from ops a
  join ops b on a.assigned_machine_id = b.assigned_machine_id and a.id < b.id
            and a.planned_start < b.planned_finish and b.planned_start < a.planned_finish
  join public.machines m on m.id = a.assigned_machine_id
  union all
  select 'precedence: ' || a.pon || ' ' || a.operation_number || ' starts before ' || p.operation_number || ' finishes'
  from ops a
  join public.routing_operation_dependencies d on d.operation_id = a.routing_operation_id
  join public.production_operations p on p.production_order_id = a.production_order_id
                                     and p.routing_operation_id = d.predecessor_id and p.status <> 'Cancelled'
  where coalesce(p.actual_finish, p.planned_finish) > a.planned_start
  union all
  select 'downtime: ' || a.pon || ' ' || a.operation_number || ' on ' || m.machine_code || ' overlaps ' || dt.reason
  from ops a
  join public.machine_downtime dt on dt.machine_id = a.assigned_machine_id and dt.status = 'Confirmed'
                                 and dt.start_datetime < a.planned_finish and a.planned_start < dt.end_datetime
  join public.machines m on m.id = a.assigned_machine_id
  union all
  select 'assignment: ' || a.pon || ' ' || a.operation_number || coalesce(' on ' || m.machine_code, ' has no machine')
  from ops a
  join public.routing_operations ro on ro.id = a.routing_operation_id
  left join public.machines m on m.id = a.assigned_machine_id
  where m.id is null or m.work_center_id <> ro.work_center_id or not (m.capability_tags @> ro.required_capabilities)
     or not m.is_active
  union all
  select 'times: ' || a.pon || ' ' || a.operation_number || ' finishes before it starts'
  from ops a where a.planned_finish <= a.planned_start;
$$;
revoke execute on function private.schedule_hard_violations(uuid) from public, anon;
grant execute on function private.schedule_hard_violations(uuid) to authenticated;

-- ------------------------------------------------------------------ 3. guard: validation flag and re-check
create or replace function private.guard_approval_transitions()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_problem text;
begin
  if private.audit_maintenance() then
    return new;
  end if;

  -- The database re-check runs on every move to Proposed/Approved, also inside decide_plan_approval().
  if tg_table_name = 'schedule_versions' and tg_op = 'UPDATE'
     and new.status in ('Proposed', 'Approved') and new.status is distinct from old.status then
    select x into v_problem from private.schedule_hard_violations(new.id) x limit 1;
    if v_problem is not null then
      raise exception 'Version % fails the database schedule re-check (%)', new.version_number, v_problem
        using errcode = 'check_violation';
    end if;
  end if;

  if coalesce(current_setting('app.approval_workflow', true), '') = 'on' then
    return new;
  end if;

  if tg_table_name = 'approval_requests' then
    if tg_op = 'INSERT' then
      if new.status in ('Approved', 'Rejected') then
        raise exception 'Plan decisions must go through decide_plan_approval()' using errcode = 'insufficient_privilege';
      end if;
    elsif new.status is distinct from old.status and new.status in ('Approved', 'Rejected') then
      raise exception 'Plan decisions must go through decide_plan_approval()' using errcode = 'insufficient_privilege';
    end if;

  elsif tg_table_name = 'schedule_versions' then
    if tg_op = 'INSERT' then
      if new.is_baseline or new.status in ('Approved', 'Superseded', 'Rejected') then
        raise exception 'Approving, rejecting or superseding a schedule must go through decide_plan_approval()'
          using errcode = 'insufficient_privilege';
      end if;
      if coalesce(new.validation_passed, false) and coalesce(current_setting('app.schedule_record', true), '') <> 'on' then
        raise exception 'Only record_schedule_run() can mark a schedule as validated' using errcode = 'insufficient_privilege';
      end if;
    else
      if new.is_baseline is distinct from old.is_baseline
         or (new.status is distinct from old.status and new.status in ('Approved', 'Superseded', 'Rejected')) then
        raise exception 'Approving, rejecting or superseding a schedule must go through decide_plan_approval()'
          using errcode = 'insufficient_privilege';
      end if;
      if coalesce(new.validation_passed, false) and not coalesce(old.validation_passed, false)
         and coalesce(current_setting('app.schedule_record', true), '') <> 'on' then
        raise exception 'Only record_schedule_run() can mark a schedule as validated' using errcode = 'insufficient_privilege';
      end if;
    end if;
  end if;

  return new;
end;
$$;
revoke execute on function private.guard_approval_transitions() from public, anon;

-- Editing planned times or machines of a Draft/Proposed version invalidates its validation.
-- SECURITY DEFINER: supervisors may edit operations but cannot update schedule_versions under RLS; the function
-- only ever clears the flag.
create or replace function private.invalidate_edited_schedule()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if private.audit_maintenance() or coalesce(current_setting('app.schedule_record', true), '') = 'on' then
    return null;
  end if;
  update public.schedule_versions v
     set validation_passed = false,
         metrics = v.metrics || jsonb_build_object('validation_stale', 'operations edited after validation')
   where v.status in ('Draft', 'Proposed') and coalesce(v.validation_passed, false)
     and v.id = (select po.schedule_version_id from public.production_orders po
                  where po.id = coalesce(new.production_order_id, old.production_order_id));
  return null;
end;
$$;
revoke execute on function private.invalidate_edited_schedule() from public, anon;

create trigger production_operations_invalidate_validation
  after insert or delete or update of planned_start, planned_finish, assigned_machine_id, status
  on public.production_operations
  for each row execute function private.invalidate_edited_schedule();

-- ------------------------------------------------------------------ 4. record a run
create or replace function public.record_schedule_run(p jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_run      uuid;
  v_run_no   bigint;
  v_base     public.schedule_versions;
  v_major    text;
  v_minor    int;
  v_version  uuid;
  v_number   text;
  v_passed   boolean;
  v_po       uuid;
  v_problems text[];
  o          jsonb;
  op         jsonb;
begin
  if not (select private.has_any_role(array['planner', 'admin'])) then
    raise exception 'Only planners can record schedule runs' using errcode = 'insufficient_privilege';
  end if;
  perform set_config('app.schedule_record', 'on', true);

  insert into public.planning_runs (run_type, planning_horizon_start, planning_horizon_end, input_snapshot,
                                    algorithm_version, solver_status, objective_value, solve_seconds,
                                    started_at, completed_at, created_by)
  values ('schedule', (p ->> 'horizon_start')::date, (p ->> 'horizon_end')::date, coalesce(p -> 'input', '{}'::jsonb),
          p ->> 'algorithm_version', p ->> 'status', (p ->> 'objective')::numeric, (p ->> 'seconds')::numeric,
          (p ->> 'started_at')::timestamptz, now(), (select auth.uid()))
  returning id, run_number into v_run, v_run_no;

  -- No schedule to store when the solver found none (spec §8.7: never fabricate one).
  if p ->> 'status' in ('Infeasible', 'Solver limit reached', 'Failed') or jsonb_array_length(coalesce(p -> 'orders', '[]')) = 0 then
    perform set_config('app.schedule_record', '', true);
    return jsonb_build_object('run_number', v_run_no, 'version_number', null, 'validation_passed', false);
  end if;

  select * into v_base from public.schedule_versions where is_baseline;
  v_major := coalesce(split_part(v_base.version_number, '.', 1), '1');
  select coalesce(max(split_part(version_number, '.', 2)::int), 0) + 1 into v_minor
    from public.schedule_versions
   where version_number ~ '^[0-9]+\.[0-9]+$' and split_part(version_number, '.', 1) = v_major;
  v_number := v_major || '.' || v_minor;
  v_passed := coalesce((p #>> '{version,validation_passed}')::boolean, false);

  insert into public.schedule_versions (planning_run_id, version_number, label, status, is_baseline, based_on_version_id,
                                        change_summary, metrics, validation_passed, created_by)
  values (v_run, v_number, coalesce(p #>> '{version,label}', 'Solver run'), 'Draft', false, v_base.id,
          p #>> '{version,change_summary}', coalesce(p #> '{version,metrics}', '{}'::jsonb), v_passed, (select auth.uid()))
  returning id into v_version;

  for o in select * from jsonb_array_elements(p -> 'orders') loop
    v_po := null;
    insert into public.production_orders (production_order_number, schedule_version_id, sales_order_item_id, product_id,
                                          quantity, status, planned_start, planned_finish)
    select o ->> 'production_order_number', v_version, soi.id, pr.id, (o ->> 'quantity')::numeric,
           coalesce(o ->> 'status', 'Planned'), (o ->> 'planned_start')::timestamptz, (o ->> 'planned_finish')::timestamptz
      from public.products pr
      left join public.sales_orders so on so.order_number = o ->> 'order_number'
      left join public.sales_order_items soi on soi.sales_order_id = so.id and soi.line_number = (o ->> 'line_number')::int
     where pr.product_code = o ->> 'product_code'
    returning id into v_po;
    if v_po is null then
      raise exception 'Unknown product % in schedule payload', o ->> 'product_code' using errcode = 'foreign_key_violation';
    end if;

    for op in select * from jsonb_array_elements(o -> 'operations') loop
      -- The routing operation must belong to an approved routing of this product.
      if not exists (select 1 from public.routing_operations ro join public.routing_headers rh on rh.id = ro.routing_header_id
                      where ro.id = (op ->> 'routing_operation_id')::uuid and rh.status = 'Approved'
                        and rh.product_id = (select product_id from public.production_orders where id = v_po)) then
        raise exception 'Operation % of % is not on an approved routing', op ->> 'operation_number',
          o ->> 'production_order_number' using errcode = 'check_violation';
      end if;
      insert into public.production_operations (production_order_id, routing_operation_id, operation_number, quantity,
                                                status, planned_start, planned_finish, actual_finish,
                                                assigned_machine_id, is_frozen, notes)
      values (v_po, (op ->> 'routing_operation_id')::uuid, op ->> 'operation_number', (o ->> 'quantity')::numeric,
              coalesce(op ->> 'status', 'Planned'), (op ->> 'planned_start')::timestamptz,
              (op ->> 'planned_finish')::timestamptz, (op ->> 'actual_finish')::timestamptz,
              (select id from public.machines where machine_code = op ->> 'machine_code'),
              coalesce((op ->> 'is_frozen')::boolean, false), op ->> 'notes');
    end loop;
  end loop;

  -- Database re-check of what was actually stored (independent of the client's validation report).
  select array_agg(x) into v_problems from private.schedule_hard_violations(v_version) x;
  if v_problems is not null then
    update public.schedule_versions
       set validation_passed = false,
           metrics = metrics || jsonb_build_object('db_check', to_jsonb(v_problems[1:20]))
     where id = v_version;
    v_passed := false;
  end if;

  perform set_config('app.schedule_record', '', true);
  return jsonb_build_object('run_number', v_run_no, 'version_number', v_number, 'validation_passed', v_passed);
end;
$$;
revoke execute on function public.record_schedule_run(jsonb) from public, anon;
grant execute on function public.record_schedule_run(jsonb) to authenticated;
