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
from superqt.utils import signals_blocked

from ._fov_widget import Center, Grid, Random, _FOVSelectrorWidget
from ._plate_widget import _PlateWidget
from ._update_plate_dialog import _PlateDatabaseWidget
from ._well_plate_model import PLATE_DB_PATH, WellPlate, load_database

if TYPE_CHECKING:
    from pathlib import Path

    from ._graphics_items import WellInfo

AlignCenter = Qt.AlignmentFlag.AlignCenter

ALPHABET = string.ascii_uppercase
CALIBRATED_PLATE: WellPlate | None = None


class WellsAndFovs(NamedTuple):
    """Named tuple with information about the selected wells and FOVs.

    Attributes
    ----------
    plate : WellPlate
        The selected well plate.
    wells : list[WellInfo] | None
        The list of selected wells.
    fovs : list[tuple[float, float]]
        The list of (x, y) coordinates of the selected FOVs per well.
    fov_info : Center | Random | Grid
        The FOV selection mode.
    fov_scene_px_size_mm : tuple[float, float]
        The (x, y) scene pixel size expressed in mm.
    """

    plate: WellPlate
    wells: list[WellInfo] | None
    fovs: list[tuple[float, float]]
    fov_info: Center | Random | Grid
    fov_scene_px_size_mm: tuple[float, float]


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

        self._plate = _PlateDatabaseWidget(
            parent=self,
            plate_database=self._plate_db,
            plate_database_path=self._plate_db_path,
        )

        self._fov_selector._load_plate_info(self._plate_widget.current_plate())

        # connect
        self._plate_widget.valueChanged.connect(self._update_fov_scene)
        self._plate_widget.custom_plate.clicked.connect(self._show_custom_plate_dialog)
        self._plate.valueChanged.connect(self._update_plate_widget_combo)

    def _update_fov_scene(self, plate: WellPlate) -> None:
        self._fov_selector._load_plate_info(plate)

    def _show_custom_plate_dialog(self) -> None:
        """Show the custom plate Qdialog widget."""
        if hasattr(self, "_plate"):
            self._plate.close()
        self._plate.show()
        self._plate.plate_table.clearSelection()
        self._plate.reset_values()

    def _update_plate_widget_combo(self, new_plate: WellPlate) -> None:
        """Update the well plate combobox with the new plate."""
        with signals_blocked(self._plate_widget.wp_combo):
            self._plate_widget.wp_combo.clear()
            self._plate_widget.wp_combo.addItems(list(self._plate_db))
        self._plate_widget.wp_combo.setCurrentText(
            new_plate.id if new_plate else self._plate_widget.wp_combo.itemText(0)
        )

    def value(self) -> WellsAndFovs:
        """Return the selected wells and coordinates for the FOVs selection."""
        plate, wells = self._plate_widget.value()
        fovs, fovs_info, scene_px_size_mm = self._fov_selector.value()
        return WellsAndFovs(
            plate=plate,
            wells=wells,
            fovs=fovs,
            fov_info=fovs_info,
            fov_scene_px_size_mm=scene_px_size_mm,
        )

    def setValue(self, wells_and_fovs: WellsAndFovs) -> None:
        """Set the selected wells and coordinates for the FOVs selection."""
        self._plate_widget.setValue(wells_and_fovs.plate, wells_and_fovs.wells)
        self._fov_selector.setValue(wells_and_fovs.fov_info)
