from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock, call

from pymmcore_plus import CMMCorePlus
from useq import GridFromEdges, GridRelative, MDASequence

from pymmcore_widgets._mda import GridWidget, MDAWidget

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


def test_mda_widget_load_state(qtbot: QtBot):
    wdg = MDAWidget(include_run_button=True)
    qtbot.addWidget(wdg)
    assert wdg.position_groupbox._table.rowCount() == 0
    assert wdg.channel_groupbox._table.rowCount() == 0
    assert not wdg.time_groupbox.isChecked()

    wdg._set_enabled(False)
    assert not wdg.time_groupbox.isEnabled()
    assert not wdg.buttons_wdg.acquisition_order_comboBox.isEnabled()
    assert not wdg.channel_groupbox.isEnabled()
    assert not wdg.position_groupbox.isEnabled()
    assert not wdg.stack_groupbox.isEnabled()
    wdg._set_enabled(True)

    sequence = MDASequence(
        channels=[
            {"config": "Cy5", "exposure": 20},
            {"config": "FITC", "exposure": 50},
        ],
        time_plan={"interval": 2, "loops": 5},
        z_plan={"range": 4, "step": 0.5},
        axis_order="tpgcz",
        stage_positions=(
            {"name": "Pos000", "x": 222, "y": 1, "z": 1},
            {"name": "Pos001", "x": 111, "y": 0, "z": 0},
        ),
    )
    wdg.set_state(sequence)
    assert wdg.position_groupbox._table.rowCount() == 2
    assert wdg.channel_groupbox._table.rowCount() == 2
    assert wdg.time_groupbox.isChecked()

    # round trip
    assert wdg.get_state() == sequence


def test_mda_buttons(qtbot: QtBot, global_mmcore: CMMCorePlus):
    wdg = MDAWidget(include_run_button=True)
    qtbot.addWidget(wdg)

    assert wdg.channel_groupbox._table.rowCount() == 0
    wdg.channel_groupbox._add_button.click()
    wdg.channel_groupbox._add_button.click()
    assert wdg.channel_groupbox._table.rowCount() == 2
    wdg.channel_groupbox._table.selectRow(0)
    wdg.channel_groupbox._remove_button.click()
    assert wdg.channel_groupbox._table.rowCount() == 1
    wdg.channel_groupbox._clear_button.click()
    assert wdg.channel_groupbox._table.rowCount() == 0

    assert wdg.position_groupbox._table.rowCount() == 0
    wdg.position_groupbox.setChecked(True)
    wdg.position_groupbox.add_button.click()
    wdg.position_groupbox.add_button.click()
    assert wdg.position_groupbox._table.rowCount() == 2
    wdg.position_groupbox._table.selectRow(0)
    wdg.position_groupbox.remove_button.click()
    assert wdg.position_groupbox._table.rowCount() == 1
    wdg.position_groupbox.clear_button.click()
    assert wdg.position_groupbox._table.rowCount() == 0


def test_mda_methods(qtbot: QtBot, global_mmcore: CMMCorePlus):
    wdg = MDAWidget(include_run_button=True)
    qtbot.addWidget(wdg)

    wdg._on_mda_started()
    assert not wdg.time_groupbox.isEnabled()
    assert not wdg.buttons_wdg.acquisition_order_comboBox.isEnabled()
    assert not wdg.channel_groupbox.isEnabled()
    assert not wdg.position_groupbox.isEnabled()
    assert not wdg.stack_groupbox.isEnabled()
    assert wdg.buttons_wdg.run_button.isHidden()
    assert not wdg.buttons_wdg.pause_button.isHidden()
    assert not wdg.buttons_wdg.cancel_button.isHidden()

    wdg._on_mda_finished()
    assert wdg.time_groupbox.isEnabled()
    assert wdg.buttons_wdg.acquisition_order_comboBox.isEnabled()
    assert wdg.channel_groupbox.isEnabled()
    assert wdg.position_groupbox.isEnabled()
    assert wdg.stack_groupbox.isEnabled()
    assert not wdg.buttons_wdg.run_button.isHidden()
    assert wdg.buttons_wdg.pause_button.isHidden()
    assert wdg.buttons_wdg.cancel_button.isHidden()


def test_mda_grid(qtbot: QtBot, global_mmcore: CMMCorePlus):
    grid_wdg = GridWidget()
    qtbot.addWidget(grid_wdg)

    global_mmcore.setProperty("Objective", "Label", "Objective-2")
    assert not global_mmcore.getPixelSizeUm()
    grid_wdg._update_info_label()
    assert (
        grid_wdg.info_lbl.text()
        == "Width: _ mm    Height: _ mm    (Columns: _    Rows: _)"
    )

    global_mmcore.setProperty("Objective", "Label", "Nikon 20X Plan Fluor ELWD")
    assert global_mmcore.getPixelSizeUm() == 0.5
    assert tuple(global_mmcore.getXYPosition()) == (0.0, 0.0)
    assert tuple(global_mmcore.getROI()) == (0, 0, 512, 512)

    grid_wdg.set_state(GridRelative(rows=2, columns=2))
    assert (
        grid_wdg.info_lbl.text()
        == "Width: 0.512 mm    Height: 0.512 mm    (Columns: 2    Rows: 2)"
    )

    mock = Mock()
    grid_wdg.valueChanged.connect(mock)

    grid_wdg._emit_grid_positions()

    mock.assert_has_calls([call(grid_wdg.value())])

    grid_wdg.set_state(
        GridFromEdges(top=256, bottom=-256, left=-256, right=256, overlap=(0.0, 50.0))
    )
    assert (
        grid_wdg.info_lbl.text()
        == "Width: 0.768 mm    Height: 0.768 mm    (Columns: 3    Rows: 3)"
    )

    grid_wdg._emit_grid_positions()

    mock.assert_has_calls([call(grid_wdg.value())])


def test_set_and_get_state(qtbot: QtBot, global_mmcore: CMMCorePlus):
    grid_wdg = GridWidget()
    qtbot.addWidget(grid_wdg)

    grid_wdg.set_state(
        GridRelative(rows=3, columns=3, overlap=15.0, relative_to="top_left")
    )
    assert grid_wdg.value() == {
        "overlap": (15.0, 15.0),
        "mode": "row_wise_snake",
        "rows": 3,
        "columns": 3,
        "relative_to": "top_left",
    }

    grid_wdg.set_state(
        GridFromEdges(top=512, bottom=-512, left=-512, right=512, mode="spiral")
    )
    assert grid_wdg.value() == {
        "overlap": (0.0, 0.0),
        "mode": "spiral",
        "top": 512.0,
        "bottom": -512.0,
        "left": -512.0,
        "right": 512.0,
    }

    grid_wdg.set_state(
        {
            "overlap": (10.0, 0.0),
            "mode": "row_wise_snake",
            "top": 512.0,
            "bottom": -512.0,
            "left": -512.0,
            "right": 512.0,
        }
    )
    assert grid_wdg.value() == {
        "overlap": (10.0, 0.0),
        "mode": "row_wise_snake",
        "top": 512.0,
        "bottom": -512.0,
        "left": -512.0,
        "right": 512.0,
    }


def test_gui_labels(qtbot: QtBot, global_mmcore: CMMCorePlus):
    global_mmcore.setExposure(100)
    wdg = MDAWidget(include_run_button=True)
    qtbot.addWidget(wdg)
    wdg.show()

    assert wdg.channel_groupbox._table.rowCount() == 0
    wdg.channel_groupbox._add_button.click()
    assert wdg.channel_groupbox._table.rowCount() == 1
    assert wdg.channel_groupbox._table.cellWidget(0, 1).value() == 100.0
    assert not wdg.time_groupbox.isChecked()

    txt = "Minimum total acquisition time: 100.0000 ms.\n"
    assert wdg.time_lbl._total_time_lbl.text() == txt
    assert not wdg.time_groupbox._warning_widget.isVisible()

    assert not wdg.time_groupbox.isChecked()
    wdg.time_groupbox.setChecked(True)
    wdg.time_groupbox._units_combo.setCurrentText("ms")
    assert wdg.time_groupbox._warning_widget.isVisible()

    txt = (
        "Minimum total acquisition time: 100.0000 ms.\n"
        "Minimum acquisition time per timepoint: 100.0000 ms."
    )
    assert wdg.time_lbl._total_time_lbl.text() == txt

    wdg.time_groupbox._timepoints_spinbox.setValue(3)
    txt = (
        "Minimum total acquisition time: 300.0000 ms.\n"
        "Minimum acquisition time per timepoint: 100.0000 ms."
    )
    assert wdg.time_lbl._total_time_lbl.text() == txt

    wdg.time_groupbox._interval_spinbox.setValue(10)
    txt1 = (
        "Minimum total acquisition time: 300.0000 ms.\n"
        "Minimum acquisition time per timepoint: 100.0000 ms."
    )
    assert wdg.time_lbl._total_time_lbl.text() == txt1

    wdg.time_groupbox._interval_spinbox.setValue(200)
    txt1 = (
        "Minimum total acquisition time: 500.0000 ms.\n"
        "Minimum acquisition time per timepoint: 100.0000 ms."
    )
    assert wdg.time_lbl._total_time_lbl.text() == txt1
    assert not wdg.time_groupbox._warning_widget.isVisible()
