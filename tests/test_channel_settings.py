from __future__ import annotations

from typing import TYPE_CHECKING, cast

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QHeaderView

from pymmcore_widgets import ChannelSettingsTable
from pymmcore_widgets._light_sources import IntensitySpinBox

if TYPE_CHECKING:
    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot

LS_FLOAT = "Camera · TestProperty2"


def _cell(tbl: ChannelSettingsTable, row: int, key: str):  # type: ignore[no-untyped-def]
    table = tbl.table()
    col = next(c for c in range(table.columnCount()) if table.columnInfo(c).key == key)
    return table.cellWidget(row, col) or table.item(row, col)


def test_rows_are_the_group_presets(global_mmcore: CMMCorePlus, qtbot: QtBot) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)

    assert tbl.channelGroup() == global_mmcore.getChannelGroup() == "Channel"
    assert list(tbl.value()) == list(global_mmcore.getAvailableConfigs("Channel"))

    tbl.setChannelGroup("Objective")
    assert list(tbl.value()) == list(global_mmcore.getAvailableConfigs("Objective"))
    assert tbl.table().rowCount() == len(global_mmcore.getAvailableConfigs("Objective"))


def test_settings_remembered_across_groups(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)

    _cell(tbl, 0, "exposure").setValue(42)
    _cell(tbl, 0, "light_source").setCurrentText(LS_FLOAT)
    _cell(tbl, 0, "intensity").setValue(0.5)
    preset = _cell(tbl, 0, "config").text()

    tbl.setChannelGroup("Objective")
    tbl.setChannelGroup("Channel")

    settings = tbl.channelSettings(preset)
    assert settings is not None
    assert settings["exposure"] == 42
    assert settings["light_source"] == LS_FLOAT
    assert settings["intensity"] == 0.5
    assert _cell(tbl, 0, "exposure").value() == 42
    # also available without showing the group
    tbl.setChannelGroup("Objective")
    assert tbl.channelSettings(preset, "Channel") == settings


def test_all_channel_settings_round_trip(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    _cell(tbl, 1, "exposure").setValue(7)
    saved = tbl.allChannelSettings()

    other = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(other)
    other.setAllChannelSettings(saved)
    assert other.allChannelSettings() == saved
    assert _cell(other, 1, "exposure").value() == 7


def test_camera_property_columns(global_mmcore: CMMCorePlus, qtbot: QtBot) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)

    # DemoCamera: Gain (ranged int), Offset (int), ScanMode (choice); never the
    # shape/dtype-changing ones
    props = tbl.cameraProperties()
    assert {"Gain", "Offset", "ScanMode"} <= set(props)
    assert "Binning" not in props
    assert "PixelType" not in props

    _cell(tbl, 0, "camera:Gain").setValue(3)
    _cell(tbl, 0, "camera:ScanMode").setCurrentText("2")
    preset = _cell(tbl, 0, "config").text()
    settings = tbl.channelSettings(preset)
    assert settings is not None
    assert settings["camera_properties"]["Gain"] == 3
    assert settings["camera_properties"]["ScanMode"] == "2"


def test_intensity_is_ranged_by_the_light_source(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    wdg = cast("IntensitySpinBox", _cell(tbl, 0, "intensity"))
    assert isinstance(wdg, IntensitySpinBox)
    assert not wdg.isEnabled()

    _cell(tbl, 0, "light_source").setCurrentText(LS_FLOAT)
    assert wdg.group() == LS_FLOAT
    assert wdg.isEnabled()
    assert wdg.maximum() == global_mmcore.getPropertyUpperLimit(
        "Camera", "TestProperty2"
    )
    # starts at the property's current value
    assert wdg.value() == float(global_mmcore.getProperty("Camera", "TestProperty2"))


def test_apply_channel(global_mmcore: CMMCorePlus, qtbot: QtBot) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    preset = _cell(tbl, 1, "config").text()
    _cell(tbl, 1, "exposure").setValue(33)
    _cell(tbl, 1, "light_source").setCurrentText(LS_FLOAT)
    _cell(tbl, 1, "intensity").setValue(0.25)
    _cell(tbl, 1, "camera:Gain").setValue(2)

    tbl.applyChannel(preset)
    assert global_mmcore.getCurrentConfig("Channel") == preset
    assert global_mmcore.getExposure() == 33
    assert float(global_mmcore.getProperty("Camera", "TestProperty2")) == 0.25
    assert global_mmcore.getProperty("Camera", "Gain") == "2"


def test_light_source_and_camera_checkboxes(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    table = tbl.table()

    def camera_hidden() -> list[bool]:
        return [
            table.isColumnHidden(table.indexOf(f"{prop}"))
            for prop in tbl.cameraProperties()
        ]

    def light_hidden() -> list[bool]:
        return [
            table.isColumnHidden(table.indexOf(header))
            for header in ("Light Source", "Intensity")
        ]

    # the exposure is always shown; light sources by default, camera ones not
    assert not table.isColumnHidden(table.indexOf("Exposure [ms]"))
    assert tbl.lightSourcesVisible()
    assert not any(light_hidden())
    assert not tbl.cameraVisible()
    assert all(camera_hidden())

    tbl.camera.setChecked(True)
    assert tbl.cameraVisible()
    assert not any(camera_hidden())
    tbl.light_sources.setChecked(False)
    assert not tbl.lightSourcesVisible()
    assert all(light_hidden())

    # still applied after the columns are rebuilt
    tbl.refresh()
    assert not any(camera_hidden())
    assert all(light_hidden())
    tbl.setCameraVisible(False)
    tbl.setLightSourcesVisible(True)
    assert not tbl.camera.isChecked()
    assert tbl.light_sources.isChecked()
    assert all(camera_hidden())
    assert not any(light_hidden())


def test_current_column_tracks_core(global_mmcore: CMMCorePlus, qtbot: QtBot) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    table = tbl.table()
    assert table.indexOf(tbl.CURRENT) == 0

    global_mmcore.setConfig("Channel", "FITC")
    assert tbl.activePreset() == "FITC"
    row = next(
        r for r in range(table.rowCount()) if _cell(tbl, r, "config").text() == "FITC"
    )
    assert table.item(row, 0).data(Qt.ItemDataRole.UserRole)
    assert table.selectionModel().isRowSelected(row)
    assert (
        sum(
            bool(table.item(r, 0).data(Qt.ItemDataRole.UserRole))
            for r in range(table.rowCount())
        )
        == 1
    )

    tbl.refresh()
    assert tbl.activePreset() == "FITC"
    assert table.item(row, 0).data(Qt.ItemDataRole.UserRole)
    assert table.selectionModel().isRowSelected(row)

    # a different group shows that group's active preset
    tbl.setChannelGroup("Objective")
    assert tbl.activePreset() == global_mmcore.getCurrentConfig("Objective")


def test_clicking_selector_columns_applies_preset(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    table = tbl.table()
    global_mmcore.setConfig("Channel", "DAPI")
    rows = range(table.rowCount())
    row = next(r for r in rows if _cell(tbl, r, "config").text() == "Rhodamine")
    _cell(tbl, row, "exposure").setValue(55)

    # the current column...
    table.cellClicked.emit(row, table.indexOf(tbl.CURRENT))
    assert global_mmcore.getCurrentConfig("Channel") == "Rhodamine"
    assert global_mmcore.getExposure() == 55
    assert tbl.activePreset() == "Rhodamine"

    # ...and the preset name apply the row's preset
    row = next(r for r in rows if _cell(tbl, r, "config").text() == "FITC")
    table.cellClicked.emit(row, table.indexOf(tbl.PRESET))
    assert global_mmcore.getCurrentConfig("Channel") == "FITC"

    # clicking any other column applies nothing
    table.cellClicked.emit(0, table.indexOf(tbl.EXPOSURE))
    assert global_mmcore.getCurrentConfig("Channel") == "FITC"


def test_only_selector_columns_select(global_mmcore: CMMCorePlus, qtbot: QtBot) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    tbl.show()
    qtbot.waitExposed(tbl)
    table = tbl.table()
    sel = table.selectionModel()
    global_mmcore.setConfig("Channel", "DAPI")
    rows = range(table.rowCount())
    dapi = next(r for r in rows if _cell(tbl, r, "config").text() == "DAPI")
    other, third = [r for r in rows if r != dapi][:2]

    def click(row: int, key: str) -> None:
        # clicked through the viewport, so the event goes to whatever is under
        # the cursor (e.g. a cell widget) and propagates as from a real click
        col = next(
            c for c in range(table.columnCount()) if table.columnInfo(c).key == key
        )
        center = table.visualRect(table.model().index(row, col)).center()
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=center)

    # another row's widgets take the focus and stay editable, without selecting
    # or applying that row
    spin = _cell(tbl, other, "exposure")
    click(other, "exposure")
    # the window's focus widget: unlike QApplication.focusWidget(), this does not
    # need the window to be active, which CI cannot always make it
    assert tbl.focusWidget() is spin
    qtbot.keyClick(spin, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClicks(spin, "42")
    qtbot.keyClick(spin, Qt.Key.Key_Return)
    assert spin.value() == 42
    assert tbl.channelSettings(_cell(tbl, other, "config").text())["exposure"] == 42
    assert tbl.activePreset() == "DAPI"
    assert sel.isRowSelected(dapi)
    assert not sel.isRowSelected(other)

    # clicking its preset name selects and applies it
    click(other, "config")
    assert tbl.activePreset() == _cell(tbl, other, "config").text()
    assert global_mmcore.getExposure() == 42
    assert sel.isRowSelected(other)
    assert not sel.isRowSelected(dapi)

    # and so does clicking a row's current-column ring
    click(third, "_current")
    assert tbl.activePreset() == _cell(tbl, third, "config").text()
    assert sel.isRowSelected(third)
    assert not sel.isRowSelected(other)


def test_columns_fill_width_and_absorb_resizes(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    tbl.resize(1000, 300)
    tbl.show()
    qtbot.waitExposed(tbl)
    table = tbl.table()
    header = table.horizontalHeader()
    vp_width = table.viewport().width()

    def used() -> int:
        return sum(
            table.columnWidth(c)
            for c in range(table.columnCount())
            if not table.isColumnHidden(c)
        )

    current_col = table.indexOf(tbl.CURRENT)
    assert header.sectionResizeMode(current_col) == QHeaderView.ResizeMode.Fixed
    assert table.columnWidth(current_col) == 30
    assert used() == vp_width

    ls_col = table.indexOf(tbl._light_source_column)
    last = table.indexOf(tbl.INTENSITY)
    # narrowing a column hands the freed width to the last one
    header.resizeSection(ls_col, table.columnWidth(ls_col) - 100)
    assert used() == vp_width
    # widening one past the room left stops before the last one gets cut off
    header.resizeSection(ls_col, table.columnWidth(ls_col) + 5000)
    assert used() == vp_width
    assert table.columnWidth(last) == tbl._fitter.contentWidth(last)
    assert table.columnWidth(current_col) == 30


def test_unset_camera_properties_are_left_unchanged(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    global_mmcore.setProperty("Camera", "Gain", "5")
    preset = _cell(tbl, 0, "config").text()
    settings = tbl.channelSettings(preset)
    assert settings is not None
    assert settings["camera_properties"] == {}  # every cell starts at "—"

    tbl.applyChannel(preset)
    assert global_mmcore.getProperty("Camera", "Gain") == "5"

    # setting a value and clearing it again leaves nothing stored
    gain = _cell(tbl, 0, "camera:Gain")
    gain.setOptionalValue(2)
    assert tbl.channelSettings(preset)["camera_properties"] == {"Gain": 2}
    gain.setOptionalValue(None)
    assert tbl.channelSettings(preset)["camera_properties"] == {}


def test_table_fits_its_rows(global_mmcore: CMMCorePlus, qtbot: QtBot) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    tbl.show()
    qtbot.waitExposed(tbl)
    table = tbl.table()
    assert not tbl._resize_grip.isVisible()
    assert table.verticalScrollBar().maximum() == 0

    def rows_fit() -> None:
        rows = sum(table.rowHeight(r) for r in range(table.rowCount()))
        assert table.viewport().height() >= rows

    qtbot.waitUntil(rows_fit)
    assert table.minimumHeight() == table.maximumHeight()
