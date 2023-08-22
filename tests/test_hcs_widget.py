from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pymmcore_widgets._hcs_widget._graphics_items import WellInfo
from pymmcore_widgets._hcs_widget._plate_widget import WellPlateInfo, _PlateWidget

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


@pytest.fixture()
def database_path():
    return Path(__file__).parent / "plate_database_for_tests.json"


def test_plate_widget_set_get_value(qtbot: QtBot, database_path: Path):
    wdg = _PlateWidget(plate_database_path=database_path)
    qtbot.addWidget(wdg)

    plate = wdg._plate_db["standard 96 wp"]
    wells = [
        WellInfo("A1", 0, 0),
        WellInfo("A2", 0, 1),
        WellInfo("B3", 1, 2),
        WellInfo("B4", 1, 3),
        WellInfo("C5", 2, 4),
    ]
    info = WellPlateInfo(plate=plate, wells=wells)

    wdg.setValue(info)
    # sort the list of wells by name
    assert sorted(wdg.scene.value(), key=lambda x: x.name) == wells

    wdg.clear_button.click()
    assert wdg.scene.value() is None


def test_plate_widget_combo(qtbot: QtBot, database_path: Path):
    wdg = _PlateWidget(plate_database_path=database_path)
    qtbot.addWidget(wdg)

    wdg.plate_combo.setCurrentText("standard 96 wp")
    assert wdg.value().plate.id == "standard 96 wp"
