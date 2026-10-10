-- M10 · What-if scenarios and baseline-impact flags (spec §10, acceptance test 9).
-- 1. record_schedule_run() stores the run type ('schedule' | 'simulation') and the scenario assumptions; operations
--    on a hypothetical scenario machine are stored without a machine and labelled in notes.
-- 2. What-if-only scenario versions (hypothetical supply/demand/resources) can never be proposed or approved.
-- 3. When a supplier receipt moves later than a baseline operation that consumes the item, the approved baseline is
--    flagged with a critical alert (the baseline itself is never changed).

-- ------------------------------------------------------------------ 1. record runs of both types
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
  v_type     text := coalesce(p ->> 'run_type', 'schedule');
  o          jsonb;
  op         jsonb;
begin
  if not (select private.has_any_role(array['planner', 'admin'])) then
    raise exception 'Only planners can record schedule runs' using errcode = 'insufficient_privilege';
  end if;
  if v_type not in ('schedule', 'simulation') then
    raise exception 'Unknown run type %', v_type using errcode = 'check_violation';
  end if;
  perform set_config('app.schedule_record', 'on', true);

  insert into public.planning_runs (run_type, planning_horizon_start, planning_horizon_end, input_snapshot,
                                    scenario_assumptions, algorithm_version, solver_status, objective_value,
                                    solve_seconds, started_at, completed_at, created_by)
  values (v_type, (p ->> 'horizon_start')::date, (p ->> 'horizon_end')::date, coalesce(p -> 'input', '{}'::jsonb),
          coalesce(p -> 'assumptions', '{}'::jsonb), p ->> 'algorithm_version', p ->> 'status',
          (p ->> 'objective')::numeric, (p ->> 'seconds')::numeric, (p ->> 'started_at')::timestamptz, now(),
          (select auth.uid()))
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
              coalesce((op ->> 'is_frozen')::boolean, false),
              case when v_type = 'simulation' and op ? 'machine_code'
                        and not exists (select 1 from public.machines where machine_code = op ->> 'machine_code')
                   then left('Scenario machine ' || (op ->> 'machine_code') || coalesce(': ' || (op ->> 'notes'), ''), 200)
                   else op ->> 'notes' end);
    end loop;
  end loop;

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

-- Operations on a hypothetical scenario machine have no machine row; everything else is still re-checked.
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
  where (m.id is null and coalesce(a.notes, '') not like 'Scenario machine %')
     or m.work_center_id <> ro.work_center_id or not (m.capability_tags @> ro.required_capabilities) or not m.is_active
  union all
  select 'times: ' || a.pon || ' ' || a.operation_number || ' finishes before it starts'
  from ops a where a.planned_finish <= a.planned_start;
$$;
revoke execute on function private.schedule_hard_violations(uuid) from public, anon;
grant execute on function private.schedule_hard_violations(uuid) to authenticated;

-- ------------------------------------------------------------------ 2. what-if versions are never proposed
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

  if tg_table_name = 'schedule_versions' and tg_op = 'UPDATE'
     and new.status in ('Proposed', 'Approved') and new.status is distinct from old.status then
    -- Hypothetical scenarios are for comparison only (D-031): apply the change to the plant data and re-run.
    if exists (select 1 from public.planning_runs r
                where r.id = new.planning_run_id and coalesce((r.scenario_assumptions ->> 'what_if_only')::boolean, false)) then
      raise exception 'Version % is a what-if scenario built on hypothetical changes and cannot be submitted or approved; apply the changes to the plant data and re-run the solver',
        new.version_number using errcode = 'check_violation';
    end if;
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

-- ------------------------------------------------------------------ 3. flag the baseline on receipt changes
-- SECURITY DEFINER: purchasing may move receipt dates but cannot write planning alerts under RLS.
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

  -- Earliest baseline operation of each production order that consumes the item (BOM operation reference, or any
  -- operation when the BOM line has none) and is planned before the new receipt date.
  for r in
    select distinct on (po.id) po.id as production_order_id, po.sales_order_item_id, po.production_order_number,
           so.order_number, op.operation_number, op.planned_start
      from public.production_orders po
      join public.bom_headers bh on bh.product_id = po.product_id and bh.status = 'Approved'
      join public.bom_items bi on bi.bom_header_id = bh.id and bi.component_product_id = new.product_id
      join public.production_operations op on op.production_order_id = po.id
           and op.status not in ('Completed', 'Cancelled') and op.planned_start is not null
           and (bi.operation_reference is null or op.operation_number = bi.operation_reference)
      left join public.sales_order_items soi on soi.id = po.sales_order_item_id
      left join public.sales_orders so on so.id = soi.sales_order_id
     where po.schedule_version_id = v_base.id
       and (new.expected_delivery_date is null
            or op.planned_start < (new.expected_delivery_date::timestamp at time zone 'America/Chicago'))
     order by po.id, op.planned_start
  loop
    insert into public.planning_alerts (planning_run_id, sales_order_item_id, production_order_id, alert_type, severity,
                                        description, recommended_action, details)
    values (v_base.planning_run_id, r.sales_order_item_id, r.production_order_id, 'late_supplier_receipt', 'critical',
            format('%s/%s (%s) now expected %s — approved baseline v%s starts %s %s of %s on %s',
                   v_po, new.line_number, v_item, coalesce(to_char(new.expected_delivery_date, 'Mon DD'), 'without a date'),
                   v_base.version_number, r.production_order_number, r.operation_number, coalesce(r.order_number, '—'),
                   to_char(r.planned_start at time zone 'America/Chicago', 'Mon DD HH24:MI')),
            'The approved baseline stays in force. Re-run the solver or build a scenario to see the impact, then submit the new version for approval.',
            jsonb_build_object('source', 'receipt_change', 'po_number', v_po, 'line_number', new.line_number,
                               'item', v_item, 'old_date', old.expected_delivery_date,
                               'new_date', new.expected_delivery_date, 'baseline_version', v_base.version_number,
                               'operation', r.operation_number, 'planned_start', r.planned_start,
                               'order_number', r.order_number));
  end loop;
  return null;
end;
$$;
revoke execute on function private.flag_receipt_change_impact() from public, anon;

create trigger purchase_order_items_flag_baseline_impact
  after update of expected_delivery_date on public.purchase_order_items
  for each row execute function private.flag_receipt_change_impact();
