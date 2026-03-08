from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pymmcore_plus import DeviceType

from pymmcore_widgets.control._shutter_widget import ShuttersWidget, ShutterWidgetBasic
from tests._utils import wait_signal

if TYPE_CHECKING:
    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot


def _make_shutter(
    qtbot: QtBot,
    mmcore: CMMCorePlus,
    device: str = "Shutter",
    autoshutter: bool = True,
) -> ShuttersWidget:
    wdg = ShuttersWidget(
        device,
        autoshutter=autoshutter,
        button_text_open=f"{device} opened",
        button_text_closed=f"{device} closed",
        mmcore=mmcore,
    )
    qtbot.addWidget(wdg)
    return wdg


def test_initial_state(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore

    # Core shutter ("Shutter") with autoshutter on → button disabled
    shutter = _make_shutter(qtbot, mmc, "Shutter", autoshutter=False)
    assert not shutter.shutter_button.isEnabled()

    # Non-core shutter → button enabled
    state_dev = _make_shutter(qtbot, mmc, "StateDev Shutter", autoshutter=False)
    assert state_dev.shutter_button.isEnabled()

    # Autoshutter checkbox shown and checked
    multi = _make_shutter(qtbot, mmc, "Multi Shutter", autoshutter=True)
    assert multi.autoshutter_checkbox.isChecked()
    assert multi.shutter_button.isEnabled()


def test_shutter_state_change(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter = _make_shutter(qtbot, mmc, "Shutter")

    # Explicitly set to closed, then open
    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setShutterOpen("Shutter", False)
    assert shutter.shutter_button.text() == "Shutter closed"

    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setShutterOpen("Shutter", True)
    assert shutter.shutter_button.text() == "Shutter opened"


def test_autoshutter_enables_disables(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter = _make_shutter(qtbot, mmc, "Shutter")
    state_dev = _make_shutter(qtbot, mmc, "StateDev Shutter")

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        mmc.setAutoShutter(False)
    assert shutter.shutter_button.isEnabled()
    assert state_dev.shutter_button.isEnabled()

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        mmc.setAutoShutter(True)
    assert not shutter.shutter_button.isEnabled()
    assert state_dev.shutter_button.isEnabled()


def test_config_set_updates_enabled(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter = _make_shutter(qtbot, mmc, "Shutter")

    with wait_signal(qtbot, mmc.events.configSet):
        mmc.setConfig("Channel", "DAPI")
    # Core shutter + autoshutter on → disabled
    assert not shutter.shutter_button.isEnabled()


def test_autoshutter_checkbox(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter = _make_shutter(qtbot, mmc, "Shutter", autoshutter=True)
    assert shutter.autoshutter_checkbox.isChecked()

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        shutter.autoshutter_checkbox.setChecked(False)
    assert shutter.shutter_button.isEnabled()

    with wait_signal(qtbot, mmc.events.autoShutterSet):
        shutter.autoshutter_checkbox.setChecked(True)
    assert not shutter.shutter_button.isEnabled()


def test_button_click_toggles(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    mmc.setAutoShutter(False)

    shutter = _make_shutter(qtbot, mmc, "Shutter")

    # Set a known state first
    mmc.setShutterOpen("Shutter", True)

    with wait_signal(qtbot, mmc.events.propertyChanged):
        shutter.shutter_button.click()
    assert shutter.shutter_button.text() == "Shutter closed"
    assert not mmc.getShutterOpen("Shutter")

    with wait_signal(qtbot, mmc.events.propertyChanged):
        shutter.shutter_button.click()
    assert shutter.shutter_button.text() == "Shutter opened"
    assert mmc.getShutterOpen("Shutter")


def test_system_config_loaded(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    multi = _make_shutter(qtbot, mmc, "Multi Shutter")

    with pytest.warns(UserWarning):
        with wait_signal(qtbot, mmc.events.systemConfigurationLoaded):
            mmc.loadSystemConfiguration("MMConfig_demo.cfg")
    assert multi.shutter_button.text() == "None"


def test_core_shutter_device_change(qtbot: QtBot, global_mmcore: CMMCorePlus):
    mmc = global_mmcore
    shutter = _make_shutter(qtbot, mmc, "Shutter")
    multi = _make_shutter(qtbot, mmc, "Multi Shutter")

    # Initially Shutter is core shutter → disabled, Multi is not → enabled
    assert not shutter.shutter_button.isEnabled()
    assert multi.shutter_button.isEnabled()

    # Change core shutter to Multi Shutter
    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setProperty("Core", "Shutter", "Multi Shutter")
    assert shutter.shutter_button.isEnabled()
    assert not multi.shutter_button.isEnabled()

    # Change back
    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setProperty("Core", "Shutter", "Shutter")
    assert not shutter.shutter_button.isEnabled()
    assert multi.shutter_button.isEnabled()


# ---------------------------------------------------------------------------
# ShutterWidgetBasic tests
# ---------------------------------------------------------------------------


def _make_shutter_basic(
    qtbot: QtBot,
    mmcore: CMMCorePlus,
) -> ShutterWidgetBasic:
    wdg = ShutterWidgetBasic(mmcore=mmcore)
    qtbot.addWidget(wdg)
    return wdg


def test_basic_initial_state(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    mmc = global_mmcore
    wdg = _make_shutter_basic(qtbot, mmc)

    # Combo should list all loaded shutter devices
    loaded = list(mmc.getLoadedDevicesOfType(DeviceType.ShutterDevice))
    combo_items = [
        wdg.shutter_combo.itemText(i) for i in range(wdg.shutter_combo.count())
    ]
    assert combo_items == loaded

    # Button text reflects the actual shutter open/closed state
    current = wdg.shutter_combo.currentText()
    expected = "Close" if mmc.getShutterOpen(current) else "Open"
    assert wdg.shutter_button.text() == expected

    # Autoshutter checkbox reflects core state
    assert wdg.autoshutter_checkbox.isChecked() == mmc.getAutoShutter()


def test_basic_button_opens_and_closes_shutter(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    mmc = global_mmcore
    mmc.setAutoShutter(False)
    wdg = _make_shutter_basic(qtbot, mmc)

    # Select "Shutter" in the combo
    wdg.shutter_combo.setCurrentText("Shutter")
    # Ensure shutter is closed to start
    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setShutterOpen("Shutter", False)
    assert wdg.shutter_button.text() == "Open"

    # Click → open the shutter
    with wait_signal(qtbot, mmc.events.propertyChanged):
        wdg.shutter_button.click()
    assert mmc.getShutterOpen("Shutter")
    assert wdg.shutter_button.text() == "Close"

    # Click again → close the shutter
    with wait_signal(qtbot, mmc.events.propertyChanged):
        wdg.shutter_button.click()
    assert not mmc.getShutterOpen("Shutter")
    assert wdg.shutter_button.text() == "Open"


def test_basic_autoshutter_disables_button(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    mmc = global_mmcore
    wdg = _make_shutter_basic(qtbot, mmc)

    # Select the core shutter device in the combo
    core_shutter = mmc.getShutterDevice()
    wdg.shutter_combo.setCurrentText(core_shutter)

    # Enable autoshutter → button disabled
    with wait_signal(qtbot, mmc.events.autoShutterSet):
        mmc.setAutoShutter(True)
    assert not wdg.shutter_button.isEnabled()

    # Disable autoshutter → button enabled
    with wait_signal(qtbot, mmc.events.autoShutterSet):
        mmc.setAutoShutter(False)
    assert wdg.shutter_button.isEnabled()


def test_basic_autoshutter_checkbox_controls_autoshutter(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    mmc = global_mmcore
    wdg = _make_shutter_basic(qtbot, mmc)

    # Toggle off autoshutter via checkbox
    with wait_signal(qtbot, mmc.events.autoShutterSet):
        wdg.autoshutter_checkbox.setChecked(False)
    assert not mmc.getAutoShutter()

    # Toggle on autoshutter via checkbox
    with wait_signal(qtbot, mmc.events.autoShutterSet):
        wdg.autoshutter_checkbox.setChecked(True)
    assert mmc.getAutoShutter()


def test_basic_combo_switch_updates_button_state(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    mmc = global_mmcore
    mmc.setAutoShutter(True)
    wdg = _make_shutter_basic(qtbot, mmc)

    # Switch to a non-core shutter → button should be enabled even with autoshutter on
    core_shutter = mmc.getShutterDevice()
    non_core = next(
        d
        for d in mmc.getLoadedDevicesOfType(DeviceType.ShutterDevice)
        if d != core_shutter
    )
    wdg.shutter_combo.setCurrentText(non_core)
    assert wdg.shutter_button.isEnabled()

    # Switch back to core shutter → button disabled
    wdg.shutter_combo.setCurrentText(core_shutter)
    assert not wdg.shutter_button.isEnabled()


def test_basic_property_changed_updates_button_text(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    mmc = global_mmcore
    mmc.setAutoShutter(False)
    wdg = _make_shutter_basic(qtbot, mmc)

    wdg.shutter_combo.setCurrentText("Shutter")

    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setShutterOpen("Shutter", True)
    assert wdg.shutter_button.text() == "Close"

    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setShutterOpen("Shutter", False)
    assert wdg.shutter_button.text() == "Open"


def test_basic_button_icon_changes_with_state(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    mmc = global_mmcore
    mmc.setAutoShutter(False)
    wdg = _make_shutter_basic(qtbot, mmc)

    wdg.shutter_combo.setCurrentText("Shutter")

    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setShutterOpen("Shutter", False)
    assert wdg.shutter_button.icon().cacheKey() == wdg._icon_closed.cacheKey()

    with wait_signal(qtbot, mmc.events.propertyChanged):
        mmc.setShutterOpen("Shutter", True)
    assert wdg.shutter_button.icon().cacheKey() == wdg._icon_open.cacheKey()


def test_basic_system_config_loaded_repopulates_combo(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    mmc = global_mmcore
    wdg = _make_shutter_basic(qtbot, mmc)
    prev_count = wdg.shutter_combo.count()
    assert prev_count > 0

    # ShutterWidgetBasic has no fixed device, so no UserWarning is expected
    with wait_signal(qtbot, mmc.events.systemConfigurationLoaded):
        mmc.loadSystemConfiguration("MMConfig_demo.cfg")

    # Combo should be repopulated with devices from the new config
    loaded = list(mmc.getLoadedDevicesOfType(DeviceType.ShutterDevice))
    combo_items = [
        wdg.shutter_combo.itemText(i) for i in range(wdg.shutter_combo.count())
    ]
    assert combo_items == loaded
