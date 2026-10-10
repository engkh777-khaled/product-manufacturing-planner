-- M04 · Customer demand: sales orders and lines (spec §6.6–6.7).

create table public.sales_orders (
  id                      uuid primary key default gen_random_uuid(),
  order_number            text not null unique,
  customer_name           text not null,
  customer_reference      text,
  order_date              date not null default current_date,
  requested_delivery_date date not null,
  priority                text not null default 'Normal' check (priority in ('Critical', 'High', 'Normal', 'Low')),
  status                  text not null default 'Draft' check (status in (
                            'Draft', 'Confirmed', 'Planning', 'Planned', 'In Production', 'Completed', 'Cancelled')),
  currency                char(3) not null default 'USD',
  notes                   text,
  created_by              uuid references auth.users (id) on delete set null,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  constraint sales_orders_dates check (requested_delivery_date >= order_date)
);
create index sales_orders_created_by_idx on public.sales_orders (created_by);
create index sales_orders_status_idx on public.sales_orders (status);

create table public.sales_order_items (
  id                      uuid primary key default gen_random_uuid(),
  sales_order_id          uuid not null references public.sales_orders (id) on delete cascade,
  line_number             integer not null check (line_number > 0),
  product_id              uuid not null references public.products (id) on delete restrict,
  product_configuration   jsonb not null default '{}'::jsonb,
  bom_revision_id         uuid references public.bom_headers (id) on delete restrict,
  routing_revision_id     uuid references public.routing_headers (id) on delete restrict,
  quantity                numeric(14, 4) not null check (quantity > 0),
  unit_of_measure         text not null default 'EA',
  unit_price              numeric(14, 2) check (unit_price >= 0),
  requested_delivery_date date not null,
  priority                text check (priority in ('Critical', 'High', 'Normal', 'Low')),
  status                  text not null default 'Draft' check (status in (
                            'Draft', 'Confirmed', 'Planning', 'Planned', 'In Production', 'Completed', 'Cancelled')),
  notes                   text,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  constraint sales_order_items_line_key unique (sales_order_id, line_number)
);
create index sales_order_items_product_id_idx on public.sales_order_items (product_id);
create index sales_order_items_bom_revision_id_idx on public.sales_order_items (bom_revision_id);
create index sales_order_items_routing_revision_id_idx on public.sales_order_items (routing_revision_id);
comment on column public.sales_order_items.priority is 'NULL = inherit the order header priority.';

-- Selected BOM/routing must belong to the line's product, and a planned line's engineering
-- configuration must not change silently (spec §6.7).
create or replace function public.sales_order_items_check_engineering()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.bom_revision_id is not null
     and (select product_id from public.bom_headers where id = new.bom_revision_id) <> new.product_id then
    raise exception 'BOM revision belongs to a different product' using errcode = 'check_violation';
  end if;
  if new.routing_revision_id is not null
     and (select product_id from public.routing_headers where id = new.routing_revision_id) <> new.product_id then
    raise exception 'Routing revision belongs to a different product' using errcode = 'check_violation';
  end if;
  if tg_op = 'UPDATE'
     and old.status in ('Planned', 'In Production', 'Completed')
     and (new.product_id, new.bom_revision_id, new.routing_revision_id)
         is distinct from (old.product_id, old.bom_revision_id, old.routing_revision_id) then
    raise exception 'Engineering configuration of a % line cannot be changed; re-plan instead', old.status
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger sales_order_items_check_engineering
  before insert or update on public.sales_order_items
  for each row execute function public.sales_order_items_check_engineering();

create trigger sales_orders_set_updated_at before update on public.sales_orders
  for each row execute function public.set_updated_at();
create trigger sales_order_items_set_updated_at before update on public.sales_order_items
  for each row execute function public.set_updated_at();

alter table public.sales_orders      enable row level security;
alter table public.sales_order_items enable row level security;
