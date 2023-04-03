from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, cast

import pytest
from qtpy.QtWidgets import QSpinBox, QTableWidget

from pymmcore_widgets._mda import TimePlanWidget

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot

    from pymmcore_widgets._mda._time_plan_widget import _DoubleSpinAndCombo

INTERVAL = 0
TIMEPOINTS = 1


def _value(table: QTableWidget, row: int):
    interval = cast("_DoubleSpinAndCombo", table.cellWidget(row, INTERVAL))
    timepoints = cast("QSpinBox", table.cellWidget(row, TIMEPOINTS))
    return interval, timepoints


def test_time_table_widget(qtbot: QtBot):
    t = TimePlanWidget()
    qtbot.addWidget(t)

    assert t._table.rowCount() == 0
    t._add_button.click()
    t._add_button.click()
    assert t._table.rowCount() == 2

    interval, timepoints = _value(t._table, 0)
    assert interval.value() == timedelta(seconds=1)
    assert timepoints.value() == 1

    t._table.selectRow(0)
    t._remove_button.click()
    assert t._table.rowCount() == 1

    t._clear_button.click()
    assert t._table.rowCount() == 0


def test_set_get_state(qtbot: QtBot):
    t = TimePlanWidget()
    qtbot.addWidget(t)

    state = {
        "phases": [
            {"interval": timedelta(seconds=10), "loops": 10},
            {"interval": timedelta(minutes=5), "loops": 5},
        ]
    }

    t.set_state(state)

    assert t._table.rowCount() == 2

    interval, timepoints = _value(t._table, 0)
    assert interval.value() == timedelta(seconds=10)
    assert timepoints.value() == 10

    interval, timepoints = _value(t._table, 1)
    assert interval.value() == timedelta(minutes=5)
    assert timepoints.value() == 5

    assert t.value() == state

    t._clear()
    assert t._table.rowCount() == 0

    state = {"interval": 10, "loops": 10}
    t.set_state(state)
    interval, timepoints = _value(t._table, 0)
    assert interval.value() == timedelta(seconds=10)
    assert timepoints.value() == 10

    state = {"loops": 10}
    with pytest.raises(KeyError, match="The time_plans dictionary must incluede"):
        t.set_state(state)

    state = {"interval": 10}
    with pytest.raises(KeyError, match="The time_plans dictionary must incluede"):
        t.set_state(state)