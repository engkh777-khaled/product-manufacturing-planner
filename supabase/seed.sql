-- =====================================================================================================
-- FICTIONAL DEMONSTRATION DATA (spec §13). Every company, person, order and number below is invented.
-- Plant: "Valvetech Plant 04 - Houston TX" (fictional). Times are plant local time, CST (UTC-06).
--
-- Story (same as the UI mock data): SO-1005 for Shell Offshore GOM is requested Nov 30 but the baseline
-- schedule (v4.2) finishes Dec 3 because actuator PO-8821 arrives Nov 28 and Hydro Rig 1 (WC-40) is the
-- bottleneck. Scenario v4.3 (expedite PO-8821 + Saturday OT) finishes Nov 29 and awaits approval.
-- SO-1021 has only a Draft BOM and no routing ("needs data"). SO-1022 competes for PO-8821 actuators.
--
-- Runs after migrations on `supabase db reset`. Rows are looked up by business code, never by UUID.
-- =====================================================================================================

-- Seeding is maintenance: no audit rows, and the approval workflow guard is open (M07; owner role only).
select set_config('app.audit_maintenance', 'on', false);

-- ------------------------------------------------------------------ Capacity
insert into public.work_centers (work_center_code, name, description, department) values
  ('WC-10', 'CNC Horizontal Machining Center', 'Mazak / Okuma 5-axis turning & milling', 'Machining'),
  ('WC-14', 'Conventional Heavy Turning', 'VTL Bullard 64" large-bore facing', 'Machining'),
  ('WC-22', 'Automated TIG Cladding & Overlay', 'Inconel 625 hot wire, PTA Stellite, SAW', 'Welding'),
  ('WC-30', 'Precision Valve Assembly & Trim Fit', 'Bays 1-3 cleanroom: lapping, packing, final inspection', 'Assembly'),
  ('WC-35', 'Final Valve Assembly', '30-ton crane bays', 'Assembly'),
  ('WC-40', 'Hydrostatic & Cryo Pressure Cell', 'Testing & NDE', 'Quality'),
  ('WC-50', 'Industrial Blasting, Primer & Coating', 'Enclosed epoxy booth and cure oven', 'Finishing');

insert into public.machines (machine_code, name, description, work_center_id, capability_tags)
select v.code, v.name, v.description, wc.id, v.tags
from (values
  ('MC-010', 'Mazak Integrex e-670H',       'Heavy Milling / 5-Axis',       'WC-10', array['turning', 'milling', '5-axis']),
  ('MC-014', 'Okuma Multus U4000',          'Seat & Cavity Turning',        'WC-10', array['turning', 'milling']),
  ('VT-001', 'VTL Bullard 64"',             'Large Bore Facing',            'WC-14', array['vtl', 'large-bore']),
  ('WL-002', 'Cladding Cell 1 (Inconel)',   'Weld Overlay Station',         'WC-22', array['cladding', 'pta', 'stellite', 'tig']),
  ('WL-005', 'Automated Submerged Arc',     'Flange Butt Welder',           'WC-22', array['saw', 'flange-weld']),
  ('SA-008', 'Stem & Ball Lapping Bench',   'Optical Flat Tolerancing',     'WC-30', array['lapping']),
  ('SA-012', 'Packing Assembly Station',    'Graphite Seal Insert',         'WC-30', array['packing', 'final-inspection']),
  ('FA-001', 'Heavy Valve Bay A',           '30-Ton Crane Assist',          'WC-35', array['assembly', 'heavy-lift', 'actuation']),
  ('FA-002', 'Valve Bay B (Medium Bore)',   'Standard Frame Assembly',      'WC-35', array['assembly']),
  ('TE-001', 'Hydrostatic Rig 1 (HP)',      '22,500 PSI Enclosure',         'WC-40', array['hydro', 'api-598']),
  ('TE-004', 'Gas Helium Cryo Cell',        '-196°C Sniffer Chamber',       'WC-40', array['cryo', 'helium-leak']),
  ('ND-001', 'Radiographic Bunker',         'X-Ray / Gamma RT Vault',       'WC-40', array['radiography', 'ut', 'mpi']),
  ('SF-001', 'Blast Booth (Garnet)',        'Sa 2.5 Profiling',             'WC-50', array['blast']),
  ('SF-003', 'Epoxy Paint Line',            'Marine C5M Curing Oven',       'WC-50', array['epoxy', 'cure-oven'])
) as v (code, name, description, wc_code, tags)
join public.work_centers wc on wc.work_center_code = v.wc_code;

-- Two shifts, Monday–Friday, for every work center.
insert into public.shift_calendars (work_center_id, shift_name, start_time, end_time, valid_from, working_days)
select wc.id, s.shift_name, s.start_time, s.end_time, date '2025-01-01', '{1,2,3,4,5}'
from public.work_centers wc
cross join (values ('S1', time '06:00', time '14:00'), ('S2', time '14:00', time '22:00')) as s (shift_name, start_time, end_time);

insert into public.calendar_exceptions (exception_type, description, starts_at, ends_at, is_approved) values
  ('holiday', 'Christmas Day',  '2025-12-25 00:00-06', '2025-12-26 00:00-06', true),
  ('holiday', 'New Year''s Day', '2026-01-01 00:00-06', '2026-01-02 00:00-06', true);

insert into public.machine_downtime (machine_id, start_datetime, end_datetime, downtime_type, reason, status)
select m.id, v.starts::timestamptz, v.ends::timestamptz, v.kind, v.reason, v.status
from (values
  ('MC-010', '2025-11-21 14:00-06', '2025-11-21 22:00-06', 'planned_maintenance', 'Spindle PM',                'Confirmed'),
  ('TE-001', '2025-11-24 06:00-06', '2025-11-25 18:00-06', 'planned_maintenance', 'Pump overhaul',             'Confirmed'),
  ('SF-001', '2025-12-02 06:00-06', '2025-12-02 12:00-06', 'planned_maintenance', 'Garnet media change',       'Planned')
) as v (machine, starts, ends, kind, reason, status)
join public.machines m on m.machine_code = v.machine;

-- ------------------------------------------------------------------ Products
insert into public.products (product_code, name, item_type, product_family, valve_type, size, pressure_class,
                             body_material, trim_material, connection_type, product_status,
                             standard_lead_time_days, attributes) values
  ('V-600-TBV-10',  '10" Class 600 Trunnion Mounted Ball Valve', 'finished_good', 'Trunnion Ball', 'Ball', '10"', '600',
   'ASTM A350 LF2 Class 1', 'Forged 316SS + 75µ ENP', 'RTJ', 'Active', 18.5,
   '{"design_standard":"API 6D, 25th Edition","fire_safe":"API 607 / ISO 10497","sour_service":"NACE MR0175 / ISO 15156","hydro_shell_psi":2225}'),
  ('V-300-FBV-08',  '8" Class 300 Floating Ball Valve', 'finished_good', 'Floating Ball', 'Ball', '8"', '300',
   'ASTM A216 WCB', '304SS', 'RF', 'Active', 11,
   '{"design_standard":"API 6D","fire_safe":"API 607","hydro_shell_psi":1125}'),
  ('V-900-MSB-14',  '14" Class 900 Metal-Seated Ball Valve', 'finished_good', 'Trunnion Ball', 'Ball', '14"', '900',
   'F22 Alloy', 'Stellite', 'RTJ', 'Active', 28,
   '{"design_standard":"API 6D","service":"Emergency shutdown"}'),
  ('V-150-DPC-06',  '6" Class 150 Dual Plate Check Valve', 'finished_good', 'Check', 'Check', '6"', '150',
   'CF8M Stainless', 'Inconel spring', 'Wafer', 'Active', 9,
   '{"design_standard":"API 594"}'),
  ('V-150-DPC-04',  '4" Class 150 Dual Plate Check Valve', 'finished_good', 'Check', 'Check', '4"', '150',
   'CF8M Stainless', 'Inconel X-750 spring', 'Wafer', 'Active', 8,
   '{"design_standard":"API 594"}'),
  ('V-1500-CBV-12', '12" Class 1500 Cryogenic Ball Valve', 'finished_good', 'Cryogenic', 'Ball', '12"', '1500',
   '316L', 'Kel-F seats', 'RTJ', 'Active', 35,
   '{"design_standard":"BS 6364","service":"Cryogenic -196°C"}'),
  ('V-300-CGV-04',  '4" Class 300 Control Globe Valve', 'finished_good', 'Control', 'Globe', '4"', '300',
   'ASTM A216 WCC', 'Monel plug / 410 cage', 'RF', 'Active', 14,
   '{"design_standard":"IEC 60534"}'),
  ('V-300-CGT-06',  '6" Class 300 Cryogenic Gate Valve', 'finished_good', 'Cryogenic', 'Gate', '6"', '300',
   'CF8M', 'Extended bonnet', 'RF', 'Active', 21,
   '{"design_standard":"BS 6364"}'),
  ('V-800-FGV-02',  '2" Class 800 Forged Gate Valve', 'finished_good', 'Forged Gate', 'Gate', '2"', '800',
   'ASTM A105N', '13Cr / Stellite', 'SW', 'Pending Release', null,
   '{"design_standard":"API 602"}');

insert into public.products (product_code, name, item_type, body_material, unit_of_measure) values
  -- V-600-TBV-10
  ('VB-LF2-U10',   'Valve Body Upper Half 10"',            'manufactured', 'Forged A350 LF2', 'EA'),
  ('VB-LF2-L10',   'Valve Body Lower Half 10"',            'manufactured', 'Forged A350 LF2', 'EA'),
  ('TB-316-HC10',  'Trunnion Ball 10"',                    'manufactured', '316SS + Hard Chrome', 'EA'),
  ('SR-ST6-10',    'Seat Ring with Stellite #6 Inlay 10"', 'manufactured', 'Stellite #6', 'EA'),
  ('GS-PK-010',    'Grafoil Stem Packing Set 10"',         'purchased', null, 'EA'),
  ('DS-IN718-4',   'Inconel 718 Disc Spring',              'purchased', 'Inconel 718', 'EA'),
  ('AMK-TBV-10',   'Actuation & Mounting Kit 10"',         'manufactured', null, 'EA'),
  ('ACT-DA-400',   'Double Acting Pneumatic Actuator 4"',  'purchased', null, 'EA'),
  ('BRK-MNT-A36',  'Actuator Mounting Bracket',            'manufactured', 'ASTM A36', 'EA'),
  ('PL-A36-12',    'A36 Plate 12 mm',                      'purchased', 'ASTM A36', 'M2'),
  -- V-300-FBV-08
  ('BD-WCB-08',    'Body Casting 8"',                      'purchased', 'A216 WCB', 'EA'),
  ('BL-304-08',    'Floating Ball 8"',                     'manufactured', '304SS', 'EA'),
  ('ST-PTFE-08',   'RPTFE Seat 8"',                        'purchased', 'RPTFE', 'EA'),
  ('GS-PK-008',    'Grafoil Stem Packing Set 8"',          'purchased', null, 'EA'),
  ('LV-WR-08',     'Wrench Operator 8"',                   'purchased', null, 'EA'),
  -- V-900-MSB-14
  ('BD-F22-14',    'Body Forging 14"',                     'purchased', 'F22', 'EA'),
  ('BL-STL-14',    'Metal-Seated Ball 14"',                'manufactured', 'Stellite overlay', 'EA'),
  ('ST-STL-14',    'Metal Seat 14"',                       'manufactured', 'Stellite', 'EA'),
  ('ST-IN718-M24', 'Inconel 718 Flange Stud M24x180',      'purchased', 'Inconel 718', 'EA'),
  ('GS-PK-014',    'Grafoil Stem Packing Set 14"',         'purchased', null, 'EA'),
  -- V-150-DPC-06
  ('BD-CF8M-06',   'Check Valve Body 6"',                  'purchased', 'CF8M', 'EA'),
  ('PL-CF8M-06',   'Check Plate 6"',                       'manufactured', 'CF8M', 'EA'),
  ('SP-IN-06',     'Torsion Spring 6"',                    'purchased', 'Inconel', 'EA'),
  -- V-150-DPC-04
  ('BD-CF8M-04',   'Check Valve Body 4"',                  'purchased', 'CF8M', 'EA'),
  ('PL-CF8M-04',   'Check Plate 4"',                       'manufactured', 'CF8M', 'EA'),
  ('SP-X750-04',   'Torsion Spring 4"',                    'purchased', 'Inconel X-750', 'EA'),
  -- V-1500-CBV-12
  ('BD-316L-12',   'Cryogenic Body Casting 12"',           'purchased', '316L', 'EA'),
  ('BL-316L-12',   'Cryogenic Ball 12"',                   'manufactured', '316L', 'EA'),
  ('SL-KELF-12',   'Kel-F Seat 12"',                       'purchased', 'PCTFE', 'EA'),
  ('BN-EXT-12',    'Extended Bonnet 12"',                  'manufactured', '316L', 'EA'),
  -- V-300-CGV-04
  ('BD-WCC-04',    'Globe Body Casting 4"',                'purchased', 'A216 WCC', 'EA'),
  ('PG-MNL-04',    'Monel Plug 4"',                        'manufactured', 'Monel', 'EA'),
  ('CG-410-04',    'Cage 4"',                              'manufactured', '410SS', 'EA'),
  ('GS-PK-004',    'Grafoil Stem Packing Set 4"',          'purchased', null, 'EA'),
  -- V-300-CGT-06
  ('BD-CF8M-G06',  'Gate Body Casting 6"',                 'purchased', 'CF8M', 'EA'),
  ('WG-CF8M-06',   'Gate Wedge 6"',                        'manufactured', 'CF8M', 'EA'),
  ('BN-EXT-06',    'Extended Bonnet 6"',                   'manufactured', 'CF8M', 'EA'),
  ('GS-PK-006',    'Grafoil Stem Packing Set 6"',          'purchased', null, 'EA'),
  -- V-800-FGV-02
  ('BD-A105-02',   'Forged Gate Body 2"',                  'purchased', 'A105N', 'EA'),
  ('WG-13CR-02',   'Solid Wedge 2"',                       'manufactured', '13Cr', 'EA');

-- ------------------------------------------------------------------ BOM headers
insert into public.bom_headers (product_id, revision, status, effective_from, effective_to, eco_number, approved_at, notes)
select p.id, v.revision, v.status, v.eff_from::date, v.eff_to::date, v.eco, v.approved_at::timestamptz, v.notes
from (values
  ('V-600-TBV-10',  'Rev B', 'Superseded', '2025-03-01', '2025-11-01', 'ECO-84102',       '2025-02-20 10:00-06', 'Superseded by Rev C (disc springs added)'),
  ('V-600-TBV-10',  'Rev C', 'Approved',   '2025-11-01', null,         'ECO-84920-A',     '2025-10-28 10:00-06', 'Approved by E. Lindqvist (Chief Engineer, fictional)'),
  ('AMK-TBV-10',    'Rev A', 'Approved',   '2025-06-01', null,         'ECO-83310',       '2025-05-20 10:00-06', null),
  ('BRK-MNT-A36',   'Rev A', 'Approved',   '2025-06-01', null,         'ECO-83310',       '2025-05-20 10:00-06', null),
  ('V-300-FBV-08',  'Rev B', 'Approved',   '2025-06-12', null,         'ECO-84133',       '2025-06-12 10:00-06', null),
  ('V-900-MSB-14',  'Rev D', 'Approved',   '2025-09-22', null,         'ECO-84711',       '2025-09-22 10:00-06', 'ECN active'),
  ('V-150-DPC-06',  'Rev A', 'Approved',   '2025-08-02', null,         'ECO-84250',       '2025-08-02 10:00-06', 'Frozen'),
  ('V-150-DPC-04',  'Rev B', 'Approved',   '2025-07-18', null,         'ECO-84198',       '2025-07-18 10:00-06', null),
  ('V-1500-CBV-12', 'Rev B', 'Approved',   '2025-10-21', null,         'ECO-84890',       '2025-10-21 10:00-06', null),
  ('V-300-CGV-04',  'Rev C', 'Approved',   '2025-09-30', null,         'ECO-84760',       '2025-09-30 10:00-06', null),
  ('V-300-CGT-06',  'Rev A', 'Approved',   '2025-10-25', null,         'ECO-84905',       '2025-10-25 10:00-06', null),
  ('V-800-FGV-02',  'Rev A', 'Draft',      '2025-12-01', null,         'ECO-85002 (open)', null,                 'Awaiting engineering review')
) as v (product, revision, status, eff_from, eff_to, eco, approved_at, notes)
join public.products p on p.product_code = v.product;

-- BOM lines: (parent product, revision, line, component, qty/parent, uom, scrap)
insert into public.bom_items (bom_header_id, line_number, component_product_id, quantity_per_parent, unit_of_measure, scrap_factor)
select bh.id, v.line, c.id, v.qty, v.uom, v.scrap
from (values
  ('V-600-TBV-10',  'Rev B', 1, 'VB-LF2-U10',   1,    'EA', 0.02),
  ('V-600-TBV-10',  'Rev B', 2, 'VB-LF2-L10',   1,    'EA', 0.02),
  ('V-600-TBV-10',  'Rev B', 3, 'TB-316-HC10',  1,    'EA', 0.035),
  ('V-600-TBV-10',  'Rev B', 4, 'SR-ST6-10',    2,    'EA', 0.05),
  ('V-600-TBV-10',  'Rev B', 5, 'GS-PK-010',    1,    'EA', 0.01),
  ('V-600-TBV-10',  'Rev B', 6, 'AMK-TBV-10',   1,    'EA', 0),
  ('V-600-TBV-10',  'Rev C', 1, 'VB-LF2-U10',   1,    'EA', 0.02),
  ('V-600-TBV-10',  'Rev C', 2, 'VB-LF2-L10',   1,    'EA', 0.02),
  ('V-600-TBV-10',  'Rev C', 3, 'TB-316-HC10',  1,    'EA', 0.035),
  ('V-600-TBV-10',  'Rev C', 4, 'SR-ST6-10',    2,    'EA', 0.05),
  ('V-600-TBV-10',  'Rev C', 5, 'GS-PK-010',    1,    'EA', 0.01),
  ('V-600-TBV-10',  'Rev C', 6, 'DS-IN718-4',   8,    'EA', 0),
  ('V-600-TBV-10',  'Rev C', 7, 'AMK-TBV-10',   1,    'EA', 0),
  ('AMK-TBV-10',    'Rev A', 1, 'ACT-DA-400',   1,    'EA', 0),
  ('AMK-TBV-10',    'Rev A', 2, 'BRK-MNT-A36',  1,    'EA', 0.015),
  ('BRK-MNT-A36',   'Rev A', 1, 'PL-A36-12',    0.25, 'M2', 0.10),
  ('V-300-FBV-08',  'Rev B', 1, 'BD-WCB-08',    1,    'EA', 0.02),
  ('V-300-FBV-08',  'Rev B', 2, 'BL-304-08',    1,    'EA', 0.03),
  ('V-300-FBV-08',  'Rev B', 3, 'ST-PTFE-08',   2,    'EA', 0),
  ('V-300-FBV-08',  'Rev B', 4, 'GS-PK-008',    1,    'EA', 0.01),
  ('V-300-FBV-08',  'Rev B', 5, 'LV-WR-08',     1,    'EA', 0),
  ('V-900-MSB-14',  'Rev D', 1, 'BD-F22-14',    1,    'EA', 0.02),
  ('V-900-MSB-14',  'Rev D', 2, 'BL-STL-14',    1,    'EA', 0.04),
  ('V-900-MSB-14',  'Rev D', 3, 'ST-STL-14',    2,    'EA', 0.05),
  ('V-900-MSB-14',  'Rev D', 4, 'ST-IN718-M24', 24,   'EA', 0),
  ('V-900-MSB-14',  'Rev D', 5, 'GS-PK-014',    1,    'EA', 0.01),
  ('V-150-DPC-06',  'Rev A', 1, 'BD-CF8M-06',   1,    'EA', 0.02),
  ('V-150-DPC-06',  'Rev A', 2, 'PL-CF8M-06',   2,    'EA', 0.03),
  ('V-150-DPC-06',  'Rev A', 3, 'SP-IN-06',     1,    'EA', 0),
  ('V-150-DPC-04',  'Rev B', 1, 'BD-CF8M-04',   1,    'EA', 0.02),
  ('V-150-DPC-04',  'Rev B', 2, 'PL-CF8M-04',   2,    'EA', 0.03),
  ('V-150-DPC-04',  'Rev B', 3, 'SP-X750-04',   1,    'EA', 0),
  ('V-1500-CBV-12', 'Rev B', 1, 'BD-316L-12',   1,    'EA', 0.02),
  ('V-1500-CBV-12', 'Rev B', 2, 'BL-316L-12',   1,    'EA', 0.04),
  ('V-1500-CBV-12', 'Rev B', 3, 'SL-KELF-12',   2,    'EA', 0),
  ('V-1500-CBV-12', 'Rev B', 4, 'BN-EXT-12',    1,    'EA', 0.02),
  ('V-300-CGV-04',  'Rev C', 1, 'BD-WCC-04',    1,    'EA', 0.02),
  ('V-300-CGV-04',  'Rev C', 2, 'PG-MNL-04',    1,    'EA', 0.03),
  ('V-300-CGV-04',  'Rev C', 3, 'CG-410-04',    1,    'EA', 0.03),
  ('V-300-CGV-04',  'Rev C', 4, 'GS-PK-004',    1,    'EA', 0.01),
  ('V-300-CGT-06',  'Rev A', 1, 'BD-CF8M-G06',  1,    'EA', 0.02),
  ('V-300-CGT-06',  'Rev A', 2, 'WG-CF8M-06',   1,    'EA', 0.03),
  ('V-300-CGT-06',  'Rev A', 3, 'BN-EXT-06',    1,    'EA', 0.02),
  ('V-300-CGT-06',  'Rev A', 4, 'GS-PK-006',    1,    'EA', 0),
  ('V-800-FGV-02',  'Rev A', 1, 'BD-A105-02',   1,    'EA', 0.01),
  ('V-800-FGV-02',  'Rev A', 2, 'WG-13CR-02',   1,    'EA', 0.02)
) as v (parent, revision, line, component, qty, uom, scrap)
join public.products pp on pp.product_code = v.parent
join public.bom_headers bh on bh.product_id = pp.id and bh.revision = v.revision
join public.products c on c.product_code = v.component;

-- ------------------------------------------------------------------ Routings (V-800-FGV-02 has none on purpose)
insert into public.routing_headers (product_id, revision, status, effective_from, approved_at)
select p.id, v.revision, 'Approved', v.eff::date, (v.eff || ' 10:00-06')::timestamptz
from (values
  ('V-600-TBV-10',  'RT-600-09',        '2025-11-01'),
  ('V-300-FBV-08',  'RT-02-std',        '2025-06-12'),
  ('V-900-MSB-14',  'RT-09-Heavy',      '2025-09-22'),
  ('V-150-DPC-06',  'RT-01-Fast',       '2025-08-02'),
  ('V-150-DPC-04',  'RT-01-Fast',       '2025-07-18'),
  ('V-1500-CBV-12', 'RT-Cryo-HighSpec', '2025-10-21'),
  ('V-300-CGV-04',  'RT-05-Globe',      '2025-09-30'),
  ('V-300-CGT-06',  'RT-Cryo-Std',      '2025-10-25')
) as v (product, revision, eff)
join public.products p on p.product_code = v.product;

-- (product, op number, seq, name, work center, setup min, cycle min, basis, min batch, quality gate, capabilities, notes)
insert into public.routing_operations (routing_header_id, operation_number, sequence, operation_name, work_center_id,
                                       setup_time_minutes, cycle_time_minutes, time_basis, minimum_batch_quantity,
                                       is_quality_gate, required_capabilities, notes)
select rh.id, v.op, v.seq, v.name, wc.id, v.setup, v.cycle, v.basis, v.min_batch, v.gate, v.caps, v.notes
from (values
  ('V-600-TBV-10',  'OP-010', 1, 'Raw Material Ultrasonic & MPI Inspection',   'WC-40', 0,   45,  'per_batch', null::int, false, array['ut','mpi'],        'ASNT Level II certified'),
  ('V-600-TBV-10',  'OP-020', 2, 'CNC Rough & Finish Turning',                 'WC-10', 90,  140, 'per_unit',  2,         false, array['turning'],         null),
  ('V-600-TBV-10',  'OP-030', 3, 'Seat Pocket Milling & Stellite Cladding',    'WC-22', 0,   180, 'per_unit',  null,      false, array['cladding'],        'Stellite #6 overlay qualification'),
  ('V-600-TBV-10',  'OP-040', 4, 'Ball Lapping & Seat Match-Grinding',         'WC-30', 0,   75,  'per_unit',  null,      false, array['lapping'],         'Micro-finish Ra 0.2 µm'),
  ('V-600-TBV-10',  'OP-050', 5, 'Sub-Assembly Body & Seat Fit-Up',            'WC-35', 0,   120, 'per_unit',  null,      false, array['heavy-lift'],      'Requires disc spring preload rig'),
  ('V-600-TBV-10',  'OP-060', 6, 'API 598 Hydrostatic Shell & Seat Leak Test', 'WC-40', 0,   90,  'per_unit',  null,      true,  array['hydro'],           'Zero bubbles. Shell 1.5x WP, seat 1.1x WP. Needs packing lot QC sign-off'),
  ('V-600-TBV-10',  'OP-070', 7, 'Actuator Mounting & Calibration',            'WC-35', 0,   60,  'per_unit',  null,      false, array['actuation'],       'Needs actuator ACT-DA-400 in stock'),
  ('V-600-TBV-10',  'OP-080', 8, 'High-Build Epoxy Coating & Curing',          'WC-50', 0,   240, 'per_batch', 4,         false, array['epoxy'],           'Batch oven, min 4 valves'),
  ('V-600-TBV-10',  'OP-090', 9, 'Final Inspection, Tagging & ASME Stamping',  'WC-30', 0,   45,  'per_unit',  null,      true,  array['final-inspection'],'ASME B16.34 nameplate'),
  ('V-300-FBV-08',  'OP-010', 1, 'Incoming Casting Inspection',                'WC-40', 0,   20,  'per_unit',  null,      false, array['mpi'],             null),
  ('V-300-FBV-08',  'OP-035', 2, 'Bore & Face Machining',                      'WC-10', 60,  85,  'per_unit',  null,      false, array['turning'],         null),
  ('V-300-FBV-08',  'OP-055', 3, 'Ball & Seat Lapping',                        'WC-30', 0,   40,  'per_unit',  null,      false, array['lapping'],         null),
  ('V-300-FBV-08',  'OP-060', 4, 'API 598 Hydrostatic Test',                   'WC-40', 0,   45,  'per_unit',  null,      true,  array['hydro'],           null),
  ('V-300-FBV-08',  'OP-082', 5, 'Final Assembly & Operator Fit',              'WC-35', 0,   50,  'per_unit',  null,      false, array['assembly'],        null),
  ('V-300-FBV-08',  'OP-090', 6, 'Final Inspection & Packing',                 'WC-30', 0,   20,  'per_unit',  null,      true,  array['final-inspection'],null),
  ('V-900-MSB-14',  'OP-015', 1, 'Axial Body Facing',                          'WC-14', 120, 600, 'per_unit',  null,      false, array['vtl'],             'Only VT-001 qualified'),
  ('V-900-MSB-14',  'OP-050', 2, 'Flange Circumferential Weld',                'WC-22', 0,   480, 'per_unit',  null,      false, array['saw'],             'Tandem wire'),
  ('V-900-MSB-14',  'OP-055', 3, '100% Volumetric Radiography',                'WC-40', 0,   240, 'per_unit',  null,      true,  array['radiography'],     null),
  ('V-900-MSB-14',  'OP-060', 4, 'API 598 Hydrostatic Test',                   'WC-40', 0,   180, 'per_unit',  null,      true,  array['hydro'],           null),
  ('V-900-MSB-14',  'OP-090', 5, 'Final Inspection & Packing',                 'WC-30', 0,   60,  'per_unit',  null,      true,  array['final-inspection'],null),
  ('V-150-DPC-06',  'OP-030', 1, 'Disc Machining',                             'WC-10', 45,  60,  'per_unit',  null,      false, array['turning'],         null),
  ('V-150-DPC-06',  'OP-060', 2, 'API 598 Hydrostatic Test',                   'WC-40', 0,   30,  'per_unit',  null,      true,  array['hydro'],           null),
  ('V-150-DPC-06',  'OP-080', 3, 'Epoxy Coat',                                 'WC-50', 0,   240, 'per_batch', 4,         false, array['epoxy'],           null),
  ('V-150-DPC-06',  'OP-090', 4, 'Final Inspection & Packing',                 'WC-30', 0,   15,  'per_unit',  null,      true,  array['final-inspection'],null),
  ('V-150-DPC-04',  'OP-075', 1, 'Spring & Hinge Pin Assembly',                'WC-30', 0,   40,  'per_unit',  null,      false, array['packing'],         null),
  ('V-150-DPC-04',  'OP-085', 2, 'Sa 2.5 Blast',                               'WC-50', 0,   30,  'per_unit',  null,      false, array['blast'],           'Garnet'),
  ('V-150-DPC-04',  'OP-088', 3, 'C5M 3-Coat System',                          'WC-50', 0,   240, 'per_batch', 4,         false, array['epoxy'],           'Force cure'),
  ('V-150-DPC-04',  'OP-090', 4, 'Final Inspection & Packing',                 'WC-30', 0,   15,  'per_unit',  null,      true,  array['final-inspection'],null),
  ('V-1500-CBV-12', 'OP-020', 1, 'Body Turning',                               'WC-10', 90,  300, 'per_unit',  null,      false, array['turning'],         null),
  ('V-1500-CBV-12', 'OP-060', 2, 'Cryogenic Test (-196°C)',                    'WC-40', 0,   240, 'per_unit',  null,      true,  array['cryo'],            null),
  ('V-1500-CBV-12', 'OP-090', 3, 'Final Inspection & Packing',                 'WC-30', 0,   60,  'per_unit',  null,      true,  array['final-inspection'],null),
  ('V-300-CGV-04',  'OP-025', 1, 'Plug & Cage Machining',                      'WC-10', 60,  180, 'per_unit',  null,      false, array['turning'],         null),
  ('V-300-CGV-04',  'OP-045', 2, 'TIG Hardfacing',                             'WC-22', 0,   90,  'per_unit',  null,      false, array['tig'],             'Robot #2'),
  ('V-300-CGV-04',  'OP-055', 3, 'Globe Assembly',                             'WC-35', 0,   120, 'per_unit',  null,      false, array['assembly'],        null),
  ('V-300-CGV-04',  'OP-062', 4, 'Helium Leak Test',                           'WC-40', 0,   120, 'per_unit',  null,      true,  array['helium-leak'],     null),
  ('V-300-CGV-04',  'OP-090', 5, 'Final Inspection & Packing',                 'WC-30', 0,   20,  'per_unit',  null,      true,  array['final-inspection'],null),
  ('V-300-CGT-06',  'OP-020', 1, 'Body Turning',                               'WC-10', 90,  240, 'per_unit',  null,      false, array['turning'],         null),
  ('V-300-CGT-06',  'OP-060', 2, 'Cryogenic Test (-196°C)',                    'WC-40', 0,   120, 'per_unit',  null,      true,  array['cryo'],            null),
  ('V-300-CGT-06',  'OP-090', 3, 'Final Inspection & Packing',                 'WC-30', 0,   30,  'per_unit',  null,      true,  array['final-inspection'],null)
) as v (product, op, seq, name, wc_code, setup, cycle, basis, min_batch, gate, caps, notes)
join public.products p on p.product_code = v.product
join public.routing_headers rh on rh.product_id = p.id and rh.status = 'Approved'
join public.work_centers wc on wc.work_center_code = v.wc_code;

-- Linear precedence: each operation depends on the previous one in its routing.
insert into public.routing_operation_dependencies (operation_id, predecessor_id)
select id, previous_id
from (
  select id, lag(id) over (partition by routing_header_id order by sequence) as previous_id
  from public.routing_operations
) s
where previous_id is not null;

-- ------------------------------------------------------------------ Inventory
insert into public.warehouses (warehouse_code, name, location) values
  ('WH-MAIN', 'Main Stores',                       'Plant 04, Building A'),
  ('WH-DOCK', 'Dock Receiving #2 / QC Quarantine', 'Plant 04, Dock 2'),
  ('WH-YARD', 'Heavy Forging Yard',                'Plant 04, North Yard');

insert into public.inventory_balances (product_id, warehouse_id, lot_number, on_hand_quantity, reserved_quantity, quality_hold_quantity)
select p.id, w.id, v.lot, v.on_hand, v.reserved, v.hold
from (values
  ('VB-LF2-U10',   'WH-YARD', 'VB-99214-LF2', 7,   0,  0),
  ('VB-LF2-L10',   'WH-YARD', 'VB-99214-LF2', 7,   0,  0),
  ('TB-316-HC10',  'WH-MAIN', '',             7,   0,  0),
  ('SR-ST6-10',    'WH-MAIN', '',             10,  0,  0),
  ('SR-ST6-10',    'WH-DOCK', 'HT-9921-A',    12,  0,  12),   -- received on PO-8840, PMI audit pending
  ('GS-PK-010',    'WH-MAIN', '',             24,  0,  0),
  ('DS-IN718-4',   'WH-MAIN', '',             120, 0,  0),
  ('ACT-DA-400',   'WH-MAIN', '',             0,   0,  0),    -- nothing on hand: PO-8821 open
  ('BRK-MNT-A36',  'WH-MAIN', '',             8,   0,  0),
  ('PL-A36-12',    'WH-MAIN', '',             5,   0,  0),
  ('BD-WCB-08',    'WH-MAIN', '',             16,  0,  0),
  ('BL-304-08',    'WH-MAIN', '',             14,  0,  0),
  ('ST-PTFE-08',   'WH-MAIN', '',             30,  0,  0),
  ('GS-PK-008',    'WH-MAIN', '',             40,  0,  0),
  ('LV-WR-08',     'WH-MAIN', '',             20,  0,  0),
  ('BD-F22-14',    'WH-YARD', 'F22-7731',     5,   0,  0),
  ('BL-STL-14',    'WH-MAIN', '',             5,   0,  0),
  ('ST-STL-14',    'WH-MAIN', '',             9,   0,  0),
  ('ST-IN718-M24', 'WH-MAIN', '',             0,   0,  0),    -- PO-8815 in transit
  ('GS-PK-014',    'WH-MAIN', '',             6,   0,  0),
  ('BD-CF8M-06',   'WH-MAIN', '',             20,  0,  0),
  ('PL-CF8M-06',   'WH-MAIN', '',             40,  0,  0),
  ('SP-IN-06',     'WH-MAIN', '',             20,  0,  0),
  ('BD-CF8M-04',   'WH-MAIN', '',             12,  0,  0),
  ('PL-CF8M-04',   'WH-MAIN', '',             22,  0,  0),
  ('SP-X750-04',   'WH-MAIN', '',             12,  0,  0),
  ('BD-316L-12',   'WH-MAIN', 'CRY-316L-55',  3,   0,  0),
  ('BL-316L-12',   'WH-MAIN', '',             3,   0,  0),
  ('SL-KELF-12',   'WH-MAIN', '',             6,   0,  0),
  ('BN-EXT-12',    'WH-MAIN', '',             3,   0,  0),
  ('BD-WCC-04',    'WH-MAIN', '',             9,   0,  0),
  ('PG-MNL-04',    'WH-MAIN', '',             9,   0,  0),
  ('CG-410-04',    'WH-MAIN', '',             9,   0,  0),
  ('GS-PK-004',    'WH-MAIN', '',             12,  0,  0),
  ('BD-CF8M-G06',  'WH-MAIN', '',             6,   0,  0),
  ('WG-CF8M-06',   'WH-MAIN', '',             6,   0,  0),
  ('BN-EXT-06',    'WH-MAIN', '',             6,   0,  0),
  ('GS-PK-006',    'WH-MAIN', '',             10,  0,  0),
  ('BD-A105-02',   'WH-MAIN', '',             60,  20, 0)     -- 20 reserved for another (fictional) order
) as v (product, warehouse, lot, on_hand, reserved, hold)
join public.products p on p.product_code = v.product
join public.warehouses w on w.warehouse_code = v.warehouse;

-- ------------------------------------------------------------------ Purchasing
insert into public.purchase_orders (po_number, supplier_name, order_date, status, notes) values
  ('PO-8821', 'Rotork UK Ltd (fictional)',    '2025-10-08', 'Open',     'Air freight option: Nov 25 (+$650)'),
  ('PO-8840', 'Deloro Stellite (fictional)',  '2025-10-15', 'Received', 'Heat lot HT-9921-A, PMI audit pending'),
  ('PO-8815', 'Bolt Alloys Inc (fictional)',  '2025-10-20', 'Open',     'Carrier ETA 09:00'),
  ('PO-8833', 'GrafTech Seals (fictional)',   '2025-10-01', 'Received', null);

insert into public.purchase_order_items (purchase_order_id, line_number, product_id, ordered_quantity, received_quantity,
                                         expected_delivery_date, status, quality_status, warehouse_id)
select po.id, v.line, p.id, v.ordered, v.received, v.expected::date, v.status, v.quality, w.id
from (values
  ('PO-8821', 1, 'ACT-DA-400',   9,  0,  '2025-11-28', 'Open',       'Not Inspected', 'WH-DOCK'),
  ('PO-8840', 1, 'SR-ST6-10',    12, 12, '2025-11-20', 'Received',   'QC Hold',       'WH-DOCK'),
  ('PO-8815', 1, 'ST-IN718-M24', 96, 0,  '2025-11-25', 'In Transit', 'Not Inspected', 'WH-MAIN'),
  ('PO-8833', 1, 'GS-PK-008',    30, 30, '2025-11-12', 'Received',   'Released',      'WH-MAIN'),
  ('PO-8833', 2, 'GS-PK-010',    30, 30, '2025-11-12', 'Received',   'Released',      'WH-MAIN')
) as v (po_number, line, product, ordered, received, expected, status, quality, warehouse)
join public.purchase_orders po on po.po_number = v.po_number
join public.products p on p.product_code = v.product
join public.warehouses w on w.warehouse_code = v.warehouse;

-- ------------------------------------------------------------------ Demand
insert into public.sales_orders (order_number, customer_name, customer_reference, order_date, requested_delivery_date, priority, status, notes) values
  ('SO-1005', 'Shell Offshore GOM (fictional)',   'PO-2025-9981', '2025-10-06', '2025-11-30', 'Critical', 'Planning',      'Strict contractual late penalties'),
  ('SO-1008', 'Chevron Pascagoula (fictional)',   'PO-CHV-4410',  '2025-10-14', '2025-12-08', 'Normal',   'Planned',       null),
  ('SO-1012', 'ExxonMobil Baytown (fictional)',   'PO-XOM-7734',  '2025-09-30', '2025-12-15', 'High',     'Planning',      'LD clause 0.5%/day'),
  ('SO-1014', 'BP Whiting Ref (fictional)',       'PO-BP-8802',   '2025-10-20', '2025-12-22', 'Normal',   'In Production', null),
  ('SO-1015', 'Woodside Energy (fictional)',      'PO-WDS-3307',  '2025-10-02', '2025-12-12', 'Normal',   'In Production', null),
  ('SO-1017', 'ConocoPhillips AK (fictional)',    'PO-COP-1029',  '2025-10-28', '2026-01-12', 'Normal',   'Confirmed',     'Arctic spec'),
  ('SO-1019', 'ExxonMobil Beaumont (fictional)',  'PO-XOM-7790',  '2025-10-09', '2025-12-04', 'High',     'Planning',      null),
  ('SO-1020', 'Chevron Deepwater (fictional)',    'PO-CHV-4471',  '2025-10-30', '2025-12-18', 'Normal',   'Confirmed',     null),
  ('SO-1021', 'Shell Deer Park (fictional)',      'PO-SDP-0042',  '2025-11-10', '2026-01-05', 'Normal',   'Draft',         'Blocked: no approved BOM / routing'),
  ('SO-1022', 'Equinor Mongstad (fictional)',     'PO-EQN-5512',  '2025-11-03', '2025-12-30', 'Low',      'Confirmed',     'Competes with SO-1005 for PO-8821 actuators');

-- Lines take the product's approved BOM/routing when one exists (SO-1021 gets none → needs data).
insert into public.sales_order_items (sales_order_id, line_number, product_id, product_configuration, bom_revision_id,
                                      routing_revision_id, quantity, unit_price, requested_delivery_date, status, notes)
select so.id, v.line, p.id, v.config::jsonb, bh.id, rh.id, v.qty, v.price, so.requested_delivery_date,
       so.status, v.notes
from (values
  ('SO-1005', 1, 'V-600-TBV-10',  4,  81000,   '{"tags":"XV-2101-A..D"}',        'Production master runs'),
  ('SO-1005', 2, 'V-600-TBV-10',  2,  81000,   '{"tags":"XV-2101-SP1/SP2"}',     'Subsea spares, held for batch pack'),
  ('SO-1008', 1, 'V-300-FBV-08',  12, 14000,   '{"tags":"FB-8300-C01..C12"}',    null),
  ('SO-1012', 1, 'V-900-MSB-14',  4,  98000,   '{"tags":"ESDV-0091-HT..HT4"}',   null),
  ('SO-1014', 1, 'V-150-DPC-06',  8,  5388.89, '{"tags":"N-NRV-04-01..08"}',     'Unit 4'),
  ('SO-1014', 2, 'V-150-DPC-06',  6,  5388.89, '{"tags":"N-NRV-05-01..06"}',     'Unit 5'),
  ('SO-1014', 3, 'V-150-DPC-06',  4,  5388.89, '{"tags":"N-NRV-SP"}',            'Spares'),
  ('SO-1015', 1, 'V-150-DPC-04',  10, 7400,    '{"tags":"LNG-DPC-04-01..10"}',   null),
  ('SO-1017', 1, 'V-1500-CBV-12', 2,  119000,  '{"tags":"CRYO-VL-02-A/B"}',      null),
  ('SO-1019', 1, 'V-300-CGV-04',  8,  11000,   '{"tags":"FV-4410-01..08"}',      null),
  ('SO-1020', 1, 'V-300-CGT-06',  5,  24200,   '{"tags":"CG-0611-01..05"}',      null),
  ('SO-1021', 1, 'V-800-FGV-02',  40, 1150,    '{"tags":"HV-0200-01..40"}',      null),
  ('SO-1022', 1, 'V-600-TBV-10',  3,  81000,   '{"tags":"XV-3300-A..C"}',        null)
) as v (order_number, line, product, qty, price, config, notes)
join public.sales_orders so on so.order_number = v.order_number
join public.products p on p.product_code = v.product
left join public.bom_headers bh on bh.product_id = p.id and bh.status = 'Approved'
left join public.routing_headers rh on rh.product_id = p.id and rh.status = 'Approved';

-- ------------------------------------------------------------------ Planning runs & schedule versions
insert into public.planning_runs (run_number, run_type, planning_horizon_start, planning_horizon_end, input_snapshot,
                                  scenario_assumptions, algorithm_version, solver_status, solve_seconds, started_at, completed_at)
values
  (1038, 'schedule',   '2025-11-15', '2025-12-15', '{"source":"seed","fictional":true}', '{}',
   'seed-demo (pre-solver)', 'Feasible with lateness', 3.9, '2025-11-17 05:40:00-06', '2025-11-17 05:40:04-06'),
  (1042, 'simulation', '2025-11-15', '2025-12-15', '{"source":"seed","fictional":true}',
   '{"expedite":{"po_number":"PO-8821","new_expected_date":"2025-11-25","cost_usd":650},
     "overtime":{"machine_code":"TE-001","starts_at":"2025-11-29T06:00:00-06:00","ends_at":"2025-11-29T14:00:00-06:00","cost_usd":840}}',
   'seed-demo (pre-solver)', 'Feasible with lateness', 4.2, '2025-11-20 14:12:00-06', '2025-11-20 14:12:04-06');

insert into public.schedule_versions (planning_run_id, version_number, label, status, is_baseline, change_summary,
                                      metrics, validation_passed, approved_at)
select pr.id, '4.2', 'Version 4.2 [Baseline Active]', 'Approved', true, 'Weekly baseline',
       '{"late_orders":2,"total_lateness_hours":192,"overtime_cost":0,"hard_violations":0,"soft_violations":2}',
       true, '2025-11-17 08:00-06'
from public.planning_runs pr where pr.run_number = 1038;

insert into public.schedule_versions (planning_run_id, version_number, label, status, is_baseline, based_on_version_id,
                                      change_summary, metrics, validation_passed)
select pr.id, '4.3', 'Version 4.3 [Scenario C — Candidate]', 'Proposed', false, base.id,
       'Expedite PO-8821 to Nov 25 (air freight) + Saturday OT shift on Hydro Rig 1',
       '{"late_orders":1,"total_lateness_hours":120,"overtime_cost":1490,"hard_violations":0,"soft_violations":1}', true
from public.planning_runs pr, public.schedule_versions base
where pr.run_number = 1042 and base.version_number = '4.2';

-- ------------------------------------------------------------------ Production orders (one set per version)
insert into public.production_orders (production_order_number, schedule_version_id, sales_order_item_id, product_id,
                                      quantity, status, planned_start, planned_finish, actual_start)
select v.po_number, sv.id, soi.id, soi.product_id, soi.quantity, v.status, v.planned_start::timestamptz,
       case when sv.version_number = '4.3' and v.order_number = 'SO-1005'
            then '2025-11-29 23:30-06'::timestamptz else v.planned_finish::timestamptz end,
       v.actual_start::timestamptz
from (values
  ('PRD-1005-1', 'SO-1005', 1, 'Planned',     '2025-11-17 06:00-06', '2025-12-03 18:00-06', null),
  ('PRD-1005-2', 'SO-1005', 2, 'Planned',     '2025-11-17 06:00-06', '2025-12-03 18:00-06', null),
  ('PRD-1008-1', 'SO-1008', 1, 'Planned',     '2025-11-18 06:00-06', '2025-12-06 14:00-06', null),
  ('PRD-1012-1', 'SO-1012', 1, 'Planned',     '2025-11-17 06:00-06', '2025-12-20 14:00-06', null),
  ('PRD-1014-1', 'SO-1014', 1, 'In Progress', '2025-11-05 06:00-06', '2025-12-22 14:00-06', '2025-11-05 06:00-06'),
  ('PRD-1014-2', 'SO-1014', 2, 'Planned',     '2025-11-24 06:00-06', '2025-12-22 14:00-06', null),
  ('PRD-1014-3', 'SO-1014', 3, 'Planned',     '2025-12-01 06:00-06', '2025-12-22 14:00-06', null),
  ('PRD-1015-1', 'SO-1015', 1, 'In Progress', '2025-11-18 06:00-06', '2025-12-08 14:00-06', '2025-11-18 06:00-06'),
  ('PRD-1017-1', 'SO-1017', 1, 'Planned',     '2025-12-08 06:00-06', '2026-01-09 14:00-06', null),
  ('PRD-1019-1', 'SO-1019', 1, 'Planned',     '2025-11-19 06:00-06', '2025-12-04 14:00-06', null),
  ('PRD-1020-1', 'SO-1020', 1, 'Planned',     '2025-11-24 06:00-06', '2025-12-16 14:00-06', null),
  ('PRD-1022-1', 'SO-1022', 1, 'Planned',     '2025-12-08 06:00-06', '2025-12-23 14:00-06', null)
) as v (po_number, order_number, line, status, planned_start, planned_finish, actual_start)
join public.sales_orders so on so.order_number = v.order_number
join public.sales_order_items soi on soi.sales_order_id = so.id and soi.line_number = v.line
cross join public.schedule_versions sv;

-- Operations inside the Nov 17 – Dec 5 Gantt window (plus two completed ones for SO-1014).
-- ver: 'both' | '4.2' | '4.3'
insert into public.production_operations (production_order_id, routing_operation_id, operation_number, quantity, status,
                                          planned_start, planned_finish, actual_start, actual_finish,
                                          assigned_machine_id, notes)
select pro.id, ro.id, v.op, pro.quantity, v.status, v.starts::timestamptz, v.ends::timestamptz,
       case when v.status = 'Completed' then v.starts::timestamptz end,
       case when v.status = 'Completed' then v.ends::timestamptz end,
       m.id, v.notes
from (values
  ('both', 'PRD-1005-1', 'OP-020', 'MC-010', '2025-11-17 06:00-06', '2025-11-18 18:00-06', 'Planned',   '36h • Setup cleared'),
  ('both', 'PRD-1005-1', 'OP-030', 'WL-002', '2025-11-19 06:00-06', '2025-11-20 18:00-06', 'Planned',   'PTA arc'),
  ('both', 'PRD-1005-1', 'OP-040', 'SA-008', '2025-11-21 06:00-06', '2025-11-21 22:00-06', 'Planned',   'Ra 0.2 µm'),
  ('both', 'PRD-1005-1', 'OP-050', 'FA-001', '2025-11-24 06:00-06', '2025-11-25 18:00-06', 'Planned',   'Disc spring preload'),
  ('4.2',  'PRD-1005-1', 'OP-060', 'TE-001', '2025-12-01 06:00-06', '2025-12-01 18:00-06', 'Planned',   'Rig free Dec 1'),
  ('4.2',  'PRD-1005-1', 'OP-070', 'FA-001', '2025-12-02 06:00-06', '2025-12-02 14:00-06', 'Planned',   'Needs PO-8821 (ETA Nov 28)'),
  ('4.2',  'PRD-1005-1', 'OP-080', 'SF-003', '2025-12-02 14:00-06', '2025-12-03 10:00-06', 'Planned',   'Batch oven'),
  ('4.2',  'PRD-1005-1', 'OP-090', 'SA-012', '2025-12-03 10:00-06', '2025-12-03 18:00-06', 'Planned',   'Ships Dec 3'),
  ('4.3',  'PRD-1005-1', 'OP-060', 'TE-001', '2025-11-29 06:00-06', '2025-11-29 12:00-06', 'Planned',   'Saturday OT shift'),
  ('4.3',  'PRD-1005-1', 'OP-070', 'FA-001', '2025-11-29 12:00-06', '2025-11-29 16:00-06', 'Planned',   'PO-8821 expedited to Nov 25'),
  ('4.3',  'PRD-1005-1', 'OP-080', 'SF-003', '2025-11-29 16:00-06', '2025-11-29 21:00-06', 'Planned',   'Force cure'),
  ('4.3',  'PRD-1005-1', 'OP-090', 'SA-012', '2025-11-29 21:00-06', '2025-11-29 23:30-06', 'Planned',   'Ships Nov 29'),
  ('both', 'PRD-1008-1', 'OP-035', 'MC-014', '2025-11-18 06:00-06', '2025-11-19 14:00-06', 'Planned',   null),
  ('both', 'PRD-1008-1', 'OP-055', 'SA-008', '2025-11-24 06:00-06', '2025-11-25 10:00-06', 'Planned',   null),
  ('both', 'PRD-1008-1', 'OP-060', 'TE-001', '2025-11-26 06:00-06', '2025-11-27 14:00-06', 'Planned',   '12 valves'),
  ('both', 'PRD-1008-1', 'OP-082', 'FA-002', '2025-11-27 14:00-06', '2025-11-28 22:00-06', 'Planned',   null),
  ('both', 'PRD-1012-1', 'OP-015', 'VT-001', '2025-11-17 06:00-06', '2025-11-21 18:00-06', 'Planned',   'Only qualified machine'),
  ('both', 'PRD-1012-1', 'OP-050', 'WL-005', '2025-11-24 06:00-06', '2025-11-25 22:00-06', 'Planned',   '40h • Tandem wire'),
  ('both', 'PRD-1012-1', 'OP-055', 'ND-001', '2025-11-26 06:00-06', '2025-11-27 06:00-06', 'Planned',   null),
  ('both', 'PRD-1012-1', 'OP-060', 'TE-001', '2025-11-27 14:00-06', '2025-11-28 22:00-06', 'Planned',   '4 valves'),
  ('both', 'PRD-1014-1', 'OP-030', 'MC-014', '2025-11-05 06:00-06', '2025-11-06 18:00-06', 'Completed', null),
  ('both', 'PRD-1014-1', 'OP-060', 'TE-001', '2025-11-13 06:00-06', '2025-11-13 18:00-06', 'Completed', null),
  ('both', 'PRD-1014-1', 'OP-080', 'SF-003', '2025-12-01 06:00-06', '2025-12-02 10:00-06', 'Planned',   'Unit 4 in paint'),
  ('both', 'PRD-1014-2', 'OP-030', 'MC-014', '2025-11-24 06:00-06', '2025-11-26 18:00-06', 'Planned',   'Unit 5'),
  ('both', 'PRD-1015-1', 'OP-075', 'SA-012', '2025-11-18 06:00-06', '2025-11-19 14:00-06', 'Planned',   null),
  ('both', 'PRD-1015-1', 'OP-085', 'SF-001', '2025-11-24 06:00-06', '2025-11-24 18:00-06', 'Planned',   'Garnet'),
  ('both', 'PRD-1015-1', 'OP-088', 'SF-003', '2025-11-25 06:00-06', '2025-11-26 18:00-06', 'Planned',   'Force cure'),
  ('both', 'PRD-1019-1', 'OP-025', 'MC-010', '2025-11-19 06:00-06', '2025-11-21 14:00-06', 'Planned',   null),
  ('both', 'PRD-1019-1', 'OP-045', 'WL-002', '2025-11-21 14:00-06', '2025-11-21 22:00-06', 'Planned',   'Robot #2'),
  ('both', 'PRD-1019-1', 'OP-055', 'FA-002', '2025-11-24 06:00-06', '2025-11-26 14:00-06', 'Planned',   null),
  ('both', 'PRD-1019-1', 'OP-062', 'TE-004', '2025-12-01 06:00-06', '2025-12-02 14:00-06', 'Planned',   null),
  ('both', 'PRD-1020-1', 'OP-020', 'MC-010', '2025-11-24 06:00-06', '2025-11-26 18:00-06', 'Planned',   null),
  ('both', 'PRD-1020-1', 'OP-060', 'TE-004', '2025-11-28 06:00-06', '2025-11-28 18:00-06', 'Planned',   '-196°C')
) as v (ver, po_number, op, machine, starts, ends, status, notes)
join public.schedule_versions sv on v.ver = 'both' or sv.version_number = v.ver
join public.production_orders pro on pro.schedule_version_id = sv.id and pro.production_order_number = v.po_number
join public.sales_order_items soi on soi.id = pro.sales_order_item_id
join public.routing_operations ro on ro.routing_header_id = soi.routing_revision_id and ro.operation_number = v.op
join public.machines m on m.machine_code = v.machine;

-- ------------------------------------------------------------------ Alerts & approvals
insert into public.planning_alerts (planning_run_id, sales_order_item_id, alert_type, severity, description, recommended_action, details)
select pr.id, soi.id, v.alert_type, v.severity, v.description, v.action, v.details::jsonb
from (values
  ('SO-1005', 'late_supplier_receipt', 'critical',
   'PO-8821 (ACT-DA-400) expected 2025-11-28; required 2025-11-24 for OP-070 actuator mounting (+4 days).',
   'Expedite PO-8821 by air freight to 2025-11-25 (+$650).', '{"po_number":"PO-8821","component":"ACT-DA-400"}'),
  ('SO-1005', 'material_shortage', 'critical',
   'SR-ST6-10 seat rings short 3: need 13 (6 valves × 2 ÷ 0.95 yield), 10 available; 12 more on QC hold.',
   'Expedite PMI audit for lot HT-9921-A.', '{"component":"SR-ST6-10","required":13,"available":10,"shortage":3}'),
  ('SO-1005', 'quality_hold', 'warning',
   'PO-8840 lot HT-9921-A (12 × SR-ST6-10) on QC hold — PMI audit pending.',
   'Schedule metrology / PMI review.', '{"po_number":"PO-8840","lot":"HT-9921-A"}'),
  ('SO-1005', 'delivery_date_at_risk', 'critical',
   'Planned completion 2025-12-03 vs requested 2025-11-30 (+3 days).',
   'Simulate expedite + Saturday overtime on TE-001 (scenario v4.3).', '{"lateness_days":3}'),
  ('SO-1012', 'delivery_date_at_risk', 'critical',
   'Planned completion 2025-12-20 vs requested 2025-12-15 (+5 days); VT-001 is the only qualified machine for OP-015.',
   'Evaluate offloading OP-015 roughing to MC-010.', '{"lateness_days":5}'),
  ('SO-1021', 'missing_bom', 'warning',
   'V-800-FGV-02 has only a Draft BOM (Rev A, ECO-85002 open).',
   'Request ECO-85002 review and approval.', '{}'),
  ('SO-1021', 'missing_routing', 'warning',
   'No routing exists for V-800-FGV-02.',
   'Engineering to author and approve a routing.', '{}')
) as v (order_number, alert_type, severity, description, action, details)
join public.sales_orders so on so.order_number = v.order_number
join public.sales_order_items soi on soi.sales_order_id = so.id and soi.line_number = 1
cross join public.planning_runs pr
where pr.run_number = 1038;

insert into public.planning_alerts (planning_run_id, alert_type, severity, description, recommended_action, details)
select pr.id, 'capacity_overload', 'critical',
       'WC-40 Hydrostatic & Cryo Pressure Cell loaded to 96% of 2-shift capacity; TE-001 pump overhaul 2025-11-24/25.',
       'Approve one Saturday overtime shift on TE-001.', '{"work_center":"WC-40","utilization_pct":96}'
from public.planning_runs pr where pr.run_number = 1038;

insert into public.approval_requests (request_number, schedule_version_id, status, requested_at)
select 'APR-1042', sv.id, 'Pending', '2025-11-20 14:12-06'
from public.schedule_versions sv where sv.version_number = '4.3';

-- ------------------------------------------------------------------ M06 additions
-- Which routing operation consumes each V-600 BOM line (drives "shortage blocked" on the Gantt).
update public.bom_items bi set operation_reference = v.op
from (values (1, 'OP-020'), (2, 'OP-020'), (3, 'OP-040'), (4, 'OP-030'), (5, 'OP-050'), (6, 'OP-050'), (7, 'OP-070')) as v (line, op),
     public.bom_headers bh, public.products p
where bi.bom_header_id = bh.id and bh.product_id = p.id and p.product_code = 'V-600-TBV-10'
  and bh.revision = 'Rev C' and bi.line_number = v.line;
update public.bom_items bi set operation_reference = v.op
from (values (1, 'OP-020'), (2, 'OP-020'), (3, 'OP-040'), (4, 'OP-030'), (5, 'OP-050'), (6, 'OP-070')) as v (line, op),
     public.bom_headers bh, public.products p
where bi.bom_header_id = bh.id and bh.product_id = p.id and p.product_code = 'V-600-TBV-10'
  and bh.revision = 'Rev B' and bi.line_number = v.line;

-- Capacity outputs of the (seeded, pre-solver) planning runs. M09 recomputes these from real schedules.
update public.schedule_versions set metrics = metrics || '{
  "load_source": "seed (fictional) — recomputed by the solver from M09",
  "work_center_load": {
    "WC-10": {"scheduled_hours": 187.2, "capacity_hours": 240, "queue_note": "Queue: 3 Pallets • Avg Setup: 42m"},
    "WC-14": {"scheduled_hours": 148.8, "capacity_hours": 240, "queue_note": "Queue: 1 Flange casting • Tooling OK"},
    "WC-22": {"scheduled_hours": 211.2, "capacity_hours": 240, "queue_note": "Queue: 4 Seat pockets • Shield gas: 98%"},
    "WC-30": {"scheduled_hours": 170.4, "capacity_hours": 240, "queue_note": "3 Crews manned • Torque cal: Active"},
    "WC-35": {"scheduled_hours": 196.8, "capacity_hours": 240, "queue_note": "Bay A crane booked 06:00–14:00"},
    "WC-40": {"scheduled_hours": 230.4, "capacity_hours": 240, "queue_note": "Critical Backlog: 7 valves • Wait Time: 14.2h"},
    "WC-50": {"scheduled_hours": 129.6, "capacity_hours": 240, "queue_note": "Cure Oven: Running Nominally"}},
  "machine_utilization": {"MC-010": 94, "MC-014": 78, "VT-001": 62, "WL-002": 88, "WL-005": 65, "SA-008": 72, "SA-012": 81,
                          "FA-001": 96, "FA-002": 60, "TE-001": 100, "TE-004": 50, "ND-001": 40, "SF-001": 55, "SF-003": 70}
}'::jsonb where version_number = '4.2';
update public.schedule_versions set metrics = metrics || '{
  "load_source": "seed (fictional) — recomputed by the solver from M09",
  "work_center_load": {
    "WC-10": {"scheduled_hours": 187.2, "capacity_hours": 240, "queue_note": "Queue: 3 Pallets • Avg Setup: 42m"},
    "WC-14": {"scheduled_hours": 148.8, "capacity_hours": 240, "queue_note": "Queue: 1 Flange casting • Tooling OK"},
    "WC-22": {"scheduled_hours": 211.2, "capacity_hours": 240, "queue_note": "Queue: 4 Seat pockets • Shield gas: 98%"},
    "WC-30": {"scheduled_hours": 172.9, "capacity_hours": 240, "queue_note": "3 Crews manned • Torque cal: Active"},
    "WC-35": {"scheduled_hours": 200.8, "capacity_hours": 240, "queue_note": "Bay A crane booked 06:00–14:00"},
    "WC-40": {"scheduled_hours": 236.4, "capacity_hours": 248, "queue_note": "Saturday OT shift on TE-001 (+8h capacity)"},
    "WC-50": {"scheduled_hours": 134.6, "capacity_hours": 240, "queue_note": "Cure Oven: Running Nominally"}},
  "machine_utilization": {"MC-010": 94, "MC-014": 78, "VT-001": 62, "WL-002": 88, "WL-005": 65, "SA-008": 72, "SA-012": 83,
                          "FA-001": 97, "FA-002": 60, "TE-001": 100, "TE-004": 50, "ND-001": 40, "SF-001": 55, "SF-003": 73}
}'::jsonb where version_number = '4.3';

-- (inventory quantities include scrap allowance so only the story's shortages remain)
-- Required dates that MRP will compute in M08 (needed to show supplier slippage now).
update public.planning_alerts set details = details || '{"required_date": "2025-11-24"}'::jsonb
where alert_type = 'late_supplier_receipt' and details ->> 'po_number' = 'PO-8821';
update public.planning_alerts set details = details || '{"required_date": "2025-11-19"}'::jsonb
where alert_type = 'quality_hold' and details ->> 'po_number' = 'PO-8840';
