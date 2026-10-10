-- M05 · Users, roles and Row Level Security policies (spec §5).
--
-- Model: every user has a profile and one or more roles. Policies call has_role()/is_staff(), which read
-- user_roles for auth.uid(). Function calls are wrapped in (select …) so Postgres evaluates them once per
-- statement instead of once per row.

-- ------------------------------------------------------------------ Profiles & roles
create table public.profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  full_name  text not null,
  job_title  text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
comment on table public.profiles is 'Display data for auth.users. Created automatically on sign-up.';

create table public.user_roles (
  user_id    uuid not null references auth.users (id) on delete cascade,
  role       text not null check (role in (
               'planner', 'supervisor', 'stores', 'purchasing', 'engineering', 'approver', 'admin')),
  granted_at timestamptz not null default now(),
  granted_by uuid references auth.users (id) on delete set null,
  primary key (user_id, role)
);
create index user_roles_granted_by_idx on public.user_roles (granted_by);
comment on table public.user_roles is 'Spec §5 roles. A user with no role can sign in but sees no plant data.';

create trigger profiles_set_updated_at before update on public.profiles
  for each row execute function public.set_updated_at();

-- Create a profile whenever an auth user is created (sign-up, admin API or seed script).
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, full_name, job_title)
  values (new.id,
          coalesce(new.raw_user_meta_data ->> 'full_name', split_part(new.email, '@', 1)),
          new.raw_user_meta_data ->> 'job_title')
  on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ------------------------------------------------------------------ Role helpers
-- SECURITY DEFINER so policies can read user_roles without recursing into user_roles' own RLS.
create or replace function public.has_role(required_role text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.user_roles
    where user_id = (select auth.uid()) and role = required_role
  );
$$;

create or replace function public.has_any_role(required_roles text[])
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.user_roles
    where user_id = (select auth.uid()) and role = any (required_roles)
  );
$$;

-- Any plant role at all (self-signed-up users without a role are not staff).
create or replace function public.is_staff()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.user_roles where user_id = (select auth.uid()));
$$;

-- Identity of the caller for the app header and permission checks: one round trip.
create or replace function public.me()
returns json
language sql
stable
security definer
set search_path = ''
as $$
  select json_build_object(
    'id', u.id,
    'email', u.email,
    'full_name', p.full_name,
    'job_title', p.job_title,
    'roles', coalesce((select json_agg(r.role order by r.role) from public.user_roles r where r.user_id = u.id), '[]'::json)
  )
  from auth.users u
  left join public.profiles p on p.id = u.id
  where u.id = (select auth.uid());
$$;

revoke execute on function public.has_role(text), public.has_any_role(text[]), public.is_staff(), public.me()
  from public, anon;
grant execute on function public.has_role(text), public.has_any_role(text[]), public.is_staff(), public.me()
  to authenticated;
revoke execute on function public.handle_new_user() from public, anon, authenticated;

alter table public.profiles   enable row level security;
alter table public.user_roles enable row level security;

create policy profiles_select on public.profiles for select to authenticated
  using (id = (select auth.uid()) or (select public.is_staff()));
create policy profiles_update on public.profiles for update to authenticated
  using (id = (select auth.uid()) or (select public.has_role('admin')))
  with check (id = (select auth.uid()) or (select public.has_role('admin')));

create policy user_roles_select on public.user_roles for select to authenticated
  using (user_id = (select auth.uid()) or (select public.has_role('admin')));
create policy user_roles_insert on public.user_roles for insert to authenticated
  with check ((select public.has_role('admin')));
create policy user_roles_delete on public.user_roles for delete to authenticated
  using ((select public.has_role('admin')));

-- ------------------------------------------------------------------ Read access
-- All staff can read plant data (planning needs a plant-wide view). Audit history: approver/admin only.
do $$
declare
  t text;
begin
  foreach t in array array[
    'work_centers', 'machines', 'shift_calendars', 'calendar_exceptions', 'machine_downtime',
    'products', 'bom_headers', 'bom_items', 'routing_headers', 'routing_operations',
    'routing_operation_dependencies', 'warehouses', 'inventory_balances', 'purchase_orders',
    'purchase_order_items', 'sales_orders', 'sales_order_items', 'planning_runs', 'schedule_versions',
    'production_orders', 'production_operations', 'planning_alerts', 'approval_requests']
  loop
    execute format(
      'create policy %I on public.%I for select to authenticated using ((select public.is_staff()))',
      t || '_select', t);
  end loop;
end;
$$;

create policy audit_logs_select on public.audit_logs for select to authenticated
  using ((select public.has_any_role(array['approver', 'admin'])));

-- ------------------------------------------------------------------ Simple write access: role list per table
-- (table, roles allowed to insert/update/delete)
do $$
declare
  rec record;
begin
  for rec in
    select * from (values
      ('work_centers',        array['admin']),
      ('machines',            array['admin']),
      ('shift_calendars',     array['admin']),
      ('warehouses',          array['admin']),
      ('calendar_exceptions', array['admin', 'supervisor']),
      ('machine_downtime',    array['admin', 'supervisor']),
      ('products',            array['admin', 'engineering']),
      ('inventory_balances',  array['admin', 'stores']),
      ('purchase_orders',     array['admin', 'purchasing']),
      ('planning_runs',       array['admin', 'planner'])
    ) as v (tbl, roles)
  loop
    execute format(
      'create policy %I on public.%I for insert to authenticated with check ((select public.has_any_role(%L)))',
      rec.tbl || '_insert', rec.tbl, rec.roles);
    execute format(
      'create policy %I on public.%I for update to authenticated using ((select public.has_any_role(%L))) with check ((select public.has_any_role(%L)))',
      rec.tbl || '_update', rec.tbl, rec.roles, rec.roles);
    execute format(
      'create policy %I on public.%I for delete to authenticated using ((select public.has_any_role(%L)))',
      rec.tbl || '_delete', rec.tbl, rec.roles);
  end loop;
end;
$$;

-- ------------------------------------------------------------------ Engineering revisions (spec §5 Engineering, §6.2)
-- Engineering edits Draft / Under Review revisions only. Approving (or superseding) is the approver's call.
-- Lines of an Approved revision are therefore read-only for everyone.
create policy bom_headers_insert on public.bom_headers for insert to authenticated
  with check ((select public.has_any_role(array['engineering', 'admin'])) and status in ('Draft', 'Under Review'));
create policy bom_headers_update on public.bom_headers for update to authenticated
  using (((select public.has_any_role(array['engineering', 'admin'])) and status in ('Draft', 'Under Review'))
         or (select public.has_role('approver')))
  with check (((select public.has_any_role(array['engineering', 'admin'])) and status in ('Draft', 'Under Review', 'Obsolete'))
              or (select public.has_role('approver')));
create policy bom_headers_delete on public.bom_headers for delete to authenticated
  using ((select public.has_any_role(array['engineering', 'admin'])) and status = 'Draft');

create policy routing_headers_insert on public.routing_headers for insert to authenticated
  with check ((select public.has_any_role(array['engineering', 'admin'])) and status in ('Draft', 'Under Review'));
create policy routing_headers_update on public.routing_headers for update to authenticated
  using (((select public.has_any_role(array['engineering', 'admin'])) and status in ('Draft', 'Under Review'))
         or (select public.has_role('approver')))
  with check (((select public.has_any_role(array['engineering', 'admin'])) and status in ('Draft', 'Under Review', 'Obsolete'))
              or (select public.has_role('approver')));
create policy routing_headers_delete on public.routing_headers for delete to authenticated
  using ((select public.has_any_role(array['engineering', 'admin'])) and status = 'Draft');

do $$
declare
  action text;
  editable_bom constant text :=
    '(select public.has_any_role(array[''engineering'', ''admin''])) and exists (select 1 from public.bom_headers h '
    'where h.id = bom_header_id and h.status in (''Draft'', ''Under Review''))';
  editable_routing constant text :=
    '(select public.has_any_role(array[''engineering'', ''admin''])) and exists (select 1 from public.routing_headers h '
    'where h.id = routing_header_id and h.status in (''Draft'', ''Under Review''))';
  editable_dependency constant text :=
    '(select public.has_any_role(array[''engineering'', ''admin''])) and exists (select 1 from public.routing_operations o '
    'join public.routing_headers h on h.id = o.routing_header_id where o.id = operation_id and h.status in (''Draft'', ''Under Review''))';
begin
  foreach action in array array['insert', 'update', 'delete'] loop
    execute format('create policy %I on public.bom_items for %s to authenticated %s',
      'bom_items_' || action, action,
      case action when 'insert' then format('with check (%s)', editable_bom)
                  when 'update' then format('using (%s) with check (%s)', editable_bom, editable_bom)
                  else format('using (%s)', editable_bom) end);
    execute format('create policy %I on public.routing_operations for %s to authenticated %s',
      'routing_operations_' || action, action,
      case action when 'insert' then format('with check (%s)', editable_routing)
                  when 'update' then format('using (%s) with check (%s)', editable_routing, editable_routing)
                  else format('using (%s)', editable_routing) end);
    execute format('create policy %I on public.routing_operation_dependencies for %s to authenticated %s',
      'routing_operation_dependencies_' || action, action,
      case action when 'insert' then format('with check (%s)', editable_dependency)
                  when 'update' then format('using (%s) with check (%s)', editable_dependency, editable_dependency)
                  else format('using (%s)', editable_dependency) end);
  end loop;
end;
$$;

-- ------------------------------------------------------------------ Purchasing / stores (spec §5)
create policy purchase_order_items_insert on public.purchase_order_items for insert to authenticated
  with check ((select public.has_any_role(array['purchasing', 'admin'])));
create policy purchase_order_items_update on public.purchase_order_items for update to authenticated
  using ((select public.has_any_role(array['purchasing', 'stores', 'admin'])))
  with check ((select public.has_any_role(array['purchasing', 'stores', 'admin'])));
create policy purchase_order_items_delete on public.purchase_order_items for delete to authenticated
  using ((select public.has_any_role(array['purchasing', 'admin'])));

-- ------------------------------------------------------------------ Demand (spec §5 Planner)
create policy sales_orders_insert on public.sales_orders for insert to authenticated
  with check ((select public.has_any_role(array['planner', 'admin'])));
create policy sales_orders_update on public.sales_orders for update to authenticated
  using ((select public.has_any_role(array['planner', 'admin'])))
  with check ((select public.has_any_role(array['planner', 'admin'])));
create policy sales_orders_delete on public.sales_orders for delete to authenticated
  using ((select public.has_any_role(array['planner', 'admin'])) and status = 'Draft');

create policy sales_order_items_insert on public.sales_order_items for insert to authenticated
  with check ((select public.has_any_role(array['planner', 'admin'])));
create policy sales_order_items_update on public.sales_order_items for update to authenticated
  using ((select public.has_any_role(array['planner', 'admin'])))
  with check ((select public.has_any_role(array['planner', 'admin'])));
create policy sales_order_items_delete on public.sales_order_items for delete to authenticated
  using ((select public.has_any_role(array['planner', 'admin'])) and status = 'Draft');

-- ------------------------------------------------------------------ Schedules (spec §5, §6.19, §11.3 rule 10)
-- Planners create and edit Draft/Proposed versions but can never approve or make a baseline.
-- Approvers decide, but not on versions they created themselves.
create policy schedule_versions_insert on public.schedule_versions for insert to authenticated
  with check ((select public.has_any_role(array['planner', 'admin'])) and status in ('Draft', 'Proposed') and not is_baseline);
create policy schedule_versions_update on public.schedule_versions for update to authenticated
  using (((select public.has_any_role(array['planner', 'admin'])) and status in ('Draft', 'Proposed'))
         or ((select public.has_role('approver')) and created_by is distinct from (select auth.uid())))
  with check (((select public.has_any_role(array['planner', 'admin'])) and status in ('Draft', 'Proposed') and not is_baseline)
              or ((select public.has_role('approver')) and created_by is distinct from (select auth.uid())));

create policy production_orders_insert on public.production_orders for insert to authenticated
  with check ((select public.has_any_role(array['planner', 'admin'])));
create policy production_orders_update on public.production_orders for update to authenticated
  using ((select public.has_any_role(array['planner', 'supervisor', 'admin'])))
  with check ((select public.has_any_role(array['planner', 'supervisor', 'admin'])));
create policy production_orders_delete on public.production_orders for delete to authenticated
  using ((select public.has_any_role(array['planner', 'admin'])));

create policy production_operations_insert on public.production_operations for insert to authenticated
  with check ((select public.has_any_role(array['planner', 'admin'])));
create policy production_operations_update on public.production_operations for update to authenticated
  using ((select public.has_any_role(array['planner', 'supervisor', 'admin'])))
  with check ((select public.has_any_role(array['planner', 'supervisor', 'admin'])));
create policy production_operations_delete on public.production_operations for delete to authenticated
  using ((select public.has_any_role(array['planner', 'admin'])));

create policy planning_alerts_insert on public.planning_alerts for insert to authenticated
  with check ((select public.has_any_role(array['planner', 'admin'])));
create policy planning_alerts_update on public.planning_alerts for update to authenticated
  using ((select public.is_staff())) with check ((select public.is_staff()));

-- ------------------------------------------------------------------ Approvals (spec §5, §6.21)
-- Planners submit their own requests (and may withdraw them). Approvers decide others' requests only.
create policy approval_requests_insert on public.approval_requests for insert to authenticated
  with check ((select public.has_any_role(array['planner', 'admin']))
              and requested_by = (select auth.uid()) and status = 'Pending');
create policy approval_requests_update on public.approval_requests for update to authenticated
  using (((select public.has_role('approver')) and requested_by is distinct from (select auth.uid()))
         or requested_by = (select auth.uid()))
  with check (((select public.has_role('approver')) and requested_by is distinct from (select auth.uid()))
              or (requested_by = (select auth.uid()) and status in ('Pending', 'Withdrawn')));

-- ------------------------------------------------------------------ Audit (append-only for users)
create policy audit_logs_insert on public.audit_logs for insert to authenticated
  with check ((select public.is_staff()) and user_id = (select auth.uid()));
-- No update/delete policies: audit rows cannot be changed through the API.
