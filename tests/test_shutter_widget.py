from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pymmcore_plus import DeviceType

from pymmcore_widgets.control._shutter_widget import (
    GRAY,
    GREEN,
    ShuttersWidget,
    ShuttersWidgetBasic,
)
from tests._utils import wait_signal

if TYPE_CHECKING:
    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot


def _make_shutters(
    qtbot: QtBot, mmcore: CMMCorePlus | None = None
) -> tuple[ShuttersWidget, ShuttersWidget, ShuttersWidget]:
    _shutters = []
    for name, auto in [
        ("Shutter", False),
        ("StateDev Shutter", False),
        ("Multi Shutter", True),
    ]:
        shutter = ShuttersWidget(name, autoshutter=auto, mmcore=mmcore)
        shutter.button_text_open = f"{name} opened"
        shutter.button_text_closed = f"{name} closed"
        shutter._refresh_shutter_widget()
        _shutters.append(shutter)
        qtbot.addWidget(shutter)
    return tuple(_shutters)  # type: ignore


@pytest.mark.xfail(reason="flaky test")
def test_create_shutter_widgets(qtbot: QtBot):
    shutter, state_dev_shutter, multi_shutter = _make_shutters(qtbot)

    assert shutter.shutter_button.text() == "Shutter opened"
    assert not shutter.shutter_button.isEnabled()
    assert state_dev_shutter.shutter_button.text() == "StateDev Shutter opened"
    assert state_dev_shutter.shutter_button.isEnabled()
    assert multi_shutter.shutter_button.text() == "Multi Shutter closed"
    assert multi_shutter.autoshutter_checkbox.isChecked()
    assert multi_shutter.shutter_button.isEnabled()


@pytest.mark.xfail(reason="flaky test")
def test_shutter_widget_propertyChanged(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter, _, multi_shutter = _make_shutters(qtbot, mmcore=mmc)

    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setProperty("Shutter", "State", False)

    assert not shutter.shutter_button.isEnabled()
    assert not mmc.getShutterOpen("Shutter")
    assert shutter.shutter_button.text() == "Shutter closed"
    assert mmc.getProperty("Shutter", "State") == "0"
    assert multi_shutter.shutter_button.isEnabled()
    assert not mmc.getShutterOpen("Multi Shutter")
    assert multi_shutter.shutter_button.text() == "Multi Shutter closed"
    assert mmc.getProperty("Multi Shutter", "State") == "0"


def test_shutter_widget_autoShutterSet(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter, state_dev_shutter, multi_shutter = _make_shutters(qtbot, mmcore=mmc)

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        mmc.setAutoShutter(False)
    assert shutter.shutter_button.isEnabled()
    assert state_dev_shutter.shutter_button.isEnabled()
    assert multi_shutter.shutter_button.isEnabled()
    mmc.setAutoShutter(True)
    assert not shutter.shutter_button.isEnabled()
    assert state_dev_shutter.shutter_button.isEnabled()
    assert multi_shutter.shutter_button.isEnabled()


def test_shutter_widget_configSet(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter, state_dev_shutter, multi_shutter = _make_shutters(qtbot, mmcore=mmc)

    with (
        wait_signal(qtbot, mmc.events.configSet),
        wait_signal(qtbot, mmc.events.propertyChanged),
    ):
        mmc.setConfig("Channel", "DAPI")
        mmc.setShutterOpen("Multi Shutter", True)
    assert multi_shutter.shutter_button.text() == "Multi Shutter opened"
    assert mmc.getProperty("Multi Shutter", "State") == "1"
    assert mmc.getShutterOpen("Multi Shutter")
    assert shutter.shutter_button.text() == "Shutter opened"
    assert mmc.getProperty("Shutter", "State") == "1"
    assert mmc.getShutterOpen("Shutter")
    assert state_dev_shutter.shutter_button.text() == "StateDev Shutter opened"
    assert mmc.getShutterOpen("StateDev Shutter")
    assert mmc.getProperty("StateDev", "Label") == "State-1"


def test_shutter_widget_SequenceAcquisition(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter, state_dev_shutter, multi_shutter = _make_shutters(qtbot, mmcore=mmc)

    with wait_signal(qtbot, mmc.events.configSet):
        mmc.setConfig("Channel", "DAPI")

    with wait_signal(qtbot, mmc.events.continuousSequenceAcquisitionStarted):
        mmc.startContinuousSequenceAcquisition()
    assert shutter.shutter_button.text() == "Shutter opened"
    assert not shutter.shutter_button.isEnabled()

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        mmc.setAutoShutter(False)
    assert shutter.shutter_button.isEnabled()
    assert state_dev_shutter.shutter_button.isEnabled()
    assert multi_shutter.shutter_button.isEnabled()
    assert shutter.shutter_button.text() == "Shutter opened"

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        mmc.setAutoShutter(True)
    with wait_signal(qtbot, mmc.events.sequenceAcquisitionStopped):
        mmc.stopSequenceAcquisition()
    assert not shutter.shutter_button.isEnabled()
    assert state_dev_shutter.shutter_button.isEnabled()
    assert multi_shutter.shutter_button.isEnabled()
    assert shutter.shutter_button.text() == "Shutter closed"


def test_shutter_widget_autoshutter(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter, state_dev_shutter, multi_shutter = _make_shutters(qtbot, mmcore=mmc)

    assert multi_shutter.autoshutter_checkbox.isChecked()

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        multi_shutter.autoshutter_checkbox.setChecked(False)
    assert shutter.shutter_button.isEnabled()
    assert state_dev_shutter.shutter_button.isEnabled()
    assert multi_shutter.shutter_button.isEnabled()

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        multi_shutter.autoshutter_checkbox.setChecked(True)
    assert not shutter.shutter_button.isEnabled()
    assert state_dev_shutter.shutter_button.isEnabled()
    assert multi_shutter.shutter_button.isEnabled()


@pytest.mark.xfail(reason="flaky test")
def test_shutter_widget_button(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter, state_dev_shutter, multi_shutter = _make_shutters(qtbot, mmcore=mmc)

    with wait_signal(qtbot, mmc.events.configSet):
        mmc.setConfig("Channel", "DAPI")

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        multi_shutter.autoshutter_checkbox.setChecked(False)

    with wait_signal(qtbot, mmc.events.propertyChanged):
        shutter.shutter_button.click()
    assert shutter.shutter_button.text() == "Shutter closed"
    assert not mmc.getShutterOpen("Shutter")
    assert mmc.getProperty("Shutter", "State") == "0"

    with wait_signal(qtbot, mmc.events.propertyChanged):
        shutter.shutter_button.click()
    assert shutter.shutter_button.text() == "Shutter opened"
    assert mmc.getShutterOpen("Shutter")
    assert mmc.getProperty("Shutter", "State") == "1"

    with wait_signal(qtbot, mmc.events.propertyChanged):
        state_dev_shutter.shutter_button.click()
    assert state_dev_shutter.shutter_button.text() == "StateDev Shutter opened"
    assert mmc.getShutterOpen("StateDev Shutter")
    assert mmc.getProperty("StateDev", "Label") == "State-1"

    with wait_signal(qtbot, mmc.events.propertyChanged):
        state_dev_shutter.shutter_button.click()
    assert state_dev_shutter.shutter_button.text() == "StateDev Shutter closed"
    assert not mmc.getShutterOpen("StateDev Shutter")
    assert mmc.getProperty("StateDev", "Label") == "State-1"

    with wait_signal(qtbot, mmc.events.propertyChanged):
        multi_shutter.shutter_button.click()
    assert multi_shutter.shutter_button.text() == "Multi Shutter opened"
    assert mmc.getShutterOpen("Multi Shutter")
    assert mmc.getProperty("Multi Shutter", "State") == "1"
    assert shutter.shutter_button.text() == "Shutter opened"
    assert mmc.getShutterOpen("Shutter")
    assert mmc.getProperty("Shutter", "State") == "1"
    assert mmc.getShutterOpen("StateDev Shutter")
    assert mmc.getProperty("StateDev", "Label") == "State-1"


def test_shutter_widget_setters(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter, _, _ = _make_shutters(qtbot, mmcore=mmc)

    assert shutter.icon_size == 25
    shutter.icon_size = 30
    assert shutter.icon_size == 30

    assert shutter.icon_color_open == GREEN
    shutter.icon_color_open = GRAY
    assert shutter.icon_color_open == GRAY

    assert shutter.icon_color_closed == GRAY
    shutter.icon_color_closed = GREEN
    assert shutter.icon_color_closed == GREEN

    assert shutter.button_text_open == "Shutter opened"
    shutter.button_text_open = "O"
    assert shutter.button_text_open == "O"

    assert shutter.button_text_closed == "Shutter closed"
    shutter.button_text_closed = "C"
    assert shutter.button_text_closed == "C"

    with wait_signal(qtbot, mmc.events.continuousSequenceAcquisitionStarted):
        mmc.startContinuousSequenceAcquisition()
    assert shutter.shutter_button.text() == "O"
    with wait_signal(qtbot, mmc.events.sequenceAcquisitionStopped):
        mmc.stopSequenceAcquisition()
    assert shutter.shutter_button.text() == "C"


def test_shutter_widget_UserWarning(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    _, _, multi_shutter = _make_shutters(qtbot, mmcore=mmc)

    assert multi_shutter.shutter_button.text() == "Multi Shutter closed"
    with wait_signal(qtbot, mmc.events.systemConfigurationLoaded):
        with pytest.warns(UserWarning):
            mmc.loadSystemConfiguration("MMConfig_demo.cfg")
            assert multi_shutter.shutter_button.text() == "None"


@pytest.mark.xfail(reason="flaky test")
def test_multi_shutter_state_changed(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter, _shutter1, multi_shutter = _make_shutters(qtbot)

    with (
        wait_signal(qtbot, mmc.events.propertyChanged),
        wait_signal(qtbot, mmc.events.configSet),
    ):
        mmc.setProperty("Core", "Shutter", "Multi Shutter")
        mmc.setConfig("Channel", "DAPI")

    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setProperty("Multi Shutter", "State", "0")

    assert mmc.getProperty("Multi Shutter", "State") == "0"
    assert mmc.getProperty("Shutter", "State") == "0"

    assert multi_shutter.shutter_button.text() == "Multi Shutter closed"
    assert shutter.shutter_button.text() == "Shutter closed"

    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setProperty("Multi Shutter", "State", "1")

    assert mmc.getProperty("Multi Shutter", "State") == "1"
    assert mmc.getProperty("Shutter", "State") == "1"

    assert multi_shutter.shutter_button.text() == "Multi Shutter opened"
    assert shutter.shutter_button.text() == "Shutter opened"


def test_on_shutter_device_changed(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter, shutter1, multi_shutter = _make_shutters(qtbot, mmcore=mmc)

    with (
        wait_signal(qtbot, mmc.events.propertyChanged),
        wait_signal(qtbot, mmc.events.configSet),
    ):
        mmc.setProperty("Core", "Shutter", "Multi Shutter")
        mmc.setConfig("Channel", "DAPI")

    assert mmc.getShutterDevice() == "Multi Shutter"
    assert not multi_shutter.shutter_button.isEnabled()
    assert shutter.shutter_button.isEnabled()
    assert shutter1.shutter_button.isEnabled()

    with (
        wait_signal(qtbot, mmc.events.propertyChanged),
        wait_signal(qtbot, mmc.events.configSet),
    ):
        mmc.setProperty("Core", "Shutter", "Shutter")
        mmc.setConfig("Channel", "DAPI")

    assert mmc.getShutterDevice() == "Shutter"
    assert multi_shutter.shutter_button.isEnabled()
    assert not shutter.shutter_button.isEnabled()
    assert shutter1.shutter_button.isEnabled()


# ---------------------------------------------------------------------------
# ShuttersWidgetBasic tests
# ---------------------------------------------------------------------------


def test_shutter_widget_basic_initial_state(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """Combo is populated, button/checkbox reflect core state on creation."""
    mmc = global_mmcore
    wdg = ShuttersWidgetBasic(mmcore=mmc)
    qtbot.addWidget(wdg)

    loaded = list(mmc.getLoadedDevicesOfType(DeviceType.ShutterDevice))
    assert wdg.shutter_combo.count() == len(loaded)
    for name in loaded:
        assert wdg.shutter_combo.findText(name) >= 0

    current = wdg.shutter_combo.currentText()
    assert wdg._is_open == mmc.getShutterOpen(current)
    expected_text = "Close" if wdg._is_open else "Open"
    assert wdg.shutter_button.text() == expected_text
    assert wdg.autoshutter_checkbox.isChecked() == mmc.getAutoShutter()


def test_shutter_widget_basic_no_devices(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """When no shutter devices are loaded button and checkbox are disabled."""
    mmc = global_mmcore
    mmc.unloadAllDevices()
    wdg = ShuttersWidgetBasic(mmcore=mmc)
    qtbot.addWidget(wdg)

    assert wdg.shutter_combo.count() == 0
    assert not wdg.shutter_button.isEnabled()
    assert not wdg.autoshutter_checkbox.isEnabled()
    assert wdg.shutter_button.text() == "Open"


def test_shutter_widget_basic_button_click_toggles_shutter(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """Clicking the button opens or closes the currently selected shutter."""
    mmc = global_mmcore
    # Disable autoshutter so the button is enabled for the core shutter device.
    mmc.setAutoShutter(False)
    wdg = ShuttersWidgetBasic(mmcore=mmc)
    qtbot.addWidget(wdg)

    current = wdg.shutter_combo.currentText()
    initial_open = mmc.getShutterOpen(current)

    with wait_signal(qtbot, mmc.events.propertyChanged):
        wdg.shutter_button.click()

    assert mmc.getShutterOpen(current) != initial_open
    expected_text = "Close" if mmc.getShutterOpen(current) else "Open"
    assert wdg.shutter_button.text() == expected_text

    # Toggle back.
    with wait_signal(qtbot, mmc.events.propertyChanged):
        wdg.shutter_button.click()

    assert mmc.getShutterOpen(current) == initial_open


def test_shutter_widget_basic_autoshutter_checkbox(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """Toggling the auto checkbox calls setAutoShutter on the core."""
    mmc = global_mmcore
    wdg = ShuttersWidgetBasic(mmcore=mmc)
    qtbot.addWidget(wdg)

    initial = mmc.getAutoShutter()
    with wait_signal(qtbot, mmc.events.autoShutterSet):
        wdg.autoshutter_checkbox.setChecked(not initial)

    assert mmc.getAutoShutter() == (not initial)


def test_shutter_widget_basic_autoshutter_changed_signal(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """autoShutterSet from core updates checkbox and button enabled state."""
    mmc = global_mmcore
    wdg = ShuttersWidgetBasic(mmcore=mmc)
    qtbot.addWidget(wdg)

    # The core shutter device is the current combo selection; button disabled when auto.
    current = wdg.shutter_combo.currentText()
    assert mmc.getShutterDevice() == current

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        mmc.setAutoShutter(False)
    assert not wdg.autoshutter_checkbox.isChecked()
    assert wdg.shutter_button.isEnabled()

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        mmc.setAutoShutter(True)
    assert wdg.autoshutter_checkbox.isChecked()
    assert not wdg.shutter_button.isEnabled()


def test_shutter_widget_basic_property_changed_state(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """propertyChanged for the shutter State updates button text."""
    mmc = global_mmcore
    mmc.setAutoShutter(False)
    mmc.setShutterOpen(mmc.getShutterDevice(), False)
    wdg = ShuttersWidgetBasic(mmcore=mmc)
    qtbot.addWidget(wdg)

    assert wdg.shutter_button.text() == "Open"

    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setProperty(mmc.getShutterDevice(), "State", True)

    assert wdg.shutter_button.text() == "Close"


def test_shutter_widget_basic_property_changed_core_shutter(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """propertyChanged Core/Shutter updates button enabled state."""
    mmc = global_mmcore
    wdg = ShuttersWidgetBasic(mmcore=mmc)
    qtbot.addWidget(wdg)

    # Set combo to a non-core shutter and verify button is enabled.
    shutter_devices = list(mmc.getLoadedDevicesOfType(DeviceType.ShutterDevice))
    non_core = next((d for d in shutter_devices if d != mmc.getShutterDevice()), None)
    if non_core is None:
        pytest.skip("Need at least two shutter devices for this test")

    wdg.shutter_combo.setCurrentText(non_core)
    assert wdg.shutter_button.isEnabled()

    # Reassign core shutter to non_core - btn should become disabled (autoshutter on).
    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setProperty("Core", "Shutter", non_core)

    assert not wdg.shutter_button.isEnabled()


def test_shutter_widget_basic_system_config_loaded(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """systemConfigurationLoaded refreshes the widget."""
    mmc = global_mmcore
    wdg = ShuttersWidgetBasic(mmcore=mmc)
    qtbot.addWidget(wdg)

    count_before = wdg.shutter_combo.count()
    assert count_before > 0

    with wait_signal(qtbot, mmc.events.systemConfigurationLoaded):
        mmc.loadSystemConfiguration("MMConfig_demo.cfg")

    # After reload combo should still reflect new loaded devices.
    loaded = list(mmc.getLoadedDevicesOfType(DeviceType.ShutterDevice))
    assert wdg.shutter_combo.count() == len(loaded)


def test_shutter_widget_basic_config_set_updates_button(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """configSet signal re-evaluates button enabled state."""
    mmc = global_mmcore
    wdg = ShuttersWidgetBasic(mmcore=mmc)
    qtbot.addWidget(wdg)

    # With autoshutter on and core shutter selected, button is disabled.
    assert mmc.getAutoShutter()
    assert not wdg.shutter_button.isEnabled()

    with wait_signal(qtbot, mmc.events.configSet):
        mmc.setConfig("Channel", "DAPI")

    # Still disabled because autoshutter is still on.
    assert not wdg.shutter_button.isEnabled()
