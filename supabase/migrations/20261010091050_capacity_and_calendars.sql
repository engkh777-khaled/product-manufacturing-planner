-- M04 · Capacity: work centers, machines, shift calendars, calendar exceptions, downtime (spec §6.12–6.15).

create table public.work_centers (
  id               uuid primary key default gen_random_uuid(),
  work_center_code text not null unique,
  name             text not null,
  description      text,
  department       text,
  capacity_unit    text not null default 'hours' check (capacity_unit in ('hours', 'units')),
  is_active        boolean not null default true,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
comment on table public.work_centers is 'Groups of interchangeable resources (CNC, welding, assembly, testing…).';

create table public.machines (
  id              uuid primary key default gen_random_uuid(),
  machine_code    text not null unique,
  name            text not null,
  description     text,
  work_center_id  uuid not null references public.work_centers (id) on delete restrict,
  capability_tags text[] not null default '{}',
  capacity_factor numeric(5, 3) not null default 1 check (capacity_factor > 0),
  is_active       boolean not null default true,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index machines_work_center_id_idx on public.machines (work_center_id);
comment on column public.machines.capability_tags is 'Scheduler assigns an operation only to machines whose tags satisfy it (spec §6.13).';

-- A shift belongs to exactly one resource: a whole work center or a single machine.
create table public.shift_calendars (
  id              uuid primary key default gen_random_uuid(),
  work_center_id  uuid references public.work_centers (id) on delete cascade,
  machine_id      uuid references public.machines (id) on delete cascade,
  shift_name      text not null,
  start_time      time not null,
  end_time        time not null,
  valid_from      date not null,
  valid_to        date,
  working_days    smallint[] not null default '{1,2,3,4,5}',
  capacity_factor numeric(5, 3) not null default 1 check (capacity_factor > 0),
  created_at      timestamptz not null default now(),
  constraint shift_calendars_one_resource check (num_nonnulls(work_center_id, machine_id) = 1),
  constraint shift_calendars_valid_range check (valid_to is null or valid_to >= valid_from),
  constraint shift_calendars_nonzero_length check (start_time <> end_time),
  constraint shift_calendars_working_days check (
    cardinality(working_days) > 0 and working_days <@ '{1,2,3,4,5,6,7}'::smallint[])
);
create index shift_calendars_work_center_id_idx on public.shift_calendars (work_center_id);
create index shift_calendars_machine_id_idx on public.shift_calendars (machine_id);
comment on column public.shift_calendars.working_days is 'ISO weekdays: 1 = Monday … 7 = Sunday. end_time < start_time means the shift crosses midnight.';

-- Holidays, shutdowns and approved overtime. No resource = plant-wide.
create table public.calendar_exceptions (
  id             uuid primary key default gen_random_uuid(),
  exception_type text not null check (exception_type in ('holiday', 'shutdown', 'overtime')),
  description    text not null,
  starts_at      timestamptz not null,
  ends_at        timestamptz not null,
  work_center_id uuid references public.work_centers (id) on delete cascade,
  machine_id     uuid references public.machines (id) on delete cascade,
  is_approved    boolean not null default false,
  created_at     timestamptz not null default now(),
  constraint calendar_exceptions_range check (ends_at > starts_at),
  constraint calendar_exceptions_resource check (num_nonnulls(work_center_id, machine_id) <= 1)
);
create index calendar_exceptions_work_center_id_idx on public.calendar_exceptions (work_center_id);
create index calendar_exceptions_machine_id_idx on public.calendar_exceptions (machine_id);
create index calendar_exceptions_starts_at_idx on public.calendar_exceptions (starts_at);

create table public.machine_downtime (
  id             uuid primary key default gen_random_uuid(),
  machine_id     uuid not null references public.machines (id) on delete cascade,
  start_datetime timestamptz not null,
  end_datetime   timestamptz not null,
  downtime_type  text not null default 'breakdown'
                 check (downtime_type in ('planned_maintenance', 'breakdown', 'other')),
  reason         text not null,
  status         text not null default 'Planned' check (status in ('Planned', 'Confirmed', 'Cancelled')),
  created_by     uuid references auth.users (id) on delete set null,
  created_at     timestamptz not null default now(),
  constraint machine_downtime_range check (end_datetime > start_datetime)
);
create index machine_downtime_machine_start_idx on public.machine_downtime (machine_id, start_datetime);
create index machine_downtime_created_by_idx on public.machine_downtime (created_by);
comment on table public.machine_downtime is 'Confirmed downtime blocks the machine during scheduling (spec §6.15).';

create trigger work_centers_set_updated_at before update on public.work_centers
  for each row execute function public.set_updated_at();
create trigger machines_set_updated_at before update on public.machines
  for each row execute function public.set_updated_at();

-- RLS on, no policies yet: only server-side roles can access until M05 adds role policies.
alter table public.work_centers        enable row level security;
alter table public.machines            enable row level security;
alter table public.shift_calendars     enable row level security;
alter table public.calendar_exceptions enable row level security;
alter table public.machine_downtime    enable row level security;
