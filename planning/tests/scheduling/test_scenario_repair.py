"""Scenario repair (D-032 follow-up): untouched lines keep their reference slots, so a what-if comparison only shows
what the scenario causes — even when a time-limited solve moved them."""

from __future__ import annotations

from planning.scheduling.calendar import start_pieces
from planning.scheduling.solver import Tables, _snap_to_reference, _todo, build_grid
from planning.tests.scheduling.factory import at, job, machine, make_input, op


def setup(hint=True):
    a = job("SO-1", [op("OP-010", "WC-10", 4, "turning")], 5, baseline={"OP-010": ("MC-1", at(0, 10), at(0, 14))})
    b = job("SO-2", [op("OP-010", "WC-10", 4, "turning")], 5, baseline={"OP-010": ("MC-1", at(0, 14), at(0, 18))})
    inp = make_input([machine("MC-1", "WC-10", "turning")], [a, b], hint_from_baseline=hint)
    grid = build_grid(inp)
    tables = Tables(inp, grid)
    pieces = {(j.key, o.number): {"MC-1": start_pieces(*tables.table(j, o.number, "MC-1"), tables.release(j, o.number))}
              for j in inp.jobs for o in _todo(j)}
    return inp, grid, tables, pieces


def test_a_line_moved_without_reason_goes_back_to_its_reference_slot():
    inp, grid, tables, pieces = setup()
    slot = lambda h0, h1: ("MC-1", grid.floor(at(0, h0)), grid.floor(at(0, h1)))  # noqa: E731
    moved = {("SO-1/1", "OP-010"): slot(6, 10), ("SO-2/1", "OP-010"): slot(14, 18)}   # SO-1 pulled 4 h earlier
    snapped = _snap_to_reference(inp, tables, pieces, dict(moved))
    assert snapped[("SO-1/1", "OP-010")] == slot(10, 14)                   # back where today's plan has it
    assert snapped[("SO-2/1", "OP-010")] == slot(14, 18)


def test_a_line_whose_slot_is_taken_keeps_the_solver_position():
    inp, grid, tables, pieces = setup()
    slot = lambda h0, h1: ("MC-1", grid.floor(at(0, h0)), grid.floor(at(0, h1)))  # noqa: E731
    # the scenario forced SO-2 into SO-1's reference slot, so SO-1 cannot go back
    forced = {("SO-1/1", "OP-010"): slot(14, 18), ("SO-2/1", "OP-010"): slot(10, 14)}
    snapped = _snap_to_reference(inp, tables, pieces, dict(forced))
    assert snapped == forced
