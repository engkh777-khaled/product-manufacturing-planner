-- M07 · Fix: the workflow guard must not reference columns of the other table (or OLD on INSERT).
-- PL/pgSQL resolves record fields when an expression runs, so `new.is_baseline` failed for approval_requests
-- even behind a false `tg_table_name = 'schedule_versions'` test. Branch by table and operation instead.

create or replace function private.guard_approval_transitions()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if private.audit_maintenance() or coalesce(current_setting('app.approval_workflow', true), '') = 'on' then
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
    elsif new.is_baseline is distinct from old.is_baseline
          or (new.status is distinct from old.status and new.status in ('Approved', 'Superseded', 'Rejected')) then
      raise exception 'Approving, rejecting or superseding a schedule must go through decide_plan_approval()'
        using errcode = 'insufficient_privilege';
    end if;
  end if;

  return new;
end;
$$;
revoke execute on function private.guard_approval_transitions() from public, anon;
