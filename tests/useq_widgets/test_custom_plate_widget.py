from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import useq
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QMessageBox

from pymmcore_widgets.useq_widgets import CustomPlateWidget, WellPlateWidget
from pymmcore_widgets.useq_widgets._custom_plate_widget import (
    _PLATE_REGISTRY,
    load_custom_plate_database,
)
from pymmcore_widgets.useq_widgets._well_plate_widget import _sort_plate

if TYPE_CHECKING:
    from pathlib import Path

    from pytestqt.qtbot import QtBot


@pytest.fixture
def _cleanup_registry() -> None:
    # `useq.register_well_plates` mutates a module-level dict, and this widget
    # can now delete built-ins from it too - snapshot and fully restore so
    # nothing leaks into (or goes missing from) other tests.
    before = dict(_PLATE_REGISTRY)
    yield
    _PLATE_REGISTRY.clear()
    _PLATE_REGISTRY.update(before)


def _set_form(
    dlg: CustomPlateWidget,
    *,
    name: str,
    rows: int = 4,
    columns: int = 5,
    well_size: tuple[float, float] = (2.5, 2.5),
    well_spacing: tuple[float, float] = (5.0, 5.0),
    circular: bool = True,
) -> None:
    dlg._name.setText(name)
    dlg._rows.setValue(rows)
    dlg._columns.setValue(columns)
    dlg._well_size_x.setValue(well_size[0])
    dlg._well_size_y.setValue(well_size[1])
    dlg._spacing_x.setValue(well_spacing[0])
    dlg._spacing_y.setValue(well_spacing[1])
    dlg._circular.setChecked(circular)


def test_custom_plate_widget_save_and_persist(
    qtbot: QtBot, tmp_path: Path, _cleanup_registry: None
) -> None:
    db_path = tmp_path / "custom_well_plates.json"
    dlg = CustomPlateWidget(plate_db_path=db_path)
    qtbot.addWidget(dlg)

    _set_form(dlg, name="my-custom-plate")

    with qtbot.waitSignal(dlg.plateSaved, timeout=1000) as blocker:
        dlg._on_save_clicked()
    assert blocker.args == ["my-custom-plate"]

    # registered with useq, and usable via WellPlate.from_str
    plate = useq.WellPlate.from_str("my-custom-plate")
    assert (plate.rows, plate.columns) == (4, 5)

    # persisted to disk
    db = load_custom_plate_database(db_path)
    assert "my-custom-plate" in db

    # a fresh dialog pointed at the same db loads (and re-registers) the plate
    dlg2 = CustomPlateWidget(plate_db_path=db_path)
    qtbot.addWidget(dlg2)
    assert "my-custom-plate" in dlg2._db


def test_custom_plate_widget_lists_and_allows_editing_builtins(
    qtbot: QtBot, tmp_path: Path, _cleanup_registry: None
) -> None:
    """The list shows every registered plate, not just ones saved locally.

    Built-ins are listed alongside custom plates, and are just as editable
    and deletable - nothing here is read-only.
    """
    dlg = CustomPlateWidget(plate_db_path=tmp_path / "db.json")
    qtbot.addWidget(dlg)

    names = []
    for i in range(dlg._plate_list.count()):
        row = dlg._plate_list.item(i)
        assert row is not None
        names.append(row.text())
    assert "96-well" in names
    assert "6-well" in names

    item = dlg._plate_list.findItems("96-well", Qt.MatchFlag.MatchExactly)[0]
    dlg._plate_list.setCurrentItem(item)

    assert dlg._delete_btn.isEnabled()
    assert dlg._name.text() == "96-well"
    assert dlg._rows.value() == 8
    assert dlg._columns.value() == 12

    # editing and re-saving the selected built-in is an in-place edit (no
    # confirmation), and persists the override into this dialog's own db
    dlg._rows.setValue(1)
    with patch.object(QMessageBox, "question") as mock_question:
        dlg._on_save_clicked()
    mock_question.assert_not_called()
    assert dlg._db["96-well"]["rows"] == 1
    assert useq.WellPlate.from_str("96-well").rows == 1

    # and it can be deleted like any other plate
    yes = QMessageBox.StandardButton.Yes
    with patch.object(QMessageBox, "question", return_value=yes):
        dlg._on_delete_clicked()
    assert "96-well" not in dlg._db
    assert "96-well" not in useq.registered_well_plate_keys()


def test_custom_plate_widget_save_selects_and_focuses_new_plate(
    qtbot: QtBot, tmp_path: Path, _cleanup_registry: None
) -> None:
    """The list is one flat, sorted list (built-ins and customs together);
    a newly saved plate lands wherever it naturally sorts, and is selected
    and focused so it's clearly visible even if that's off the top of the
    (scrolled) list.
    """
    dlg = CustomPlateWidget(plate_db_path=tmp_path / "db.json")
    qtbot.addWidget(dlg)
    dlg.show()

    _set_form(dlg, name="zzz-plate")
    dlg._save_btn.click()

    names = [dlg._plate_list.item(i).text() for i in range(dlg._plate_list.count())]  # type: ignore[union-attr]
    assert names == sorted(useq.registered_well_plate_keys(), key=_sort_plate)

    current = dlg._plate_list.currentItem()
    assert current is not None
    assert current.text() == "zzz-plate"
    # hasFocus() needs the top-level window to be OS-active, which isn't
    # guaranteed under a headless test runner; focusWidget() doesn't.
    assert dlg.focusWidget() is dlg._plate_list


def test_custom_plate_widget_can_reclaim_name_registered_elsewhere(
    qtbot: QtBot, tmp_path: Path, _cleanup_registry: None
) -> None:
    """A name already live-registered with useq (e.g. by another
    CustomPlateWidget instance/process in the same session), but that isn't in
    *this* dialog's own db yet, is treated like any other name clash: confirm,
    then overwrite - it's savable, just not silently.
    """
    useq.register_well_plates(
        {
            "shared-plate": {
                "rows": 2,
                "columns": 2,
                "well_spacing": 1.0,
                "well_size": 1.0,
            }
        }
    )

    dlg = CustomPlateWidget(plate_db_path=tmp_path / "db.json")
    qtbot.addWidget(dlg)
    assert "shared-plate" not in dlg._db

    _set_form(dlg, name="shared-plate")
    yes = QMessageBox.StandardButton.Yes
    with patch.object(QMessageBox, "question", return_value=yes) as mock_question:
        dlg._on_save_clicked()
    mock_question.assert_called_once()
    assert "shared-plate" in dlg._db


def test_custom_plate_widget_prompts_before_overwrite(
    qtbot: QtBot, tmp_path: Path, _cleanup_registry: None
) -> None:
    """Saving a name that collides with a *different* existing plate asks first."""
    dlg = CustomPlateWidget(plate_db_path=tmp_path / "db.json")
    qtbot.addWidget(dlg)

    _set_form(dlg, name="plate-a", rows=2, columns=2)
    dlg._save_btn.click()

    # start a new (unselected) entry that happens to reuse "plate-a"
    dlg._new_btn.click()
    _set_form(dlg, name="plate-a", rows=9, columns=9)

    # decline the overwrite -> nothing changes
    no = QMessageBox.StandardButton.No
    with patch.object(QMessageBox, "question", return_value=no):
        dlg._on_save_clicked()
    assert dlg._db["plate-a"]["rows"] == 2

    # accept the overwrite -> the new values win
    yes = QMessageBox.StandardButton.Yes
    with patch.object(QMessageBox, "question", return_value=yes) as mock_question:
        dlg._on_save_clicked()
    mock_question.assert_called_once()
    assert dlg._db["plate-a"]["rows"] == 9


def test_custom_plate_widget_editing_selected_plate_does_not_prompt(
    qtbot: QtBot, tmp_path: Path, _cleanup_registry: None
) -> None:
    """Re-saving the currently *selected* plate (an in-place edit) is silent."""
    dlg = CustomPlateWidget(plate_db_path=tmp_path / "db.json")
    qtbot.addWidget(dlg)

    _set_form(dlg, name="plate-a", rows=2, columns=2)
    dlg._save_btn.click()
    current = dlg._plate_list.currentItem()
    assert current is not None
    assert current.text() == "plate-a"

    dlg._rows.setValue(7)
    with patch.object(QMessageBox, "question") as mock_question:
        dlg._on_save_clicked()
    mock_question.assert_not_called()
    assert dlg._db["plate-a"]["rows"] == 7


def test_custom_plate_widget_delete(
    qtbot: QtBot, tmp_path: Path, _cleanup_registry: None
) -> None:
    db_path = tmp_path / "db.json"
    dlg = CustomPlateWidget(plate_db_path=db_path)
    qtbot.addWidget(dlg)

    _set_form(dlg, name="to-delete")
    dlg._on_save_clicked()
    assert "to-delete" in useq.registered_well_plate_keys()

    item = dlg._plate_list.findItems("to-delete", Qt.MatchFlag.MatchExactly)[0]
    dlg._plate_list.setCurrentItem(item)

    yes = QMessageBox.StandardButton.Yes
    with patch.object(QMessageBox, "question", return_value=yes):
        with qtbot.waitSignal(dlg.plateDeleted, timeout=1000) as blocker:
            dlg._on_delete_clicked()
    assert blocker.args == ["to-delete"]

    assert "to-delete" not in useq.registered_well_plate_keys()
    assert "to-delete" not in load_custom_plate_database(db_path)


def test_custom_plate_widget_reselect_after_new(
    qtbot: QtBot, tmp_path: Path, _cleanup_registry: None
) -> None:
    """Clicking New, then re-clicking the same item, should re-enable Delete.

    Regression test: `clearSelection()` alone doesn't reset QListWidget's
    "current" item, so re-clicking the same (already-current) row silently
    failed to re-fire `currentTextChanged`, leaving Delete stuck disabled.
    """
    db_path = tmp_path / "db.json"
    dlg = CustomPlateWidget(plate_db_path=db_path)
    qtbot.addWidget(dlg)
    dlg.show()

    _set_form(dlg, name="plate-a")
    dlg._save_btn.click()
    assert dlg._delete_btn.isEnabled()

    dlg._new_btn.click()
    assert dlg._plate_list.currentItem() is None
    assert not dlg._delete_btn.isEnabled()

    # the list now also contains useq's built-ins, so look up "plate-a" by
    # name rather than assuming it's the first row
    item = dlg._plate_list.findItems("plate-a", Qt.MatchFlag.MatchExactly)[0]
    rect = dlg._plate_list.visualItemRect(item)
    qtbot.mouseClick(
        dlg._plate_list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center()
    )

    assert dlg._plate_list.currentItem() is item
    assert dlg._delete_btn.isEnabled()


def test_well_plate_widget_picks_up_custom_plate(
    qtbot: QtBot, tmp_path: Path, _cleanup_registry: None
) -> None:
    db_path = tmp_path / "db.json"
    dlg = CustomPlateWidget(plate_db_path=db_path)
    qtbot.addWidget(dlg)
    _set_form(dlg, name="from-dialog")
    dlg._on_save_clicked()

    wpw = WellPlateWidget()
    qtbot.addWidget(wpw)
    names = [wpw.plate_name.itemText(i) for i in range(wpw.plate_name.count())]
    assert "from-dialog" in names

    wpw._on_custom_plate_saved("from-dialog")
    assert wpw.plate_name.currentText() == "from-dialog"
