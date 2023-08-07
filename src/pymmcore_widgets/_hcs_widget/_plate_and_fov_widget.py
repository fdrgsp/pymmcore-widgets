from __future__ import annotations

import string
from typing import TYPE_CHECKING, NamedTuple

from pymmcore_plus import CMMCorePlus
from PyQt6.QtWidgets import QWidget
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QGroupBox,
    QVBoxLayout,
)

from ._fov_widget import Center, Grid, Random, _FOVSelectrorWidget
from ._plate_widget import _PlateWidget
from ._well_plate_model import PLATE_DB_PATH, WellPlate, load_database

if TYPE_CHECKING:
    from pathlib import Path


AlignCenter = Qt.AlignmentFlag.AlignCenter

ALPHABET = string.ascii_uppercase
CALIBRATED_PLATE: WellPlate | None = None


class WellsAndFovs(NamedTuple):
    plate: WellPlate
    wells: list[tuple[str, int, int]] | None
    fovs: list[tuple[float, float]]
    fov_info: Center | Random | Grid


class PlateAndFovWidget(QWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        mmcore: CMMCorePlus | None = None,
        plate_database_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)

        self._mmc = mmcore or CMMCorePlus.instance()

        self._plate_db_path = plate_database_path or PLATE_DB_PATH
        self._plate_db = load_database(self._plate_db_path)

        self._plate_widget = _PlateWidget(plate_database=self._plate_db)
        plate_groupbox = QGroupBox()
        plate_groupbox.setLayout(QVBoxLayout())
        plate_groupbox.layout().setContentsMargins(0, 0, 0, 0)
        plate_groupbox.layout().addWidget(self._plate_widget)

        self._fov_selector = _FOVSelectrorWidget(mmcore=self._mmc)
        fov_groupbox = QGroupBox()
        fov_groupbox.setLayout(QVBoxLayout())
        fov_groupbox.layout().setContentsMargins(0, 0, 0, 0)
        fov_groupbox.layout().addWidget(self._fov_selector)

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(10)
        self.layout().addWidget(plate_groupbox)
        self.layout().addWidget(fov_groupbox)

        self._fov_selector._load_plate_info(self._plate_widget.current_plate())

        # connect
        self._plate_widget.valueChanged.connect(self._update_fov_scene)

    def _update_fov_scene(self, plate: WellPlate) -> None:
        self._fov_selector._load_plate_info(plate)

    def value(self) -> WellsAndFovs:
        """Return the selected wells and coordinates for the FOVs selection."""
        plate, wells = self._plate_widget.value()
        fovs, fovs_info = self._fov_selector.value()
        return WellsAndFovs(plate=plate, wells=wells, fovs=fovs, fov_info=fovs_info)

    def setValue(self, wells_and_fovs: WellsAndFovs) -> None:
        """Set the selected wells and coordinates for the FOVs selection."""
        self._plate_widget.setValue(wells_and_fovs.plate, wells_and_fovs.wells)
        self._fov_selector.setValue(wells_and_fovs.fov_info)
