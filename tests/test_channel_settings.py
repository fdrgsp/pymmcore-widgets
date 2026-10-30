from __future__ import annotations

from typing import TYPE_CHECKING, cast

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QHeaderView

from pymmcore_widgets import ChannelSettingsTable

if TYPE_CHECKING:
    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot

    from pymmcore_widgets.control._channel_settings_table import IntensitySlider

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


def test_intensity_slider_tracks_spinbox(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    wdg = cast("IntensitySlider", _cell(tbl, 0, "intensity"))
    assert not wdg._slider.isEnabled()

    _cell(tbl, 0, "light_source").setCurrentText(LS_FLOAT)
    assert wdg.group() == LS_FLOAT
    assert wdg._slider.isEnabled()
    # starts at the property's current value
    assert wdg.value() == float(global_mmcore.getProperty("Camera", "TestProperty2"))

    wdg._slider.setValue(wdg._slider.maximum())
    assert wdg.value() == global_mmcore.getPropertyUpperLimit("Camera", "TestProperty2")
    wdg.setValue(0)
    assert wdg._slider.value() == 0


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


def test_camera_columns_hidden_until_advanced(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    table = tbl.table()

    def hidden() -> list[bool]:
        return [
            table.isColumnHidden(table.indexOf(f"{prop}"))
            for prop in tbl.cameraProperties()
        ]

    assert not tbl.advancedVisible()
    assert all(hidden())
    assert not table.isColumnHidden(table.indexOf("Exposure [ms]"))

    tbl.advanced.setChecked(True)
    assert tbl.advancedVisible()
    assert not any(hidden())

    # still applied after the columns are rebuilt
    tbl.refresh()
    assert not any(hidden())
    tbl.setAdvancedVisible(False)
    assert not tbl.advanced.isChecked()
    assert all(hidden())


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

    # a different group shows that group's active preset
    tbl.setChannelGroup("Objective")
    assert tbl.activePreset() == global_mmcore.getCurrentConfig("Objective")


def test_clicking_current_column_applies_preset(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    tbl = ChannelSettingsTable(mmcore=global_mmcore)
    qtbot.addWidget(tbl)
    global_mmcore.setConfig("Channel", "DAPI")
    row = next(
        r
        for r in range(tbl.table().rowCount())
        if _cell(tbl, r, "config").text() == "Rhodamine"
    )
    _cell(tbl, row, "exposure").setValue(55)

    tbl.table().cellClicked.emit(row, 0)
    assert global_mmcore.getCurrentConfig("Channel") == "Rhodamine"
    assert global_mmcore.getExposure() == 55
    assert tbl.activePreset() == "Rhodamine"

    # clicking any other column applies nothing
    tbl.table().cellClicked.emit(0, 1)
    assert global_mmcore.getCurrentConfig("Channel") == "Rhodamine"


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
    assert table.columnWidth(last) == tbl._content_width(last)
    assert table.columnWidth(current_col) == 30
