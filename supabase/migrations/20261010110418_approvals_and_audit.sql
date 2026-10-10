-- M07 · Approval workflow and tamper-resistant audit trail (spec §5 Approver, §6.19, §6.21, §6.22, §12.8).

-- ------------------------------------------------------------------ Columns
alter table public.audit_logs
  add column entity_label text,
  add column actor_role text;
comment on column public.audit_logs.entity_label is 'Business key of the changed row (e.g. SO-1005, APR-2001, V-600-TBV-10 BOM Rev C).';
comment on column public.audit_logs.actor_role is 'Database role that made the change: authenticated (app users), service_role, postgres…';

alter table public.approval_requests add column decision_comments text;
comment on column public.approval_requests.comments is 'Submitter notes.';
comment on column public.approval_requests.decision_comments is 'Approver comments on the decision.';

create sequence public.approval_request_number_seq start with 2000;
grant usage on sequence public.approval_request_number_seq to authenticated;

-- ------------------------------------------------------------------ Maintenance mode (database owner only)
-- Seed scripts and test cleanup may switch auditing off for their own session. Only the owner roles qualify,
-- and API callers cannot run SET/set_config through PostgREST, so app users can never use this.
create or replace function private.audit_maintenance()
returns boolean
language sql
stable
set search_path = ''
as $$
  select current_user in ('postgres', 'supabase_admin')
         and coalesce(current_setting('app.audit_maintenance', true), '') = 'on';
$$;

-- ------------------------------------------------------------------ Labels for audit rows
create or replace function private.audit_label(p_table text, r jsonb)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select case p_table
    when 'sales_orders' then r ->> 'order_number'
    when 'sales_order_items' then coalesce((select o.order_number from public.sales_orders o where o.id = (r ->> 'sales_order_id')::uuid), 'order')
                                  || ' line ' || (r ->> 'line_number')
    when 'approval_requests' then r ->> 'request_number'
    when 'schedule_versions' then 'v' || (r ->> 'version_number')
    when 'bom_headers' then (select p.product_code from public.products p where p.id = (r ->> 'product_id')::uuid) || ' BOM ' || (r ->> 'revision')
    when 'routing_headers' then (select p.product_code from public.products p where p.id = (r ->> 'product_id')::uuid) || ' routing ' || (r ->> 'revision')
    when 'bom_items' then coalesce((select p.product_code || ' BOM ' || h.revision from public.bom_headers h
                                    join public.products p on p.id = h.product_id where h.id = (r ->> 'bom_header_id')::uuid), 'BOM')
                          || ' line ' || (r ->> 'line_number')
    when 'routing_operations' then coalesce((select p.product_code || ' ' || h.revision from public.routing_headers h
                                             join public.products p on p.id = h.product_id where h.id = (r ->> 'routing_header_id')::uuid), 'routing')
                                   || ' ' || (r ->> 'operation_number')
    when 'inventory_balances' then (select p.product_code from public.products p where p.id = (r ->> 'product_id')::uuid)
                                   || ' @ ' || (select w.warehouse_code from public.warehouses w where w.id = (r ->> 'warehouse_id')::uuid)
                                   || coalesce(nullif(' lot ' || coalesce(r ->> 'lot_number', ''), ' lot '), '')
    when 'purchase_order_items' then (select po.po_number from public.purchase_orders po where po.id = (r ->> 'purchase_order_id')::uuid)
                                     || '/' || (r ->> 'line_number')
    when 'user_roles' then coalesce((select u.email from auth.users u where u.id = (r ->> 'user_id')::uuid), 'user') || ' · ' || (r ->> 'role')
    when 'machine_downtime' then (select m.machine_code from public.machines m where m.id = (r ->> 'machine_id')::uuid) || ' · ' || (r ->> 'reason')
    else null
  end;
$$;

-- ------------------------------------------------------------------ Row-change audit trigger
-- SECURITY INVOKER: the row is written as the caller, so the audit_logs insert policy (user_id = auth.uid())
-- applies and `actor_role` is the caller's real role. Owner/service roles bypass RLS as usual.
create or replace function private.audit_row_change()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_old    jsonb := case when tg_op in ('UPDATE', 'DELETE') then to_jsonb(old) end;
  v_new    jsonb := case when tg_op in ('INSERT', 'UPDATE') then to_jsonb(new) end;
  v_row    jsonb := coalesce(v_new, v_old);
  v_action text  := lower(tg_op);
begin
  if private.audit_maintenance() then
    return null;
  end if;
  if tg_op = 'UPDATE' then
    if (v_old - 'updated_at') = (v_new - 'updated_at') then
      return null;  -- nothing actually changed
    end if;
    if v_old ? 'status' and (v_old ->> 'status') is distinct from (v_new ->> 'status') then
      v_action := 'status: ' || coalesce(v_old ->> 'status', '—') || ' → ' || coalesce(v_new ->> 'status', '—');
    end if;
  end if;
  insert into public.audit_logs (user_id, action, entity_type, entity_id, entity_label, before_data, after_data, reason, actor_role)
  values ((select auth.uid()), v_action, tg_table_name,
          coalesce((v_row ->> 'id')::uuid, (v_row ->> 'user_id')::uuid),
          private.audit_label(tg_table_name, v_row),
          v_old, v_new, nullif(current_setting('app.audit_reason', true), ''), current_user);
  return null;
end;
$$;

do $$
declare
  t text;
begin
  foreach t in array array[
    'sales_orders', 'sales_order_items', 'approval_requests', 'schedule_versions', 'bom_headers', 'bom_items',
    'routing_headers', 'routing_operations', 'inventory_balances', 'purchase_order_items', 'user_roles', 'machine_downtime']
  loop
    execute format(
      'create trigger %I after insert or update or delete on public.%I for each row execute function private.audit_row_change()',
      t || '_audit', t);
  end loop;
end;
$$;

-- ------------------------------------------------------------------ Append-only audit log
create or replace function private.audit_logs_immutable()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if private.audit_maintenance() then
    return coalesce(new, old);
  end if;
  raise exception 'Audit log entries are append-only' using errcode = 'insufficient_privilege';
end;
$$;

create trigger audit_logs_immutable before update or delete on public.audit_logs
  for each row execute function private.audit_logs_immutable();
create trigger audit_logs_no_truncate before truncate on public.audit_logs
  for each statement execute function private.audit_logs_immutable();

revoke execute on function private.audit_maintenance(), private.audit_label(text, jsonb),
  private.audit_row_change(), private.audit_logs_immutable() from public, anon;
grant execute on function private.audit_maintenance(), private.audit_label(text, jsonb) to authenticated;

-- ------------------------------------------------------------------ Workflow guard
-- Approving, rejecting, superseding and baseline changes may only happen inside decide_plan_approval(), so an
-- approved request always means the baseline really switched (also for service-role and direct SQL callers).
create or replace function private.guard_approval_transitions()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if private.audit_maintenance() or coalesce(current_setting('app.approval_workflow', true), '') = 'on' then
    return new;
  end if;
  if tg_table_name = 'approval_requests'
     and (tg_op = 'INSERT' or new.status is distinct from old.status)
     and new.status in ('Approved', 'Rejected') then
    raise exception 'Plan decisions must go through decide_plan_approval()' using errcode = 'insufficient_privilege';
  end if;
  if tg_table_name = 'schedule_versions'
     and ((tg_op = 'INSERT' and (new.is_baseline or new.status in ('Approved', 'Superseded', 'Rejected')))
          or (tg_op = 'UPDATE' and (new.is_baseline is distinct from old.is_baseline
                                    or (new.status is distinct from old.status
                                        and new.status in ('Approved', 'Superseded', 'Rejected'))))) then
    raise exception 'Approving, rejecting or superseding a schedule must go through decide_plan_approval()'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create trigger approval_requests_workflow_guard before insert or update on public.approval_requests
  for each row execute function private.guard_approval_transitions();
create trigger schedule_versions_workflow_guard before insert or update on public.schedule_versions
  for each row execute function private.guard_approval_transitions();
revoke execute on function private.guard_approval_transitions() from public, anon;

-- ------------------------------------------------------------------ Approval workflow
-- All three functions are SECURITY INVOKER: the M05 RLS policies still apply to every statement. The explicit
-- checks give clear messages; RLS remains the backstop. Comments become the audit reason of every change.

create or replace function public.submit_plan_for_approval(p_version_number text, p_comments text default null)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v        public.schedule_versions;
  v_number text;
begin
  if not (select private.has_any_role(array['planner', 'admin'])) then
    raise exception 'Only planners can submit plans for approval' using errcode = 'insufficient_privilege';
  end if;
  -- Plain read (no FOR UPDATE): FOR UPDATE would also apply UPDATE policies and hide the row, turning clear
  -- errors into 'not found'. The one-pending-request index and the guarded updates prevent races.
  select * into v from public.schedule_versions where version_number = p_version_number;
  if not found then
    raise exception 'Schedule version % not found', p_version_number using errcode = 'no_data_found';
  end if;
  if v.status not in ('Draft', 'Proposed') then
    raise exception 'Version % is % and cannot be submitted', p_version_number, v.status using errcode = 'check_violation';
  end if;
  if not coalesce(v.validation_passed, false) then
    raise exception 'Version % has not passed independent schedule validation', p_version_number using errcode = 'check_violation';
  end if;
  if exists (select 1 from public.approval_requests where schedule_version_id = v.id and status = 'Pending') then
    raise exception 'Version % is already awaiting approval', p_version_number using errcode = 'check_violation';
  end if;

  perform set_config('app.audit_reason', coalesce(p_comments, ''), true);
  update public.schedule_versions set status = 'Proposed' where id = v.id and status = 'Draft';
  v_number := 'APR-' || nextval('public.approval_request_number_seq');
  insert into public.approval_requests (request_number, schedule_version_id, requested_by, status, comments)
  values (v_number, v.id, (select auth.uid()), 'Pending', nullif(trim(coalesce(p_comments, '')), ''));
  return v_number;
end;
$$;

create or replace function public.decide_plan_approval(p_request_number text, p_decision text, p_comments text default null)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
  r     public.approval_requests;
  v     public.schedule_versions;
  v_uid uuid := (select auth.uid());
begin
  if p_decision not in ('Approved', 'Rejected') then
    raise exception 'Decision must be Approved or Rejected' using errcode = 'check_violation';
  end if;
  if not (select private.has_role('approver')) then
    raise exception 'Only approvers can decide on plans' using errcode = 'insufficient_privilege';
  end if;
  select * into r from public.approval_requests where request_number = p_request_number for update;
  if not found then
    raise exception 'Approval request % not found', p_request_number using errcode = 'no_data_found';
  end if;
  if r.status <> 'Pending' then
    raise exception 'Request % is already %', p_request_number, r.status using errcode = 'check_violation';
  end if;
  if r.requested_by is not distinct from v_uid then
    raise exception 'You cannot decide on a plan you submitted yourself' using errcode = 'insufficient_privilege';
  end if;
  select * into v from public.schedule_versions where id = r.schedule_version_id;  -- plain read, see submit
  if v.created_by is not distinct from v_uid then
    raise exception 'You cannot decide on a schedule version you created' using errcode = 'insufficient_privilege';
  end if;
  if p_decision = 'Approved' and not coalesce(v.validation_passed, false) then
    raise exception 'A schedule that failed validation cannot be approved (spec §8.8)' using errcode = 'check_violation';
  end if;

  perform set_config('app.audit_reason', coalesce(p_comments, ''), true);
  perform set_config('app.approval_workflow', 'on', true);  -- opens the workflow guard for this transaction only
  update public.approval_requests
     set status = p_decision, decided_at = now(), approver_id = v_uid,
         decision_comments = nullif(trim(coalesce(p_comments, '')), '')
   where id = r.id;
  if p_decision = 'Approved' then
    -- The previous baseline is preserved as Superseded (spec acceptance test 9); exactly one baseline remains.
    update public.schedule_versions set status = 'Superseded', is_baseline = false where is_baseline and id <> v.id;
    update public.schedule_versions
       set status = 'Approved', is_baseline = true, approved_by = v_uid, approved_at = now()
     where id = v.id;
  else
    update public.schedule_versions set status = 'Rejected' where id = v.id;
  end if;
end;
$$;

create or replace function public.withdraw_plan_approval(p_request_number text)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
  r public.approval_requests;
begin
  select * into r from public.approval_requests where request_number = p_request_number for update;
  if not found then
    raise exception 'Approval request % not found', p_request_number using errcode = 'no_data_found';
  end if;
  if r.requested_by is distinct from (select auth.uid()) then
    raise exception 'Only the requester can withdraw a request' using errcode = 'insufficient_privilege';
  end if;
  if r.status <> 'Pending' then
    raise exception 'Request % is already %', p_request_number, r.status using errcode = 'check_violation';
  end if;
  update public.approval_requests set status = 'Withdrawn' where id = r.id;
  update public.schedule_versions set status = 'Draft' where id = r.schedule_version_id and status = 'Proposed';
end;
$$;

revoke execute on function public.submit_plan_for_approval(text, text), public.decide_plan_approval(text, text, text),
  public.withdraw_plan_approval(text) from public, anon;
grant execute on function public.submit_plan_for_approval(text, text), public.decide_plan_approval(text, text, text),
  public.withdraw_plan_approval(text) to authenticated;
