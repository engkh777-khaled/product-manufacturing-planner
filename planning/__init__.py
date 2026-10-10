"""Deterministic planning engine (spec §7–§8). Pure Python: no web framework, no database access.

The planning engine calculates; the AI explains (spec §1). Inputs are plain data built from a
`public.planning_snapshot()` document, so the same engine runs in the app, in the CLI and in tests.
"""
