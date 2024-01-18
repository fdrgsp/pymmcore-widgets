from __future__ import annotations

import string
from typing import TYPE_CHECKING, NamedTuple

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QBrush, QPen
from qtpy.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked

from ._custom_plate_widget import CustomPlateWidget
from ._plate_graphics_scene import _HCSGraphicsScene
from ._util import _ResizingGraphicsView, draw_plate
from ._well_plate_model import PLATE_DB_PATH, load_database

if TYPE_CHECKING:
    from pathlib import Path

    from ._graphics_items import Well
    from ._well_plate_model import Plate

AlignCenter = Qt.AlignmentFlag.AlignCenter

ALPHABET = string.ascii_uppercase
PLATE_GRAPHICS_VIEW_HEIGHT = 440
BRUSH = QBrush(Qt.GlobalColor.green)
PEN = QPen(Qt.GlobalColor.black)
PEN.setWidth(1)


class PlateInfo(NamedTuple):
    """Information about a well plate.

    Attributes
    ----------
    plate : Plate
        The well plate object.
    wells : list[WellInfo] | None
        The list of selected wells in the well plate.
    """

    plate: Plate
    wells: list[Well] | None


class PlateSelectorWidget(QWidget):
    """Widget for selecting the well plate and its wells."""

    valueChanged = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        plate_database_path: Path | str = PLATE_DB_PATH,
        plate_database: dict[str, Plate] | None = None,
    ) -> None:
        super().__init__(parent)

        self._plate_db_path = plate_database_path
        self._plate_db = plate_database or load_database(self._plate_db_path)

        # well plate combobox
        combo_label = QLabel()
        combo_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        combo_label.setText("Plate:")
        self.plate_combo = QComboBox()
        self.plate_combo.addItems(list(self._plate_db))
        wp_combo_wdg = QWidget()
        wp_combo_wdg.setLayout(QHBoxLayout())
        wp_combo_wdg.layout().setContentsMargins(0, 0, 0, 0)
        wp_combo_wdg.layout().setSpacing(5)
        wp_combo_wdg.layout().addWidget(combo_label)
        wp_combo_wdg.layout().addWidget(self.plate_combo)

        # clear and custom plate buttons
        self.custom_plate_button = QPushButton(text="Custom Plate")
        self.clear_button = QPushButton(text="Clear Selection")
        self.load_plate_db_button = QPushButton(text="Load Plate Database")
        btns_wdg = QWidget()
        btns_wdg.setLayout(QHBoxLayout())
        btns_wdg.layout().setContentsMargins(0, 0, 0, 0)
        btns_wdg.layout().setSpacing(5)
        btns_wdg.layout().addWidget(self.clear_button)
        btns_wdg.layout().addWidget(self.custom_plate_button)
        btns_wdg.layout().addWidget(self.load_plate_db_button)

        top_wdg = QWidget()
        top_wdg.setLayout(QHBoxLayout())
        top_wdg.layout().setContentsMargins(0, 0, 0, 0)
        top_wdg.layout().setSpacing(5)
        top_wdg.layout().addWidget(wp_combo_wdg)
        top_wdg.layout().addWidget(btns_wdg)

        self.scene = _HCSGraphicsScene(parent=self)
        self.view = _ResizingGraphicsView(self.scene, self)
        self.view.setStyleSheet("background:grey; border-radius: 5px;")
        self.view.setMinimumHeight(PLATE_GRAPHICS_VIEW_HEIGHT)
        self.view.setMinimumWidth(int(PLATE_GRAPHICS_VIEW_HEIGHT * 1.5))

        self.setLayout(QVBoxLayout())
        self.layout().setSpacing(15)
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().addWidget(top_wdg)
        self.layout().addWidget(self.view)

        self._custom_plate = CustomPlateWidget(
            parent=self,
            plate_database=self._plate_db,
            plate_database_path=self._plate_db_path,
        )

        # connect
        self.scene.valueChanged.connect(self.valueChanged)
        self.clear_button.clicked.connect(self.scene._clear_selection)
        self.plate_combo.currentTextChanged.connect(self._draw_plate)
        self.custom_plate_button.clicked.connect(self._show_custom_plate_dialog)
        self._custom_plate.valueChanged.connect(self._update_wdg)
        self.load_plate_db_button.clicked.connect(self._load_plate_database)

        self._draw_plate(self.plate_combo.currentText())

    # _________________________PUBLIC METHODS_________________________ #

    def value(self) -> PlateInfo:
        """Return the current selected wells as a list of (name, row, column)."""
        return PlateInfo(self.current_plate(), self.scene.value())

    def setValue(self, plateinfo: PlateInfo) -> None:
        """Set the current plate and the selected wells.

        `value` is a list of (well_name, row, column).
        """
        if plateinfo.plate.id not in self._plate_db:
            raise ValueError(f"Plate {plateinfo.plate.id} not in the database.")

        self.plate_combo.setCurrentText(plateinfo.plate.id)

        if not plateinfo.wells:
            return

        self.scene.setValue(plateinfo.wells)

    def load_plate_database(self, plate_database_path: Path | str) -> None:
        """Load a new plate database."""
        # update the plate database
        self._plate_db_path = plate_database_path
        self._plate_db = load_database(self._plate_db_path)

        # update the well plate combobox
        with signals_blocked(self.plate_combo):
            self.plate_combo.clear()
        self.plate_combo.addItems(list(self._plate_db))

        # update the custom plate widget
        self._custom_plate.load_plate_database(self._plate_db_path)

    def get_plate_database(self) -> dict[str, Plate]:
        """Return the current plate database."""
        return self._plate_db

    # _________________________PRIVATE METHODS________________________ #

    def _draw_plate(self, plate_name: str) -> None:
        draw_plate(
            self.view, self.scene, self._plate_db[plate_name], brush=BRUSH, pen=PEN
        )
        self.valueChanged.emit()

    def current_plate(self) -> Plate:
        """Return the current selected plate."""
        return self._plate_db[self.plate_combo.currentText()]

    def _show_custom_plate_dialog(self) -> None:
        """Show the custom plate Qdialog widget."""
        if hasattr(self, "_plate"):
            self._custom_plate.close()
        self._custom_plate.show()
        self._custom_plate.plate_table.clearSelection()
        self._custom_plate.reset_values()

    def _update_wdg(
        self,
        new_plate: Plate | None,
        plate_db: dict[str, Plate],
        plate_db_path: Path | str,
    ) -> None:
        """Update the widget with the updated plate database."""
        # if a new plate database is loaded in the custom plate widget, update this
        # widget as well with the new plate database
        if plate_db != self._plate_db:
            self.load_plate_database(plate_db_path)
            return

        # if a new plate is created in the custom plate widget, add it to the
        # plate_combo and set it as the current plate
        with signals_blocked(self.plate_combo):
            self.plate_combo.clear()
        self.plate_combo.addItems(list(self._plate_db))
        if new_plate:
            self.plate_combo.setCurrentText(new_plate.id)  # trigger _draw_plate

    def _load_plate_database(self) -> None:
        """Load a new plate database."""
        (plate_database_path, _) = QFileDialog.getOpenFileName(
            self, "Select a Plate Database", "", "json(*.json)"
        )
        if plate_database_path:
            self.load_plate_database(plate_database_path)
