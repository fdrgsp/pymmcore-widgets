"""Stage coordinates of the fields of view an MDA position list will visit."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import useq

if TYPE_CHECKING:
    from collections.abc import Iterable

    from useq import MultiPointPlan

# A grid with more points than this is not expanded. An area grid (edges,
# width/height, polygon) without a FOV size steps 1 µm at a time, so it has
# millions of points; even computing its first one can take seconds (polygon).
MAX_GRID_POINTS = 10_000


def positions_to_xy(
    positions: Iterable[useq.Position] | useq.WellPlatePlan,
) -> np.ndarray:
    """Return the stage XY (µm) of every field of view, as an (N, 2) array.

    Sub-sequence grid plans are expanded; one with too many points (see
    `MAX_GRID_POINTS`) shows only its position, or nothing if the grid is
    absolute. Positions with no x or y are skipped.
    """
    if isinstance(positions, useq.WellPlatePlan):
        return _plate_xy(positions)

    points: list[tuple[float, float]] = []
    for pos in positions:
        grid = pos.sequence.grid_plan if pos.sequence is not None else None
        if grid is not None and not grid.is_relative:
            points.extend(_grid_xy(grid) or ())
        elif pos.x is not None and pos.y is not None:
            offsets = _grid_xy(grid) if grid is not None else None
            points.extend((pos.x + x, pos.y + y) for x, y in offsets or [(0, 0)])
    return np.asarray(points, dtype=np.float32).reshape(-1, 2)


def _plate_xy(plan: useq.WellPlatePlan) -> np.ndarray:
    wpp = plan.well_points_plan
    if isinstance(wpp, useq.RandomPoints) and wpp.random_seed is None:
        # Unseeded, each well draws its own points: only useq can produce them.
        points = [(p.x, p.y) for p in plan.image_positions]
        return np.asarray(points, dtype=np.float32).reshape(-1, 2)
    # Same result as `plan.image_positions`, without one Position per point:
    # every well gets the same offsets, so broadcast them over the well centers.
    centers = plan.selected_well_coordinates[:, ::-1] * 1000  # (y, x) mm -> x, y µm
    offsets = np.asarray(_grid_xy(wpp) or [(0, 0)], dtype=float)
    xy: np.ndarray = (centers[:, None] + offsets[None]).reshape(-1, 2)
    return xy.astype(np.float32)


def _grid_xy(grid: MultiPointPlan) -> list[tuple[float, float]] | None:
    """Return the points of `grid`, or None if it has too many to expand."""
    if isinstance(grid, useq.RelativePosition):
        return [(grid.x or 0.0, grid.y or 0.0)]
    if isinstance(grid, useq.RandomPoints):
        n = grid.num_points
    else:
        try:
            n = grid.num_positions()
        except (ValueError, ZeroDivisionError):  # no FOV size, or a zero one
            return None
    if n > MAX_GRID_POINTS:
        return None
    return [(p.x, p.y) for p in grid if p.x is not None and p.y is not None]
