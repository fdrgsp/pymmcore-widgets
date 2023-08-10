from __future__ import annotations

import string
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from ._plate_graphics_scene import _HCSGraphicsScene

# from pymmcore_widgets._util import GRAPHICS_VIEW_HEIGHT
from ._util import GRAPHICS_VIEW_HEIGHT, ResizingGraphicsView, draw_well_plate
from ._well_plate_model import WellPlate

if TYPE_CHECKING:
    from ._graphics_items import WellInfo

AlignCenter = Qt.AlignmentFlag.AlignCenter

ALPHABET = string.ascii_uppercase
CALIBRATED_PLATE: WellPlate | None = None


# class ResizingGraphicsView(QGraphicsView):
#     """A QGraphicsView that resizes the scene to fit the view."""

#     def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None) -> None:
#         super().__init__(scene, parent)

#     def resizeEvent(self, event: QResizeEvent) -> None:
#         self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


class _PlateWidget(QWidget):
    valueChanged = Signal(WellPlate)

    def __init__(
        self, parent: QWidget | None = None, *, plate_database: dict[str, WellPlate]
    ) -> None:
        super().__init__(parent)

        self._plate_db = plate_database

        # well plate combobox
        combo_label = QLabel()
        combo_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        combo_label.setText("Plate:")
        self.wp_combo = QComboBox()
        self.wp_combo.addItems(list(self._plate_db))
        wp_combo_wdg = QWidget()
        wp_combo_wdg.setLayout(QHBoxLayout())
        wp_combo_wdg.layout().setContentsMargins(0, 0, 0, 0)
        wp_combo_wdg.layout().setSpacing(5)
        wp_combo_wdg.layout().addWidget(combo_label)
        wp_combo_wdg.layout().addWidget(self.wp_combo)

        # clear and custom plate buttons
        self.custom_plate = QPushButton(text="Custom Plate")
        self.clear_button = QPushButton(text="Clear Selection")
        btns_wdg = QWidget()
        btns_wdg.setLayout(QHBoxLayout())
        btns_wdg.layout().setContentsMargins(0, 0, 0, 0)
        btns_wdg.layout().setSpacing(5)
        btns_wdg.layout().addWidget(self.custom_plate)
        btns_wdg.layout().addWidget(self.clear_button)

        top_wdg = QWidget()
        top_wdg.setLayout(QHBoxLayout())
        top_wdg.layout().setContentsMargins(0, 0, 0, 0)
        top_wdg.layout().setSpacing(5)
        top_wdg.layout().addWidget(wp_combo_wdg)
        top_wdg.layout().addWidget(btns_wdg)

        self.scene = _HCSGraphicsScene(parent=self)
        self.view = ResizingGraphicsView(self.scene, self)
        self.view.setStyleSheet("background:grey; border-radius: 5px;")
        self.view.setMinimumHeight(GRAPHICS_VIEW_HEIGHT)

        self.setLayout(QVBoxLayout())
        self.layout().setSpacing(15)
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().addWidget(top_wdg)
        self.layout().addWidget(self.view)

        # connect
        self.clear_button.clicked.connect(self.scene._clear_selection)
        self.wp_combo.currentTextChanged.connect(self._update_plate)

        self._update_plate(self.wp_combo.currentText())

    def _update_plate(self, plate_name: str) -> None:
        draw_well_plate(self.view, self.scene, self._plate_db[plate_name])
        self.valueChanged.emit(self.current_plate())

    def current_plate(self) -> WellPlate:
        """Return the current selected plate."""
        return self._plate_db[self.wp_combo.currentText()]

    def value(self) -> tuple[WellPlate, list[WellInfo] | None]:
        """Return the current selected wells as a list of (name, row, column)."""
        return self.current_plate(), self.scene.value()

    def setValue(self, plate: WellPlate, wells: list[WellInfo] | None) -> None:
        """Set the current selected wells.

        `value` is a list of (well_name, row, column).
        """
        self.wp_combo.setCurrentText(plate.id)

        if not wells:
            return

        self.scene.setValue(wells)
