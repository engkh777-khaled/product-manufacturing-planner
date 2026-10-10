-- M12 · Fix: planning run timestamps come from two clocks. started_at is sent by the application server, completed_at
-- is the database's now() (transaction start). When the server clock runs ahead of the database, or inside a long
-- transaction, started_at can be later than completed_at and the planning_runs_timing check rejects the run.
-- Clamp started_at to completed_at instead of failing the whole save.
create or replace function private.clamp_run_timing()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.started_at is not null and new.completed_at is not null and new.started_at > new.completed_at then
    new.started_at := new.completed_at;
  end if;
  return new;
end;
$$;
revoke execute on function private.clamp_run_timing() from public, anon;

create trigger planning_runs_clamp_timing
  before insert or update of started_at, completed_at on public.planning_runs
  for each row execute function private.clamp_run_timing();
