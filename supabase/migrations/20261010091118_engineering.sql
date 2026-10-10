-- M04 · Engineering master data: products, BOMs, routings (spec §6.1–6.5).
-- Components are products too (item_type = manufactured | purchased). A multilevel BOM is a chain of
-- single-level BOMs: a manufactured component has its own bom_headers row.

create table public.products (
  id                      uuid primary key default gen_random_uuid(),
  product_code            text not null unique,
  name                    text not null,
  description             text,
  item_type               text not null check (item_type in ('finished_good', 'manufactured', 'purchased')),
  product_family          text,
  valve_type              text,
  size                    text,
  pressure_class          text,
  body_material           text,
  trim_material           text,
  connection_type         text,
  unit_of_measure         text not null default 'EA',
  product_status          text not null default 'Active'
                          check (product_status in ('Active', 'Pending Release', 'Inactive', 'Obsolete')),
  standard_lead_time_days numeric(6, 2) check (standard_lead_time_days >= 0),
  attributes              jsonb not null default '{}'::jsonb,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now()
);
comment on column public.products.attributes is 'Certifications and specs: design standard, fire-safe, sour service, test pressures…';

-- ---------------------------------------------------------------- BOMs
create table public.bom_headers (
  id             uuid primary key default gen_random_uuid(),
  product_id     uuid not null references public.products (id) on delete restrict,
  revision       text not null,
  status         text not null default 'Draft'
                 check (status in ('Draft', 'Under Review', 'Approved', 'Superseded', 'Obsolete')),
  effective_from date not null,
  effective_to   date,
  eco_number     text,
  approved_by    uuid references auth.users (id) on delete set null,
  approved_at    timestamptz,
  notes          text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint bom_headers_product_revision_key unique (product_id, revision),
  constraint bom_headers_effective_range check (effective_to is null or effective_to > effective_from),
  constraint bom_headers_approval_stamped check (status not in ('Approved', 'Superseded') or approved_at is not null)
);
-- Only one approved revision per product may drive executable planning (spec §6.2).
create unique index bom_headers_one_approved_per_product on public.bom_headers (product_id) where status = 'Approved';
create index bom_headers_approved_by_idx on public.bom_headers (approved_by);

create table public.bom_items (
  id                   uuid primary key default gen_random_uuid(),
  bom_header_id        uuid not null references public.bom_headers (id) on delete cascade,
  line_number          integer not null check (line_number > 0),
  component_product_id uuid not null references public.products (id) on delete restrict,
  quantity_per_parent  numeric(14, 4) not null check (quantity_per_parent > 0),
  unit_of_measure      text not null default 'EA',
  scrap_factor         numeric(5, 4) not null default 0 check (scrap_factor >= 0 and scrap_factor < 1),
  is_phantom           boolean not null default false,
  is_optional          boolean not null default false,
  operation_reference  text,
  created_at           timestamptz not null default now(),
  constraint bom_items_line_key unique (bom_header_id, line_number)
);
create index bom_items_component_product_id_idx on public.bom_items (component_product_id);
comment on column public.bom_items.scrap_factor is 'Fraction. Yield = 1 - scrap; required = parent qty × qty/parent ÷ yield (applied once).';

-- Reject a BOM line that would make a product (indirectly) contain itself (spec §6.3).
create or replace function public.bom_items_prevent_cycle()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  parent_product uuid;
  cycle_found    boolean;
begin
  select product_id into parent_product from public.bom_headers where id = new.bom_header_id;

  if new.component_product_id = parent_product then
    raise exception 'BOM line cannot use its own parent product as a component'
      using errcode = 'check_violation';
  end if;

  -- Walk down from the new component through every non-obsolete BOM. Reaching the parent = cycle.
  with recursive descendants (product_id, path) as (
    select new.component_product_id, array[new.component_product_id]
    union all
    select bi.component_product_id, d.path || bi.component_product_id
    from descendants d
    join public.bom_headers bh on bh.product_id = d.product_id and bh.status <> 'Obsolete'
    join public.bom_items bi on bi.bom_header_id = bh.id
    where not (bi.component_product_id = any (d.path))
  )
  select exists (select 1 from descendants where product_id = parent_product) into cycle_found;

  if cycle_found then
    raise exception 'Circular BOM reference: component % already contains product %',
      new.component_product_id, parent_product
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger bom_items_prevent_cycle
  before insert or update of component_product_id, bom_header_id on public.bom_items
  for each row execute function public.bom_items_prevent_cycle();

-- ---------------------------------------------------------------- Routings
create table public.routing_headers (
  id             uuid primary key default gen_random_uuid(),
  product_id     uuid not null references public.products (id) on delete restrict,
  revision       text not null,
  status         text not null default 'Draft'
                 check (status in ('Draft', 'Under Review', 'Approved', 'Superseded', 'Obsolete')),
  effective_from date not null,
  effective_to   date,
  approved_by    uuid references auth.users (id) on delete set null,
  approved_at    timestamptz,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint routing_headers_product_revision_key unique (product_id, revision),
  constraint routing_headers_effective_range check (effective_to is null or effective_to > effective_from),
  constraint routing_headers_approval_stamped check (status not in ('Approved', 'Superseded') or approved_at is not null)
);
create unique index routing_headers_one_approved_per_product on public.routing_headers (product_id) where status = 'Approved';
create index routing_headers_approved_by_idx on public.routing_headers (approved_by);

create table public.routing_operations (
  id                      uuid primary key default gen_random_uuid(),
  routing_header_id       uuid not null references public.routing_headers (id) on delete cascade,
  operation_number        text not null,
  sequence                integer not null check (sequence > 0),
  operation_code          text,
  operation_name          text not null,
  work_center_id          uuid not null references public.work_centers (id) on delete restrict,
  setup_time_minutes      numeric(10, 2) not null default 0 check (setup_time_minutes >= 0),
  cycle_time_minutes      numeric(10, 2) not null default 0 check (cycle_time_minutes >= 0),
  inspection_time_minutes numeric(10, 2) not null default 0 check (inspection_time_minutes >= 0),
  time_basis              text not null default 'per_unit' check (time_basis in ('per_unit', 'per_batch')),
  minimum_batch_quantity  integer check (minimum_batch_quantity > 0),
  maximum_batch_quantity  integer check (maximum_batch_quantity > 0),
  required_skill          text,
  required_capabilities   text[] not null default '{}',
  is_mandatory            boolean not null default true,
  is_quality_gate         boolean not null default false,
  notes                   text,
  created_at              timestamptz not null default now(),
  constraint routing_operations_number_key unique (routing_header_id, operation_number),
  constraint routing_operations_sequence_key unique (routing_header_id, sequence),
  constraint routing_operations_batch_range check (
    maximum_batch_quantity is null or minimum_batch_quantity is null
    or maximum_batch_quantity >= minimum_batch_quantity)
);
create index routing_operations_work_center_id_idx on public.routing_operations (work_center_id);
comment on column public.routing_operations.time_basis is
  'per_unit: duration = setup + qty × cycle. per_batch: cycle is charged once per batch. Setup is charged once per production order (spec §8.2).';

-- Explicit precedence; an operation may have several predecessors (spec §6.5).
create table public.routing_operation_dependencies (
  operation_id   uuid not null references public.routing_operations (id) on delete cascade,
  predecessor_id uuid not null references public.routing_operations (id) on delete cascade,
  primary key (operation_id, predecessor_id),
  constraint routing_operation_dependencies_not_self check (operation_id <> predecessor_id)
);
create index routing_operation_dependencies_predecessor_idx on public.routing_operation_dependencies (predecessor_id);

create or replace function public.routing_dependencies_same_routing()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (select routing_header_id from public.routing_operations where id = new.operation_id)
     is distinct from
     (select routing_header_id from public.routing_operations where id = new.predecessor_id) then
    raise exception 'Operation dependencies must stay within one routing' using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger routing_dependencies_same_routing
  before insert or update on public.routing_operation_dependencies
  for each row execute function public.routing_dependencies_same_routing();

create trigger products_set_updated_at before update on public.products
  for each row execute function public.set_updated_at();
create trigger bom_headers_set_updated_at before update on public.bom_headers
  for each row execute function public.set_updated_at();
create trigger routing_headers_set_updated_at before update on public.routing_headers
  for each row execute function public.set_updated_at();

alter table public.products                       enable row level security;
alter table public.bom_headers                    enable row level security;
alter table public.bom_items                      enable row level security;
alter table public.routing_headers                enable row level security;
alter table public.routing_operations             enable row level security;
alter table public.routing_operation_dependencies enable row level security;
