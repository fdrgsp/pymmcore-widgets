from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from useq import GridRowsColumns, RandomPoints  # type: ignore

from pymmcore_widgets._hcs_widget._calibration_widget import (
    ROLE,
    CalibrationData,
    CalibrationInfo,
    CalibrationTableData,
    FourPoints,
    ThreePoints,
    TwoPoints,
    _CalibrationModeWidget,
    _CalibrationTable,
    _CalibrationWidget,
    _TestCalibrationWidget,
)
from pymmcore_widgets._hcs_widget._fov_widget import (
    Center,
    FOVSelectrorWidget,
    RandomFOVWidget,
    _CenterFOVWidget,
    _GridFovWidget,
)
from pymmcore_widgets._hcs_widget._graphics_items import WellInfo, _WellGraphicsItem
from pymmcore_widgets._hcs_widget._main_wizard_widget import HCSWizard
from pymmcore_widgets._hcs_widget._plate_widget import (
    WellPlateInfo,
    _CustomPlateWidget,
    _PlateWidget,
)
from pymmcore_widgets._hcs_widget._well_plate_model import WellPlate, load_database

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
    wdg = _PlateWidget(plate_database_path=database_path)
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
    wdg = _PlateWidget(plate_database_path=database_path)
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
    wdg = _CalibrationWidget(mmcore=global_mmcore)
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

    assert not wdg._radio_btn.isChecked()
    assert wdg.value() == Center()
    wdg.setValue(Center())
    assert wdg._radio_btn.isChecked()


def test_random_widget(
    global_mmcore: CMMCorePlus, qtbot: QtBot, database: dict[str, WellPlate]
):
    wdg = RandomFOVWidget()
    qtbot.addWidget(wdg)
    wdg._radio_btn.setChecked(True)

    assert not wdg.is_circular
    assert wdg._area_y.isEnabled()

    value = wdg.value()
    assert value.fov_width == value.fov_height == 0.512
    assert value.num_points == 1
    assert value.max_width == value.max_height == 0.0
    assert value.shape.value == "rectangle"
    assert isinstance(value.random_seed, int)

    wdg.setValue(
        RandomPoints(
            num_points=10, max_width=20, max_height=30, shape="ellipse", random_seed=0
        )
    )
    value = wdg.value()
    assert value.num_points == 10
    assert value.max_width == 20
    assert value.max_height == 30
    assert value.random_seed == 0
    assert wdg.is_circular

    wdg.area_x.setValue(5)
    assert wdg._area_y.value() == 5


def test_grid_widget(global_mmcore: CMMCorePlus, qtbot: QtBot):
    wdg = _GridFovWidget()
    qtbot.addWidget(wdg)

    wdg._radio_btn.setChecked(True)

    value = wdg.value()
    assert value.fov_width == value.fov_height == 0.512
    assert value.overlap == (0.0, 0.0)
    assert value.mode.value == "row_wise_snake"
    assert value.rows == value.columns == 1
    assert value.relative_to.value == "center"

    wdg.setValue(GridRowsColumns(overlap=10.0, mode="row_wise", rows=2, columns=3))
    value = wdg.value()
    assert value.overlap == (10.0, 10.0)
    assert value.mode.value == "row_wise"
    assert value.rows == 2
    assert value.columns == 3
    assert value.relative_to.value == "center"


def test_fov_selector_widget(
    global_mmcore: CMMCorePlus, qtbot: QtBot, database: dict[str, WellPlate]
):
    wdg = FOVSelectrorWidget()
    qtbot.addWidget(wdg)

    # center
    assert wdg.value() == (WellPlate(), Center())

    # grid
    grid = GridRowsColumns(overlap=10.0, mode="row_wise", rows=2, columns=3)
    wdg.setValue(database["coverslip 22mm"], grid)
    plate, mode = wdg.value()
    assert plate == database["coverslip 22mm"]
    assert mode.fov_width == mode.fov_height == 0.512
    assert mode.rows == 2
    assert mode.columns == 3
    assert mode.overlap == (10.0, 10.0)

    # random
    rnd = RandomPoints(
        num_points=2, max_width=10, max_height=10, shape="rectangle", random_seed=0
    )
    wdg.setValue(database["coverslip 22mm"], rnd)

    plate, mode = wdg.value()
    assert mode.fov_width == mode.fov_height == 0.512
    assert mode.num_points == 2
    assert mode.max_width == mode.max_height == 10
    assert mode.shape.value == "rectangle"
    assert mode.random_seed == 0

    # assertion error well plate shape != RandomPoints shape
    with pytest.raises(AssertionError, match="Well plate shape is"):
        rnd = RandomPoints(
            num_points=2, max_width=10, max_height=10, shape="ellipse", random_seed=0
        )
        wdg.setValue(database["coverslip 22mm"], rnd)

    # ok
    rnd = RandomPoints(
        num_points=2, max_width=5, max_height=5, shape="ellipse", random_seed=0
    )
    wdg.setValue(database["standard 96 wp"], rnd)

    # assertion error well plate area_x < RandomPoints shape
    with pytest.raises(AssertionError, match="RandomPoints max width"):
        rnd = RandomPoints(
            num_points=2, max_width=30, max_height=10, shape="ellipse", random_seed=0
        )
        wdg.setValue(database["standard 96 wp"], rnd)


def test_hcs_wizard(global_mmcore: CMMCorePlus, qtbot: QtBot, database_path: Path):
    wdg = HCSWizard(plate_database_path=database_path)
    qtbot.addWidget(wdg)
