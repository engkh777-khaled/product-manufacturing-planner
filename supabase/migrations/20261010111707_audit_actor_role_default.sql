-- M07 · App-written audit rows (blocked attempts, solver requests) record the inserting role too.
-- DEFAULT current_user is evaluated at insert time as the caller (e.g. authenticated).
alter table public.audit_logs alter column actor_role set default current_user;
