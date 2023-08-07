from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock, call

import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsRectItem,
    QTableWidgetItem,
)
from useq import MDASequence

from pymmcore_widgets._hcs_widget._graphics_items import _FOVPoints, _Well, _WellArea
from pymmcore_widgets._hcs_widget._main_hcs_widget import HCSWidget
from pymmcore_widgets._mda._zstack_widget import ZRangeAroundSelect
from pymmcore_widgets._util import PLATE_FROM_CALIBRATION

if TYPE_CHECKING:
    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot


# NOTE NOT using a fixture like this because
# it gives "_run_after_each_test" error
# @pytest.fixture()
# def hcs_wdg(global_mmcore: CMMCorePlus, qtbot: QtBot):
#     hcs = HCSWidget(mmcore=global_mmcore)
#     hcs._set_enabled(True)
#     mmc = hcs._mmc
#     cal = hcs.calibration
#     qtbot.add_widget(hcs)
#     with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
#         hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")
#     return hcs, mmc, cal


@pytest.fixture()
def database_path():
    return Path(__file__).parent / "plate_database_for_tests.json"


def _get_image_size(mmc: CMMCorePlus):
    _cam_x = mmc.getROI(mmc.getCameraDevice())[-2]
    _cam_y = mmc.getROI(mmc.getCameraDevice())[-1]
    assert _cam_x == 512
    assert _cam_y == 512
    _image_size_mm_x = (_cam_x * mmc.getPixelSizeUm()) / 1000
    _image_size_mm_y = (_cam_y * mmc.getPixelSizeUm()) / 1000
    return _image_size_mm_x, _image_size_mm_y


def test_hcs_plate_selection(
    global_mmcore: CMMCorePlus, qtbot: QtBot, database_path: Path
):
    hcs = HCSWidget(mmcore=global_mmcore, plate_database_path=database_path)
    hcs._set_enabled(True)
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    assert hcs.tabwidget.currentIndex() == 0
    assert hcs.tabwidget.tabText(0) == "  Plate and FOVVs Selection  "

    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "standard 6"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 6
    assert not [
        item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()
    ]

    wells = []
    for item in reversed(hcs._plate_and_fov_tab.scene.items()):
        assert isinstance(item, _Well)
        well, _, col = item.get_name_row_col()
        wells.append(well)
        if col in {0, 1}:
            item.setSelected(True)
    assert wells == ["A1", "A2", "A3", "B1", "B2", "B3"]
    assert (
        len(
            [item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()]
        )
        == 4
    )

    well_order = hcs._plate_and_fov_tab.scene.value()
    assert well_order == [("A1", 0, 0), ("A2", 0, 1), ("B2", 1, 1), ("B1", 1, 0)]

    hcs._plate_and_fov_tab.clear_button.click()
    assert not [
        item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()
    ]

    hcs._plate_and_fov_tab.custom_plate.click()
    assert hcs._plate._id.text() == ""
    assert hcs._plate._rows.value() == 0
    assert hcs._plate._cols.value() == 0
    assert hcs._plate._well_spacing_x.value() == 0
    assert hcs._plate._well_spacing_y.value() == 0
    assert hcs._plate._well_size_x.value() == 0
    assert hcs._plate._well_size_y.value() == 0
    assert not hcs._plate._circular_checkbox.isChecked()

    hcs._plate._id.setText("new_plate")
    hcs._plate._rows.setValue(3)
    hcs._plate._cols.setValue(3)
    hcs._plate._well_spacing_x.setValue(5)
    hcs._plate._well_spacing_y.setValue(5)
    hcs._plate._well_size_x.setValue(2)
    hcs._plate._well_size_y.setValue(2)
    hcs._plate._circular_checkbox.setChecked(True)

    with qtbot.waitSignal(hcs._plate.valueChanged):
        hcs._plate._ok_btn.click()

    items = [
        hcs._plate_and_fov_tab.wp_combo.itemText(i)
        for i in range(hcs._plate_and_fov_tab.wp_combo.count())
    ]
    assert "new_plate" in items
    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "new_plate"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 9
    assert not [
        item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()
    ]
    with open(Path(hcs._plate_db_path)) as file:
        db = json.load(file)
        assert "new_plate" in [well["id"] for well in db]

    hcs._plate_and_fov_tab.custom_plate.click()
    match = hcs._plate.plate_table.findItems("new_plate", Qt.MatchExactly)
    hcs._plate.plate_table.item(match[0].row(), 0).setSelected(True)

    hcs._plate._delete_btn.click()
    items = [
        hcs._plate_and_fov_tab.wp_combo.itemText(i)
        for i in range(hcs._plate_and_fov_tab.wp_combo.count())
    ]
    assert "new_plate" not in items
    with open(Path(hcs._plate_db_path)) as file:
        db = json.load(file)
        assert "new_plate" not in [well["id"] for well in db]

    hcs._plate.close()


def test_hcs_fov_selection_FOVPoints_size(global_mmcore: CMMCorePlus, qtbot: QtBot):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    mmc = hcs._mmc
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    assert hcs.tabwidget.currentIndex() == 0
    assert hcs.tabwidget.tabText(0) == "  Plate and FOVVs Selection  "
    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.currentIndex() == 0
    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.tabText(0) == "Center"

    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "standard 6"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 6

    scene_width = hcs._plate_and_fov_tab.FOV_selector.scene.sceneRect().width()
    scene_height = hcs._plate_and_fov_tab.FOV_selector.scene.sceneRect().height()
    assert scene_width == 200
    assert scene_height == 200

    assert mmc.getPixelSizeUm() == 1.0
    _image_size_mm_x, _image_size_mm_y = _get_image_size(mmc)
    assert _image_size_mm_x == 0.512
    assert _image_size_mm_y == 0.512
    fov, well = list(hcs._plate_and_fov_tab.FOV_selector.scene.items())
    assert isinstance(fov, _FOVPoints)
    assert isinstance(well, QGraphicsEllipseItem)
    assert fov.get_center_and_size() == (scene_width / 2, scene_height / 2, 160, 160)
    assert fov._x_size == (160 * _image_size_mm_x) / hcs.wp.well_size_x
    assert fov._y_size == (160 * _image_size_mm_y) / hcs.wp.well_size_y
    assert fov._x_size == fov._y_size == 2.3540229885057475

    mmc.setProperty("Objective", "Label", "Nikon 20X Plan Fluor ELWD")
    with qtbot.waitSignal(mmc.events.pixelSizeChanged):
        mmc.events.pixelSizeChanged.emit(mmc.getPixelSizeUm())
    assert mmc.getPixelSizeUm() == 0.5
    _image_size_mm_x, _image_size_mm_y = _get_image_size(mmc)
    assert _image_size_mm_x == 0.256
    assert _image_size_mm_y == 0.256
    items = list(hcs._plate_and_fov_tab.FOV_selector.scene.items())
    assert len(items) == 2
    fov, well = items
    assert isinstance(fov, _FOVPoints)
    assert isinstance(well, QGraphicsEllipseItem)
    assert fov.get_center_and_size() == (scene_width / 2, scene_height / 2, 160, 160)
    assert fov._x_size == (160 * _image_size_mm_x) / hcs.wp.well_size_x
    assert fov._y_size == (160 * _image_size_mm_y) / hcs.wp.well_size_y
    assert fov._x_size == fov._y_size == 1.1770114942528738


def test_hcs_fov_selection_center(global_mmcore: CMMCorePlus, qtbot: QtBot):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    assert hcs.tabwidget.currentIndex() == 0
    assert hcs.tabwidget.tabText(0) == "  Plate and FOVVs Selection  "
    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.currentIndex() == 0
    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.tabText(0) == "Center"

    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "standard 6"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 6

    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 384")
    assert len(hcs._plate_and_fov_tab.scene.items()) == 384
    items = list(hcs._plate_and_fov_tab.FOV_selector.scene.items())
    assert len(items) == 2
    fov, well = items
    assert isinstance(fov, _FOVPoints)
    assert isinstance(well, QGraphicsRectItem)


def test_hcs_fov_selection_random(global_mmcore: CMMCorePlus, qtbot: QtBot):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")
    assert hcs.tabwidget.currentIndex() == 0
    assert hcs.tabwidget.tabText(0) == "  Plate and FOVVs Selection  "
    hcs.tabwidget.setCurrentIndex(1)
    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.tabText(1) == "Random"

    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "standard 6"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 6

    hcs._plate_and_fov_tab.FOV_selector.random_wdg.number_of_FOV.setValue(3)
    assert len(hcs._plate_and_fov_tab.FOV_selector.scene.items()) == 5
    items = list(hcs._plate_and_fov_tab.FOV_selector.scene.items())
    well = items[-1]
    well_area = items[-2]
    fovs = items[:3]
    assert isinstance(well, QGraphicsEllipseItem)
    assert isinstance(well_area, _WellArea)
    for i in fovs:
        assert isinstance(i, _FOVPoints)

    w, h = hcs.wp.well_size_x, hcs.wp.well_size_y
    ax = hcs._plate_and_fov_tab.FOV_selector.random_wdg.plate_area_x
    ay = hcs._plate_and_fov_tab.FOV_selector.random_wdg.plate_area_y
    assert ax.value() == w
    assert ay.value() == h
    _, _, width, height = well_area._rect.getRect()
    assert width == (160 * ax.value()) / w
    assert height == (160 * ay.value()) / h

    hcs._plate_and_fov_tab.FOV_selector.random_wdg.number_of_FOV.setValue(1)
    ax.setValue(3.0)
    ay.setValue(3.0)
    items = list(hcs._plate_and_fov_tab.FOV_selector.scene.items())
    well_area = items[-2]
    fov_1 = items[0]
    assert isinstance(well_area, _WellArea)
    assert isinstance(fov_1, _FOVPoints)
    assert ax.value() != w
    assert ay.value() != h
    _, _, width, height = well_area._rect.getRect()
    assert width == (160 * ax.value()) / w
    assert height == (160 * ay.value()) / h

    hcs._plate_and_fov_tab.FOV_selector.random_wdg.random_button.click()

    items = list(hcs._plate_and_fov_tab.FOV_selector.scene.items())
    fov_2 = items[0]
    assert isinstance(fov_2, _FOVPoints)

    assert fov_1._center_x != fov_2._center_x
    assert fov_1._center_y != fov_2._center_y


def test_hcs_fov_selection_grid(global_mmcore: CMMCorePlus, qtbot: QtBot):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    mmc = hcs._mmc
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    assert hcs.tabwidget.currentIndex() == 0
    assert hcs.tabwidget.tabText(0) == "  Plate and FOVVs Selection  "
    hcs.tabwidget.setCurrentIndex(2)
    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.tabText(2) == "Grid"

    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 384")
    assert len(hcs._plate_and_fov_tab.scene.items()) == 384

    hcs._plate_and_fov_tab.FOV_selector.grid_wdg.rows.setValue(3)
    hcs._plate_and_fov_tab.FOV_selector.grid_wdg.cols.setValue(3)
    hcs._plate_and_fov_tab.FOV_selector.grid_wdg.spacing_x.setValue(500.0)
    hcs._plate_and_fov_tab.FOV_selector.grid_wdg.spacing_y.setValue(500.0)
    items = list(hcs._plate_and_fov_tab.FOV_selector.scene.items())
    assert len(items) == 10
    well = items[-1]
    assert isinstance(well, QGraphicsRectItem)

    _image_size_mm_x, _image_size_mm_y = _get_image_size(mmc)
    fovs = items[:9]
    for fov in fovs:
        assert isinstance(fov, _FOVPoints)
        assert fov._x_size == (160 * _image_size_mm_x) / hcs.wp.well_size_x
        assert fov._y_size == (160 * _image_size_mm_y) / hcs.wp.well_size_y

    fov_1 = cast(_FOVPoints, items[4])
    fov_2 = cast(_FOVPoints, items[5])
    cx, cy, w, h = fov_1.get_center_and_size()
    assert (round(cx, 2), round(cy, 2), w, h) == (100.00, 100.00, 160, 160)
    cx, cy, w, h = fov_2.get_center_and_size()
    assert (round(cx, 2), round(cy, 2), w, h) == (140.48, 100.00, 160, 160)


def test_calibration_label(global_mmcore: CMMCorePlus, qtbot: QtBot):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    cal = hcs._calibration
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 96")
    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "standard 96"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 96
    assert not [
        item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()
    ]

    hcs.tabwidget.setCurrentIndex(1)
    assert hcs.tabwidget.tabText(1) == "  Plate Calibration  "

    assert cal.cal_lbl.text() == "Plate non Calibrated!"

    assert cal._calibration_wells_combo.currentIndex() == 0
    assert cal._calibration_wells_combo.currentText() == "1 Well (A1)"
    text = (
        "Calibrate Wells: A1\n"
        "\n"
        "Add 3 points on the circonference of the round well "
        "and click on 'Calibrate Plate'."
    )
    assert cal.info_lbl.text() == text

    cal._calibration_wells_combo.setCurrentIndex(1)
    assert cal._calibration_wells_combo.currentText() == "2 Wells (A1,  A12)"
    text = (
        "Calibrate Wells: A1,  A12\n"
        "\n"
        "Add 3 points on the circonference of the round well "
        "and click on 'Calibrate Plate'."
    )
    assert cal.info_lbl.text() == text

    hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 384")
    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "standard 384"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 384
    assert not [
        item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()
    ]

    cal._calibration_wells_combo.setCurrentIndex(1)
    assert cal._calibration_wells_combo.currentText() == "2 Wells (A1,  A24)"

    text = (
        "Calibrate Wells: A1,  A24\n"
        "\n"
        "Add 2 points (opposite vertices) "
        "or 4 points (1 point per side) "
        "and click on 'Calibrate Plate'."
    )
    assert cal.info_lbl.text() == text


def test_calibration_one_well(global_mmcore: CMMCorePlus, qtbot: QtBot):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    mmc = hcs._mmc
    cal = hcs._calibration
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    hcs.tabwidget.setCurrentIndex(1)
    assert hcs.tabwidget.tabText(1) == "  Plate Calibration  "

    assert cal.cal_lbl.text() == "Plate non Calibrated!"

    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "standard 6"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 6
    assert not [
        item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()
    ]

    assert cal._calibration_wells_combo.currentText() == "1 Well (A1)"

    assert not cal.table_1.isHidden()
    assert cal.table_2.isHidden()

    mmc.setXYPosition(-50.0, 0.0)
    mmc.waitForDevice(mmc.getXYStageDevice())
    cal.table_1._add_pos()

    assert cal.table_1.tb.item(0, 0).text() == "Well A1_pos000"
    assert round(cal.table_1.tb.cellWidget(0, 1).value()) == -50
    assert round(cal.table_1.tb.cellWidget(0, 2).value()) == -0.0

    mmc.setXYPosition(0.0, 50.0)
    mmc.waitForDevice(mmc.getXYStageDevice())
    cal.table_1._add_pos()
    assert cal.table_1.tb.item(1, 0).text() == "Well A1_pos001"
    assert round(cal.table_1.tb.cellWidget(1, 1).value()) == -0.0
    assert round(cal.table_1.tb.cellWidget(1, 2).value()) == 50.0

    error = "Not enough points for Well A1. Add 3 points to the table."
    with pytest.warns(match=error):
        cal._calibrate_plate()

    mmc.setXYPosition(50.0, 0.0)
    mmc.waitForDevice(mmc.getXYStageDevice())
    cal.table_1._add_pos()
    assert cal.table_1.tb.item(2, 0).text() == "Well A1_pos002"
    assert round(cal.table_1.tb.cellWidget(2, 1).value()) == 50.0
    assert round(cal.table_1.tb.cellWidget(2, 2).value()) == -0.0

    cal.table_1._add_pos()
    assert cal.table_1.tb.rowCount() == 4
    error = "Add only 3 points to the table."
    with pytest.warns(match=error):
        cal._calibrate_plate()

    cal.table_1.tb.removeRow(3)

    assert cal._get_well_center(cal.table_1.tb) == (0.0, 0.0)

    cal._calibrate_plate()
    assert cal.cal_lbl.text() == "Plate Calibrated!"

    assert cal.plate_angle_deg == 0.0
    assert not cal.plate_rotation_matrix


def test_calibration_one_well_square(global_mmcore: CMMCorePlus, qtbot: QtBot):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    cal = hcs._calibration
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    hcs.tabwidget.setCurrentIndex(1)
    assert hcs.tabwidget.tabText(1) == "  Plate Calibration  "

    assert cal.cal_lbl.text() == "Plate non Calibrated!"

    hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 384")
    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "standard 384"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 384
    assert not [
        item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()
    ]

    assert cal._calibration_wells_combo.currentText() == "1 Well (A1)"

    assert not cal.table_1.isHidden()
    assert cal.table_2.isHidden()

    cal.table_1.tb.insertRow(0)
    cal.table_1.tb.setItem(0, 0, QTableWidgetItem("Well A1_pos000"))
    cal.table_1._add_table_value(-50, 0, 1)
    cal.table_1._add_table_value(50, 0, 2)
    assert cal.table_1.tb.rowCount() == 1

    error = "Not enough points for Well A1. Add 2 or 4 points to the table."
    with pytest.warns(match=error):
        cal._calibrate_plate()

    cal.table_1.tb.insertRow(1)
    cal.table_1.tb.setItem(1, 0, QTableWidgetItem("Well A1_pos001"))
    cal.table_1._add_table_value(50, 1, 1)
    cal.table_1._add_table_value(-50, 1, 2)

    assert cal.table_1.tb.rowCount() == 2

    assert cal._get_well_center(cal.table_1.tb) == (0.0, 0.0)

    cal._calibrate_plate()
    assert cal.cal_lbl.text() == "Plate Calibrated!"

    assert cal.plate_angle_deg == 0.0
    assert not cal.plate_rotation_matrix


def test_calibration_two_wells(global_mmcore: CMMCorePlus, qtbot: QtBot):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    cal = hcs._calibration
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    hcs.tabwidget.setCurrentIndex(1)
    assert hcs.tabwidget.tabText(1) == "  Plate Calibration  "

    assert cal.cal_lbl.text() == "Plate non Calibrated!"

    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "standard 6"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 6
    assert not [
        item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()
    ]

    cal._calibration_wells_combo.setCurrentText("2 Wells (A1,  A3)")

    assert not cal.table_1.isHidden()
    assert not cal.table_2.isHidden()

    cal.table_1.tb.setRowCount(3)
    # A1
    cal.table_1.tb.setItem(0, 0, QTableWidgetItem("Well A1_pos000"))
    cal.table_1._add_table_value(-50, 0, 1)
    cal.table_1._add_table_value(0, 0, 2)
    cal.table_1.tb.setItem(1, 0, QTableWidgetItem("Well A1_pos001"))
    cal.table_1._add_table_value(0, 1, 1)
    cal.table_1._add_table_value(50, 1, 2)
    cal.table_1.tb.setItem(2, 0, QTableWidgetItem("Well A1_pos002"))
    cal.table_1._add_table_value(50, 2, 1)
    cal.table_1._add_table_value(0, 2, 2)
    assert cal.table_1.tb.rowCount() == 3

    cal.table_2.tb.setRowCount(3)
    # A3
    cal.table_2.tb.setItem(0, 0, QTableWidgetItem("Well A3_pos000"))
    cal.table_2._add_table_value(1364, 0, 1)
    cal.table_2._add_table_value(1414, 0, 2)
    cal.table_2.tb.setItem(1, 0, QTableWidgetItem("Well A3_pos001"))
    cal.table_2._add_table_value(1414, 1, 1)
    cal.table_2._add_table_value(1364, 1, 2)
    cal.table_2.tb.setItem(2, 0, QTableWidgetItem("Well A3_pos002"))
    cal.table_2._add_table_value(1464, 2, 1)
    cal.table_2._add_table_value(1414, 2, 2)
    assert cal.table_2.tb.rowCount() == 3

    x, y = cal._get_well_center(cal.table_1.tb)
    assert (round(x), round(y)) == (0.0, 0.0)
    x, y = cal._get_well_center(cal.table_2.tb)
    assert (round(x), round(y)) == (1414, 1414)

    cal._calibrate_plate()

    assert round(cal.plate_angle_deg) == -45.0
    assert (
        str(cal.plate_rotation_matrix)
        == "[[ 0.70710678  0.70710678]\n [-0.70710678  0.70710678]]"
    )
    assert cal.cal_lbl.text() == "Plate Calibrated!"


def test_calibration_from_calibration(global_mmcore: CMMCorePlus, qtbot: QtBot):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    cal = hcs._calibration
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    hcs.tabwidget.setCurrentIndex(1)
    assert hcs.tabwidget.tabText(1) == "  Plate Calibration  "

    assert cal.cal_lbl.text() == "Plate non Calibrated!"

    hcs._plate_and_fov_tab.wp_combo.setCurrentText(PLATE_FROM_CALIBRATION)
    assert hcs._plate_and_fov_tab.wp_combo.currentText() == PLATE_FROM_CALIBRATION
    assert len(hcs._plate_and_fov_tab.scene.items()) == 1
    assert (
        len(
            [item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()]
        )
        == 1
    )

    assert cal._calibration_wells_combo.currentText() == "1 Well (A1)"
    assert (
        len(
            [
                cal._calibration_wells_combo.itemText(i)
                for i in range(cal._calibration_wells_combo.count())
            ]
        )
        == 1
    )

    assert not cal.table_1.isHidden()
    assert cal.table_2.isHidden()

    cal.table_1.tb.setRowCount(2)
    cal.table_1.tb.setItem(0, 0, QTableWidgetItem("Well A1_pos000"))
    cal.table_1._add_table_value(-50, 0, 1)
    cal.table_1._add_table_value(50, 0, 2)
    cal.table_1.tb.setItem(1, 0, QTableWidgetItem("Well A1_pos001"))
    cal.table_1._add_table_value(50, 1, 1)
    cal.table_1._add_table_value(-50, 1, 2)
    assert cal.table_1.tb.rowCount() == 2

    assert cal._get_well_center(cal.table_1.tb) == (0.0, 0.0)

    mock = Mock()
    cal.valueChanged.connect(mock)

    with qtbot.waitSignal(cal.valueChanged):
        cal._calibrate_plate()

    pos = cal._get_pos_from_table(cal.table_1.tb)
    mock.assert_has_calls([call(pos)])

    assert cal.cal_lbl.text() == "Plate Calibrated!"

    assert cal.plate_angle_deg == 0.0
    assert not cal.plate_rotation_matrix


def test_generate_pos_list(global_mmcore: CMMCorePlus, qtbot: QtBot):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    cal = hcs._calibration
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    pos_table = hcs._mda.position_groupbox._table

    hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 384")
    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "standard 384"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 384

    wells = []
    for item in reversed(hcs._plate_and_fov_tab.scene.items()):
        assert isinstance(item, _Well)
        well, row, col = item.get_name_row_col()
        if col in {0, 1} and row in {0, 1}:
            item.setSelected(True)
            wells.append(well)
    assert (
        len(
            [item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()]
        )
        == 4
    )
    assert wells == ["A1", "A2", "B1", "B2"]

    assert cal._calibration_wells_combo.currentText() == "1 Well (A1)"

    cal.table_1.tb.setRowCount(2)
    cal.table_1.tb.setItem(0, 0, QTableWidgetItem("Well A1_pos000"))
    cal.table_1._add_table_value(-50, 0, 1)
    cal.table_1._add_table_value(50, 0, 2)
    cal.table_1.tb.setItem(1, 0, QTableWidgetItem("Well A1_pos001"))
    cal.table_1._add_table_value(50, 1, 1)
    cal.table_1._add_table_value(-50, 1, 2)
    assert cal.table_1.tb.rowCount() == 2

    cal._calibrate_plate()
    assert cal.cal_lbl.text() == "Plate Calibrated!"

    # center
    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.currentIndex() == 0
    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.tabText(0) == "Center"

    hcs._generate_pos_list()
    assert pos_table.rowCount() == 4

    table_info = []
    for r in range(pos_table.rowCount()):
        well_name = pos_table.item(r, 0).text()
        _x = pos_table.cellWidget(r, 1).value()
        _y = pos_table.cellWidget(r, 2).value()
        _z = pos_table.cellWidget(r, 3).value()
        table_info.append((well_name, _x, _y, _z))

    assert table_info == [
        ("A1_pos000", 0.0, 0.0, 0.0),
        ("A2_pos000", 4500.0, 0.0, 0.0),
        ("B2_pos000", 4500.0, -4500.0, 0.0),
        ("B1_pos000", 0.0, -4500.0, 0.0),
    ]

    # random
    hcs.tabwidget.setCurrentIndex(1)
    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.tabText(1) == "Random"
    hcs._plate_and_fov_tab.FOV_selector.random_wdg.number_of_FOV.setValue(2)

    hcs._generate_pos_list()
    assert pos_table.rowCount() == 8

    table_info = []
    for r in range(pos_table.rowCount()):
        well_name = pos_table.item(r, 0).text()
        table_info.append(well_name)

    assert table_info == [
        "A1_pos000",
        "A1_pos001",
        "A2_pos000",
        "A2_pos001",
        "B2_pos000",
        "B2_pos001",
        "B1_pos000",
        "B1_pos001",
    ]

    # grid
    hcs.tabwidget.setCurrentIndex(2)
    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.tabText(2) == "Grid"
    hcs._plate_and_fov_tab.FOV_selector.rows.setValue(2)
    hcs._plate_and_fov_tab.FOV_selector.cols.setValue(2)

    hcs._generate_pos_list()
    assert pos_table.rowCount() == 16

    table_info = []
    for r in range(pos_table.rowCount()):
        well_name = pos_table.item(r, 0).text()
        table_info.append(well_name)

    assert table_info == [
        "A1_pos000",
        "A1_pos001",
        "A1_pos002",
        "A1_pos003",
        "A2_pos000",
        "A2_pos001",
        "A2_pos002",
        "A2_pos003",
        "B2_pos000",
        "B2_pos001",
        "B2_pos002",
        "B2_pos003",
        "B1_pos000",
        "B1_pos001",
        "B1_pos002",
        "B1_pos003",
    ]


def test_hcs_state(global_mmcore: CMMCorePlus, qtbot: QtBot):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    cal = hcs._calibration
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    mda = hcs._mda
    pos_table = mda.position_groupbox._table

    hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 384")
    assert hcs._plate_and_fov_tab.wp_combo.currentText() == "standard 384"
    assert len(hcs._plate_and_fov_tab.scene.items()) == 384

    for item in reversed(hcs._plate_and_fov_tab.scene.items()):
        assert isinstance(item, _Well)
        _, row, col = item.get_name_row_col()
        if col in {0, 1} and row in {0}:
            item.setSelected(True)
    assert (
        len(
            [item for item in hcs._plate_and_fov_tab.scene.items() if item.isSelected()]
        )
        == 2
    )

    assert cal._calibration_wells_combo.currentText() == "1 Well (A1)"

    cal.table_1.tb.setRowCount(2)
    cal.table_1.tb.setItem(0, 0, QTableWidgetItem("Well A1_pos000"))
    cal.table_1._add_table_value(-50, 0, 1)
    cal.table_1._add_table_value(50, 0, 2)
    cal.table_1.tb.setItem(1, 0, QTableWidgetItem("Well A1_pos001"))
    cal.table_1._add_table_value(50, 1, 1)
    cal.table_1._add_table_value(-50, 1, 2)
    assert cal.table_1.tb.rowCount() == 2

    cal._calibrate_plate()
    assert cal.cal_lbl.text() == "Plate Calibrated!"

    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.currentIndex() == 0
    assert hcs._plate_and_fov_tab.FOV_selector.tab_wdg.tabText(0) == "Center"

    # positions
    hcs._generate_pos_list()
    assert pos_table.rowCount() == 2

    # channels
    mda.channel_groupbox._add_button.click()
    assert mda.channel_groupbox._table.rowCount() == 1

    # time
    mda.time_groupbox.setChecked(True)
    mda.time_groupbox._timepoints_spinbox.setValue(2)
    mda.time_groupbox._interval_spinbox.setValue(1.00)
    mda.time_groupbox._units_combo.setCurrentText("sec")

    # z stack
    mda.stack_groupbox.setChecked(True)
    assert mda.stack_groupbox._zmode_tabs.currentIndex() == 0
    mda.stack_groupbox._zmode_tabs.setCurrentIndex(1)
    assert mda.stack_groupbox._zmode_tabs.tabText(1) == "RangeAround"
    range_around = cast(
        ZRangeAroundSelect, mda.stack_groupbox._zmode_tabs.currentWidget()
    )
    range_around._zrange_spinbox.setValue(2)
    mda.stack_groupbox._zstep_spinbox.setValue(1)

    assert mda.position_groupbox.isChecked()
    state = hcs.get_state()

    sequence = MDASequence(
        channels=[
            {
                "config": "Cy5",
                "group": "Channel",
                "exposure": 10,
            }
        ],
        time_plan={"interval": {"seconds": 1.0}, "loops": 2},
        z_plan={"range": 2, "step": 1.0},
        axis_order="tpcz",
        stage_positions=(
            {"name": "A1_pos000", "x": 0.0, "y": 0.0, "z": 0.0},
            {"name": "A2_pos000", "x": 4500.0, "y": 0.0, "z": 0.0},
        ),
    )

    assert state.channels == sequence.channels
    assert state.time_plan == sequence.time_plan
    assert state.z_plan == sequence.z_plan
    assert state.axis_order == sequence.axis_order
    assert state.stage_positions == sequence.stage_positions


def test_load_positions(global_mmcore: CMMCorePlus, qtbot: QtBot, tmp_path: Path):
    hcs = HCSWidget(mmcore=global_mmcore)
    hcs._set_enabled(True)
    cal = hcs._calibration
    qtbot.add_widget(hcs)
    with qtbot.waitSignal(hcs._plate_and_fov_tab.wp_combo.currentTextChanged):
        hcs._plate_and_fov_tab.wp_combo.setCurrentText("standard 6")

    mda = hcs._mda
    cal.A1_stage_coords_center = (100.0, 100.0)

    positions = [
        {"name": "A1_center_coords", "x": 0.0, "y": 0.0},
        {"name": "A1_pos000", "x": -100.0, "y": 100.0, "z": 0.0},
        {"name": "A1_pos001", "x": 200.0, "y": 200.0, "z": 0.0},
        {"name": "A1_pos002", "x": 300.0, "y": -300.0, "z": 0.0},
    ]

    saved = tmp_path / "test.json"
    saved.write_text(json.dumps(positions))
    assert saved.exists()

    pos_list = json.loads(saved.read_text())

    hcs._add_loaded_positions_and_translate(pos_list)
    assert mda.position_groupbox._table.rowCount() == 3

    pos = mda.position_groupbox.value()

    assert pos == [
        {"name": "A1_pos000", "x": 0.0, "y": 200.0, "z": 0.0},
        {"name": "A1_pos001", "x": 300.0, "y": 300.0, "z": 0.0},
        {"name": "A1_pos002", "x": 400.0, "y": -200.0, "z": 0.0},
    ]