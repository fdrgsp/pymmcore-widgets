from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest
from qtpy.QtWidgets import QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsRectItem
from useq import (  # type: ignore
    AnyGridPlan,
    GridFromEdges,
    GridRowsColumns,
    RandomPoints,
)

from pymmcore_widgets import HCSWizard
from pymmcore_widgets.hcs._calibration_widget import (
    ROLE,
    CalibrationData,
    CalibrationInfo,
    CalibrationTableData,
    FourPoints,
    PlateCalibrationWidget,
    ThreePoints,
    TwoPoints,
    _CalibrationModeWidget,
    _CalibrationTable,
    _TestCalibrationWidget,
)
from pymmcore_widgets.hcs._fov_widget import (
    Center,
    FOVSelectorWidget,
    WellView,
    _CenterFOVWidget,
    _GridFovWidget,
    _RandomFOVWidget,
)
from pymmcore_widgets.hcs._graphics_items import (
    WellInfo,
    _FOVGraphicsItem,
    _WellAreaGraphicsItem,
    _WellGraphicsItem,
)
from pymmcore_widgets.hcs._plate_widget import (
    PlateSelectorWidget,
    WellPlateInfo,
    _CustomPlateWidget,
)
from pymmcore_widgets.hcs._well_plate_model import WellPlate, load_database

if TYPE_CHECKING:
    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot


@pytest.fixture()
def database_path():
    return Path(__file__).parent / "plate_database_for_tests.json"


@pytest.fixture()
def database(database_path):
    return load_database(database_path)


def test_plate_widget_set_get_value(qtbot: QtBot, database_path: Path):
    wdg = PlateSelectorWidget(plate_database_path=database_path)
    qtbot.addWidget(wdg)

    plate = wdg._plate_db["standard 96 wp"]
    wells = [
        WellInfo("A1", 0, 0),
        WellInfo("A2", 0, 1),
        WellInfo("B3", 1, 2),
        WellInfo("B4", 1, 3),
        WellInfo("C5", 2, 4),
    ]
    info = WellPlateInfo(plate=plate, wells=wells)

    wdg.setValue(info)
    # sort the list of wells by name
    assert sorted(wdg.scene.value(), key=lambda x: x.name) == wells

    wdg.clear_button.click()
    assert wdg.scene.value() is None


def test_plate_widget_combo(qtbot: QtBot, database_path: Path):
    wdg = PlateSelectorWidget(plate_database_path=database_path)
    qtbot.addWidget(wdg)

    wdg.plate_combo.setCurrentText("standard 96 wp")
    assert wdg.value().plate.id == "standard 96 wp"


def test_custom_plate_widget_set_get_value(qtbot: QtBot, database_path: Path):
    wdg = _CustomPlateWidget(plate_database_path=database_path)
    qtbot.addWidget(wdg)

    current_plate_id = wdg.plate_table.item(0, 0).text()
    assert wdg.value().id == current_plate_id

    custom_plate = WellPlate(
        id="custom plate",
        circular=True,
        rows=2,
        columns=4,
        well_spacing_x=15,
        well_spacing_y=15,
        well_size_x=10,
        well_size_y=10,
    )

    wdg.setValue(custom_plate)
    assert wdg.value() == custom_plate

    scene_items = list(wdg.scene.items())
    assert len(scene_items) == 8
    assert all(isinstance(item, _WellGraphicsItem) for item in scene_items)


def test_calibration_mode_widget(qtbot: QtBot):
    wdg = _CalibrationModeWidget()
    qtbot.addWidget(wdg)

    modes = [TwoPoints(), ThreePoints(), FourPoints()]
    wdg.setValue(modes)

    assert wdg._mode_combo.count() == 3

    for i in range(wdg._mode_combo.count()):
        assert wdg._mode_combo.itemData(i, ROLE) == modes[i]


def test_calibration_table_widget(
    global_mmcore: CMMCorePlus, qtbot: QtBot, database: dict[str, WellPlate]
):
    mmc = global_mmcore

    wdg = _CalibrationTable(mmcore=mmc)
    qtbot.addWidget(wdg)

    assert wdg.tb.rowCount() == 0
    assert wdg._well_label.text() == ""

    wdg.setValue(
        CalibrationTableData([], database["coverslip 22mm"], TwoPoints(), "A1")
    )
    assert wdg.calibration_mode == TwoPoints()
    assert wdg._well_label.text() == "A1"
    assert wdg.plate == database["coverslip 22mm"]

    mmc.waitForSystem()

    mmc.setXYPosition(mmc.getXYStageDevice(), -10, 10)
    mmc.waitForDevice(mmc.getXYStageDevice())
    wdg.act_add_row.trigger()
    assert wdg.tb.rowCount() == 1
    assert wdg.tb.cellWidget(0, 0).value() == -10
    assert wdg.tb.cellWidget(0, 1).value() == 10

    mmc.setXYPosition(mmc.getXYStageDevice(), 10, -10)
    mmc.waitForDevice(mmc.getXYStageDevice())
    wdg.act_add_row.trigger()
    assert wdg.tb.rowCount() == 2
    assert wdg.tb.cellWidget(1, 0).value() == 10
    assert wdg.tb.cellWidget(1, 1).value() == -10

    value = wdg.value()
    assert value.list_of_points == [(-10.0, 10.0), (10.0, -10.0)]
    assert value.plate == database["coverslip 22mm"]
    assert value.calibration_mode == TwoPoints()
    assert value.well_name == "A1"

    wdg.tb.selectRow(0)
    wdg.act_go_to.trigger()
    mmc.waitForDevice(mmc.getXYStageDevice())
    assert round(mmc.getXPosition()) == -10
    assert round(mmc.getYPosition()) == 10


def test_calibration_move_to_edge_widget(
    global_mmcore: CMMCorePlus, qtbot: QtBot, database: dict[str, WellPlate]
):
    mmc = global_mmcore

    wdg = _TestCalibrationWidget(mmcore=mmc)
    qtbot.addWidget(wdg)

    assert wdg._letter_combo.count() == 0
    assert wdg._number_combo.count() == 0

    well = WellInfo(name="C3", row=2, column=2)
    wdg.setValue(database["standard 96 wp"], well)
    assert wdg._letter_combo.count() == 8
    assert wdg._number_combo.count() == 12
    assert wdg.value() == (database["standard 96 wp"], well)


def test_calibration_widget(
    global_mmcore: CMMCorePlus, qtbot: QtBot, database: dict[str, WellPlate]
):
    wdg = PlateCalibrationWidget(mmcore=global_mmcore)
    qtbot.addWidget(wdg)

    assert wdg.value() == (None, None)
    assert wdg._calibration_label.value() == "Plate Not Calibrated!"

    wdg.setValue(CalibrationInfo(database["coverslip 22mm"], None))

    assert wdg._calibration_mode._mode_combo.count() == 2
    assert wdg._calibration_mode._mode_combo.itemData(0, ROLE) == TwoPoints()
    assert wdg._calibration_mode._mode_combo.itemData(1, ROLE) == FourPoints()
    assert isinstance(wdg._calibration_mode.value(), TwoPoints)

    assert not wdg._table_a1.isHidden()
    assert wdg._table_an.isHidden()
    assert wdg._calibration_label.value() == "Plate Not Calibrated!"
    assert wdg.value() == (database["coverslip 22mm"], None)

    with pytest.raises(ValueError, match="Invalid number of points"):
        wdg._on_calibrate_button_clicked()

    wdg._table_a1.setValue(
        CalibrationTableData(
            list_of_points=[(-210, 170), (100, -100)],
            plate=database["coverslip 22mm"],
            calibration_mode=TwoPoints(),
            well_name="A1",
        )
    )
    assert len(wdg._table_a1.value().list_of_points) == 2

    wdg._on_calibrate_button_clicked()

    assert wdg._calibration_label.value() == "Plate Calibrated!"

    cal_data = CalibrationData(
        well_A1_center_x=-55.0, well_A1_center_y=35.0, rotation_matrix=None
    )
    assert wdg.value() == CalibrationInfo(database["coverslip 22mm"], cal_data)

    wdg.setValue(CalibrationInfo(database["standard 96 wp"], None))

    assert wdg._calibration_mode._mode_combo.count() == 1
    assert wdg._calibration_mode._mode_combo.itemData(0, ROLE) == ThreePoints()

    assert not wdg._table_a1.isHidden()
    assert not wdg._table_an.isHidden()

    tb_A1_value = wdg._table_a1.value()
    tb_An_value = wdg._table_an.value()
    assert tb_A1_value.well_name == " Well A1 "
    assert tb_An_value.well_name == " Well A12 "
    assert tb_A1_value.calibration_mode == tb_An_value.calibration_mode == ThreePoints()

    wdg.setValue(CalibrationInfo(None, None))
    assert wdg._table_a1.isHidden()
    assert wdg._table_an.isHidden()
    assert wdg.value() == CalibrationInfo(None, None)


def test_center_widget(qtbot: QtBot):
    wdg = _CenterFOVWidget()
    qtbot.addWidget(wdg)

    value = wdg.value()

    assert value.x == value.y == 0.0
    assert value.fov_width == value.fov_height is None

    wdg.fov_size = (5, 7)
    value = wdg.value()
    assert value.fov_width == 5
    assert value.fov_height == 7

    wdg.setValue(Center(x=10, y=20, fov_width=2, fov_height=3))

    value = wdg.value()
    assert value.x == 10
    assert value.y == 20
    assert value.fov_width == 2
    assert value.fov_height == 3


def test_random_widget(qtbot: QtBot, database: dict[str, WellPlate]):
    wdg = _RandomFOVWidget()
    qtbot.addWidget(wdg)

    assert not wdg.is_circular

    value = wdg.value()
    assert value.fov_width == value.fov_height is None
    assert value.num_points == 1
    assert value.max_width == value.max_height == 0.0
    assert value.shape.value == "rectangle"
    assert isinstance(value.random_seed, int)

    wdg.fov_size = (2, 2)
    value = wdg.value()
    assert value.fov_width == value.fov_height == 2

    wdg.setValue(
        RandomPoints(
            num_points=10,
            max_width=20,
            max_height=30,
            shape="ellipse",
            random_seed=0,
            fov_width=5,
            fov_height=5,
        )
    )
    value = wdg.value()
    assert value.num_points == 10
    assert value.max_width == 20
    assert value.max_height == 30
    assert value.random_seed == 0
    assert wdg.is_circular
    assert value.fov_width == value.fov_height == 5


def test_grid_widget(qtbot: QtBot):
    wdg = _GridFovWidget()
    qtbot.addWidget(wdg)

    value = wdg.value()
    assert value.fov_width == value.fov_height is None
    assert value.overlap == (0.0, 0.0)
    assert value.mode.value == "row_wise_snake"
    assert value.rows == value.columns == 1
    assert value.relative_to.value == "center"

    wdg.fov_size = (0.512, 0.512)
    value = wdg.value()
    assert value.fov_width == value.fov_height == 0.512

    wdg.setValue(
        GridRowsColumns(
            overlap=10.0,
            mode="row_wise",
            rows=2,
            columns=3,
            fov_width=2,
            fov_height=2,
        )
    )
    value = wdg.value()
    assert value.overlap == (10.0, 10.0)
    assert value.mode.value == "row_wise"
    assert value.rows == 2
    assert value.columns == 3
    assert value.relative_to.value == "center"
    assert value.fov_width == value.fov_height == 2


class SceneItems(NamedTuple):
    fovs: int  # _FOVGraphicsItem
    lines: int  # QGraphicsLineItem
    well_area: int  # _WellAreaGraphicsItem
    well_circle: int  # QGraphicsEllipseItem
    well_rect: int  # QGraphicsRectItem


def get_items_number(wdg: WellView) -> SceneItems:
    """Return the number of items in the scene as a SceneItems namedtuple."""
    items = wdg.scene().items()
    fovs = len([t for t in items if isinstance(t, _FOVGraphicsItem)])
    lines = len([t for t in items if isinstance(t, QGraphicsLineItem)])
    well_area = len([t for t in items if isinstance(t, _WellAreaGraphicsItem)])
    well_circle = len([t for t in items if isinstance(t, QGraphicsEllipseItem)])
    well_rect = len([t for t in items if isinstance(t, QGraphicsRectItem)])
    return SceneItems(fovs, lines, well_area, well_circle, well_rect)


modes = [
    (
        Center(x=0, y=0, fov_width=512, fov_height=512),
        SceneItems(fovs=1, lines=0, well_area=0, well_circle=0, well_rect=1),
    ),
    (
        RandomPoints(
            num_points=3,
            max_width=5,
            max_height=4,
            shape="ellipse",
            fov_width=510,
            fov_height=510,
        ),
        SceneItems(fovs=3, lines=2, well_area=1, well_circle=1, well_rect=0),
    ),
    (
        GridRowsColumns(rows=2, columns=3, fov_width=500, fov_height=500),
        SceneItems(fovs=6, lines=5, well_area=0, well_circle=0, well_rect=1),
    ),
    (
        GridFromEdges(
            top=-20, left=-20, bottom=20, right=20, fov_width=600, fov_height=600
        ),
        SceneItems(fovs=9, lines=8, well_area=0, well_circle=0, well_rect=1),
    ),
]


@pytest.mark.parametrize(["mode", "items"], modes)
def test_well_view_widget_value(
    qtbot: QtBot, mode: Center | AnyGridPlan, items: SceneItems
):
    wdg = WellView(view_size=(300, 300), well_size=(6.4, 6.4))
    qtbot.addWidget(wdg)

    assert wdg.wellSize() == (6.4, 6.4)
    assert not wdg.isCircular()

    wdg.setValue(mode)
    assert wdg.value() == mode
    assert wdg.fovSize() == (mode.fov_width / 1000, mode.fov_height / 1000)  # mm

    if isinstance(mode, RandomPoints):
        assert wdg.isCircular()

    # make sure that the graphics item in the scene are the expected ones
    assert get_items_number(wdg) == items


def test_well_view_widget_update(qtbot: QtBot):
    wdg = WellView(view_size=(300, 300), well_size=(6.4, 6.4), fov_size=(0.512, 0.512))
    qtbot.addWidget(wdg)

    grid = GridFromEdges(top=-20, left=-20, bottom=20, right=20)
    wdg.setValue(grid)

    # set fov size
    fovs = get_items_number(wdg).fovs
    assert fovs == 9

    wdg.setFovSize((0.256, 0.256))
    wdg.refresh()
    fovs = get_items_number(wdg).fovs
    assert fovs == 25

    # set circular
    assert not wdg.isCircular()
    _, _, _, well_circle, well_rect = get_items_number(wdg)
    assert well_circle == 0
    assert well_rect == 1

    wdg.setCircular(True)
    wdg.refresh()
    assert wdg.isCircular()
    _, _, _, well_circle, well_rect = get_items_number(wdg)
    assert well_circle == 1
    assert well_rect == 0

    # set padding
    assert not wdg.padding()
    well = next(t for t in wdg.scene().items() if isinstance(t, QGraphicsEllipseItem))
    assert well.rect().width() == well.rect().height() == 300

    wdg.setPadding(10)
    assert wdg.padding() == 10
    wdg.refresh()
    well = next(t for t in wdg.scene().items() if isinstance(t, QGraphicsEllipseItem))
    assert well.rect().width() == well.rect().height() == 290


def test_fov_selector_widget(qtbot: QtBot, database: dict[str, WellPlate]):
    wdg = FOVSelectorWidget(
        plate=database["standard 96 wp"],
        mode=Center(x=0, y=0, fov_width=500, fov_height=500),
    )
    qtbot.addWidget(wdg)

    # center
    assert wdg.value() == (
        database["standard 96 wp"],
        Center(x=0, y=0, fov_width=500, fov_height=500),
    )

    # grid
    grid = GridRowsColumns(overlap=10, rows=2, columns=3, fov_width=512, fov_height=512)
    wdg.setValue(database["coverslip 22mm"], grid)

    plate, mode = wdg.value()
    assert plate == database["coverslip 22mm"]
    assert mode.fov_width == mode.fov_height == 512
    assert mode.rows == 2
    assert mode.columns == 3
    assert mode.overlap == (10.0, 10.0)

    # random
    rnd = RandomPoints(
        num_points=2,
        max_width=10,
        max_height=10,
        shape="rectangle",
        fov_width=600,
        fov_height=500,
        random_seed=0,
    )

    wdg.setValue(database["coverslip 22mm"], rnd)

    plate, mode = wdg.value()
    assert mode.max_width == mode.max_height == 10
    assert mode.num_points == 2
    assert mode.fov_width == 600
    assert mode.fov_height == 500
    assert mode.shape.value == "rectangle"
    assert mode.random_seed == 0

    # warning well RandomPoints shape > plate area_X
    with pytest.raises(UserWarning, match="RandomPoints `max_width`"):
        rnd = RandomPoints(
            num_points=2, max_width=30, max_height=10, shape="ellipse", random_seed=0
        )
        wdg.setValue(database["standard 96 wp"], rnd)


def test_hcs_wizard(global_mmcore: CMMCorePlus, qtbot: QtBot, database_path: Path):
    wdg = HCSWizard(plate_database_path=database_path)
    qtbot.addWidget(wdg)
