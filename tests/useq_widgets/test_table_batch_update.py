from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
import useq
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication, QMessageBox, QWidget

from pymmcore_widgets.mda._core_positions import CoreConnectedPositionTable
from pymmcore_widgets.useq_widgets._column_info import FloatColumn, TextColumn
from pymmcore_widgets.useq_widgets._data_table import DataTable

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot


class SampleTable(DataTable):
    NAME = TextColumn(key="name")
    X = FloatColumn(key="x")


@pytest.mark.parametrize("hidden", [False, True])
def test_batch_update_preserves_visibility_rows_and_notifications(
    qtbot: QtBot, hidden: bool
) -> None:
    table = SampleTable()
    qtbot.addWidget(table)
    if not hidden:
        table.show()
    rows_inserted = Mock()
    model = table.model()
    assert model is not None
    model.rowsInserted.connect(rows_inserted)
    records = [{"name": "a", "x": 1.5}, {"name": "b", "x": 2.5}]
    with table.batchUpdate():
        assert table.isHidden()
        with table.batchUpdate():
            table.setValue(iter(records))
        assert table.isHidden()
        table.setColumnHidden(table.indexOf(SampleTable.X), True)
    assert table.isHidden() == hidden
    assert list(table.iterRecords()) == records
    assert table.cellWidget(0, table.indexOf(SampleTable.X)) is not None
    assert table.isColumnHidden(table.indexOf(SampleTable.X))
    rows_inserted.assert_called_once()
    assert rows_inserted.call_args.args[1:] == (0, 1)


@pytest.mark.parametrize("hidden", [False, True])
def test_batch_update_restores_visibility_after_cell_error(
    qtbot: QtBot, hidden: bool
) -> None:
    table = SampleTable()
    qtbot.addWidget(table)
    if not hidden:
        table.show()
    with pytest.raises(TypeError, match="Expected dict"):
        table.setValue([None])  # type: ignore[list-item]
    assert table.isHidden() == hidden


def test_failed_record_iterator_preserves_existing_table(qtbot: QtBot) -> None:
    table = SampleTable()
    qtbot.addWidget(table)
    table.show()
    records = [{"name": "original", "x": 3.5}]
    table.setValue(records)

    def broken_records() -> Iterator[dict[str, str | float]]:
        yield {"name": "new", "x": 1.0}
        raise ValueError("Failed to read positions")

    with pytest.raises(ValueError, match="Failed to read"):
        table.setValue(broken_records())
    assert table.isVisible()
    assert list(table.iterRecords()) == records


def test_batch_update_does_not_show_a_table_in_a_hidden_parent(qtbot: QtBot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    table = SampleTable(parent=parent)
    table.show()
    assert not table.isHidden() and not table.isVisible()
    table.setValue([{"name": "a", "x": 1.5}])
    assert not table.isHidden() and not parent.isVisible()
    parent.show()
    assert table.isVisible()


@pytest.mark.parametrize("via_wizard", [False, True])
@pytest.mark.parametrize("hidden", [False, True])
def test_hcs_bulk_import_preserves_positions_and_read_only_state(
    qtbot: QtBot, global_mmcore: CMMCorePlus, via_wizard: bool, hidden: bool
) -> None:
    editor = CoreConnectedPositionTable(mmcore=global_mmcore)
    qtbot.addWidget(editor)
    editor.show()
    table = editor.table()
    if hidden:
        table.hide()
    plan = useq.WellPlatePlan(
        plate=useq.WellPlate.from_str("96-well"),
        a1_center_xy=(1000, 2000),
        selected_wells=(
            tuple(r for r in range(8) for _ in range(12)),
            tuple(c for _ in range(8) for c in range(12)),
        ),
        well_points_plan=useq.GridRowsColumns(
            rows=1, columns=3, fov_width=100, fov_height=100
        ),
    )
    if via_wizard:
        editor._hcs.setValue(plan)
        plan = editor._hcs.value()
        assert plan is not None
        changed = Mock()
        editor.valueChanged.connect(changed)
        editor._hcs.accept()
        changed.assert_called_once()
    else:
        editor.setValue(plan)
    QApplication.processEvents()
    assert editor.value() == plan
    assert table.rowCount() == 288
    assert table.isHidden() == hidden
    assert table.isColumnHidden(table.indexOf(editor.SEQ))
    for row, pos in enumerate(plan):
        record = table.rowData(row)
        assert (record["name"], record["x"], record["y"]) == (pos.name, pos.x, pos.y)
        x_cell = table.cellWidget(row, table.indexOf(editor.X))
        assert x_cell is not None and not x_cell.isEnabled()

    # Updating an existing HCS plan also keeps the read-only columns and data.
    updated = plan.model_copy(update={"a1_center_xy": (3000, 4000)})
    editor._update_table_positions(updated)
    assert editor.value() == updated
    assert table.isHidden() == hidden
    assert table.rowData(0)["x"] == next(iter(updated)).x

    # Loading explicit positions must replace the cached plate and its read-only mode.
    positions = (useq.Position(name="Manual", x=3, y=4),)
    observed = []
    editor.valueChanged.connect(lambda: observed.append(editor.value()))
    editor.setValue(positions)
    assert editor.value() == positions
    assert observed == [positions]
    assert table.isHidden() == hidden
    assert not table.isColumnHidden(table.indexOf(editor.SEQ))
    assert table.cellWidget(0, table.indexOf(editor.X)).isEnabled()


@pytest.mark.parametrize("accept", [False, True])
@pytest.mark.parametrize("hidden", [False, True])
def test_make_editable_preserves_coordinates_and_existing_editors(
    qtbot: QtBot,
    global_mmcore: CMMCorePlus,
    monkeypatch: pytest.MonkeyPatch,
    accept: bool,
    hidden: bool,
) -> None:
    editor = CoreConnectedPositionTable(mmcore=global_mmcore)
    qtbot.addWidget(editor)
    editor.show()
    table = editor.table()
    plan = useq.WellPlatePlan(
        plate=useq.WellPlate.from_str("96-well"),
        a1_center_xy=(1000, 2000),
        selected_wells=((0, 1), (0, 2)),
        well_points_plan=useq.GridRowsColumns(
            rows=1, columns=3, fov_width=100, fov_height=100
        ),
    )
    editor._update_table_positions(plan)
    if hidden:
        table.hide()
    records = list(table.iterRecords())
    cells = [
        table.cellWidget(row, col)
        for row in range(table.rowCount())
        for col in range(table.columnCount())
    ]
    model = table.model()
    assert model is not None
    inserted, removed = Mock(), Mock()
    model.rowsInserted.connect(inserted)
    model.rowsRemoved.connect(removed)
    changed = Mock()
    editor.valueChanged.connect(changed)
    result = QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "exec", lambda self: result)
    # Conversion must use the existing cells, not regenerate the plate's FOVs.
    monkeypatch.setattr(
        useq.WellPlatePlan,
        "__iter__",
        Mock(side_effect=AssertionError("Positions must not be regenerated")),
    )

    editor._edit_hcs_pos.click()
    QApplication.processEvents()

    assert editor._plate_plan is (None if accept else plan)
    assert table.isHidden() == hidden
    assert list(table.iterRecords()) == records
    assert [
        table.cellWidget(row, col)
        for row in range(table.rowCount())
        for col in range(table.columnCount())
    ] == cells
    inserted.assert_not_called()
    removed.assert_not_called()
    assert changed.call_count == int(accept)
    assert table.isColumnHidden(table.indexOf(editor.SEQ)) == (not accept)
    for row in range(table.rowCount()):
        x_cell = table.cellWidget(row, table.indexOf(editor.X))
        name = table.item(row, table.indexOf(editor.NAME))
        assert x_cell is not None and name is not None
        assert x_cell.isEnabled() == accept
        assert bool(name.flags() & Qt.ItemFlag.ItemIsEditable) == accept
