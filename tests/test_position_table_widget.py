from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import pytest
from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QTableWidget

from pymmcore_widgets._mda import PositionTable

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


P = 0
X = 1
Y = 2
Z = 3
AF = 4
GRID = 5


def _get_values(table: QTableWidget, row: int) -> list[str | float]:
    result = [table.item(row, P).text()]
    for i in [X, Y, Z, AF]:
        with contextlib.suppress(AttributeError):
            result.append(table.cellWidget(row, i).value())
    return result


def test_single_position(global_mmcore: CMMCorePlus, qtbot: QtBot):
    p = PositionTable()
    qtbot.addWidget(p)

    mmc = global_mmcore
    tb = p._table

    assert not tb.rowCount()

    p.add_button.click()
    mmc.setXYPosition(100, 200)
    p.add_button.click()

    assert tb.rowCount() == 2

    assert _get_values(tb, 0) == ["Pos000", 0.0, 0.0, 0.0]
    assert _get_values(tb, 1) == ["Pos001", 100.0, 200.0, 0.0]

    assert tb.item(1, 0).text() == "Pos001"
    tb.item(0, 0).setText("test")
    assert tb.item(0, 0).text() == "test"
    assert tb.item(1, 0).text() == "Pos000"

    p.add_button.click()
    p.add_button.click()
    assert tb.item(2, 0).text() == "Pos001"
    assert tb.item(3, 0).text() == "Pos002"
    assert tb.rowCount() == 4

    tb.selectRow(2)
    p.remove_button.click()
    assert tb.rowCount() == 3
    assert tb.item(2, 0).text() == "Pos001"


def test_replace_pos(global_mmcore: CMMCorePlus, qtbot: QtBot):
    p = PositionTable()
    qtbot.addWidget(p)

    mmc = global_mmcore
    tb = p._table

    p.add_button.click()
    assert _get_values(tb, 0) == ["Pos000", 0.0, 0.0, 0.0]

    mmc.setXYPosition(100, 200)
    mmc.setPosition(50)

    tb.selectRow(0)
    p.replace_button.click()
    assert _get_values(tb, 0) == ["Pos000", 100.0, 200.0, 50.0]


def test_go_to_pos(global_mmcore: CMMCorePlus, qtbot: QtBot):
    p = PositionTable()
    qtbot.addWidget(p)

    mmc = global_mmcore
    tb = p._table

    mmc.setXYPosition(100, 200)
    mmc.setPosition(50)
    p.add_button.click()
    assert _get_values(tb, 0) == ["Pos000", 100.0, 200.0, 50.0]
    assert round(mmc.getXPosition()) == 100.0
    assert round(mmc.getYPosition()) == 200.0
    assert round(mmc.getPosition()) == 50.0

    mmc.waitForSystem()
    mmc.setXYPosition(0.0, 0.0)
    mmc.setPosition(0.0)
    assert mmc.getXPosition() == 0.0
    assert mmc.getYPosition() == 0.0
    assert mmc.getPosition() == 0.0

    tb.selectRow(0)
    mmc.waitForSystem()
    p.go_button.click()

    assert round(mmc.getXPosition()) == 100.0
    assert round(mmc.getYPosition()) == 200.0
    assert round(mmc.getPosition()) == 50.0


@pytest.fixture()
def pos():
    pos_1 = {
        "name": "Pos000",
        "x": 100.0,
        "y": 200.0,
        "z": 0.0,
        "autofocus": (None),
        "sequence": {
            "grid_plan": {
                "columns": 2,
                "mode": "spiral",
                "overlap": (10.0, 5.0),
                "relative_to": "center",
                "rows": 2,
            }
        },
    }

    pos_2 = {
        "name": "Pos001",
        "x": 10.0,
        "y": 20.0,
        "z": 0.0,
        "autofocus": None,
        "sequence": {
            "grid_plan": {
                "columns": 2,
                "mode": "spiral",
                "overlap": (10.0, 5.0),
                "relative_to": "center",
                "rows": 2,
            }
        },
    }

    pos_3 = {
        "name": "Pos002",
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "autofocus": None,
        "sequence": {
            "grid_plan": {
                "bottom": 0.0,
                "left": 0.0,
                "mode": "row_wise_snake",
                "overlap": (0.0, 0.0),
                "right": 50.0,
                "top": 100.0,
            }
        },
    }
    return pos_1, pos_2, pos_3


def test_relative_grid_position(
    global_mmcore: CMMCorePlus, qtbot: QtBot, pos: tuple[dict[str, Any], ...]
):
    pos_1, pos_2, _ = pos

    p = PositionTable()
    qtbot.addWidget(p)

    mmc = global_mmcore
    tb = p._table

    assert not tb.rowCount()

    mmc.setXYPosition(100, 200)
    p.add_button.click()
    assert tb.rowCount() == 1
    assert tb.isColumnHidden(GRID)

    p._advanced_cbox.setChecked(True)
    assert p._warn_icon.isHidden()
    assert not tb.isColumnHidden(GRID)

    add_grid_btn, remove_grid_btn = p._get_grid_buttons(0)
    assert remove_grid_btn.isHidden()

    grid_plan = pos_1["sequence"]["grid_plan"]
    p._add_grid_plan(grid_plan, 0)

    assert not remove_grid_btn.isHidden()
    assert add_grid_btn.text() == "Edit"
    assert tb.item(0, 0).data(p.GRID_ROLE) == {
        "columns": 2,
        "mode": "spiral",
        "overlap": (10.0, 5.0),
        "relative_to": "center",
        "rows": 2,
    }
    assert tb.item(0, 0).toolTip() == (
        "rows: 2,  columns: 2,  relative_to: center,  overlap: (10.0, 5.0),  "
        "mode: spiral"
    )

    assert p.value() == [pos_1]

    mmc.waitForSystem()
    mmc.setXYPosition(10, 20)
    p.add_button.click()
    p._apply_grid_to_all_positions(0)
    assert tb.item(1, 0).toolTip() == tb.item(0, 0).toolTip()

    assert p.value() == [pos_1, pos_2]

    p._advanced_cbox.setChecked(False)
    assert not p._warn_icon.isHidden()
    assert p.value() == [pos_1, pos_2]

    p._advanced_cbox.setChecked(True)
    remove_grid_btn.click()
    assert remove_grid_btn.isHidden()


def test_absolute_grid_position(
    global_mmcore: CMMCorePlus, qtbot: QtBot, pos: tuple[dict[str, Any], ...]
):
    _, _, pos_3 = pos
    p = PositionTable()
    qtbot.addWidget(p)

    tb = p._table

    assert not tb.rowCount()

    p.add_button.click()
    assert tb.rowCount() == 1
    p._advanced_cbox.setChecked(True)

    _, remove_grid_btn = p._get_grid_buttons(0)
    assert remove_grid_btn.isHidden()

    grid_plan = pos_3["sequence"]["grid_plan"]
    p._add_grid_plan(grid_plan, 0)

    assert tb.item(0, 0).data(p.GRID_ROLE) == {
        "bottom": 0.0,
        "left": 0.0,
        "mode": "row_wise_snake",
        "overlap": (0.0, 0.0),
        "right": 50.0,
        "top": 100.0,
    }

    assert tb.item(0, 0).toolTip() == (
        "top: 100.0,  bottom: 0.0,  left: 0.0,  right: 50.0,  overlap: (0.0, 0.0),  "
        "mode: row_wise_snake"
    )
    pos_3["name"] = "Pos000"
    pos_3["y"] = 100.0
    assert p.value() == [pos_3]


def test_pos_table_set_and_get_state(
    global_mmcore: CMMCorePlus, qtbot: QtBot, pos: tuple[dict[str, Any], ...]
):
    pos_1, pos_2, pos_3 = pos
    p = PositionTable()
    qtbot.addWidget(p)

    p.set_state([pos_1, pos_2, pos_3])
    assert p._warn_icon.isHidden()
    pos_3["y"] = 100.0
    assert p.value() == [pos_1, pos_2, pos_3]
    assert p._advanced_cbox.isChecked()

    p._advanced_cbox.setChecked(False)
    assert not p._warn_icon.isHidden()
    assert p.value() == [pos_1, pos_2, pos_3]


def test_autofocus_position(global_mmcore: CMMCorePlus, qtbot: QtBot):
    p = PositionTable()
    qtbot.addWidget(p)

    mmc = global_mmcore
    tb = p._table

    assert p._autofocus_wdg.value() == {"device_name": None, "use_autofocus": False}
    assert tb.isColumnHidden(AF)
    p._autofocus_wdg.setValue({"device_name": "Z1", "use_autofocus": True})
    assert not tb.isColumnHidden(AF)

    mmc.setXYPosition(100, 200)
    mmc.setZPosition(50)
    mmc.setPosition("Z1", 45)
    mmc.waitForSystem()

    with pytest.warns(UserWarning, match="Autofocus Device is not Locked in Focus"):
        p.add_button.click()
    assert tb.rowCount() == 1
    assert _get_values(tb, 0) == ["Pos000", 100.0, 200.0, 50.0, 45.0]
    assert p._autofocus_wdg.value() == {"device_name": "Z1", "use_autofocus": True}


def test_no_z_stage(global_mmcore: CMMCorePlus, qtbot: QtBot):
    mmc = global_mmcore
    mmc.unloadDevice("Z")

    p = PositionTable()
    qtbot.addWidget(p)

    tb = p._table

    assert tb.isColumnHidden(Z)

    mmc.setXYPosition(100, 200)
    mmc.setPosition("Z1", 45)
    mmc.waitForSystem()

    p._autofocus_wdg.setValue({"device_name": "Z1", "use_autofocus": True})
    assert not tb.isColumnHidden(AF)

    with pytest.warns(UserWarning, match="Autofocus Device is not Locked in Focus"):
        p.add_button.click()
    assert _get_values(tb, 0) == ["Pos000", 100.0, 200.0, 45.0]
    assert p._autofocus_wdg.value() == {"device_name": "Z1", "use_autofocus": True}


def test_no_xy_stage(global_mmcore: CMMCorePlus, qtbot: QtBot):
    mmc = global_mmcore
    mmc.unloadDevice("XY")

    p = PositionTable()
    qtbot.addWidget(p)

    tb = p._table

    mmc.setPosition("Z1", 45)
    mmc.waitForSystem()

    assert tb.isColumnHidden(X)
    assert tb.isColumnHidden(Y)
    assert tb.isColumnHidden(AF)

    p._autofocus_wdg.setValue({"device_name": "Z1", "use_autofocus": True})
    assert not tb.isColumnHidden(AF)
    with pytest.warns(UserWarning, match="Autofocus Device is not Locked in Focus"):
        p.add_button.click()
    assert _get_values(tb, 0) == ["Pos000", 0.0, 45.0]


def test_no_autofocus(global_mmcore: CMMCorePlus, qtbot: QtBot):
    mmc = global_mmcore
    mmc.unloadDevice("Autofocus")

    p = PositionTable()
    qtbot.addWidget(p)

    assert not p._autofocus_wdg._autofocus_checkbox.isEnabled()
    assert p._autofocus_wdg.value() == {"device_name": None, "use_autofocus": False}


def test_set_state_with_autofocus(global_mmcore: CMMCorePlus, qtbot: QtBot):
    p = PositionTable()
    qtbot.addWidget(p)
    tb = p._table

    pos = {
        "name": "Pos000",
        "x": 10.0,
        "y": 20.0,
        "z": 30.0,
        "autofocus": ("Z1", 45.0),
        "sequence": None,
    }

    p.set_state([pos])

    assert not tb.isColumnHidden(AF)
    assert p._autofocus_wdg._autofocus_checkbox.isChecked()
    assert p._autofocus_wdg.value() == {"device_name": "Z1", "use_autofocus": True}
    assert _get_values(tb, 0) == ["Pos000", 10.0, 20.0, 30.0, 45.0]

    assert p.value() == [pos]
