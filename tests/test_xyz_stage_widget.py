from __future__ import annotations

from typing import TYPE_CHECKING

from pymmcore_plus import CMMCorePlus, DeviceType

from pymmcore_widgets.control._xyz_stage_widget import XYZStageWidget

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


def test_xyz_stage_initialization(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = XYZStageWidget(absolute_positioning=True)
    qtbot.addWidget(wdg)
    wdg.show()

    assert global_mmcore.getXYStageDevice() == "XY"
    assert global_mmcore.getFocusDevice() == "Z"
    assert wdg._xy_stack.currentWidget() is wdg._xy_move_btns
    assert wdg._z_stack.currentWidget() is wdg._z_move_btns


def test_xyz_stage_no_device_placeholder(qtbot: QtBot) -> None:
    # a fresh, unconfigured core: nothing is loaded, so no default XY/Focus
    mmc = CMMCorePlus()
    wdg = XYZStageWidget(mmcore=mmc)
    qtbot.addWidget(wdg)
    wdg.show()

    assert wdg._xy_stack.currentWidget() is wdg._xy_no_device_lbl
    assert wdg._z_stack.currentWidget() is wdg._z_no_device_lbl
    assert not wdg._x_pos.isEnabled()
    assert not wdg._y_pos.isEnabled()
    assert not wdg._z_pos.isEnabled()
    assert not wdg._x_step.isEnabled()
    assert not wdg._y_step.isEnabled()
    assert not wdg._z_step.isEnabled()
    assert not wdg._invert_x.isEnabled()
    assert not wdg._invert_y.isEnabled()
    assert not wdg._invert_z.isEnabled()


def test_xyz_stage_device_changes_dynamically(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    wdg = XYZStageWidget()
    qtbot.addWidget(wdg)

    assert wdg._xy_stack.currentWidget() is wdg._xy_move_btns
    assert wdg._z_stack.currentWidget() is wdg._z_move_btns

    global_mmcore.setXYStageDevice("")
    qtbot.waitUntil(lambda: wdg._xy_stack.currentWidget() is wdg._xy_no_device_lbl)
    assert not wdg._x_pos.isEnabled()
    # Z is untouched
    assert wdg._z_stack.currentWidget() is wdg._z_move_btns

    global_mmcore.setXYStageDevice("XY")
    qtbot.waitUntil(lambda: wdg._xy_stack.currentWidget() is wdg._xy_move_btns)
    assert wdg._x_pos.isEnabled()

    global_mmcore.setFocusDevice("")
    qtbot.waitUntil(lambda: wdg._z_stack.currentWidget() is wdg._z_no_device_lbl)
    assert not wdg._z_pos.isEnabled()

    global_mmcore.setFocusDevice("Z")
    qtbot.waitUntil(lambda: wdg._z_stack.currentWidget() is wdg._z_move_btns)
    assert wdg._z_pos.isEnabled()


def test_xyz_stage_step_sizes(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = XYZStageWidget()
    qtbot.addWidget(wdg)

    wdg.set_x_step(5)
    wdg.set_y_step(6)
    wdg.set_z_step(7)
    assert wdg.x_step() == 5
    assert wdg.y_step() == 6
    assert wdg.z_step() == 7
    assert wdg._x_pos.value() == 0
    assert wdg._y_pos.value() == 0
    assert wdg._z_pos.value() == 0


def test_xyz_stage_movement_buttons(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = XYZStageWidget()
    qtbot.addWidget(wdg)

    wdg.set_x_step(20)
    wdg.set_y_step(20)

    x0, y0 = global_mmcore.getXYPosition()
    assert x0 == -0.0
    assert y0 == -0.0

    xy_up = wdg._xy_move_btns.layout().itemAtPosition(0, 1)
    xy_up.widget().click()
    qtbot.waitUntil(lambda: global_mmcore.getYPosition() > y0 + wdg.y_step() - 1)
    assert (
        (y0 + wdg.y_step()) - 1 < global_mmcore.getYPosition() < (y0 + wdg.y_step()) + 1
    )
    assert global_mmcore.getXPosition() == x0
    qtbot.waitUntil(
        lambda: wdg._y_pos.value() == round(global_mmcore.getYPosition(), 1)
    )

    xy_left = wdg._xy_move_btns.layout().itemAtPosition(1, 0)
    xy_left.widget().click()
    qtbot.waitUntil(lambda: global_mmcore.getXPosition() < x0 - (wdg.x_step() - 1))
    global_mmcore.waitForDeviceType(DeviceType.XYStage)
    qtbot.waitUntil(
        lambda: wdg._x_pos.value() == round(global_mmcore.getXPosition(), 1)
    )


def test_xyz_stage_independent_xy_step_diagonal(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """X and Y have independent step values, even for a diagonal move."""
    wdg = XYZStageWidget()
    qtbot.addWidget(wdg)

    wdg.set_x_step(20)
    wdg.set_y_step(5)

    x0, y0 = global_mmcore.getXYPosition()
    ne_btn = wdg._xy_move_btns.layout().itemAtPosition(0, 2)
    ne_btn.widget().click()
    global_mmcore.waitForDeviceType(DeviceType.XYStage)
    qtbot.waitUntil(
        lambda: wdg._x_pos.value() == round(global_mmcore.getXPosition(), 1)
    )
    assert 19 < global_mmcore.getXPosition() - x0 < 21
    assert 4 < global_mmcore.getYPosition() - y0 < 6

    assert "20.0" in ne_btn.widget().toolTip()


def test_z_stage_movement_buttons(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = XYZStageWidget()
    qtbot.addWidget(wdg)
    wdg.set_z_step(15.0)
    assert wdg.z_step() == 15.0
    assert wdg._z_pos.value() == 0

    z0 = global_mmcore.getPosition()
    assert z0 == 0.0

    z_up = wdg._z_move_btns.layout().itemAtPosition(0, 1)
    z_up.widget().click()
    qtbot.waitUntil(lambda: global_mmcore.getPosition() > z0 + wdg.z_step() - 1)
    assert (
        (z0 + wdg.z_step()) - 1 < global_mmcore.getPosition() < (z0 + wdg.z_step()) + 1
    )
    qtbot.waitUntil(lambda: wdg._z_pos.value() == global_mmcore.getPosition())


def test_xyz_stage_absolute_positioning(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    wdg = XYZStageWidget(absolute_positioning=True)
    qtbot.addWidget(wdg)

    wdg._x_pos.setValue(5)
    wdg._x_pos.editingFinished.emit()
    global_mmcore.waitForDeviceType(DeviceType.XYStage)
    assert 4 < global_mmcore.getXPosition() < 6

    wdg._y_pos.setValue(5)
    wdg._y_pos.editingFinished.emit()
    global_mmcore.waitForDeviceType(DeviceType.XYStage)
    assert 4 < global_mmcore.getYPosition() < 6

    wdg._z_pos.setValue(5)
    wdg._z_pos.editingFinished.emit()
    qtbot.waitUntil(lambda: 4 < global_mmcore.getPosition() < 6)


def test_xyz_stage_snap_on_click(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = XYZStageWidget()
    qtbot.addWidget(wdg)
    wdg.snap_checkbox.setChecked(True)

    xy_up = wdg._xy_move_btns.layout().itemAtPosition(0, 1)
    with qtbot.waitSignal(global_mmcore.events.imageSnapped):
        global_mmcore.waitForDeviceType(DeviceType.XYStage)
        xy_up.widget().click()

    z_up = wdg._z_move_btns.layout().itemAtPosition(0, 1)
    with qtbot.waitSignal(global_mmcore.events.imageSnapped):
        z_up.widget().click()


def test_xyz_stage_invert_axes(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = XYZStageWidget()
    qtbot.addWidget(wdg)
    wdg.set_x_step(15)
    wdg.set_y_step(15)
    wdg.set_z_step(15)

    xy_left = wdg._xy_move_btns.layout().itemAtPosition(1, 0)
    wdg._invert_x.setChecked(True)
    xy_left.widget().click()
    global_mmcore.waitForDeviceType(DeviceType.XYStage)
    assert global_mmcore.getXPosition() == 15.0

    xy_up = wdg._xy_move_btns.layout().itemAtPosition(0, 1)
    wdg._invert_y.setChecked(True)
    xy_up.widget().click()
    global_mmcore.waitForDeviceType(DeviceType.XYStage)
    assert global_mmcore.getYPosition() == -15.0

    z_up = wdg._z_move_btns.layout().itemAtPosition(0, 1)
    wdg._invert_z.setChecked(True)
    z_up.widget().click()
    qtbot.waitUntil(lambda: global_mmcore.getPosition() == -15.0)


def test_enable_xyz_position_editing(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = XYZStageWidget()
    qtbot.addWidget(wdg)
    assert wdg._x_pos.isReadOnly()
    assert wdg._y_pos.isReadOnly()
    assert wdg._z_pos.isReadOnly()

    wdg.enable_absolute_positioning(True)
    assert wdg._pos_toggle_action.isChecked()
    assert not wdg._x_pos.isReadOnly()
    assert not wdg._y_pos.isReadOnly()
    assert not wdg._z_pos.isReadOnly()

    wdg.enable_absolute_positioning(False)
    assert not wdg._pos_toggle_action.isChecked()
    assert wdg._x_pos.isReadOnly()
    assert wdg._y_pos.isReadOnly()
    assert wdg._z_pos.isReadOnly()
