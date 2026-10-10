-- M12 · Signed validation results (closes the D-029 residual risk).
-- The independent validator runs on the application server. Its result is only trusted when the payload carries an
-- HMAC-SHA256 signature made with a server-side key; the key is stored in private.app_config (not exposed through
-- the API, no RLS access for any client role) and in the server's environment. A payload that claims
-- validation_passed without a valid signature is stored as NOT validated, so it can never be submitted or approved.
-- The key itself is set outside migrations (scripts/set_signing_key.py), never committed.

create table if not exists private.app_config (
  key        text primary key,
  value      text not null,
  updated_at timestamptz not null default now()
);
alter table private.app_config enable row level security;
revoke all on private.app_config from public, anon, authenticated;

-- Returns only true/false: callers learn nothing about the key.
create or replace function private.schedule_signature_valid(p_text text, p_signature text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (select encode(extensions.hmac(convert_to(p_text, 'UTF8'), convert_to(c.value, 'UTF8'), 'sha256'), 'hex') = lower(p_signature)
       from private.app_config c where c.key = 'schedule_signing_key'),
    false)
  where p_signature is not null and length(p_signature) = 64;
$$;
revoke execute on function private.schedule_signature_valid(text, text) from public, anon;
grant execute on function private.schedule_signature_valid(text, text) to authenticated;

drop function if exists public.record_schedule_run(jsonb);

create or replace function public.record_schedule_run(p_text text, p_signature text default null)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  p          jsonb := p_text::jsonb;
  v_signed   boolean := coalesce(private.schedule_signature_valid(p_text, p_signature), false);
  v_run      uuid;
  v_run_no   bigint;
  v_base     public.schedule_versions;
  v_major    text;
  v_minor    int;
  v_version  uuid;
  v_number   text;
  v_passed   boolean;
  v_claimed  boolean;
  v_po       uuid;
  v_problems text[];
  v_metrics  jsonb;
  v_type     text := coalesce(p_text::jsonb ->> 'run_type', 'schedule');
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
  v_claimed := coalesce((p #>> '{version,validation_passed}')::boolean, false);
  v_passed := v_claimed and v_signed;               -- an unsigned claim is not trusted
  v_metrics := coalesce(p #> '{version,metrics}', '{}'::jsonb)
               || jsonb_build_object('validation_signed', v_signed);
  if v_claimed and not v_signed then
    v_metrics := v_metrics || jsonb_build_object('validation_untrusted',
                 'validation result not signed by the application server — not accepted');
  end if;

  insert into public.schedule_versions (planning_run_id, version_number, label, status, is_baseline, based_on_version_id,
                                        change_summary, metrics, validation_passed, created_by)
  values (v_run, v_number, coalesce(p #>> '{version,label}', 'Solver run'), 'Draft', false, v_base.id,
          p #>> '{version,change_summary}', v_metrics, v_passed, (select auth.uid()))
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
  return jsonb_build_object('run_number', v_run_no, 'version_number', v_number, 'validation_passed', v_passed,
                            'validation_signed', v_signed);
end;
$$;
revoke execute on function public.record_schedule_run(text, text) from public, anon;
grant execute on function public.record_schedule_run(text, text) to authenticated;
