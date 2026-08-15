from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from pymmcore_plus.model import PixelSizePreset, Setting
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QDialog, QSplitter

from pymmcore_widgets.config_presets._pixel_configuration_widget import (
    ID,
    NEW,
    PixelConfigurationWidget,
)
from pymmcore_widgets.config_presets._views._device_property_selector import (
    DevicePropertySelector,
)

if TYPE_CHECKING:
    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot

TEST_VALUE = [
    PixelSizePreset(
        "test_1",
        [Setting("Camera", "Binning", "1"), Setting("Camera", "BitDepth", "16")],
        0.5,
        (0.5, 0.0, 0.0, 0.0, 0.5, 0.0),
    ),
    PixelSizePreset(
        "test_2",
        [Setting("Camera", "Binning", "2"), Setting("Camera", "BitDepth", "12")],
        2.0,
        (2, 0.0, 0.0, 0.0, 2, 0.0),
    ),
]


def _displayed(wdg: PixelConfigurationWidget) -> list[tuple[str, str, str]]:
    """(device, property, value) currently shown in the value table."""
    return [
        (s.device_label, s.property_name, s.value) for s in wdg._value_table.settings()
    ]


def test_pixel_config_wdg(qtbot: QtBot, global_mmcore: CMMCorePlus):
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    assert wdg.value() == [
        PixelSizePreset(
            "Res10x", [Setting("Objective", "Label", "Nikon 10X S Fluor")], 1.0
        ),
        PixelSizePreset(
            "Res20x", [Setting("Objective", "Label", "Nikon 20X Plan Fluor ELWD")], 0.5
        ),
        PixelSizePreset(
            "Res40x", [Setting("Objective", "Label", "Nikon 40X Plan Fluor ELWD")], 0.25
        ),
    ]

    # the value table shows the properties of the selected resolutionID
    assert _displayed(wdg) == [("Objective", "Label", "Nikon 10X S Fluor")]

    wdg.setValue(TEST_VALUE)
    assert _displayed(wdg) == [
        ("Camera", "Binning", "1"),
        ("Camera", "BitDepth", "16"),
    ]

    assert wdg.value()[0].affine == (0.5, 0.0, 0.0, 0.0, 0.5, 0.0)
    assert wdg.value()[1].affine == (2, 0.0, 0.0, 0.0, 2, 0.0)


def test_pixel_config_layout(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    """resolutionIDs on the left, values of the selected one on the right."""
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    splitter = next(
        child for child in wdg.findChildren(QSplitter) if child.parent() is wdg
    )
    assert splitter.orientation() == Qt.Orientation.Horizontal
    assert splitter.widget(0).isAncestorOf(wdg._px_table)
    assert splitter.widget(0).isAncestorOf(wdg._affine_table)
    assert splitter.widget(1).isAncestorOf(wdg._value_table)

    # properties are managed entirely from the value table's own toolbar
    value_tb_actions = wdg._value_table._toolbar.actions()
    assert wdg._value_table.act_edit_props in value_tb_actions
    assert wdg._value_table.act_remove_props in value_tb_actions
    assert wdg._value_table.act_edit_props not in wdg._px_table.toolBar().actions()


def test_pixel_config_wdg_sys_cfg_load(qtbot: QtBot):
    # test that a new config is loaded correctly
    from pathlib import Path

    TEST_CONFIG = str(Path(__file__).parent / "test_config.cfg")
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)
    wdg._mmc.loadSystemConfiguration(TEST_CONFIG)
    assert wdg.value()


def test_pixel_config_wdg_define_configs(qtbot: QtBot, global_mmcore: CMMCorePlus):
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    assert list(wdg._mmc.getAvailablePixelSizeConfigs()) == [
        "Res10x",
        "Res20x",
        "Res40x",
    ]

    wdg._px_table._remove_all()
    wdg._on_apply()

    # assert that all configs are removed
    assert not wdg._mmc.getAvailablePixelSizeConfigs()

    wdg.setValue(TEST_VALUE)

    # adding a property applies it to *every* resolutionID
    wdg._set_properties(
        [
            wdg._prop_meta[("Camera", "AllowMultiROI")],
            wdg._prop_meta[("Camera", "Binning")],
            wdg._prop_meta[("Camera", "BitDepth")],
        ]
    )
    assert wdg._resID_map[0].settings == [
        ("Camera", "AllowMultiROI", "0"),
        ("Camera", "Binning", "1"),
        ("Camera", "BitDepth", "16"),
    ]
    # existing values are preserved per resolutionID
    assert wdg._resID_map[1].settings == [
        ("Camera", "AllowMultiROI", "0"),
        ("Camera", "Binning", "2"),
        ("Camera", "BitDepth", "12"),
    ]
    assert wdg._resID_map[0].affine == (0.5, 0.0, 0.0, 0.0, 0.5, 0.0)

    wdg._on_apply()

    assert list(wdg._mmc.getAvailablePixelSizeConfigs()) == ["test_1", "test_2"]
    assert tuple(wdg._mmc.getPixelSizeAffineByID("test_1")) == (
        0.5,
        0.0,
        0.0,
        0.0,
        0.5,
        0.0,
    )
    assert tuple(wdg._mmc.getPixelSizeAffineByID("test_2")) == (
        2,
        0.0,
        0.0,
        0.0,
        2,
        0.0,
    )


def test_pixel_config_wdg_enabled(qtbot: QtBot, global_mmcore: CMMCorePlus):
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    items = wdg._px_table._table.selectedItems()
    assert len(items) == 1
    assert wdg._value_table.view.isEnabled()

    wdg._px_table._table.clearSelection()
    qtbot.waitUntil(lambda: not wdg._value_table.view.isEnabled())
    assert wdg._value_table.settings() == []


def test_pixel_config_edit_properties_dialog(qtbot: QtBot, global_mmcore: CMMCorePlus):
    """The toolbar action opens the picker and applies its selection to all IDs."""
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    chosen = (wdg._prop_meta[("Camera", "Binning")],)

    # cancelling leaves everything untouched
    before = [list(p.settings) for p in wdg.value()]
    with patch.object(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected):
        wdg._value_table.act_edit_props.trigger()
    assert [list(p.settings) for p in wdg.value()] == before

    with (
        patch.object(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted),
        patch.object(DevicePropertySelector, "checkedProperties", lambda self: chosen),
    ):
        wdg._value_table.act_edit_props.trigger()

    assert all(
        [(s.device_name, s.property_name) for s in p.settings]
        == [("Camera", "Binning")]
        for p in wdg.value()
    )
    assert _displayed(wdg) == [("Camera", "Binning", "1")]
    assert not wdg.isClean()


def test_pixel_config_remove_properties(qtbot: QtBot, global_mmcore: CMMCorePlus):
    """The value table's Remove action drops properties from every resolutionID."""
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)
    value_table = wdg._value_table

    wdg._set_properties(
        [
            wdg._prop_meta[("Camera", "Binning")],
            wdg._prop_meta[("Objective", "Label")],
        ]
    )
    wdg.setClean()

    # nothing selected -> the action is off and triggering it is a no-op
    assert not value_table.act_remove_props.isEnabled()
    value_table.act_remove_props.trigger()
    assert len(wdg.value()[0].settings) == 2
    assert wdg.isClean()

    # selecting a row enables it...
    value_table.view.selectRow(0)
    assert value_table.selectedKeys() == [("Camera", "Binning")]
    assert value_table.act_remove_props.isEnabled()

    # ...and removing takes it out of *all* resolutionIDs
    value_table.act_remove_props.trigger()
    assert all(
        [(s.device_name, s.property_name) for s in p.settings]
        == [("Objective", "Label")]
        for p in wdg.value()
    )
    assert _displayed(wdg) == [("Objective", "Label", "Nikon 10X S Fluor")]
    assert not wdg.isClean()
    # the reset cleared the selection, so the action is off again
    assert not value_table.act_remove_props.isEnabled()


def test_pixel_config_wdg_prop_selection(qtbot: QtBot, global_mmcore: CMMCorePlus):
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    wdg._px_table._table.selectRow(1)
    qtbot.waitUntil(
        lambda: _displayed(wdg) == [("Objective", "Label", "Nikon 20X Plan Fluor ELWD")]
    )

    # add ("Camera", "AllowMultiROI") -> it must land in *all* configs
    wdg._set_properties(
        [
            wdg._prop_meta[("Camera", "AllowMultiROI")],
            wdg._prop_meta[("Objective", "Label")],
        ]
    )
    assert all(
        ("Camera", "AllowMultiROI", "0") in wdg._resID_map[i].settings
        for i in wdg._resID_map
    )

    # ...and removing it must remove it from all of them
    wdg._set_properties([wdg._prop_meta[("Objective", "Label")]])
    assert all(
        ("Camera", "AllowMultiROI", "0") not in wdg._resID_map[i].settings
        for i in wdg._resID_map
    )

    # a new resolutionID inherits the properties of the first one
    wdg._px_table._add_row()
    assert wdg._px_table.value()[-1][ID] == NEW
    assert wdg._resID_map[3].settings == [
        ("Objective", "Label", "Nikon 10X S Fluor"),
    ]


def test_pixel_config_wdg_prop_change(qtbot: QtBot, global_mmcore: CMMCorePlus):
    """Editing a value in the table updates only the selected resolutionID."""
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    assert wdg._px_table._table.selectedItems()[0].text() == "Res10x"
    assert _displayed(wdg) == [("Objective", "Label", "Nikon 10X S Fluor")]

    model = wdg._value_table._model
    idx = model.index(0, 1)
    assert model.data(idx) == "Nikon 10X S Fluor"
    assert model.flags(idx) & Qt.ItemFlag.ItemIsEditable
    # the name column is not editable
    assert not model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable
    assert model.data(model.index(0, 0)) == "Objective-Label"

    assert model.setData(idx, "Nikon 40X Plan Fluor ELWD")
    assert wdg.value()[0].settings == [
        ("Objective", "Label", "Nikon 40X Plan Fluor ELWD")
    ]
    # the other resolutionIDs keep their own values
    assert wdg.value()[1].settings == [
        ("Objective", "Label", "Nikon 20X Plan Fluor ELWD")
    ]
    # setting the same value again is a no-op
    assert not model.setData(idx, "Nikon 40X Plan Fluor ELWD")


def test_pixel_config_wdg_px_table(qtbot: QtBot, global_mmcore: CMMCorePlus):
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    assert wdg._px_table._table.selectedItems()[0].text() == "Res10x"
    assert _displayed(wdg) == [("Objective", "Label", "Nikon 10X S Fluor")]

    wdg._px_table._table.selectRow(1)
    qtbot.waitUntil(
        lambda: wdg._px_table._table.selectedItems()
        and wdg._px_table._table.selectedItems()[0].text() == "Res20x"
    )
    qtbot.waitUntil(
        lambda: _displayed(wdg) == [("Objective", "Label", "Nikon 20X Plan Fluor ELWD")]
    )

    assert wdg._resID_map[1].pixel_size_um == 0.5
    spin = wdg._px_table._table.cellWidget(1, 1)
    spin.setValue(10)
    # the above setValue does not trigger the signal, so we need to manually call it
    spin.valueChanged.emit(10)
    assert wdg.value()[1].pixel_size_um == 10
    assert wdg._affine_table.value() == (10, 0.0, 0.0, 0.0, 10, 0.0)
    assert wdg._resID_map[1].pixel_size_um == 10
    assert wdg._resID_map[1].affine == (10, 0.0, 0.0, 0.0, 10, 0.0)


def test_pixel_config_wdg_errors(qtbot: QtBot, global_mmcore: CMMCorePlus):
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    def _show_msg(msg: str):
        return msg

    wdg.setValue([PixelSizePreset("", [Setting("Camera", "AllowMultiROI", "0")], 0.5)])
    with patch.object(wdg, "_show_error_message", _show_msg):
        assert wdg._check_for_errors() == "All resolutionIDs must have a name."

    wdg.setValue(
        [
            PixelSizePreset("test", [Setting("Camera", "AllowMultiROI", "0")], 0.5),
            PixelSizePreset("test", [Setting("Camera", "AllowMultiROI", "1")], 1),
        ]
    )
    with patch.object(wdg, "_show_error_message", _show_msg):
        assert wdg._check_for_errors() == "There are duplicated resolutionIDs: ['test']"

    wdg.setValue([PixelSizePreset("test2", [], 1)])

    with patch.object(wdg, "_show_error_message", _show_msg):
        assert wdg._check_for_errors() == (
            "Each resolutionID must have at least one property."
        )


def test_pixel_config_wdg_warning(qtbot: QtBot, global_mmcore: CMMCorePlus):
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    with pytest.warns(UserWarning, match="ResolutionID 'Res40x' already exists."):
        wdg._px_table._table.item(0, 0).setText("Res40x")


def test_pixel_config_rename(qtbot: QtBot, global_mmcore: CMMCorePlus):
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    wdg._px_table.table().item(0, 0).setText("Renamed")
    assert wdg.value()[0].name == "Renamed"


def test_delete_resID(qtbot: QtBot, global_mmcore: CMMCorePlus):
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    assert len(wdg._resID_map) == 3

    wdg._px_table._table.selectRow(1)
    qtbot.waitUntil(
        lambda: wdg._px_table._table.selectedItems()
        and wdg._px_table._table.selectedItems()[0].text() == "Res20x"
    )

    wdg._px_table._remove_selected()

    assert len(wdg._resID_map) == 2
    assert wdg._resID_map[0].name == "Res10x"
    assert wdg._resID_map[1].name == "Res40x"
    assert wdg.value() == [
        PixelSizePreset(
            "Res10x", [Setting("Objective", "Label", "Nikon 10X S Fluor")], 1.0
        ),
        PixelSizePreset(
            "Res40x", [Setting("Objective", "Label", "Nikon 40X Plan Fluor ELWD")], 0.25
        ),
    ]


def test_pixel_config_clean_state(qtbot: QtBot, global_mmcore: CMMCorePlus):
    """Dirty tracking mirrors ConfigGroupsEditor's isClean/setClean/cleanChanged."""
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    flips: list[bool] = []
    wdg.cleanChanged.connect(flips.append)

    # freshly loaded from the core -> clean, and Apply has nothing to do
    assert wdg.isClean()
    assert not wdg._apply_btn.isEnabled()
    assert wdg._status_label.text() == "No changes"

    # merely selecting rows re-emits internal valueChanged signals, but does
    # not change value(), so the widget must stay clean
    wdg._px_table._table.selectRow(1)
    wdg._px_table._table.selectRow(0)
    assert wdg.isClean()
    assert flips == []

    # a real edit dirties it
    spin = wdg._px_table._table.cellWidget(0, 1)
    original = spin.value()
    spin.setValue(original + 5)
    assert not wdg.isClean()
    assert wdg._apply_btn.isEnabled()
    assert wdg._status_label.text() == "Unsaved changes"
    assert flips == [False]

    # reverting the edit by hand returns to clean, exactly as undoing would
    spin.setValue(original)
    assert wdg.isClean()
    assert flips == [False, True]

    # setClean() re-baselines whatever is currently in the widget
    spin.setValue(original + 9)
    assert not wdg.isClean()
    wdg.setClean()
    assert wdg.isClean()
    assert wdg.value()[0].pixel_size_um == original + 9


@pytest.mark.parametrize(
    "edit", ["name", "affine", "add_row", "remove_row", "properties", "value"]
)
def test_pixel_config_edits_mark_dirty(
    qtbot: QtBot, global_mmcore: CMMCorePlus, edit: str
):
    """Every user-reachable edit path flips the widget to dirty."""
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)
    assert wdg.isClean()

    if edit == "name":
        wdg._px_table.table().item(0, 0).setText("Renamed")
    elif edit == "affine":
        wdg._affine_table.cellWidget(0, 1).setValue(3.5)
    elif edit == "add_row":
        wdg._px_table._add_row()
    elif edit == "remove_row":
        wdg._px_table._table.selectRow(0)
        wdg._px_table._remove_selected()
    elif edit == "properties":
        wdg._set_properties(
            [
                wdg._prop_meta[("Objective", "Label")],
                wdg._prop_meta[("Camera", "Binning")],
            ]
        )
    elif edit == "value":
        model = wdg._value_table._model
        model.setData(model.index(0, 1), "Nikon 40X Plan Fluor ELWD")

    assert not wdg.isClean()


def test_pixel_config_apply_marks_clean(qtbot: QtBot, global_mmcore: CMMCorePlus):
    """Applying to the core makes the current state the new baseline."""
    wdg = PixelConfigurationWidget()
    qtbot.addWidget(wdg)

    spin = wdg._px_table._table.cellWidget(0, 1)
    spin.setValue(spin.value() + 3)
    assert not wdg.isClean()

    with patch.object(PixelConfigurationWidget, "close", lambda self: None):
        wdg._on_apply()

    assert wdg.isClean()
    assert not wdg._apply_btn.isEnabled()
