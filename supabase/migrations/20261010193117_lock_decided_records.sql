-- M12 · Authorization sweep fix: approvers had broad UPDATE rights (needed because the approval functions run as
-- the approver), so an approver could also edit approved BOM/routing headers and the approved baseline directly.
-- These triggers allow only the workflow transitions; everything else on decided records is read-only.
-- Owner/definer contexts (seed, maintenance, SECURITY DEFINER helpers) are not restricted.

create or replace function private.changed_columns(p_new jsonb, p_old jsonb)
returns text[]
language sql
immutable
set search_path = ''
as $$
  select coalesce(array_agg(n.key order by n.key), '{}')
    from jsonb_each(p_new) n
   where n.key <> 'updated_at' and n.value is distinct from (p_old -> n.key);
$$;

create or replace function private.guard_engineering_revisions()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  changed text[] := private.changed_columns(to_jsonb(new), to_jsonb(old));
begin
  if private.audit_maintenance() or current_user not in ('authenticated', 'anon') or changed = '{}' then
    return new;
  end if;
  -- the two approval transitions performed by approve_bom_revision() / the routing equivalent
  if old.status = 'Under Review' and new.status = 'Approved' and changed <@ array['status', 'approved_by', 'approved_at'] then
    return new;
  end if;
  if old.status = 'Approved' and new.status = 'Superseded' and changed <@ array['status', 'effective_to'] then
    return new;
  end if;
  if old.status in ('Approved', 'Superseded', 'Obsolete') then
    raise exception 'Revision % (%) is % and locked; create a new revision instead', old.revision, tg_table_name,
      old.status using errcode = 'insufficient_privilege';
  end if;
  if not (select private.has_any_role(array['engineering', 'admin'])) then
    raise exception 'Only engineering edits revisions; approvers can only approve them' using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;
revoke execute on function private.guard_engineering_revisions() from public, anon;

create trigger bom_headers_guard_revisions before update on public.bom_headers
  for each row execute function private.guard_engineering_revisions();
create trigger routing_headers_guard_revisions before update on public.routing_headers
  for each row execute function private.guard_engineering_revisions();

create or replace function private.guard_schedule_version_edits()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if private.audit_maintenance() or current_user not in ('authenticated', 'anon')
     or coalesce(current_setting('app.approval_workflow', true), '') = 'on'
     or coalesce(current_setting('app.schedule_record', true), '') = 'on'
     or private.changed_columns(to_jsonb(new), to_jsonb(old)) = '{}' then
    return new;
  end if;
  if old.status in ('Approved', 'Superseded', 'Rejected') then
    raise exception 'Version % is % and read-only', old.version_number, old.status using errcode = 'insufficient_privilege';
  end if;
  if not (select private.has_any_role(array['planner', 'admin'])) then
    raise exception 'Approvers decide through the approval workflow and cannot edit schedule versions'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;
revoke execute on function private.guard_schedule_version_edits() from public, anon;

create trigger schedule_versions_guard_edits before update on public.schedule_versions
  for each row execute function private.guard_schedule_version_edits();
