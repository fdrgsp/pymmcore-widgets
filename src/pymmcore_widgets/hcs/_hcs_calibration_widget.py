from __future__ import annotations

from typing import TYPE_CHECKING

from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from ._plate_calibration_widget import PlateCalibrationWidget
from ._plate_navigator_widget import PlateNavigatorWidget

if TYPE_CHECKING:
    import useq


class HCSCalibrationWidget(QWidget):
    def __init__(
        self, parent: QWidget | None = None, *, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent)

        self._mmc = mmcore or CMMCorePlus.instance()

        self._plate_calibration_widget = PlateCalibrationWidget(mmcore=self._mmc)
        self._plate_navigator_widget = PlateNavigatorWidget(mmcore=self._mmc)

        # tab widget
        self._tab_widget = QTabWidget()
        self._tab_widget.addTab(self._plate_calibration_widget, "Plate Calibration")
        self._tab_widget.addTab(self._plate_navigator_widget, "Test Calibration")

        # layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self._tab_widget)

        # connections
        self._plate_calibration_widget.calibrationChanged.connect(self._on_calibration)

    def setPlate(self, plate: str | useq.WellPlate | useq.WellPlatePlan) -> None:
        self._plate_calibration_widget.setPlate(plate)

    def _on_calibration(self, state: bool) -> None:
        """Update the plate navigator widget when the calibration state changes."""
        plate_plan = self._plate_calibration_widget.platePlan() if state else None
        self._plate_navigator_widget.setPlate(plate_plan)
