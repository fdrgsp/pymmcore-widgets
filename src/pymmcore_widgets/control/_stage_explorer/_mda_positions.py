"""Stage coordinates of the fields of view an MDA position list will visit."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import useq

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from useq import MultiPointPlan

# A grid with more points than this is not expanded: useq yields them one at a
# time in Python, and the map re-expands on every MDA table edit (50,000 points
# take ~0.2 s). An area grid (edges, width/height, polygon) without a FOV size
# steps 1 µm at a time, so it has millions of points.
MAX_GRID_POINTS = 50_000


class MdaPoints(NamedTuple):
    """The fields of view to mark, and how many grids were too big to expand."""

    xy: np.ndarray
    """Stage XY (µm) of every field of view, as an (N, 2) float32 array."""
    n_oversized: int
    """Grids with more than `MAX_GRID_POINTS` points, left out of `xy`."""


def positions_to_xy(
    positions: Iterable[useq.Position] | useq.WellPlatePlan,
) -> MdaPoints:
    """Return the stage XY (µm) of every field of view the positions will visit.

    Sub-sequence grid plans are expanded. One with too many points (see
    `MAX_GRID_POINTS`) is counted in `MdaPoints.n_oversized` and shows only its
    position, or nothing if the grid is absolute. Positions with no x or y, and
    grids whose points can't be computed (no FOV size), are skipped.
    """
    if isinstance(positions, useq.WellPlatePlan):
        return _plate_xy(positions)

    points: list[tuple[float, float]] = []
    n_oversized = 0
    for pos in positions:
        grid = pos.sequence.grid_plan if pos.sequence is not None else None
        offsets = _grid_xy(grid) if grid is not None else None
        if grid is not None and offsets is None:
            n_oversized += 1
        if grid is not None and not grid.is_relative:
            points.extend(offsets or ())
        elif pos.x is not None and pos.y is not None:
            points.extend((pos.x + x, pos.y + y) for x, y in offsets or [(0, 0)])
    return MdaPoints(_as_xy(points), n_oversized)


def _plate_xy(plan: useq.WellPlatePlan) -> MdaPoints:
    wpp = plan.well_points_plan
    if isinstance(wpp, useq.RandomPoints) and wpp.random_seed is None:
        # Unseeded, each well draws its own points: only useq can produce them.
        points = [(p.x, p.y) for p in plan.image_positions]
        return MdaPoints(_as_xy(points), 0)
    # Same result as `plan.image_positions`, without one Position per point:
    # every well gets the same offsets, so broadcast them over the well centers.
    centers = plan.selected_well_coordinates[:, ::-1] * 1000  # (y, x) mm -> x, y µm
    well_xy = _grid_xy(wpp)
    offsets = np.asarray(well_xy or [(0, 0)], dtype=float)
    xy: np.ndarray = (centers[:, None] + offsets[None]).reshape(-1, 2)
    return MdaPoints(xy.astype(np.float32), int(well_xy is None))


def _as_xy(points: Sequence[tuple[float | None, float | None]]) -> np.ndarray:
    return np.asarray(points, dtype=np.float32).reshape(-1, 2)


def _grid_xy(grid: MultiPointPlan) -> list[tuple[float, float]] | None:
    """Return the points of `grid`, or None if it has too many to expand.

    A grid whose points can't be counted (no FOV size, or a zero one) gives an
    empty list: it isn't a valid scan, so it isn't reported as too big either.
    """
    if isinstance(grid, useq.RelativePosition):
        return [(grid.x or 0.0, grid.y or 0.0)]
    if isinstance(grid, useq.RandomPoints):
        n = grid.num_points
    else:
        try:
            n = grid.num_positions()
        except (ValueError, ZeroDivisionError):  # no FOV size, or a zero one
            return []
    if n > MAX_GRID_POINTS:
        return None
    return [(p.x, p.y) for p in grid if p.x is not None and p.y is not None]
