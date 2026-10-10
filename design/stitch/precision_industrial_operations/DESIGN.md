---
name: Precision Industrial Operations
colors:
  surface: '#0b1326'
  surface-dim: '#0b1326'
  surface-bright: '#31394d'
  surface-container-lowest: '#060e20'
  surface-container-low: '#131b2e'
  surface-container: '#171f33'
  surface-container-high: '#222a3d'
  surface-container-highest: '#2d3449'
  on-surface: '#dae2fd'
  on-surface-variant: '#bfc7d2'
  inverse-surface: '#dae2fd'
  inverse-on-surface: '#283044'
  outline: '#89929b'
  outline-variant: '#3f4850'
  surface-tint: '#93ccff'
  primary: '#93ccff'
  on-primary: '#003351'
  primary-container: '#3198dc'
  on-primary-container: '#002c47'
  inverse-primary: '#006398'
  secondary: '#94ccff'
  on-secondary: '#003352'
  secondary-container: '#0168a0'
  on-secondary-container: '#c9e3ff'
  tertiary: '#7bd0ff'
  on-tertiary: '#00354a'
  tertiary-container: '#009bd1'
  on-tertiary-container: '#002d40'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#cce5ff'
  primary-fixed-dim: '#93ccff'
  on-primary-fixed: '#001d31'
  on-primary-fixed-variant: '#004b73'
  secondary-fixed: '#cde5ff'
  secondary-fixed-dim: '#94ccff'
  on-secondary-fixed: '#001d32'
  on-secondary-fixed-variant: '#004b74'
  tertiary-fixed: '#c4e7ff'
  tertiary-fixed-dim: '#7bd0ff'
  on-tertiary-fixed: '#001e2c'
  on-tertiary-fixed-variant: '#004c69'
  background: '#0b1326'
  on-background: '#dae2fd'
  surface-variant: '#2d3449'
typography:
  headline-xl:
    fontFamily: Geist
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 34px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Geist
    fontSize: 22px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Geist
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 22px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Geist
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  body-sm:
    fontFamily: Geist
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
    letterSpacing: 0em
  label-mono-lg:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: -0.01em
  label-mono-sm:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.02em
  label-mono-xs:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '600'
    lineHeight: 12px
    letterSpacing: 0.04em
  metric-display:
    fontFamily: JetBrains Mono
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 28px
    letterSpacing: -0.03em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 0.75rem
  gutter-mobile: 0.5rem
  margin: 1rem
  margin-mobile: 0.75rem
  space-xs: 0.125rem
  space-sm: 0.25rem
  space-md: 0.5rem
  space-lg: 0.75rem
  space-xl: 1rem
---

## Brand & Style

The design system is architected for mission-critical industrial manufacturing operations, specifically AI-assisted valve manufacturing, discrete assembly scheduling, and supply chain telemetry. It targets production planners, plant supervisors, and operations directors who require immediate cognitive clarity under high data densities.

The brand archetype embodies **Precision Industrialism**: rigorous, deterministic, disciplined, and frictionless. The interface functions like a calibrated digital workbench or high-end machine control unit (MCU), prioritizing functional density over decorative whitespace.

Visual tenets:
- **Zero Ambiguity:** Strict information hierarchies where operational states, machine capacities, and material shortages are instantly legible at a distance or across multi-monitor control desks.
- **Instrument-Grade Finish:** Subtle matte finishes, low-contrast panel separation, hairline borders, and subdued backgrounds that minimize eye strain during 10-hour supervisory shifts.
- **Technical Authenticity:** Explicit contrast between contextual UI typography and rigid monospaced data strings (BOM revisions, serials, heat lot numbers, and routing steps).

## Colors

The system uses a calibrated dark mode palette built upon slate and zinc tones to prevent fatigue under fluctuating factory floor and control room illumination.

### Functional Roles
- **Primary & Interactive (`#0284c7`, `#0369a1`):** Cobalt and precision cyan indicate active selections, primary system actions, interactive Gantt drag-handles, and AI-suggested optimization paths.
- **Surface & Structural Neutrals (`#090d16` to `#334155`):** Deep slate-blue base canvases (`#090d16`), elevated workbench panels (`#0f172a`), secondary toolbars (`#1e293b`), and precision structural hairlines (`#334155`).
- **Semantic State Identifiers:**
  - **Bottleneck / Shortage Warning (`#f59e0b`):** Material lead-time variances, pending tool wear, and non-blocking line starvation.
  - **Critical Failure / Capacity Breach (`#ef4444`):** Late valve assembly dispatches, unassigned work centers, and machine tool downtime.
  - **Optimal / Feasible State (`#10b981`):** Validated bill of materials (BOM), cleared quality inspections, and on-schedule batch runs.
  - **Auxiliary Telemetry (`#a855f7`):** AI routing inferences, automated sequencing proposals, and synthetic schedule overlays.

Semantic colors must be paired with neutral backgrounds using high-contrast tints or desaturated container backdrops (e.g., `#ef44441a`) to preserve readability without overwhelming the layout.

## Typography

Typography establishes an uncompromising hierarchy between operational narrative and hard technical parameters.

- **Primary UI Text (Geist):** Used for navigation, layout sectioning, operational tooltips, modal dialogs, and analytical summaries. Chosen for its condensed geometric clarity and neutral legibility at sub-14px scales.
- **Data & Telemetry Tokens (JetBrains Mono):** Mandatory for part numbers (e.g., `VLV-904-CF8M`), routing codes (`OP-310-CNC`), work-order identifiers, timestamps, OEE percentages, and tolerance specs.
- **Tabular Alignment:** All numeric expressions and monospaced indicators must leverage tabular figure alignment (`tnum`) to eliminate layout jitter during live-streamed production run updates.

## Layout & Spacing

The layout is built for data density using a compact 4px structural baseline grid, maximizing vertical screen area for schedules, routing trees, and tabular telemetry.

### Split-Pane Multi-Tier Architecture
- **Navigation Rail:** Persistent 56px collapsed utility icon rail on the extreme left.
- **Master-Detail Workspace:** Primary pane (60–70% width) houses the Gantt scheduler or machine utilization matrix; secondary contextual inspector pane (30–40% width) houses BOM trees, AI dispatch logic, and batch diagnostics.
- **Variable Viewports:**
  - **Desktop (>= 1440px):** 16-column flexible layout, nested sub-panes with resizable split-dividers, 12px gutters, and 16px peripheral margins.
  - **Workstation / Tablet (768px – 1439px):** 8-column layout, inspector panel converts to a right-aligned sliding drawer overlay.
  - **Shopfloor Mobile (< 768px):** Single-column stacked mode with tabbed toggles between Timeline, Alert Ledger, and Work-Order Queue. Margins collapse to 12px.

## Elevation & Depth

To sustain optical sharpness across complex grids, depth is established using **low-contrast outlines and tonal containment** rather than drop shadows.

- **Surface Tiers:**
  - **Base Canvas:** Deepest dark level (`#090d16`), used behind structural frameworks and margins.
  - **Panel Layer 1:** Standard workspace tiles, unselected table rows, and neutral Gantt tracks (`#0f172a`).
  - **Panel Layer 2:** Active inspectors, flyout panels, and hovering flyovers (`#1e293b`).
  - **Floating Overlays:** AI contextual recommendation modals and drop-down menus (`#334155`).
- **Hairline Borders:** Surfaces are isolated using 1px solid borders (`#1e293b` on base, `#334155` on active panels). Diffuse shadows are strictly confined to floating operational modals (0px 8px 24px rgba(0, 0, 0, 0.45)).
- **Tactile Indicators:** Inactive items remain completely flush; active or dragging timeline bars receive a 1px border highlight (`#38bdf8`) with zero vertical translation.

## Shapes

The design system enforces a compact, industrial corner geometry (`0.25rem` / `4px` maximum radius) to evoke precision-milled physical hardware.

- **Standard Elements (4px Radius):** Buttons, input fields, timeline job blocks, status chips, inspector cards, and table cell containers.
- **Utility Indicators (2px Radius):** Sub-status micro-pills, timeline phase markers, and work center utilization indicators.
- **Zero Radius (0px):** Outer viewport frames, pinned split-screen vertical dividers, table borders, and nested data cell grids to preserve strict tabular alignment.

## Components

### Buttons & Quick Triggers
- **Primary Action (Commit / Reschedule):** High-density cobalt background (`#0284c7`), solid white text, 4px border radius, 28px height, 12px horizontal padding. Hover state shifts to `#0369a1`.
- **Secondary / Operational:** Dark slate background (`#1e293b`), 1px outline (`#334155`), text color (`#e2e8f0`).
- **Critical Override:** Outlined red tone (`#ef444433`) with `#ef4444` label text, shifting to solid red fill only on active confirmation.

### Data Tables & BOM Lists
- Row height fixed at 32px for high-density viewing (collapsible to 28px in compact mode).
- Header row styled in `#090d16` with uppercase JetBrains Mono labels (`label-mono-xs`), 1px bottom border in `#334155`.
- Data rows feature subtle zebra striping (`#0f172a` alternating with `#131d31`) and an immediate 1px left accent border on row selection.
- Numerical columns and status codes are strictly right-aligned or centered using monospaced values.

### Micro-Status Badges
- Ultra-compact indicators (18px max height, 6px horizontal padding) using `label-mono-xs`.
- Status configurations:
  - **Shortage:** 10% amber fill (`#f59e0b1a`), 1px amber border (`#f59e0b4d`), `#f59e0b` text.
  - **Overload:** 10% red fill (`#ef44441a`), 1px red border (`#ef44444d`), `#ef4444` text.
  - **Cleared / In-Spec:** 10% emerald fill (`#10b9811a`), 1px emerald border (`#10b9814d`), `#10b981` text.

### Gantt Timeline Bars & Work-Center Cells
- 20px height track elements contained within horizontal row tracks.
- Primary schedule duration blocks use solid `#0369a1` with high-contrast text (`label-mono-xs`).
- Constrained buffer times are visually represented with diagonal hash striping.
- Critical path dependencies are visualized using 1px stepped orthogonal vectors (`#38bdf8`) with directional arrowheads.

### Form Inputs & Micro Controls
- Height standardized to 28px. Dark background (`#0b0f19`), 1px structural border (`#334155`). Focus ring is a crisp 1px `#0284c7` without glow dispersion.
- Quantity and machine incrementers feature integrated inline monospaced spinners.