from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import useq
from qtpy.QtWidgets import QGraphicsPathItem, QGraphicsRectItem
from useq import GridFromShape, Shape

from pymmcore_widgets.useq_widgets import points_plans as pp
from pymmcore_widgets.useq_widgets.points_plans._cover_area_widget import (
    area_fits_in_well,
)

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot

COVERAGE = GridFromShape(
    shape="rectangle",
    width=1200,
    height=800,
    overlap=(10, 5),
    mode="column_wise_snake",
    fov_width=100,
    fov_height=80,
)


@pytest.mark.parametrize(
    ("area", "well", "fits"),
    [
        # rectangle in rectangle / ellipse in rectangle: bounding box test
        (("rectangle", 100, 50), ("rectangle", 100, 50), True),
        (("rectangle", 101, 50), ("rectangle", 100, 50), False),
        (("ellipse", 100, 50), ("rectangle", 100, 50), True),
        # ellipse in ellipse: both radii
        (("ellipse", 100, 100), ("ellipse", 100, 100), True),
        (("ellipse", 60, 101), ("ellipse", 100, 100), False),
        # rectangle in ellipse: corners must be inside the ellipse
        (("rectangle", 100, 10), ("ellipse", 100, 100), False),
        (("rectangle", 70, 70), ("ellipse", 100, 100), True),
        (("rectangle", 71, 71), ("ellipse", 100, 100), False),
    ],
)
def test_area_fits_in_well(area: tuple, well: tuple, fits: bool) -> None:
    to_shape = lambda a: (Shape(a[0]), a[1], a[2])  # noqa: E731
    assert area_fits_in_well(to_shape(area), to_shape(well)) is fits


def test_cover_area_widget_value(qtbot: QtBot) -> None:
    wdg = pp.CoverAreaWidget()
    qtbot.addWidget(wdg)
    assert wdg.mode.currentText() == "row_wise_snake"
    assert not wdg.inside_area.isChecked()
    # no FOV: invalid, with an actionable message
    assert not wdg.isValid()
    assert "FOV" in wdg.errorMessage()

    with qtbot.waitSignal(wdg.valueChanged):
        wdg.setValue(COVERAGE)
    assert wdg.value() == COVERAGE
    assert wdg.isValid()
    assert (wdg.area_width.value(), wdg.area_height.value()) == (1200, 800)
    assert wdg.shape.currentText() == "rectangle"

    # keep the FOVs inside the area
    wdg.inside_area.setChecked(True)
    inside = wdg.value()
    assert isinstance(inside, GridFromShape)
    assert inside.placement == "inside"
    assert inside.num_positions() < COVERAGE.num_positions()
    wdg2 = pp.CoverAreaWidget()
    qtbot.addWidget(wdg2)
    wdg2.setValue(inside)
    assert wdg2.inside_area.isChecked()
    assert wdg2.value() == inside

    # no FOV fits inside a tiny area
    wdg.area_width.setValue(50)
    assert not wdg.isValid()
    assert "No FOV fits" in wdg.errorMessage()


def test_cover_area_well_geometry(qtbot: QtBot) -> None:
    wdg = pp.CoverAreaWidget()
    qtbot.addWidget(wdg)
    wdg.fov_size = (500, 400)
    wdg.setWellGeometry("ellipse", 6400, 6400)

    # the area starts as the full well
    val = wdg.value()
    assert (val.shape, val.width, val.height) == (Shape.ELLIPSE, 6400, 6400)
    assert (wdg.area_width.value(), wdg.area_height.value()) == (6400, 6400)

    wdg.shape.setCurrentText("rectangle")
    wdg.area_width.setValue(3000)
    wdg.area_height.setValue(2000)
    val = wdg.value()
    assert (val.shape, val.width, val.height) == (Shape.RECTANGLE, 3000, 2000)
    assert wdg.isValid()

    # setting the same well again (e.g. only the selected wells changed) keeps input
    wdg.setWellGeometry("ellipse", 6400, 6400)
    assert wdg.value() == val

    # the area can't be larger than the well
    assert wdg.area_width.maximum() == 6400
    wdg.area_width.setValue(9000)
    assert wdg.value().width == 6400
    # ... and must fit its shape: a rectangle as wide as a round well doesn't
    assert not wdg.isValid()
    assert "does not fit" in wdg.errorMessage()
    wdg.shape.setCurrentText("ellipse")
    assert wdg.isValid()

    # a new plate resets the area to its full well
    wdg.setWellGeometry("rectangle", 3000, 2000)
    assert (wdg.area_width.maximum(), wdg.area_height.maximum()) == (3000, 2000)
    val = wdg.value()
    assert (val.shape, val.width, val.height) == (Shape.RECTANGLE, 3000, 2000)

    # without a well, the area is only bounded by the widget's own limit
    wdg.setWellGeometry(None, None, None)
    assert wdg.area_width.maximum() > 6400


def test_cover_area_too_many_tiles(qtbot: QtBot) -> None:
    wdg = pp.CoverAreaWidget()
    qtbot.addWidget(wdg)
    wdg.setValue(
        GridFromShape(
            shape="rectangle", width=1e5, height=1e5, fov_width=1, fov_height=1
        )
    )
    assert not wdg.isValid()
    assert "maximum" in wdg.errorMessage()


def test_selector_cover_area_mode(qtbot: QtBot) -> None:
    wdg = pp.RelativePointPlanSelector()
    qtbot.addWidget(wdg)

    # Cover Area is the third (and last) mode; grids are one of its tabs
    buttons = wdg._mode_btn_group.buttons()
    assert buttons.index(wdg.cover_area_radio_btn) == 2
    assert len(buttons) == 3
    assert wdg.grid_wdg is wdg.cover_area_wdg.grid_wdg

    wdg.setValue(COVERAGE)
    assert wdg.cover_area_radio_btn.isChecked()
    assert wdg.active_plan_type is GridFromShape
    assert wdg.value() == COVERAGE

    # the shared FOV size updates the coverage plan (and its validity)
    wdg.fov_w.setValue(200)
    assert wdg.value().fov_width == 200
    assert wdg.cover_area_wdg.fov_size == (200, 80)
    wdg.fov_w.setValue(0)
    assert not wdg.cover_area_wdg.isValid()

    # all modes retain their state when switched
    random_plan = useq.RandomPoints(
        num_points=4,
        max_width=100,
        max_height=100,
        random_seed=1,
        fov_width=10,
        fov_height=10,
    )
    wdg.setValue(random_plan)
    wdg.cover_area_radio_btn.setChecked(True)
    assert wdg.value() == COVERAGE.model_copy(
        update={"fov_width": 10, "fov_height": 10}
    )
    wdg.random_radio_btn.setChecked(True)
    assert wdg.value() == random_plan


def test_points_plan_widget_cover_area(qtbot: QtBot) -> None:
    wdg = pp.PointsPlanWidget()
    qtbot.addWidget(wdg)
    wdg.show()
    wdg.setWellSize(6.4, 6.4)
    wdg.setWellShape("circle")
    # mm -> µm conversion
    assert wdg.cover_area_wdg.wellGeometry() == (Shape.ELLIPSE, 6400, 6400)

    plan = GridFromShape(
        shape="ellipse", width=6400, height=6400, fov_width=700, fov_height=600
    )
    with qtbot.waitSignal(wdg.valueChanged):
        wdg.setValue(plan)
    assert wdg.value() == plan

    # the statistics are shown on the well view
    n = plan.num_positions()
    assert wdg._well_view.infoText() == f"{n} FOVs per well"
    wdg.setSelectedWellCount(3)
    assert wdg._well_view.infoText() == f"{n} FOVs per well\n{n * 3} total (3 wells)"
    label = wdg._well_view._info_label
    assert label.isVisible()
    # at the bottom right
    assert label.geometry().right() > wdg._well_view.width() / 2
    assert label.geometry().bottom() > wdg._well_view.height() / 2

    view = wdg._well_view
    # target outline + first tile (green rect) + one path with all the other tiles
    assert view._bounding_area_item is not None
    paths = [i for i in view.scene().items() if isinstance(i, QGraphicsPathItem)]
    assert len(paths) == 2  # tiles + traversal path
    tiles_path = next(i for i in paths if i in view._fov_items)
    # every tile beyond the first is a closed sub-path of the tiles path
    n_rects = tiles_path.path().toSubpathPolygons()
    assert len(n_rects) == plan.num_positions() - 1
    first = view._fov_items[0]
    assert isinstance(first, QGraphicsRectItem)
    x0, y0, *_ = plan.tile_coordinates()[0]
    assert first.rect().center().x() == pytest.approx(x0)
    assert first.rect().center().y() == pytest.approx(-y0)

    # clicking does not change the first position for deterministic plans
    wdg._on_view_position_clicked(useq.RelativePosition(x=1, y=1))
    assert wdg.value() == plan

    # plate rotation rotates the well and area outlines, not the FOVs
    wdg.setPlateRotation(5)
    assert wdg.value().rotation == 5
    assert view._well_outline_item is not None
    assert view._well_outline_item.rotation() == -5
    assert view._bounding_area_item.rotation() == -5

    # without a FOV size nothing is drawn, and the plan is flagged invalid
    wdg._selector.fov_w.setValue(0)
    assert not any(isinstance(i, QGraphicsPathItem) for i in view.scene().items())
    assert not wdg.cover_area_wdg.isValid()
    assert wdg._well_view.infoText() == ""

    # other modes show their statistics too
    wdg.setValue(useq.RelativePosition())
    assert wdg._well_view.infoText() == "1 FOV per well\n3 total (3 wells)"


def test_cover_area_tabs_share_settings(qtbot: QtBot) -> None:
    wdg = pp.CoverAreaWidget()
    qtbot.addWidget(wdg)
    wdg.fov_size = (100, 100)
    assert wdg.planType() is GridFromShape

    grid = useq.GridRowsColumns(
        rows=2,
        columns=4,
        overlap=(-20, 5),
        mode="spiral",
        fov_width=100,
        fov_height=100,
    )
    with qtbot.waitSignal(wdg.valueChanged):
        wdg.setValue(grid)
    assert wdg.tabs.currentIndex() == 1
    assert wdg.planType() is useq.GridRowsColumns
    assert wdg.value() == grid
    # the shared controls show the grid's settings; its own are hidden
    assert wdg.overlap == (-20, 5)
    assert wdg.mode.currentText() == "spiral"
    assert not wdg.grid_wdg._common.isVisibleTo(wdg)
    assert wdg.isValid()
    # only coverage can keep the FOVs inside the area
    assert not wdg.inside_area.isEnabled()

    # shared settings apply to both tabs
    wdg.mode.setCurrentText("column_wise")
    assert wdg.value().mode.value == "column_wise"
    wdg.tabs.setCurrentIndex(0)
    assert wdg.inside_area.isEnabled()
    val = wdg.value()
    assert isinstance(val, GridFromShape)
    assert val.mode.value == "column_wise"
    # coverage needs overlap >= 0: a negative grid overlap is (visibly) clamped
    assert val.overlap == (0, 5)
    assert wdg.overlap_x.value() == 0
    wdg.tabs.setCurrentIndex(1)
    assert wdg.value().overlap == (0, 5)
    assert wdg.overlap_x.minimum() < 0  # negative overlap allowed again


def test_overlap_widget(qtbot: QtBot) -> None:
    from pymmcore_widgets.useq_widgets.points_plans._common import OverlapWidget

    wdg = OverlapWidget(0, 99)
    qtbot.addWidget(wdg)
    wdg.show()
    # a single value for x and y by default
    assert not wdg.isSeparate()
    assert not wdg.overlap_y.isVisible()
    with qtbot.waitSignal(wdg.valueChanged):
        wdg.overlap_x.setValue(10)
    assert wdg.value() == (10, 10)

    # right-click menu: different x and y overlaps
    action = wdg.contextMenu().actions()[0]
    assert action.isCheckable() and not action.isChecked()
    action.setChecked(True)
    assert wdg.isSeparate()
    assert wdg.overlap_y.isVisible()
    assert wdg.overlap_x.prefix() == "x: "
    wdg.overlap_y.setValue(5)
    assert wdg.value() == (10, 5)
    # turning it off uses the x overlap for both
    wdg.contextMenu().actions()[0].setChecked(False)
    assert wdg.value() == (10, 10)
    assert not wdg.overlap_y.isVisible()

    # different values (e.g. from a plan) show both spin boxes
    wdg.setValue((3, 7))
    assert wdg.isSeparate()
    assert wdg.value() == (3, 7)
    wdg.setValue(4)
    assert not wdg.isSeparate()
    assert wdg.value() == (4, 4)


def test_selector_labels_share_width(qtbot: QtBot) -> None:
    wdg = pp.RelativePointPlanSelector()
    qtbot.addWidget(wdg)
    labels = wdg._label_aligner.labels()
    assert len(labels) > 10
    widest = max(lbl.sizeHint().width() for lbl in labels)
    assert {lbl.minimumWidth() for lbl in labels} == {widest}
    assert {lbl.maximumWidth() for lbl in labels} == {widest}
    # the page-wide group took over the labels of the nested widgets
    assert not wdg.random_points_wdg._label_aligner.labels()
    assert not wdg.grid_wdg._label_aligner.labels()
