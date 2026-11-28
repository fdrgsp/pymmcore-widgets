from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock, patch

import pytest
import useq
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QEvent, QPoint, Qt, QTimer
from qtpy.QtWidgets import QApplication, QMessageBox, QWidget

from pymmcore_widgets import HCSWizard
from pymmcore_widgets._util import get_next_available_path
from pymmcore_widgets.mda import MDAWidget
from pymmcore_widgets.mda._core_channels import CoreConnectedChannelTable
from pymmcore_widgets.mda._core_grid import CoreConnectedGridPlanWidget
from pymmcore_widgets.mda._core_mda import AF_NO_AXIS
from pymmcore_widgets.mda._core_positions import (
    AF_UNAVAILABLE,
    CoreConnectedPositionTable,
)
from pymmcore_widgets.mda._core_z import CoreConnectedZPlanWidget
from pymmcore_widgets.mda._save_widget import OME_TIFF, OME_ZARR, WRITERS
from pymmcore_widgets.mda._xy_bounds import CoreXYBoundsControl
from pymmcore_widgets.useq_widgets._grid import Mode
from pymmcore_widgets.useq_widgets._mda_sequence import (
    AF_AXIS_TOOLTIP,
    AF_DISABLED_TOOLTIP,
    PYMMCW_METADATA_KEY,
    AutofocusAxis,
    KeepShutterOpen,
    QFileDialog,
)
from pymmcore_widgets.useq_widgets._positions import (
    AF_PER_POS_TOOLTIP,
    MDAButton,
    _MDAPopup,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pytestqt.qtbot import QtBot

    from pymmcore_widgets.mda._core_mda import CoreMDATabs


TEST_CONFIG = str(Path(__file__).parent / "test_config.cfg")
MDA = useq.MDASequence(
    time_plan=useq.TIntervalLoops(interval=0.01, loops=2),
    stage_positions=[(0, 1, 2), useq.Position(x=42, y=0, z=3)],
    channels=[{"config": "DAPI", "exposure": 1}],
    z_plan=useq.ZRangeAround(range=1, step=0.3),
    grid_plan=useq.GridRowsColumns(rows=2, columns=1),
    axis_order="tpgzc",
    keep_shutter_open_across=("z",),
)

SAVE_META = {
    "save_dir": "dir",
    "save_name": "name.ome.tiff",
    "format": "ome-tiff",
    "should_save": True,
}


def test_core_connected_mda_wdg(qtbot: QtBot):
    wdg = MDAWidget()
    core = wdg._mmc
    qtbot.addWidget(wdg)
    wdg.show()

    wdg.setValue(MDA)
    new_grid = MDA.grid_plan.replace(fov_width=512, fov_height=512)
    assert wdg.value().replace(metadata={}) == MDA.replace(grid_plan=new_grid)

    with qtbot.waitSignal(wdg._mmc.mda.events.sequenceFinished):
        wdg.control_btns.run_btn.click()

    assert wdg.control_btns.pause_btn.text() == "Pause"
    core.mda.events.sequencePauseToggled.emit(True)
    assert wdg.control_btns.pause_btn.text() == "Resume"
    core.mda.events.sequencePauseToggled.emit(False)
    assert wdg.control_btns.pause_btn.text() == "Pause"
    wdg.control_btns._disconnect()
    wdg._disconnect()


def test_core_connected_position_wdg(qtbot: QtBot, qapp) -> None:
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    pos_table = wdg.stage_positions
    assert isinstance(pos_table, CoreConnectedPositionTable)
    wdg.setValue(MDA)
    assert pos_table.table().rowCount() == 2

    p0 = pos_table.value()[0]
    assert p0.x == MDA.stage_positions[0].x
    assert p0.y == MDA.stage_positions[0].y
    assert p0.z == MDA.stage_positions[0].z

    wdg._mmc.setXYPosition(11, 22)
    wdg._mmc.setZPosition(33)
    wdg._mmc.waitForSystem()
    xyidx = pos_table.table().indexOf(pos_table._xy_btn_col)
    z_idx = pos_table.table().indexOf(pos_table._z_btn_col)
    pos_table.table().cellWidget(0, xyidx).click()
    pos_table.table().cellWidget(0, z_idx).click()

    p0 = pos_table.value()[0]
    assert round(p0.x) == 11
    assert round(p0.y) == 22
    assert round(p0.z) == 33

    wdg._mmc.waitForSystem()
    pos_table.move_to_selection.setChecked(True)
    pos_table.table().selectRow(0)
    pos_table._on_selection_change()


def _assert_position_wdg_state(
    stage: str, pos_table: CoreConnectedPositionTable, is_hidden: bool
) -> None:
    """Assert the correct widget state for the given stage."""
    if stage == "XY":
        # both x and y columns should be hidden if XY device is not loaded/selected
        x_col = pos_table.table().indexOf(pos_table.X)
        y_col = pos_table.table().indexOf(pos_table.Y)
        x_hidden = pos_table.table().isColumnHidden(x_col)
        y_hidden = pos_table.table().isColumnHidden(y_col)
        assert x_hidden == is_hidden
        assert y_hidden == is_hidden
        # the set position button should be hidden if XY device is not loaded/selected
        xy_btn_col = pos_table.table().indexOf(pos_table._xy_btn_col)
        xy_btn_hidden = pos_table.table().isColumnHidden(xy_btn_col)
        assert xy_btn_hidden == is_hidden
        # values() should return None for x and y if XY device is not loaded/selected
        if is_hidden:
            xy = [(v.x, v.y) for v in pos_table.value()]
            assert all(x is None and y is None for x, y in xy)

    elif stage == "Z":
        # the set position button should be hidden
        z_btn_col = pos_table.table().indexOf(pos_table._z_btn_col)
        assert pos_table.table().isColumnHidden(z_btn_col)
        # values() should return None for z
        if is_hidden:
            z = [v.z for v in pos_table.value()]
            assert all(z is None for z in z)
        # the include z checkbox should be unchecked
        assert not pos_table.include_z.isChecked()
        # the include z checkbox should be disabled if Z device is not loaded/selected
        assert pos_table.include_z.isEnabled() == (not is_hidden)
        # tooltip should should change if Z device is not loaded/selected
        tooltip = "Focus device unavailable." if is_hidden else ""
        assert pos_table.include_z.toolTip() == tooltip


@pytest.mark.parametrize("stage", ["XY", "Z"])
def test_core_connected_position_wdg_cfg_loaded(
    stage: str, qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    # stage device is not loaded, the respective columns should be hidden and
    # values() should return None. This behavior should change
    # when a new cfg stage device is loaded.
    mmc = global_mmcore
    mmc.unloadDevice(stage)

    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    pos_table = wdg.stage_positions
    assert isinstance(pos_table, CoreConnectedPositionTable)

    wdg.setValue(MDA)

    # stage is not loaded
    _assert_position_wdg_state(stage, pos_table, is_hidden=True)

    with qtbot.waitSignal(mmc.events.systemConfigurationLoaded):
        mmc.loadSystemConfiguration(TEST_CONFIG)

    # stage is loaded (systemConfigurationLoaded is triggered)
    _assert_position_wdg_state(stage, pos_table, is_hidden=False)


@pytest.mark.parametrize("stage", ["XY", "Z"])
def test_core_connected_position_wdg_property_changed(
    stage: str, qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    # if stage device are loaded but not set as default device, their respective columns
    # should be hidden and values() should return None. This behavior should change when
    # stage device is set as default device.
    mmc = global_mmcore

    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    pos_table = wdg.stage_positions
    assert isinstance(pos_table, CoreConnectedPositionTable)

    wdg.setValue(MDA)

    with qtbot.waitSignal(mmc.events.propertyChanged):
        if stage == "XY":
            mmc.setProperty("Core", "XYStage", "")
        elif stage == "Z":
            mmc.setProperty("Core", "Focus", "")
        mmc.waitForSystem()

    # stage is not set as default device
    _assert_position_wdg_state(stage, pos_table, is_hidden=True)

    with qtbot.waitSignal(mmc.events.propertyChanged):
        if stage == "XY":
            mmc.setProperty("Core", "XYStage", "XY")
        elif stage == "Z":
            mmc.setProperty("Core", "Focus", "Z")

    # stage is set as default device (propertyChanged is triggered)
    _assert_position_wdg_state(stage, pos_table, is_hidden=False)


@pytest.fixture
def mock_getAutoFocusOffset(global_mmcore: CMMCorePlus):
    # core.getAutoFocusOffset() with the demo Autofocus device does not do
    # anything, so we need to mock it
    def _getAutoFocusOffset():
        return 10

    with patch.object(global_mmcore, "getAutoFocusOffset", _getAutoFocusOffset):
        yield


def test_core_position_table_starts_empty(qtbot: QtBot) -> None:
    """The MDA widget has no default position at x=0, y=0, z=0.

    With the Positions tab checked and no row, the MDA uses the current position.
    """
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg._mmc.setXYPosition(11, 22)
    wdg._mmc.setZPosition(33)
    wdg._mmc.waitForSystem()

    assert wdg.stage_positions.table().rowCount() == 0
    wdg.tab_wdg.setChecked(wdg.stage_positions, True)
    (pos,) = wdg.value().stage_positions
    assert (round(pos.x), round(pos.y), round(pos.z)) == (11, 22, 33)


def test_core_position_table_add_position(
    qtbot: QtBot, mock_getAutoFocusOffset: None
) -> None:
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    wdg.tab_wdg.setChecked(wdg.stage_positions, True)
    pos_table = wdg.stage_positions
    assert isinstance(pos_table, CoreConnectedPositionTable)

    wdg._mmc.setXYPosition(11, 22)
    wdg._mmc.setZPosition(33)

    wdg.stage_positions.af_per_position.setChecked(True)

    assert pos_table.table().rowCount() == 0

    # test when autofocus is not engaged and af_per_position is checked
    with patch.object(
        QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok
    ):
        with qtbot.waitSignals([pos_table.valueChanged], order="strict", timeout=1000):
            pos_table.act_add_row.trigger()

    # a new position has NOT been added
    assert pos_table.table().rowCount() == 0

    # test when autofocus is engaged and af_per_position is checked
    with patch.object(wdg._mmc, "isContinuousFocusLocked", return_value=True):
        with qtbot.waitSignals([pos_table.valueChanged], order="strict", timeout=1000):
            pos_table.act_add_row.trigger()

    val = pos_table.value()[-1]
    assert round(val.x, 1) == 11
    assert round(val.y, 1) == 22
    assert round(val.z, 1) == 33
    # setting it to to 10 because the mock_getAutoFocusOffset() returns 10
    assert val.sequence.autofocus_plan.autofocus_motor_offset == 10

    # a new position has been added
    assert pos_table.table().rowCount() == 1


def test_core_connected_relative_z_plan(qtbot: QtBot):
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    wdg._mmc.setXYPosition(11, 22)
    wdg._mmc.setZPosition(33)
    wdg._mmc.waitForSystem()

    MDA = useq.MDASequence(
        channels=[{"config": "DAPI", "exposure": 1}],
        z_plan=useq.ZRangeAround(range=1, step=0.3),
        axis_order="pzc",
    )
    wdg.setValue(MDA)

    val = wdg.value().stage_positions[-1]
    assert round(val.x, 1) == 11
    assert round(val.y, 1) == 22
    assert round(val.z, 1) == 33
    assert not val.sequence


def test_position_table_connected_popup(qtbot: QtBot):
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    wdg.setValue(MDA)

    pos_table = wdg.stage_positions
    assert isinstance(pos_table, CoreConnectedPositionTable)
    seq_col = pos_table.table().indexOf(pos_table.SEQ)
    btn = pos_table.table().cellWidget(0, seq_col)

    def handle_dialog():
        popup = btn.findChild(_MDAPopup)
        mda = popup.mda_tabs
        assert isinstance(mda.z_plan, CoreConnectedZPlanWidget)
        assert isinstance(mda.grid_plan, CoreConnectedGridPlanWidget)
        popup.accept()

    QTimer.singleShot(100, handle_dialog)

    with qtbot.waitSignal(wdg.valueChanged):
        btn.seq_btn.click()


def test_core_position_table_checkboxes_toggled(qtbot: QtBot):
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()
    pos_table = wdg.stage_positions
    assert isinstance(pos_table, CoreConnectedPositionTable)

    wdg.setValue(MDA)

    z_btn_col = pos_table.table().indexOf(pos_table._z_btn_col)
    af_btn_col = pos_table.table().indexOf(pos_table._af_btn_col)

    pos_table.include_z.setChecked(False)
    pos_table.af_per_position.setChecked(False)

    assert pos_table.table().isColumnHidden(z_btn_col)
    assert pos_table.table().isColumnHidden(af_btn_col)

    pos_table.include_z.setChecked(True)
    pos_table.af_per_position.setChecked(True)

    assert not pos_table.table().isColumnHidden(z_btn_col)
    assert not pos_table.table().isColumnHidden(af_btn_col)


def test_core_mda_autofocus(qtbot: QtBot):
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    AF = useq.AxesBasedAF(autofocus_motor_offset=10, axes=("p",))
    POS = [
        useq.Position(x=0, y=0, z=0, sequence=useq.MDASequence(autofocus_plan=AF)),
        useq.Position(x=1, y=1, z=1, sequence=useq.MDASequence(autofocus_plan=AF)),
    ]
    MDA = useq.MDASequence(stage_positions=POS)
    wdg.setValue(MDA)

    assert wdg.value().autofocus_plan
    assert wdg.value().autofocus_plan.autofocus_motor_offset == 10
    assert not wdg.value().stage_positions[0].sequence
    assert not wdg.value().stage_positions[1].sequence

    AF1 = useq.AxesBasedAF(autofocus_motor_offset=15, axes=("p",))
    POS1 = [
        useq.Position(x=0, y=0, z=0, sequence=useq.MDASequence(autofocus_plan=AF)),
        useq.Position(x=1, y=1, z=1, sequence=useq.MDASequence(autofocus_plan=AF1)),
    ]
    MDA = MDA.replace(stage_positions=POS1)

    # here we need to mock the core isContinuousFocusLocked method because the Autofocus
    # demo device cannot be set as "Locked in Focus" and since af_per_position is
    # checked, we would trigger a warning dialog (dialog is tested in previous test)
    with patch.object(wdg._mmc, "isContinuousFocusLocked", return_value=True):
        wdg.setValue(MDA)
    assert not wdg.value().autofocus_plan
    assert (
        wdg.value().stage_positions[0].sequence.autofocus_plan.autofocus_motor_offset
        == 10
    )
    assert (
        wdg.value().stage_positions[1].sequence.autofocus_plan.autofocus_motor_offset
        == 15
    )

    POS2 = [
        useq.Position(x=0, y=0, z=0, sequence=useq.MDASequence(autofocus_plan=AF)),
        useq.Position(
            x=0,
            y=0,
            z=0,
            sequence=useq.MDASequence(
                autofocus_plan=AF,
                grid_plan=useq.GridRowsColumns(rows=2, columns=1),
            ),
        ),
    ]
    MDA = MDA.replace(stage_positions=POS2)

    with patch.object(wdg._mmc, "isContinuousFocusLocked", return_value=True):
        wdg.setValue(MDA)
    assert wdg.value().autofocus_plan
    assert wdg.value().autofocus_plan.autofocus_motor_offset == 10
    assert not wdg.value().stage_positions[0].sequence
    assert wdg.value().stage_positions[1].sequence


def test_af_axis_wdg(qtbot: QtBot):
    wdg = AutofocusAxis()
    qtbot.addWidget(wdg)
    wdg.show()

    assert not wdg.value()
    wdg.setValue(("p", "t", "g"))
    assert wdg.value() == ("p", "t", "g")


def test_keep_shutter_open_wdg(qtbot: QtBot):
    wdg = KeepShutterOpen()
    qtbot.addWidget(wdg)
    wdg.show()

    assert not wdg.value()
    wdg.setValue(("z", "t"))
    assert wdg.value() == ("z", "t")


def test_run_mda_af_warning(qtbot: QtBot):
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    MDA = useq.MDASequence(
        stage_positions=[useq.Position(x=0, y=0, z=0)],
        time_plan=useq.TIntervalLoops(interval=1, loops=2),
        autofocus_plan=useq.AxesBasedAF(axes=("p", "t")),
    )
    wdg.setValue(MDA)

    def _cancel(*args, **kwargs):
        return QMessageBox.StandardButton.Cancel

    with patch.object(QMessageBox, "warning", _cancel):
        wdg.control_btns.run_btn.click()

    assert not wdg._mmc.mda.is_running()

    def _ok(*args, **kwargs):
        return QMessageBox.StandardButton.Ok

    with patch.object(QMessageBox, "warning", _ok):
        with qtbot.waitSignal(wdg._mmc.mda.events.sequenceStarted):
            wdg.control_btns.run_btn.click()
        with qtbot.waitSignal(wdg._mmc.mda.events.sequenceFinished):
            assert wdg._mmc.mda.is_running()


def test_run_and_save_take_what_is_being_typed(
    global_mmcore: CMMCorePlus, qtbot: QtBot, tmp_path: Path
) -> None:
    """Without Enter: on macOS, clicking Run or Save leaves the editor its focus."""
    wdg = MDAWidget(mmcore=global_mmcore)
    qtbot.addWidget(wdg)
    wdg.show()
    wdg.activateWindow()
    qtbot.waitExposed(wdg)
    qtbot.wait(100)
    if not wdg.isActiveWindow():  # pragma: no cover
        pytest.skip("focus needs the window to be active, which CI cannot always do")
    wdg.setValue(useq.MDASequence(time_plan={"interval": 1, "loops": 5}))
    table = wdg.time_plan.table()

    def type_loops(text: str) -> None:
        spin = table.cellWidget(0, table.indexOf(wdg.time_plan.LOOPS))
        spin.setFocus()
        qtbot.keyClick(
            spin.lineEdit(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier
        )
        qtbot.keyClicks(spin.lineEdit(), text)

    # still the old value while typing: this is what would otherwise run
    type_loops("30")
    assert wdg.value().time_plan.loops == 5
    wdg.prepare_mda()
    assert wdg.value().time_plan.loops == 30

    type_loops("40")
    dest = tmp_path / "seq.yaml"
    wdg.save(dest)
    assert useq.MDASequence.from_file(dest).time_plan.loops == 40


def test_run_mda_af_without_axis(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.af_axis.setChecked(True)

    with patch.object(QMessageBox, "warning") as warning:
        assert wdg.prepare_mda() is False
    warning.assert_called_once_with(wdg, "Autofocus", AF_NO_AXIS)

    # nothing to warn about with autofocus switched off
    wdg.af_axis.setChecked(False)
    assert wdg.prepare_mda() is None


def test_af_per_position_enabled_after_run(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """'Set AF Offset per Position' is enabled again after an MDA run.

    The MDA widget disables its tabs while an MDA runs. When the run ends, the
    checkbox, its checked state and the AF column must come back.
    """
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.setValue(useq.MDASequence(stage_positions=[(0, 0, 0)]))
    pos_table = wdg.stage_positions
    af = pos_table.af_per_position
    af.setChecked(True)
    af_col = pos_table.table().indexOf(pos_table.AF)
    assert af.isEnabled()
    assert not pos_table.table().isColumnHidden(af_col)

    with qtbot.waitSignal(global_mmcore.mda.events.sequenceFinished):
        wdg.execute_mda(None)

    qtbot.waitUntil(af.isEnabled, timeout=2000)
    assert af.isChecked()
    assert not pos_table.table().isColumnHidden(af_col)


def test_af_axes_kept_after_run_with_unchecked_positions(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """The AF axes stay in the sequence after a run with an unchecked Positions tab.

    The content of an unchecked tab must stay disabled after a run. Else 'Set AF
    Offset per Position' is active, and value() leaves out the AF axes.
    """
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.stage_positions.af_per_position.setChecked(True)
    wdg.tab_wdg.setChecked(wdg.stage_positions, False)
    wdg.af_axis.setValue(("t",))
    assert wdg.value().autofocus_plan.axes == ("t",)

    with qtbot.waitSignal(global_mmcore.mda.events.sequenceFinished):
        wdg.execute_mda(None)

    qtbot.waitUntil(wdg.save_info.isEnabled, timeout=2000)
    assert not wdg.stage_positions.isEnabled()
    assert wdg.value().autofocus_plan.axes == ("t",)
    # also if the content of the unchecked tab is enabled in another way
    wdg.stage_positions.setEnabled(True)
    assert wdg.value().autofocus_plan.axes == ("t",)


@pytest.mark.parametrize("trigger", ["zplan", "core"])
def test_af_stays_disabled_after_run(
    qtbot: QtBot, global_mmcore: CMMCorePlus, trigger: str
) -> None:
    """The autofocus stays disabled after a run if it cannot be used.

    This is the case with an absolute (TOP_BOTTOM) z plan, or with no autofocus device.
    """
    mmc = global_mmcore
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.af_axis.setValue(("t",))
    if trigger == "zplan":
        wdg.tab_wdg.setChecked(wdg.z_plan, True)
        wdg.z_plan.setValue(useq.ZTopBottom(top=1, bottom=0, step=1))
    else:
        with qtbot.waitSignal(mmc.events.propertyChanged):
            mmc.setProperty("Core", "AutoFocus", "")
    assert not wdg.af_axis.isEnabled()
    assert not wdg.value().autofocus_plan

    with qtbot.waitSignal(mmc.mda.events.sequenceFinished):
        wdg.execute_mda(None)

    qtbot.waitUntil(wdg.save_info.isEnabled, timeout=2000)
    assert not wdg.af_axis.isEnabled()
    assert not wdg.value().autofocus_plan


def test_core_connected_channel_wdg(qtbot: QtBot):
    wdg = CoreConnectedChannelTable()
    qtbot.addWidget(wdg)
    wdg.show()

    # delete current channel group
    wdg._mmc.deleteConfigGroup("Channel")

    # "Channel" not in combo
    assert "Channel" not in [
        wdg._group_combo.itemText(i) for i in range(wdg._group_combo.count())
    ]

    # create new channel group called "Channels" (before it was "Channel")
    wdg._mmc.defineConfig("Channels", "DAPI", "Dichroic", "Label", "400DCLP")
    wdg._mmc.defineConfig("Channels", "FITC", "Dichroic", "Label", "Q505LP")

    assert "Channel" not in wdg._mmc.getAvailableConfigGroups()
    assert "Channels" in wdg._mmc.getAvailableConfigGroups()

    wdg._group_combo.setCurrentText("Channels")

    with qtbot.waitSignals([wdg.valueChanged], order="strict", timeout=1000):
        wdg.act_add_row.trigger()
        wdg.act_add_row.trigger()

    value = wdg.value()
    assert len(value) == 2
    assert value[0].group == "Channels"
    assert value[1].group == "Channels"


def test_enable_core_tab(qtbot: QtBot):
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    def wdgs_enabled(mda_tabs: CoreMDATabs) -> bool:
        return (
            mda_tabs.time_plan.isEnabled()
            and mda_tabs.stage_positions.isEnabled()
            and mda_tabs.z_plan.isEnabled()
            and mda_tabs.grid_plan.isEnabled()
            and mda_tabs.channels.isEnabled()
        )

    mda_tabs = cast("CoreMDATabs", wdg.tab_wdg)
    for t in range(mda_tabs.count()):
        mda_tabs.setChecked(t, True)
    mda_tabs._enable_tabs(False)

    mda_tabs._enable_tabs(True)
    # all tabs are enabled (you can switch between them)
    assert all(mda_tabs.tabBar().isTabEnabled(t) for t in range(mda_tabs.count()))
    # the tabs checkboxes are enabled
    assert all(cbox.isEnabled() for cbox in mda_tabs._cboxes)
    # the the tabs content is enabled
    assert wdgs_enabled(mda_tabs)

    mda_tabs._enable_tabs(False)

    # all tabs are still enabled (you can still switch between them)
    assert all(mda_tabs.tabBar().isTabEnabled(t) for t in range(mda_tabs.count()))
    # the tab checkboxes are disabled
    assert not any(cbox.isEnabled() for cbox in mda_tabs._cboxes)
    # the the tabs content is disabled
    assert not any(
        wdg.isEnabled()
        for wdg in (
            mda_tabs.time_plan,
            mda_tabs.stage_positions,
            mda_tabs.z_plan,
            mda_tabs.grid_plan,
            mda_tabs.channels,
        )
    )

    # the content of an unchecked tab stays disabled
    mda_tabs.setChecked(mda_tabs.time_plan, False)
    mda_tabs._enable_tabs(True)
    assert not mda_tabs.time_plan.isEnabled()
    assert mda_tabs.stage_positions.isEnabled()


def test_saving_tab(qtbot: QtBot) -> None:
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    tabs, save_info = wdg.tab_wdg, wdg.save_info

    assert not tabs.isChecked(save_info)
    save_info.setChecked(True)
    assert tabs.isChecked(save_info)
    tabs.setChecked(save_info, False)
    assert not save_info.isChecked()
    # its fields are disabled by the group box itself, not by the tab
    assert save_info.isEnabled()


def test_editors_locked_during_run(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    """A run locks the editors, but not Pause/Cancel or switching tabs."""
    mmc = global_mmcore
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()
    seq = useq.MDASequence(channels=["DAPI"], time_plan={"interval": 0.1, "loops": 50})
    wdg.setValue(seq)
    editors = (wdg.channels, wdg.time_plan, wdg.save_info, wdg.af_axis)
    btns = wdg.control_btns

    with qtbot.waitSignal(mmc.mda.events.sequenceStarted):
        btns.run_btn.click()
    assert not any(editor.isEnabled() for editor in editors)
    assert not wdg._settings_box.isEnabled()
    assert not wdg._load_button.isEnabled()
    wdg.tab_wdg.setCurrentWidget(wdg.z_plan)
    assert wdg.tab_wdg.currentWidget() is wdg.z_plan

    # isEnabled() is False if any ancestor is disabled
    assert btns.pause_btn.isEnabled()
    assert btns.cancel_btn.isEnabled()
    with qtbot.waitSignal(mmc.mda.events.sequenceFinished):
        btns.cancel_btn.click()

    assert all(editor.isEnabled() for editor in editors)
    assert wdg._settings_box.isEnabled()
    # the content of an unchecked tab stays disabled
    assert not wdg.z_plan.isEnabled()


def test_relative_z_with_no_include_z(global_mmcore: CMMCorePlus, qtbot: QtBot):
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    MDA = useq.MDASequence(
        channels=[{"config": "DAPI", "exposure": 1}],
        stage_positions=[(1, 2, 3), (4, 5, 6)],
        z_plan=useq.ZRangeAround(go_up=True, range=2.0, step=1.0),
    )
    wdg.setValue(MDA)

    wdg._mmc.setZPosition(30)
    wdg._mmc.waitForSystem()

    assert wdg.stage_positions.include_z.isChecked()
    assert wdg.value().stage_positions[0].z == 3
    assert wdg.value().stage_positions[1].z == 6

    wdg.stage_positions.include_z.setChecked(False)
    assert not wdg.stage_positions.include_z.isChecked()
    assert wdg.value().stage_positions[0].z == 30
    assert wdg.value().stage_positions[1].z == 30


def test_mda_no_pos_set(global_mmcore: CMMCorePlus, qtbot: QtBot):
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    global_mmcore.setXYPosition(10, 20)
    global_mmcore.setZPosition(30)

    MDA = useq.MDASequence(channels=[{"config": "DAPI", "exposure": 1}])
    wdg.setValue(MDA)
    wdg._mmc.waitForSystem()

    assert wdg.value().stage_positions
    assert round(wdg.value().stage_positions[0].x) == 10
    assert round(wdg.value().stage_positions[0].y) == 20
    assert round(wdg.value().stage_positions[0].z) == 30

    assert wdg.value().axis_order[0] == "p"


@pytest.mark.parametrize("ext", ["json", "yaml"])
def test_core_mda_wdg_load_save(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ext: str
) -> None:
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    dest = tmp_path / f"sequence.{ext}"
    # monkeypatch the dialog to load/save to our temp file
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (dest, None))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (dest, None))

    # write the sequence to file and load the widget from it
    mda = MDA.replace(metadata={**MDA.metadata, PYMMCW_METADATA_KEY: SAVE_META})
    dest.write_text(mda.yaml() if ext == "yaml" else mda.model_dump_json())
    wdg.load()

    meta = wdg.value().metadata[PYMMCW_METADATA_KEY]
    assert meta["save_dir"] == SAVE_META["save_dir"]
    assert meta["save_name"] == SAVE_META["save_name"]
    assert meta["format"] == SAVE_META["format"]

    # save the widget to file and load it back
    dest.unlink()
    wdg.save()
    assert useq.MDASequence.from_file(dest).metadata[PYMMCW_METADATA_KEY] == meta


def test_mda_set_value_with_seq_metadata(qtbot: QtBot) -> None:
    """Test setting the value of the MDAWidget with a seq that has save metadata."""
    mda = MDAWidget()
    qtbot.addWidget(mda)

    mda.setValue(useq.MDASequence(metadata={PYMMCW_METADATA_KEY: SAVE_META}))
    assert mda.save_info.isChecked()
    assert mda.save_info.save_dir.text() == SAVE_META["save_dir"]
    assert mda.save_info.save_name.text() == SAVE_META["save_name"]
    assert mda.save_info._writer_combo.currentText() == SAVE_META["format"]


def test_mda_sequenceFinished_save_name(
    global_mmcore: CMMCorePlus,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that the save name is updated after the sequence is finished."""
    mda_wdg = MDAWidget(mmcore=global_mmcore)
    qtbot.addWidget(mda_wdg)

    # add a file to tempdir
    requested_file = tmp_path / "name.ome.tiff"

    mda_wdg.save_info.setValue(requested_file)
    assert mda_wdg.save_info.isChecked()
    assert mda_wdg.save_info.value()["save_name"] == "name.ome.tiff"

    requested_file.touch()  # mock the write
    mda_wdg._on_mda_finished(mda_wdg.value())

    # the save widget should now have a new name
    assert mda_wdg.save_info.value()["save_name"] == "name_001.ome.tiff"


@pytest.mark.parametrize("extension", [".ome.tiff", ".ome.tif", ".ome.zarr", ""])
def test_get_next_available_paths(extension: str, tmp_path: Path) -> None:
    # non existing paths returns the same path
    path = tmp_path / f"test{extension}"
    assert get_next_available_path(path) == path

    make: Callable = Path.mkdir if extension in {".ome.zarr", ""} else Path.touch

    # existing files add a counter to the path
    make(path)
    assert get_next_available_path(path) == tmp_path / f"test_001{extension}"

    # if a path with a counter exists, the next (maximum) counter is used
    make(tmp_path / f"test_004{extension}")
    assert get_next_available_path(path) == tmp_path / f"test_005{extension}"


def test_get_next_available_paths_special_cases(tmp_path: Path) -> None:
    base = tmp_path / "test.txt"
    assert get_next_available_path(base).name == base.name

    # only 3+ digit numbers are considered as counters
    (tmp_path / "test_04.txt").touch()
    assert get_next_available_path(base).name == base.name

    # if an existing thing with a higher number is there, the next number is used
    # (even if the requested path does not exist, but has a lower number)
    (tmp_path / "test_004.txt").touch()
    assert get_next_available_path(tmp_path / "test_003.txt").name == "test_005.txt"

    # if we explicitly ask for a higher number, we should get it
    assert get_next_available_path(tmp_path / "test_010.txt").name == "test_010.txt"

    # only 3+ digit numbers are considered as counters
    assert get_next_available_path(tmp_path / "test_02.txt").name == "test_02_005.txt"

    # we go to the next number of digits if need be
    (tmp_path / "test_999.txt").touch()
    assert get_next_available_path(base).name == "test_1000.txt"

    # more than 3 digits are used as is
    high = tmp_path / "test_12345.txt"
    high.touch()
    assert get_next_available_path(high).name == "test_12346.txt"


def test_core_mda_with_hcs_value(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    # uncheck all tabs
    for t in range(wdg.tab_wdg.count() + 1):
        wdg.tab_wdg.setChecked(t, False)

    assert wdg.stage_positions._hcs_wizard is None
    assert wdg.stage_positions._plate_plan is None

    pos = useq.WellPlatePlan(
        plate="96-well", a1_center_xy=(0, 0), selected_wells=((0, 1), (0, 1))
    )
    seq = useq.MDASequence(stage_positions=pos)

    mock = Mock()
    wdg.valueChanged.connect(mock)
    wdg.setValue(seq)
    mock.assert_called_once()

    assert wdg.value().stage_positions == pos
    assert wdg.stage_positions.table().rowCount() == len(pos)

    assert isinstance(wdg.stage_positions._hcs_wizard, HCSWizard)
    assert wdg.stage_positions._plate_plan == pos


def test_core_mda_with_hcs_enable_disable(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    table = wdg.stage_positions.table()
    name_col = table.indexOf(wdg.stage_positions.NAME)
    xy_btn_col = table.indexOf(wdg.stage_positions._xy_btn_col)
    z_btn_col = table.indexOf(wdg.stage_positions._z_btn_col)
    z_col = table.indexOf(wdg.stage_positions.Z)
    sub_seq_btn_col = table.indexOf(wdg.stage_positions.SEQ)

    mda = useq.MDASequence(stage_positions=[(0, 0, 0), (1, 1, 1)])
    wdg.setValue(mda)

    # edit table btn is hidden
    assert wdg.stage_positions._edit_hcs_pos.isHidden()
    # all table visible
    assert not table.isColumnHidden(name_col)
    assert not table.isColumnHidden(xy_btn_col)
    assert not table.isColumnHidden(z_btn_col)
    assert not table.isColumnHidden(z_col)
    assert not table.isColumnHidden(sub_seq_btn_col)
    # name_col_checkbox can be checked
    for row in range(table.rowCount()):
        item = table.item(row, name_col)
        assert item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    # all toolbar actions enabled
    assert all(action.isEnabled() for action in wdg.stage_positions.toolBar().actions())
    # include_z checkbox enabled
    assert wdg.stage_positions.include_z.isEnabled()
    # autofocus checkbox enabled
    assert wdg.stage_positions.af_per_position.isEnabled()

    mda = useq.MDASequence(
        stage_positions=useq.WellPlatePlan(
            plate="96-well",
            a1_center_xy=(0, 0),
            selected_wells=((0, 1), (0, 1)),
        )
    )
    wdg.setValue(mda)

    # edit table btn is visible
    assert not wdg.stage_positions._edit_hcs_pos.isHidden()
    # all columns hidden but name
    assert not table.isColumnHidden(name_col)
    assert table.isColumnHidden(xy_btn_col)
    assert table.isColumnHidden(z_btn_col)
    assert table.isColumnHidden(z_col)
    assert table.isColumnHidden(sub_seq_btn_col)
    # name_col_checkbox cannot be checked
    for row in range(table.rowCount()):
        item = table.item(row, name_col)
        assert not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    # all toolbar actions disabled but the move stage checkbox
    assert all(
        not action.isEnabled() for action in wdg.stage_positions.toolBar().actions()[1:]
    )
    # include_z checkbox disabled
    assert wdg.stage_positions.include_z.isHidden()
    # autofocus checkbox disabled
    assert wdg.stage_positions.af_per_position.isHidden()


@pytest.mark.parametrize("ext", ["json", "yaml"])
def test_core_mda_with_hcs_load_save(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ext: str
) -> None:
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    dest = tmp_path / f"sequence.{ext}"
    # monkeypatch the dialog to load/save to our temp file
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (dest, None))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (dest, None))

    # write the sequence to file and load the widget from it
    mda = MDA.replace(
        stage_positions=useq.WellPlatePlan(
            plate="96-well",
            a1_center_xy=(0, 0),
            selected_wells=((0, 0), (1, 1)),
            well_points_plan=useq.RelativePosition(fov_width=512.0, fov_height=512.0),
        )
    )
    dest.write_text(mda.yaml() if ext == "yaml" else mda.model_dump_json())
    wdg.load()

    pos = wdg.value().stage_positions

    # save the widget to file and load it back
    dest.unlink()
    wdg.save()
    assert useq.MDASequence.from_file(dest).stage_positions == pos


SEQ = useq.MDASequence(
    metadata={"pymmcore_widgets": {"version": "0.7.3.dev116+g74ab0881"}},
    axis_order=("p", "c"),
    stage_positions=(
        useq.AbsolutePosition(
            x=0.0,
            y=0.0,
            z=0.0,
            sequence=useq.MDASequence(
                autofocus_plan=useq.AxesBasedAF(autofocus_motor_offset=0.0, axes=("p",))
            ),
        ),
        useq.AbsolutePosition(
            x=0.0,
            y=0.0,
            z=0.0,
            sequence=useq.MDASequence(
                autofocus_plan=useq.AxesBasedAF(
                    autofocus_motor_offset=10.0, axes=("p",)
                )
            ),
        ),
    ),
    channels=(useq.Channel(config="DAPI", exposure=100.0),),
)


def test_core_mda_autofocus_set_value(
    qtbot: QtBot,
    global_mmcore: CMMCorePlus,
) -> None:
    mmc = global_mmcore
    mmc.unloadDevice("Autofocus")  # or mmc.setProperty("Core", "AutoFocus", "")

    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    wdg.setValue(SEQ)

    # even if autofocus_plans are in SEQ, the autofocus options should be disabled
    # since the autofocus device is not loaded
    assert not wdg.value().autofocus_plan
    assert not wdg.value().stage_positions[0].sequence
    assert not wdg.value().stage_positions[1].sequence
    assert not wdg.af_axis.isEnabled()
    assert wdg.af_axis.use_af_p.isChecked()
    assert wdg.af_axis.value() == ()
    assert wdg.af_axis.toolTip() == AF_UNAVAILABLE
    assert not wdg.stage_positions.af_per_position.isEnabled()
    assert wdg.stage_positions.af_per_position.isChecked()
    assert wdg.stage_positions.af_per_position.toolTip() == AF_UNAVAILABLE
    af_btn_col = wdg.stage_positions.table().indexOf(wdg.stage_positions._af_btn_col)
    assert wdg.stage_positions.table().isColumnHidden(af_btn_col)


@pytest.mark.parametrize("trigger", ["zplan", "core"])
def test_core_mda_autofocus_and_z_plan(
    qtbot: QtBot, global_mmcore: CMMCorePlus, trigger: str
) -> None:
    mmc = global_mmcore
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    pos_table = wdg.stage_positions
    af_col = pos_table.table().indexOf(pos_table.AF)
    af_btn_col = pos_table.table().indexOf(pos_table._af_btn_col)

    wdg.setValue(SEQ)

    # no general autofocus plan since each pos has a sequence with autofocus plan
    assert not wdg.value().autofocus_plan
    assert wdg.value().stage_positions[0].sequence.autofocus_plan
    assert wdg.value().stage_positions[1].sequence.autofocus_plan

    # af_axis and af_per_position should be checked and enabled
    assert wdg.af_axis.value() == ("p",)
    assert wdg.af_axis.toolTip() == AF_AXIS_TOOLTIP
    assert pos_table.af_per_position.isEnabled()
    assert pos_table.af_per_position.isChecked()
    assert pos_table.af_per_position.toolTip() == AF_PER_POS_TOOLTIP
    # AF column should be visible
    assert not pos_table.table().isColumnHidden(af_col)
    assert not pos_table.table().isColumnHidden(af_btn_col)

    # trigger the z plan tab TopBottom mode
    if trigger == "zplan":
        # an absolute Z plan set from code rules autofocus out
        wdg.tab_wdg.setChecked(wdg.z_plan, True)
        wdg.z_plan.setValue(useq.ZTopBottom(top=1, bottom=0, step=1))

    # disable autofocus device
    elif trigger == "core":
        assert mmc.getAutoFocusDevice() == "Autofocus"
        with qtbot.waitSignal(mmc.events.propertyChanged):
            mmc.setProperty("Core", "AutoFocus", "")

    # both af_axis and af_per_position should be disabled
    assert not wdg.af_axis.isEnabled()
    assert wdg.af_axis.use_af_p.isChecked()
    assert not wdg.af_axis.value()
    assert (
        wdg.af_axis.toolTip() == AF_UNAVAILABLE
        if trigger == "core"
        else AF_DISABLED_TOOLTIP
    )
    assert not pos_table.af_per_position.isEnabled()
    assert pos_table.af_per_position.isChecked()
    assert (
        pos_table.af_per_position.toolTip() == AF_UNAVAILABLE
        if trigger == "core"
        else AF_DISABLED_TOOLTIP
    )
    # AF column should be hidden
    assert pos_table.table().isColumnHidden(af_col)
    assert pos_table.table().isColumnHidden(af_btn_col)

    assert not wdg.value().autofocus_plan
    assert not wdg.value().stage_positions[0].sequence
    assert not wdg.value().stage_positions[1].sequence

    # uncheck the z plan tab
    if trigger == "zplan":
        wdg.tab_wdg.setChecked(wdg.z_plan, False)
    # re-enable autofocus device
    elif trigger == "core":
        with qtbot.waitSignal(mmc.events.propertyChanged):
            mmc.setProperty("Core", "AutoFocus", "Autofocus")

    # af_axis and af_per_position should be enabled
    assert wdg.af_axis.value() == ("p",)
    assert pos_table.af_per_position.isEnabled()
    assert pos_table.af_per_position.isChecked()
    assert wdg.value().replace(metadata={}) == SEQ.replace(metadata={})
    # AF column should be visible
    assert not pos_table.table().isColumnHidden(af_col)
    assert not pos_table.table().isColumnHidden(af_btn_col)

    if trigger == "core":
        with qtbot.waitSignal(mmc.events.propertyChanged):
            mmc.setProperty("Core", "AutoFocus", "")

        wdg.tab_wdg.setChecked(wdg.z_plan, True)
        wdg.z_plan.setValue(useq.ZTopBottom(top=1, bottom=0, step=1))
        assert not wdg.af_axis.isEnabled()
        assert wdg.af_axis.use_af_p.isChecked()
        assert not wdg.af_axis.value()
        assert wdg.af_axis.toolTip() == AF_UNAVAILABLE
        assert not pos_table.af_per_position.isEnabled()
        assert pos_table.af_per_position.isChecked()
        assert pos_table.af_per_position.toolTip() == AF_UNAVAILABLE

        # AF column should be hidden
        assert pos_table.table().isColumnHidden(af_col)
        assert pos_table.table().isColumnHidden(af_btn_col)

        # is autofocus is re-enabled, the autofocus options should still be disabled
        # since the absolute TopBottom z plan is active
        with qtbot.waitSignal(mmc.events.propertyChanged):
            mmc.setProperty("Core", "AutoFocus", "Autofocus")

        assert not wdg.af_axis.isEnabled()
        assert wdg.af_axis.use_af_p.isChecked()
        assert not wdg.af_axis.value()
        assert wdg.af_axis.toolTip() == AF_DISABLED_TOOLTIP
        assert not pos_table.af_per_position.isEnabled()
        assert pos_table.af_per_position.isChecked()
        assert pos_table.af_per_position.toolTip() == AF_DISABLED_TOOLTIP
        # AF column should be hidden
        assert pos_table.table().isColumnHidden(af_col)
        assert pos_table.table().isColumnHidden(af_btn_col)


def test_hcs_wizard_uses_position_table_core(qtbot: QtBot) -> None:
    """The HCS wizard of the position table uses the core of the table."""
    mmc = CMMCorePlus()
    mmc.loadSystemConfiguration(TEST_CONFIG)
    wdg = MDAWidget(mmcore=mmc)
    qtbot.addWidget(wdg)
    assert mmc is not CMMCorePlus.instance()
    assert wdg.stage_positions._hcs._mmc is mmc


def test_position_fov_update_keeps_rows(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """A new FOV size changes only the grid plans of the position sub-sequences.

    Unchecked rows, values in hidden columns, and the other fields of the
    sub-sequences stay. The sizes of a sub-sequence agree with its new grid plan, and
    the table emits valueChanged one time. An event without a new FOV size, or with
    no pixel size, changes nothing.
    """
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    pos_table = wdg.stage_positions
    sub_seq = useq.MDASequence(
        grid_plan=useq.GridRowsColumns(
            rows=2, columns=1, fov_width=512, fov_height=512
        ),
        z_plan={"range": 2, "step": 1},
    )
    area_seq = useq.MDASequence(
        grid_plan=useq.GridWidthHeight(
            width=1000, height=1000, fov_width=512, fov_height=512
        )
    )
    pos_table.setValue(
        [
            useq.Position(x=0, y=0, z=5, sequence=sub_seq),
            useq.Position(x=1, y=1, z=6),
            useq.Position(x=2, y=2, z=7, sequence=area_seq),
        ]
    )
    pos_table.include_z.setChecked(False)
    table = pos_table.table()
    table.item(1, table.indexOf(pos_table.NAME)).setCheckState(Qt.CheckState.Unchecked)
    assert len(pos_table.value()) == 2
    # useq caches the sizes of a sequence
    assert pos_table.value()[1].sequence.sizes["g"] == 4
    value_changed = Mock()
    pos_table.valueChanged.connect(value_changed)

    global_mmcore.setROI(0, 0, 100, 150)

    def all_positions() -> Sequence[useq.Position]:
        return pos_table.value(exclude_unchecked=False, exclude_hidden_cols=False)

    positions = all_positions()
    assert positions[0].sequence.grid_plan.fov_width == 100
    assert len(positions) == 3
    assert len(pos_table.value()) == 2
    assert [p.z for p in positions] == [5, 6, 7]
    assert not pos_table.include_z.isChecked()
    assert positions[0].sequence.grid_plan.fov_height == 150
    assert positions[0].sequence.z_plan == sub_seq.z_plan
    area = positions[2].sequence
    assert area.sizes["g"] == len(list(area.grid_plan)) == 70
    value_changed.assert_called_once()

    # the same FOV size again
    value_changed.reset_mock()
    global_mmcore.events.roiSet.emit("Camera", 0, 0, 100, 150)
    value_changed.assert_not_called()

    # no pixel size (e.g. no pixel size config agrees with the current state)
    with patch.object(global_mmcore, "getPixelSizeUm", return_value=0):
        global_mmcore.events.pixelSizeChanged.emit(0)
    value_changed.assert_not_called()
    assert all_positions()[0].sequence.grid_plan.fov_width == 100


def test_grid_plan_fov_update(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    mmc = global_mmcore
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    wdg.tab_wdg.setChecked(wdg.grid_plan, True)
    grid_plan = useq.GridRowsColumns(rows=2, columns=1)
    wdg.grid_plan.setValue(grid_plan)

    assert wdg.value().grid_plan.fov_height == 512
    assert wdg.value().grid_plan.fov_width == 512

    with qtbot.waitSignal(mmc.events.roiSet):
        mmc.setROI(0, 0, 100, 150)
    assert wdg.value().grid_plan.fov_width == 100
    assert wdg.value().grid_plan.fov_height == 150

    with qtbot.waitSignal(mmc.events.pixelSizeChanged):
        mmc.setPixelSizeConfig("Res20x")
    assert wdg.value().grid_plan.fov_width == 50
    assert wdg.value().grid_plan.fov_height == 75


def test_grid_plan_subsequence_fov_update(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    mmc = global_mmcore
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    pos = useq.AbsolutePosition(
        x=0.0,
        y=0.0,
        z=0.0,
        sequence=useq.MDASequence(
            grid_plan=useq.GridRowsColumns(
                fov_width=512.0, fov_height=512.0, rows=3, columns=1
            )
        ),
    )
    wdg.tab_wdg.setChecked(wdg.stage_positions, True)
    wdg.stage_positions.setValue([pos])

    sp = wdg.stage_positions
    assert sp.value()[0].sequence.grid_plan.fov_width == 512
    assert sp.value()[0].sequence.grid_plan.fov_height == 512

    with qtbot.waitSignal(mmc.events.roiSet):
        mmc.setROI(0, 0, 100, 150)

    assert sp.value()[0].sequence.grid_plan.fov_width == 100
    assert sp.value()[0].sequence.grid_plan.fov_height == 150

    with qtbot.waitSignal(mmc.events.pixelSizeChanged):
        mmc.setPixelSizeConfig("Res20x")
    assert sp.value()[0].sequence.grid_plan.fov_width == 50
    assert sp.value()[0].sequence.grid_plan.fov_height == 75


def test_sub_wdg_channel_tab(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = MDAWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    poly1 = useq.GridFromPolygon(
        vertices=[(-400, 0), (1000, -500), (500, 1200), (0, 100)],
        fov_height=100,
        fov_width=100,
        overlap=(10, 10),
    )

    seq = useq.MDASequence(
        channels=(
            useq.Channel(config="DAPI", exposure=100),
            useq.Channel(config="FITC", exposure=100),
        ),
        stage_positions=(
            useq.AbsolutePosition(
                z=3,
                name="pos1",
                sequence=useq.MDASequence(grid_plan=poly1),
            ),
        ),
    )

    # Set the sequence to the widget
    wdg.setValue(seq)

    # Get the sequence button from the position table
    pos_table = wdg.stage_positions.table()
    seq_col_idx = pos_table.indexOf(wdg.stage_positions.SEQ)
    btn = pos_table.cellWidget(0, seq_col_idx)
    btn = cast("MDAButton", btn)

    # Click the button to open the _MDAPopup dialog
    def handle_dialog():
        # Find the popup dialog
        popup = btn.findChild(_MDAPopup)
        assert popup is not None, "MDA popup dialog should be created"

        # Access the channel tab (should be CoreConnectedChannelTable)
        channels_tab = popup.mda_tabs.channels
        assert isinstance(channels_tab, CoreConnectedChannelTable), (
            "Channel tab should be CoreConnectedChannelTable"
        )

        # Get the current channel group from both the main widget and popup
        main_channel_group = wdg.channels._mmc.getChannelGroup()
        popup_channel_group = channels_tab._mmc.getChannelGroup()

        # Check that the combo box shows the correct channel group
        main_combo_text = wdg.channels._group_combo.currentText()
        popup_combo_text = channels_tab._group_combo.currentText()

        # Verify that the main widget is correctly synced
        if main_channel_group:
            assert main_combo_text == main_channel_group, (
                f"Main combo should show '{main_channel_group}' "
                f"but shows '{main_combo_text}'"
            )

        # Check if popup uses the same core instance or a different one
        is_same_core = wdg.channels._mmc is channels_tab._mmc

        # Verify that popup channel tab matches its core's channel group
        if popup_channel_group:
            assert popup_combo_text == popup_channel_group, (
                f"Popup combo should show '{popup_channel_group}' "
                f"but shows '{popup_combo_text}'"
            )

        # Since we're using the same core instance, both should show the same
        # channel group
        if is_same_core and main_channel_group:
            assert popup_combo_text == main_combo_text, (
                f"Same core instance should show same channel group: "
                f"main='{main_combo_text}', popup='{popup_combo_text}'"
            )

        # The main widget should always be correctly synced
        if main_channel_group and main_combo_text != main_channel_group:
            raise AssertionError(
                f"Main widget combo should show '{main_channel_group}' "
                f"but shows '{main_combo_text}'"
            )

        # Close the dialog
        popup.accept()

    # Use QTimer to handle the dialog after it opens
    QTimer.singleShot(100, handle_dialog)

    # Click the button (this will open the dialog)
    btn.seq_btn.click()


@pytest.mark.parametrize("writer", [OME_ZARR, OME_TIFF])
def test_save_name_counts_up_after_runs(
    writer: str, global_mmcore: CMMCorePlus, qtbot: QtBot, tmp_path: Path
) -> None:
    """A name without extension gets the writer's one, so the next run counts up.

    Otherwise pymmcore-plus writes OME-Zarr to a different name than the widget
    shows, and the next run does not find it.
    """
    mda_wdg = MDAWidget(mmcore=global_mmcore)
    qtbot.addWidget(mda_wdg)
    mda_wdg.setValue(useq.MDASequence(time_plan={"interval": 0, "loops": 2}))
    mda_wdg.save_info._writer_combo.setCurrentText(writer)
    mda_wdg.save_info.setValue(tmp_path / "exp")

    ext = WRITERS[writer][0]
    for expected in (f"exp{ext}", f"exp_001{ext}"):
        save_path = mda_wdg.prepare_mda()
        assert isinstance(save_path, Path)
        assert save_path.name == expected
        assert mda_wdg.save_info.value()["save_name"] == expected
        global_mmcore.mda.run(mda_wdg.value(), output=save_path)
        assert save_path.exists()
        if writer == OME_TIFF:
            import tifffile

            with tifffile.TiffFile(save_path) as tif:
                assert tif.is_ome
                assert len(tif.pages) == 2
        mda_wdg._on_mda_finished(mda_wdg.value())


@pytest.mark.parametrize("extension", [".ome.tiff", ".ome.tif"])
def test_get_next_available_paths_multi_position_ome_tiff(
    extension: str, tmp_path: Path
) -> None:
    """A multi-position OME-TIFF is a folder without the extension."""
    path = tmp_path / f"test{extension}"
    assert get_next_available_path(path) == path

    (tmp_path / "test").mkdir()
    assert get_next_available_path(path) == tmp_path / f"test_001{extension}"

    (tmp_path / "test_004").mkdir()
    assert get_next_available_path(path) == tmp_path / f"test_005{extension}"

    # folders of other names and plain files do not count
    (tmp_path / "other_009").mkdir()
    (tmp_path / "test_007").touch()
    assert get_next_available_path(path) == tmp_path / f"test_005{extension}"

    # only for OME-TIFF: OME-Zarr is never written without its extension
    zarr = tmp_path / "test.ome.zarr"
    assert get_next_available_path(zarr) == zarr


def test_get_next_available_paths_multi_position_ome_tiff_case(
    tmp_path: Path,
) -> None:
    """Folders count whatever their case (case-insensitive file systems)."""
    (tmp_path / "EXP").mkdir()
    (tmp_path / "EXP_001").mkdir()
    path = tmp_path / "exp.ome.tif"
    assert get_next_available_path(path) == tmp_path / "exp_002.ome.tif"


def test_save_name_counts_up_after_multi_position_ome_tiff_runs(
    global_mmcore: CMMCorePlus, qtbot: QtBot, tmp_path: Path
) -> None:
    """A multi-position OME-TIFF run is a folder; the next run counts up."""
    mda_wdg = MDAWidget(mmcore=global_mmcore)
    qtbot.addWidget(mda_wdg)
    mda_wdg.setValue(useq.MDASequence(stage_positions=[(0, 0, 0), (5, 5, 0)]))
    mda_wdg.save_info.setValue(tmp_path / "exp.ome.tif")

    for expected in ("exp", "exp_001"):
        save_path = mda_wdg.prepare_mda()
        assert isinstance(save_path, Path)
        assert save_path.name == f"{expected}.ome.tif"
        global_mmcore.mda.run(mda_wdg.value(), output=save_path)
        # ome-writers writes one file per position into a folder
        assert (tmp_path / expected).is_dir()
        mda_wdg._on_mda_finished(mda_wdg.value())


def _xy_bounds_at(
    qtbot: QtBot, mmc: CMMCorePlus, x: float, y: float
) -> CoreXYBoundsControl:
    """A compact control, with a 100 x 200 um FOV and the stage at (x, y)."""
    mmc.setROI(0, 0, 100, 200)  # fov_width=100um, fov_height=200um @ px=1.0
    wdg = CoreXYBoundsControl(core=mmc, compact_layout=True)
    qtbot.addWidget(wdg)
    device = mmc.getXYStageDevice()
    mmc.setXYPosition(device, x, y)
    mmc.waitForDevice(device)
    return wdg


def test_z_modes_keep_one_height_after_rescaling(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """Regression test: a zoom that resized the buttons made the modes' heights differ.

    An app zoom rescales the widgets' pixel sizes, as done here; the rows' heights
    and the reserved height used to stay as measured when the widget was built.
    """
    wdg = CoreConnectedZPlanWidget(global_mmcore)
    qtbot.addWidget(wdg)
    wdg.show()
    for buttons in (wdg.top_btn, wdg.bottom_btn):
        buttons.mark.setFixedSize(45, 40)
    for button in (wdg.top_btn.visit, wdg.bottom_btn.visit):
        button.setMinimumHeight(40)
    controls = wdg._controls_widget
    controls.setMinimumHeight(controls.minimumHeight() * 3 // 2)
    qtbot.wait(10)

    heights = set()
    # Symmetric first: it is the shortest without the reserved height
    for mode in ("range_around", "above_below", "top_bottom", "range_around"):
        wdg.setMode(mode)
        qtbot.wait(10)
        heights.add(wdg.minimumSizeHint().height())
    assert len(heights) == 1


def test_xy_bounds_mark_uses_outer_edge(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    # GridFromEdges expects each bound to be the *outer* edge of the image at
    # that position (including the field of view), not the camera-center
    # stage position -- marking a bound must expand the span by one full FOV
    # (half on each end) once both ends of an axis are known.
    mmc = global_mmcore
    mmc.setROI(0, 0, 100, 200)  # fov_width=100um, fov_height=200um @ px=1.0

    wdg = CoreXYBoundsControl(core=mmc)
    qtbot.addWidget(wdg)

    device = mmc.getXYStageDevice()
    mmc.setXYPosition(device, 10.0, 20.0)
    mmc.waitForDevice(device)
    wdg.btn_top.click()
    wdg.btn_left.click()

    mmc.setXYPosition(device, 30.0, 40.0)
    mmc.waitForDevice(device)
    wdg.btn_bottom.click()
    wdg.btn_right.click()

    # top/bottom marked at Y=20 and Y=40 respectively: span expands from the
    # raw 20um to 20 + fov_height(200) = 220um, however that 220 splits
    # between the two fields.
    assert abs(wdg.top.value() - wdg.bottom.value()) == pytest.approx(220.0, abs=0.01)
    assert max(wdg.top.value(), wdg.bottom.value()) == pytest.approx(
        40.0 + 100.0, abs=0.01
    )
    assert min(wdg.top.value(), wdg.bottom.value()) == pytest.approx(
        20.0 - 100.0, abs=0.01
    )
    # left/right marked at X=10 and X=30 respectively: span expands from the
    # raw 20um to 20 + fov_width(100) = 120um.
    assert abs(wdg.left.value() - wdg.right.value()) == pytest.approx(120.0, abs=0.01)
    assert max(wdg.left.value(), wdg.right.value()) == pytest.approx(
        30.0 + 50.0, abs=0.01
    )
    assert min(wdg.left.value(), wdg.right.value()) == pytest.approx(
        10.0 - 50.0, abs=0.01
    )


def test_xy_bounds_mark_is_independent_of_stage_axis_orientation(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """The marked span expands correctly even if "top" ends up the smaller Y.

    Regression test: not every XY stage reports Y increasing toward the
    physical "top" -- some report the opposite (a Transpose/Mirror setting on
    the stage device). Marking must expand the region outward either way,
    rather than only working when "top" happens to be the numerically larger
    value -- which previously produced a *smaller* region than requested
    (each end shifted inward by half a FOV) when that assumption didn't hold.
    """
    mmc = global_mmcore
    mmc.setROI(0, 0, 100, 200)  # fov_width=100um, fov_height=200um @ px=1.0

    wdg = CoreXYBoundsControl(core=mmc)
    qtbot.addWidget(wdg)

    device = mmc.getXYStageDevice()
    # "Top" marked at a *smaller* raw Y than "bottom" -- the opposite of the
    # useq/Cartesian assumption that top = max(Y).
    mmc.setXYPosition(device, 10.0, 5.0)
    mmc.waitForDevice(device)
    wdg.btn_top.click()

    mmc.setXYPosition(device, 10.0, 105.0)
    mmc.waitForDevice(device)
    wdg.btn_bottom.click()

    raw_span = 105.0 - 5.0
    assert abs(wdg.top.value() - wdg.bottom.value()) == pytest.approx(
        raw_span + 200.0, abs=0.01
    )


def test_xy_bounds_mark_corners_expand_region(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """Marking two opposite corners in one click each still expands correctly."""
    mmc = global_mmcore
    mmc.setROI(0, 0, 100, 200)  # fov_width=100um, fov_height=200um @ px=1.0

    wdg = CoreXYBoundsControl(core=mmc)
    qtbot.addWidget(wdg)

    device = mmc.getXYStageDevice()
    mmc.setXYPosition(device, 10.0, 40.0)
    mmc.waitForDevice(device)
    wdg.btn_top_left.click()

    mmc.setXYPosition(device, 30.0, 20.0)
    mmc.waitForDevice(device)
    wdg.btn_bottom_right.click()

    assert abs(wdg.top.value() - wdg.bottom.value()) == pytest.approx(
        20.0 + 200.0, abs=0.01
    )
    assert abs(wdg.left.value() - wdg.right.value()) == pytest.approx(
        20.0 + 100.0, abs=0.01
    )


def test_xy_bounds_compact_mark_and_move_buttons(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """The compact layout's separate Mark and Move buttons mark and visit."""
    mmc = global_mmcore
    mmc.setROI(0, 0, 100, 200)  # fov_width=100um, fov_height=200um @ px=1.0

    wdg = CoreXYBoundsControl(core=mmc, compact_layout=True)
    qtbot.addWidget(wdg)
    wdg._corner_mode.setChecked(True)

    device = mmc.getXYStageDevice()
    mmc.setXYPosition(device, 10.0, 40.0)
    mmc.waitForDevice(device)
    wdg.mark_buttons["top_left"].click()

    mmc.setXYPosition(device, 30.0, 20.0)
    mmc.waitForDevice(device)
    wdg.mark_buttons["bottom_right"].click()

    assert abs(wdg.top.value() - wdg.bottom.value()) == pytest.approx(
        20.0 + 200.0, abs=0.01
    )
    assert abs(wdg.left.value() - wdg.right.value()) == pytest.approx(
        20.0 + 100.0, abs=0.01
    )

    # Move returns to the marked positions, one axis or both at once
    mmc.setXYPosition(device, 999.0, 999.0)
    mmc.waitForDevice(device)
    wdg.visit_buttons["top_left"].click()
    assert mmc.getXPosition(device) == pytest.approx(10.0, abs=0.01)
    assert mmc.getYPosition(device) == pytest.approx(40.0, abs=0.01)

    wdg._edge_mode.setChecked(True)
    wdg.visit_buttons["right"].click()
    assert mmc.getXPosition(device) == pytest.approx(30.0, abs=0.01)
    assert mmc.getYPosition(device) == pytest.approx(40.0, abs=0.01)

    # marking one edge leaves the other axis alone
    left = wdg.left.value()
    mmc.setXYPosition(device, 30.0, 100.0)
    mmc.waitForDevice(device)
    wdg.mark_buttons["top"].click()
    assert wdg.left.value() == left
    assert wdg.top.value() == pytest.approx(100.0 + 100.0, abs=0.01)


def test_xy_bounds_set_value_clears_raw_marks(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """Loading a saved plan must not anchor a later mark to a stale region."""
    mmc = global_mmcore
    mmc.setROI(0, 0, 100, 200)

    wdg = CoreXYBoundsControl(core=mmc)
    qtbot.addWidget(wdg)

    device = mmc.getXYStageDevice()
    mmc.setXYPosition(device, 0.0, 0.0)
    mmc.waitForDevice(device)
    wdg.btn_top.click()
    assert "top" in wdg._raw_marks

    wdg.setValue(useq.GridFromEdges(top=500, bottom=100, left=100, right=500))
    assert wdg._raw_marks == {}


def test_xy_bounds_visit_returns_to_marked_position(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """Move-to-corner must return to the raw marked position, not its outer edge.

    Regression test: _mark_axis pushes the *displayed* bound outward by half
    a FOV so GridFromEdges gets the correct outer edge (see
    test_xy_bounds_mark_uses_outer_edge) -- but _visit was feeding that
    outward-pushed edge value straight to setXYPosition instead of undoing
    the push, so "Move" to a marked corner overshot it by half a FOV.
    """
    mmc = global_mmcore
    mmc.setROI(0, 0, 100, 200)  # fov_width=100um, fov_height=200um @ px=1.0

    wdg = CoreXYBoundsControl(core=mmc)
    qtbot.addWidget(wdg)

    device = mmc.getXYStageDevice()
    mmc.setXYPosition(device, 10.0, 40.0)
    mmc.waitForDevice(device)
    wdg.btn_top_left.click()

    mmc.setXYPosition(device, 30.0, 20.0)
    mmc.waitForDevice(device)
    wdg.btn_bottom_right.click()

    # sanity check: the displayed bound is NOT the raw marked position (it's
    # pushed outward by half a FOV) -- otherwise "visit" landing there
    # wouldn't be distinguishable from landing at the marked position.
    assert wdg.top.value() != pytest.approx(40.0, abs=0.01)
    assert wdg.left.value() != pytest.approx(10.0, abs=0.01)

    wdg.go_middle.setChecked(True)
    mmc.setXYPosition(device, 999.0, 999.0)
    mmc.waitForDevice(device)
    wdg.btn_top_left.click()

    assert mmc.getXPosition(device) == pytest.approx(10.0, abs=0.01)
    assert mmc.getYPosition(device) == pytest.approx(40.0, abs=0.01)


def test_xy_bounds_visit_follows_an_edited_bound(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """Regression test: a stale mark used to win over the value in the field."""
    mmc = global_mmcore
    wdg = _xy_bounds_at(qtbot, mmc, 10, 500)
    device = mmc.getXYStageDevice()
    wdg.mark_buttons["top"].click()
    wdg.top.setValue(3000)

    mmc.setXYPosition(device, 777.0, 0.0)
    mmc.waitForDevice(device)
    wdg.visit_buttons["top"].click()

    assert mmc.getXPosition(device) == pytest.approx(777, abs=0.02)
    assert mmc.getYPosition(device) == pytest.approx(3000 - 100, abs=0.02)


def test_xy_bounds_mark_keeps_an_edited_bound(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """Regression test: marking one end used to reset an edited other end."""
    mmc = global_mmcore
    wdg = _xy_bounds_at(qtbot, mmc, 0, 500)
    device = mmc.getXYStageDevice()
    wdg.mark_buttons["top"].click()
    wdg.top.setValue(3000)

    mmc.setXYPosition(device, 0.0, 0.0)
    mmc.waitForDevice(device)
    wdg.mark_buttons["bottom"].click()

    assert wdg.top.value() == 3000
    assert wdg.bottom.value() == pytest.approx(-100, abs=0.02)


def test_xy_bounds_marks_survive_marking_the_other_end(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """Both ends stay visitable at their marks, though marking moves both fields."""
    mmc = global_mmcore
    wdg = _xy_bounds_at(qtbot, mmc, 0, 500)
    device = mmc.getXYStageDevice()
    wdg.mark_buttons["top"].click()
    mmc.setXYPosition(device, 0.0, 700.0)
    mmc.waitForDevice(device)
    wdg.mark_buttons["bottom"].click()

    assert wdg._raw_marks == {
        "top": pytest.approx(500, abs=0.02),
        "bottom": pytest.approx(700, abs=0.02),
    }


@pytest.mark.parametrize(
    "plan, expected",
    [
        (
            useq.GridFromEdges(top=1000, bottom=0, left=0, right=1000),
            {"top": 900, "bottom": 100, "left": 50, "right": 950},
        ),
        # the top and bottom fields swapped, as marking on a flipped stage does
        (
            useq.GridFromEdges(top=0, bottom=1000, left=0, right=1000),
            {"top": 100, "bottom": 900, "left": 50, "right": 950},
        ),
        # no inside to tell
        (
            useq.GridFromEdges(top=300, bottom=300, left=40, right=40),
            {"top": 300, "bottom": 300, "left": 40, "right": 40},
        ),
    ],
)
def test_xy_bounds_visit_without_marks_stays_inside_the_bounds(
    qtbot: QtBot,
    global_mmcore: CMMCorePlus,
    plan: useq.GridFromEdges,
    expected: dict[str, float],
) -> None:
    mmc = global_mmcore
    wdg = _xy_bounds_at(qtbot, mmc, 777, 888)
    wdg.setValue(plan)
    device = mmc.getXYStageDevice()

    for name, position in expected.items():
        wdg.visit_buttons[name].click()
        get = mmc.getYPosition if name in ("top", "bottom") else mmc.getXPosition
        assert get(device) == pytest.approx(position, abs=0.02)


def test_xy_bounds_visit_lands_on_the_outermost_tiles(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """With bounds a whole number of tiles apart, Move shows the first/last tiles."""
    mmc = global_mmcore
    wdg = _xy_bounds_at(qtbot, mmc, 0, 0)
    plan = useq.GridFromEdges(
        top=1000, bottom=0, left=0, right=1000, fov_width=100, fov_height=200
    )
    wdg.setValue(plan)
    xs, ys = [p.x for p in plan], [p.y for p in plan]
    device = mmc.getXYStageDevice()

    wdg.visit_buttons["top_left"].click()
    assert mmc.getXPosition(device) == pytest.approx(min(xs), abs=0.02)
    assert mmc.getYPosition(device) == pytest.approx(max(ys), abs=0.02)
    wdg.visit_buttons["bottom_right"].click()
    assert mmc.getXPosition(device) == pytest.approx(max(xs), abs=0.02)
    assert mmc.getYPosition(device) == pytest.approx(min(ys), abs=0.02)


def test_grid_plan_bounds_buttons_stay_at_the_right_edge(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """The fields take the spare width, not a gap to the right of the buttons."""
    wdg = CoreConnectedGridPlanWidget(mmcore=global_mmcore)
    qtbot.addWidget(wdg)
    wdg.setMode("bounds")
    wdg.resize(1000, 400)
    wdg.show()
    qtbot.waitExposed(wdg)

    bounds = wdg._core_xy_bounds
    visit = bounds.visit_buttons["top"]
    margin = wdg.layout().contentsMargins().right()
    assert visit.mapTo(wdg, QPoint()).x() + visit.width() == wdg.width() - margin
    assert bounds.top.width() > bounds.top.minimumWidth()


def test_grid_plan_bounds_by_in_left_column(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    wdg = CoreConnectedGridPlanWidget(mmcore=global_mmcore)
    qtbot.addWidget(wdg)
    wdg.show()
    bounds = wdg._core_xy_bounds
    bounds_by = bounds._bounds_by
    fields = {
        "number": wdg.row_col_wdg.rows,
        "area": wdg.width_height_wdg.area_width,
        "bounds": bounds.top,
    }

    # Edges/Corners sits in the left column, above the summary, shown only
    # in bounds mode but keeping its space, so the summary never moves
    assert wdg._common.isAncestorOf(bounds_by)
    assert not bounds.isAncestorOf(bounds_by)
    assert not bounds._bounds_by_label.isVisible()
    summary_y: set[int] = set()
    fields_pos: set[QPoint] = set()
    for mode, field in fields.items():
        wdg.setMode(mode)
        assert bounds_by.isVisible() == (mode == "bounds")
        assert (
            bounds_by.mapTo(wdg, QPoint()).y() < wdg._summary.mapTo(wdg, QPoint()).y()
        )
        summary_y.add(wdg._summary.mapTo(wdg, QPoint()).y())
        # every mode's first field sits at the same place, beside the overlap
        fields_pos.add(field.mapTo(wdg, QPoint()))
    assert len(summary_y) == 1
    assert len(fields_pos) == 1
    assert fields_pos.pop().y() == wdg.overlap.mapTo(wdg, QPoint()).y()

    # the radios still drive the bounds rows
    bounds._corner_mode.setChecked(True)
    assert bounds.mark_buttons["top_left"].isVisible()
    assert bounds.top.mapTo(wdg, QPoint()) == wdg.row_col_wdg.rows.mapTo(wdg, QPoint())
    bounds._edge_mode.setChecked(True)

    # each left row lines up with a bounds row: Overlap/Top, Order/Bottom,
    # Bounds by/Left, and the summary/Right
    def center_y(w: QWidget) -> float:
        return w.mapTo(wdg, QPoint()).y() + w.height() / 2

    left = (wdg.overlap, wdg.order, bounds_by, wdg._summary)
    right = (bounds.top, bounds.bottom, bounds.left, bounds.right)
    for left_wdg, right_wdg in zip(left, right, strict=True):
        assert abs(center_y(left_wdg) - center_y(right_wdg)) <= 1


def test_grid_plan_bounds_edit_updates_summary(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    wdg = CoreConnectedGridPlanWidget(mmcore=global_mmcore)
    qtbot.addWidget(wdg)
    wdg.setFovWidth(512)
    wdg.setFovHeight(512)
    wdg.overlap.setValue(0)
    wdg.setMode("bounds")
    assert wdg._summary.text().startswith("1 tile ")

    # the fields shown are the core bounds control's, not the base bounds_wdg's
    with qtbot.waitSignal(wdg.valueChanged):
        wdg._core_xy_bounds.right.setValue(1024)

    assert wdg._summary.text().startswith("2 tiles ")


def test_grid_plan_polygon_summary_is_tile_count_only(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    wdg = CoreConnectedGridPlanWidget(mmcore=global_mmcore)
    qtbot.addWidget(wdg)
    plan = useq.GridFromPolygon(
        vertices=[(0, 0), (3000, 0), (3000, 2000)], fov_width=512, fov_height=512
    )
    wdg.setValue(plan)

    count = plan.num_positions()
    assert count > 1
    assert wdg._summary.text() == f"{count} tiles"


def test_grid_mode_pages_reserve_their_current_size_after_a_style_change(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """A style change must reach the bounds page while it is hidden.

    Its fields sit in a nested layout that keeps its cached size until shown, so
    the whole editor changed height on the first switch to Absolute Bounds.
    """
    wdg = CoreConnectedGridPlanWidget(mmcore=global_mmcore)
    qtbot.addWidget(wdg)
    wdg.show()
    assert wdg.mode() is not Mode.BOUNDS
    field = wdg._core_xy_bounds.top
    before = wdg._stack.sizeHint().height()

    field.setMinimumHeight(before)
    QApplication.sendEvent(wdg._stack, QEvent(QEvent.Type.StyleChange))
    assert wdg._stack.sizeHint().height() > before

    field.setMinimumHeight(0)
    QApplication.sendEvent(wdg._stack, QEvent(QEvent.Type.StyleChange))
    assert wdg._stack.sizeHint().height() == before
