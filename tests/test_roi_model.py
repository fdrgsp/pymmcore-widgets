from __future__ import annotations

import numpy as np
import pytest
import useq

from pymmcore_widgets.control._rois.roi_model import ROI, RectangleROI


@pytest.fixture
def large_rect() -> RectangleROI:
    """A rectangle larger than a typical FOV (10x10)."""
    return RectangleROI((0.0, 0.0), (100.0, 100.0), fov_size=(10.0, 10.0))


@pytest.fixture
def small_rect() -> RectangleROI:
    """A rectangle smaller than a typical FOV (10x10) -> no grid needed."""
    return RectangleROI((0.0, 0.0), (5.0, 5.0), fov_size=(10.0, 10.0))


@pytest.fixture
def large_polygon() -> ROI:
    """A polygon (triangle) larger than a typical FOV."""
    verts = np.array([(0.0, 0.0), (200.0, 0.0), (100.0, 200.0)])
    return ROI(vertices=verts, fov_size=(10.0, 10.0))


# ---------------------------------------------------------------------------
# create_grid_plan - overlap & mode forwarding (new params in this PR)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("overlap", [0.0, 0.1, (0.2, 0.3)])
def test_rect_grid_plan_overlap_forwarded(
    large_rect: RectangleROI, overlap: float | tuple[float, float]
) -> None:
    plan = large_rect.create_grid_plan(overlap=overlap)
    assert plan is not None
    expected = overlap if isinstance(overlap, tuple) else (overlap, overlap)
    assert plan.overlap == expected


@pytest.mark.parametrize("overlap", [0.0, 0.15, (0.2, 0.3)])
def test_polygon_grid_plan_overlap_forwarded(
    large_polygon: ROI, overlap: float | tuple[float, float]
) -> None:
    plan = large_polygon.create_grid_plan(overlap=overlap)
    assert plan is not None
    expected = overlap if isinstance(overlap, tuple) else (overlap, overlap)
    assert plan.overlap == expected


@pytest.mark.parametrize("mode", [useq.OrderMode.row_wise_snake, useq.OrderMode.spiral])
def test_rect_grid_plan_mode_forwarded(
    large_rect: RectangleROI, mode: useq.OrderMode
) -> None:
    plan = large_rect.create_grid_plan(mode=mode)
    assert plan is not None
    assert plan.mode == mode


@pytest.mark.parametrize("mode", [useq.OrderMode.row_wise_snake, useq.OrderMode.spiral])
def test_polygon_grid_plan_mode_forwarded(
    large_polygon: ROI, mode: useq.OrderMode
) -> None:
    plan = large_polygon.create_grid_plan(mode=mode)
    assert plan is not None
    assert plan.mode == mode


def test_self_intersecting_polygon_returns_none() -> None:
    """A self-intersecting (bowtie) polygon should return None, not crash."""
    verts = np.array([(0.0, 0.0), (100.0, 100.0), (100.0, 0.0), (0.0, 100.0)])
    assert ROI(vertices=verts, fov_size=(10.0, 10.0)).create_grid_plan() is None


# ---------------------------------------------------------------------------
# create_useq_position (reworked in this PR)
# ---------------------------------------------------------------------------


def test_position_name_contains_uuid(large_rect: RectangleROI) -> None:
    pos = large_rect.create_useq_position()
    assert pos.name is not None
    assert large_rect.text in pos.name
    assert pos.name.endswith(large_rect._uuid.hex[-4:])


def test_position_has_grid_plan_when_large(large_rect: RectangleROI) -> None:
    pos = large_rect.create_useq_position()
    assert pos.sequence is not None
    assert pos.sequence.grid_plan is not None


def test_position_has_no_sequence_when_small(small_rect: RectangleROI) -> None:
    pos = small_rect.create_useq_position()
    assert pos.sequence is None


def test_position_forwards_overlap_and_scan_order() -> None:
    roi = RectangleROI(
        (0.0, 0.0),
        (100.0, 100.0),
        fov_size=(10.0, 10.0),
    )
    pos = roi.create_useq_position(overlap=(0.2, 0.2), mode=useq.OrderMode.spiral)
    assert pos.sequence is not None
    grid = pos.sequence.grid_plan
    assert grid is not None
    assert grid.overlap == (0.2, 0.2)
    assert grid.mode == useq.OrderMode.spiral


def test_position_xy_is_roi_center_when_small(small_rect: RectangleROI) -> None:
    """A single-FOV position's x/y must be the ROI's own center.

    There's no grid to supply x/y here -- this position *is* the whole
    acquisition for this ROI, so a stray (0, 0) would be a real, wrong
    coordinate rather than a placeholder hidden behind a grid plan.
    """
    pos = small_rect.create_useq_position()
    assert (pos.x, pos.y) == small_rect.center()


def test_position_xy_is_unset_when_large(large_rect: RectangleROI) -> None:
    """A multi-FOV position must leave x/y unset, not stamped from the grid.

    useq.AbsolutePosition's own validator clears x/y back to None (warning
    that a future useq version will make this a hard error) whenever they're
    set alongside an absolute grid plan -- so stamping them with the grid's
    first point doesn't survive: PositionTable.setValue() round-trips every
    position through useq.Position.model_validate(), which would silently
    wipe it back to 0 anyway. Leaving x/y unset here avoids relying on
    values useq itself won't keep.
    """
    pos = large_rect.create_useq_position()
    assert pos.sequence is not None
    assert pos.sequence.grid_plan is not None
    assert pos.x is None
    assert pos.y is None


def test_position_survives_revalidation_when_large(
    large_rect: RectangleROI, recwarn: pytest.WarningsRecorder
) -> None:
    """A multi-FOV position must round-trip cleanly through model_validate.

    This is what caught the regression above in practice:
    `PositionTable.setValue()` calls `useq.Position.model_validate(pos)` on
    every incoming position, which re-runs useq's x/y-vs-grid-plan
    validator. A position built with x/y already set (however well-meant)
    passes through `create_useq_position()` unchanged -- `model_copy()`
    doesn't re-validate -- but silently loses that x/y, with a warning, the
    moment anything downstream revalidates it.
    """
    pos = large_rect.create_useq_position()
    revalidated = useq.Position.model_validate(pos)
    assert revalidated.x is None
    assert revalidated.y is None
    assert not recwarn.list
