-- M04 · Warehouses, inventory balances, purchase orders (spec §6.8–6.11).

create table public.warehouses (
  id             uuid primary key default gen_random_uuid(),
  warehouse_code text not null unique,
  name           text not null,
  location       text,
  is_active      boolean not null default true,
  created_at     timestamptz not null default now()
);

create table public.inventory_balances (
  id                     uuid primary key default gen_random_uuid(),
  product_id             uuid not null references public.products (id) on delete restrict,
  warehouse_id           uuid not null references public.warehouses (id) on delete restrict,
  lot_number             text not null default '',
  on_hand_quantity       numeric(14, 4) not null default 0 check (on_hand_quantity >= 0),
  reserved_quantity      numeric(14, 4) not null default 0 check (reserved_quantity >= 0),
  quality_hold_quantity  numeric(14, 4) not null default 0 check (quality_hold_quantity >= 0),
  -- Net Available = On Hand − Reserved − Quality Hold (spec §6.9). Stored, so it can't drift.
  net_available_quantity numeric(14, 4) generated always as
                         (on_hand_quantity - reserved_quantity - quality_hold_quantity) stored,
  updated_at             timestamptz not null default now(),
  constraint inventory_balances_key unique (product_id, warehouse_id, lot_number),
  constraint inventory_balances_not_overcommitted check (reserved_quantity + quality_hold_quantity <= on_hand_quantity)
);
create index inventory_balances_warehouse_id_idx on public.inventory_balances (warehouse_id);
comment on column public.inventory_balances.lot_number is 'Empty string when the item is not lot-controlled.';

create table public.purchase_orders (
  id            uuid primary key default gen_random_uuid(),
  po_number     text not null unique,
  supplier_name text not null,
  order_date    date not null default current_date,
  status        text not null default 'Open'
                check (status in ('Draft', 'Open', 'Partially Received', 'Received', 'Closed', 'Cancelled')),
  notes         text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table public.purchase_order_items (
  id                     uuid primary key default gen_random_uuid(),
  purchase_order_id      uuid not null references public.purchase_orders (id) on delete cascade,
  line_number            integer not null check (line_number > 0),
  product_id             uuid not null references public.products (id) on delete restrict,
  ordered_quantity       numeric(14, 4) not null check (ordered_quantity > 0),
  received_quantity      numeric(14, 4) not null default 0 check (received_quantity >= 0),
  open_quantity          numeric(14, 4) generated always as (ordered_quantity - received_quantity) stored,
  expected_delivery_date date,
  status                 text not null default 'Open'
                         check (status in ('Open', 'In Transit', 'Partially Received', 'Received', 'Cancelled')),
  quality_status         text not null default 'Not Inspected'
                         check (quality_status in ('Not Inspected', 'QC Hold', 'Released', 'Rejected')),
  warehouse_id           uuid not null references public.warehouses (id) on delete restrict,
  notes                  text,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  constraint purchase_order_items_line_key unique (purchase_order_id, line_number),
  constraint purchase_order_items_no_over_receipt check (received_quantity <= ordered_quantity)
);
create index purchase_order_items_product_id_idx on public.purchase_order_items (product_id);
create index purchase_order_items_warehouse_id_idx on public.purchase_order_items (warehouse_id);
create index purchase_order_items_expected_idx on public.purchase_order_items (expected_delivery_date);
comment on table public.purchase_order_items is
  'Only open, non-cancelled, released (or releasable) quantity counts toward future availability (spec §6.11).';

create trigger inventory_balances_set_updated_at before update on public.inventory_balances
  for each row execute function public.set_updated_at();
create trigger purchase_orders_set_updated_at before update on public.purchase_orders
  for each row execute function public.set_updated_at();
create trigger purchase_order_items_set_updated_at before update on public.purchase_order_items
  for each row execute function public.set_updated_at();

alter table public.warehouses           enable row level security;
alter table public.inventory_balances   enable row level security;
alter table public.purchase_orders      enable row level security;
alter table public.purchase_order_items enable row level security;
