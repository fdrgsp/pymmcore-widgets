from __future__ import annotations

from typing import TYPE_CHECKING

from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QHBoxLayout, QWidget

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
        self._plate_navigator_widget.hide()

        # add the plate navigator widget to the top of the plate calibration widget
        # TODO: get the layout without making the 'top' layout available from
        # PlateCalibrationWidget
        self._plate_calibration_widget.top.insertWidget(
            0, self._plate_navigator_widget, 1
        )

        # layout
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._plate_calibration_widget)

        # connections
        self._plate_calibration_widget.calibrationChanged.connect(self._on_calibration)
        self._plate_calibration_widget._test_checkbox.toggled.connect(
            self._on_test_toggled
        )

    def setPlate(self, plate: str | useq.WellPlate | useq.WellPlatePlan) -> None:
        self._plate_calibration_widget.setPlate(plate)

    def _on_calibration(self, state: bool) -> None:
        """Update the plate navigator widget when the calibration state changes."""
        plate_plan = self._plate_calibration_widget.platePlan() if state else None
        self._plate_navigator_widget.setPlate(plate_plan)

    def _on_test_toggled(self, state: bool) -> None:
        well = self._plate_calibration_widget._plate_view.selectedIndices()[0]
        well_wdg = self._plate_calibration_widget._calibration_widgets[well]
        well_wdg.setEnabled(not state)
        if state:
            self._plate_navigator_widget.show()
            self._plate_calibration_widget._plate_view.hide()
            self._plate_calibration_widget._info.hide()
            self._plate_calibration_widget._info_icon.hide()
        else:
            self._plate_navigator_widget.hide()
            self._plate_calibration_widget._plate_view.show()
            self._plate_calibration_widget._info.show()
            self._plate_calibration_widget._info_icon.show()
