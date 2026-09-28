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

if TYPE_CHECKING:
    from pathlib import Path

    from pytestqt.qtbot import QtBot


@pytest.fixture
def _cleanup_registry() -> None:
    # `useq.register_well_plates` mutates a module-level dict; make sure any
    # plate keys we create here don't leak into other tests.
    before = set(_PLATE_REGISTRY)
    yield
    for key in set(_PLATE_REGISTRY) - before:
        _PLATE_REGISTRY.pop(key, None)


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


def test_custom_plate_widget_rejects_builtin_name(
    qtbot: QtBot, tmp_path: Path, _cleanup_registry: None
) -> None:
    dlg = CustomPlateWidget(plate_db_path=tmp_path / "db.json")
    qtbot.addWidget(dlg)

    _set_form(dlg, name="96-well")
    with patch.object(QMessageBox, "warning") as mock_warning:
        dlg._on_save_clicked()
    mock_warning.assert_called_once()
    assert "96-well" not in dlg._db


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
