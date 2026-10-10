-- M05 · Security advisor fix (lint 0029): SECURITY DEFINER helpers must not be callable through the REST API.
-- Move them to a schema that PostgREST does not expose. RLS policies reference functions by OID, so every
-- existing policy keeps working unchanged. `authenticated` still needs EXECUTE because policies run as the caller.

create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated;

alter function public.has_role(text)       set schema private;
alter function public.has_any_role(text[]) set schema private;
alter function public.is_staff()           set schema private;
alter function public.handle_new_user()    set schema private;

revoke execute on all functions in schema private from public, anon;
grant execute on function private.has_role(text), private.has_any_role(text[]), private.is_staff() to authenticated;

-- me() stays in the API for the app, but as SECURITY INVOKER: it reads only what RLS already lets the caller
-- see (own profile, own roles) and takes the email from the caller's JWT instead of auth.users.
drop function public.me();
create function public.me()
returns json
language sql
stable
security invoker
set search_path = ''
as $$
  select json_build_object(
    'id', (select auth.uid()),
    'email', (select auth.jwt() ->> 'email'),
    'full_name', p.full_name,
    'job_title', p.job_title,
    'roles', coalesce(
      (select json_agg(r.role order by r.role) from public.user_roles r where r.user_id = (select auth.uid())),
      '[]'::json)
  )
  from (select 1) as one
  left join public.profiles p on p.id = (select auth.uid());
$$;
revoke execute on function public.me() from public, anon;
grant execute on function public.me() to authenticated;
comment on function public.me() is 'Caller identity for the app header: id, email, name, job title, roles.';
