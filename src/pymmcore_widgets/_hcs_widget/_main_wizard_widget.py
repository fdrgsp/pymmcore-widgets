from pathlib import Path

import matplotlib.pyplot as plt
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)
from rich import print

from ._calibration_widget import _CalibrationWidget
from ._fov_widget import _FOVSelectrorWidget
from ._graphics_items import WellInfo
from ._plate_widget import _PlateWidget
from ._util import apply_rotation_matrix, get_well_center
from ._well_plate_model import PLATE_DB_PATH, WellPlate, load_database


class PlatePage(QWizardPage):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        plate_database_path: Path | str,
        plate_database: dict[str, WellPlate],
    ) -> None:
        super().__init__(parent)

        self.setTitle("Plate and Well Selection")

        self._plate_db = plate_database
        self._plate_db_path = plate_database_path

        self._plate_widget = _PlateWidget(
            plate_database_path=self._plate_db_path, plate_database=self._plate_db
        )

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(self._plate_widget)

        self.setButtonText(QWizard.WizardButton.NextButton, "Calibration >")


class PlateCalibrationPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Plate Calibration")

        self._calibration = _CalibrationWidget()

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(self._calibration)

        self.setButtonText(QWizard.WizardButton.NextButton, "FOVs >")


class FOVSelectorPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Field of View Selection")

        self._fov_widget = _FOVSelectrorWidget()

        spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addItem(spacer)
        self.layout().addWidget(self._fov_widget)
        self.layout().addItem(spacer)

        self.setButtonText(QWizard.WizardButton.FinishButton, "Run")


class HCSWizard(QWizard):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        mmcore: CMMCorePlus | None = None,
        plate_database_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        self.setWindowTitle("HCS Wizard")

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 50, 0, 0)
        self.layout().setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._mmc = mmcore or CMMCorePlus.instance()

        self._plate_db_path = plate_database_path or PLATE_DB_PATH
        self._plate_db = load_database(self._plate_db_path)

        self.plate_page = PlatePage(
            plate_database_path=self._plate_db_path, plate_database=self._plate_db
        )
        self.plate_page._plate_widget.plate_combo.currentTextChanged.connect(
            self._on_plate_combo_changed
        )

        self.calibration_page = PlateCalibrationPage()

        self.fov_page = FOVSelectorPage()

        self.addPage(self.plate_page)
        self.addPage(self.calibration_page)
        self.addPage(self.fov_page)

        _run = self.button(QWizard.WizardButton.FinishButton)  # name set in self.page3
        _run.disconnect(self)  # disconnect default behavior
        _run.clicked.connect(self._on_finish_clicked)

        self._on_plate_combo_changed(
            self.plate_page._plate_widget.plate_combo.currentText()
        )

        self.fov_page._fov_widget.center_wdg._radio_btn.setChecked(True)

    def _on_plate_combo_changed(self, plate_id: str) -> None:
        plate = self._plate_db[plate_id]
        self.calibration_page._calibration._update(plate)
        self.fov_page._fov_widget._update(plate)

    def _on_finish_clicked(self) -> None:
        print("__________________________")
        print(self.plate_page._plate_widget.value())
        print(self.calibration_page._calibration.value())
        print(self.fov_page._fov_widget.value())
        print("__________________________")

        well_centers = self._get_well_center_stage_coordinates()
        if not well_centers:
            return

        print(well_centers)

        fovs, c = self._get_fovs_stage_coords(well_centers)

        print(fovs)

        # this is only for testing, remove later____________________________________
        plt.cla()
        calib_info = self.calibration_page._calibration.value()
        a1_x, a1_y = (calib_info.well_a1_center_x, calib_info.well_a1_center_y)
        plt.plot(a1_x, a1_y, "ko")
        for fx, fy in fovs:
            plt.plot(fx, fy, "go")
        for cx, cy in c:
            plt.plot(cx, cy, "bo")
        plt.axis("equal")
        # ax = plt.gca()
        # ax.invert_yaxis()
        plt.show()
        # ______________________________________________________________________________

    def _get_well_center_stage_coordinates(
        self,
    ) -> list[tuple[WellInfo, float, float]] | None:
        well_list = self.plate_page._plate_widget.value().wells
        if well_list is None:
            return None

        calib_info = self.calibration_page._calibration.value()

        if calib_info is None:
            return None

        plate = self.plate_page._plate_widget.value().plate

        a1_x, a1_y = (calib_info.well_a1_center_x, calib_info.well_a1_center_y)
        wells_center_stage_coords = []
        for well in well_list:
            x, y = get_well_center(plate, well, a1_x, a1_y)
            wells_center_stage_coords.append((well, x, y))

        return wells_center_stage_coords

    def _get_fovs_stage_coords(
        self, wells_center: list[tuple[WellInfo, float, float]]
    ) -> None:
        """Get the calibrated stage coords of each FOV of the selected wells."""
        calib_info = self.calibration_page._calibration.value()
        if calib_info is None:
            return

        plate = self.plate_page._plate_widget.value().plate
        _, fov_list = self.fov_page._fov_widget.value()
        a1_x, a1_y = (calib_info.well_a1_center_x, calib_info.well_a1_center_y)
        rotation_matrix = calib_info.rotation_matrix
        c = []
        fovs = []
        for well, well_center_x, well_center_y in wells_center:
            for fov in fov_list:
                # well_size_x in px is the width of the graphics view
                well_size_x_px = self.fov_page._fov_widget.view.sceneRect().width()
                # calculate the the value of 1px in µm
                px_um = plate.well_size_x * 1000 / well_size_x_px
                # get the stage coordinates of the top left corner of the well
                well_stage_coord_left = well_center_x - (plate.well_size_x * 1000 / 2)
                well_stage_coord_top = well_center_y - (plate.well_size_y * 1000 / 2)
                # get the stage coordinates of the fov
                fov_stage_coord_x = well_stage_coord_left + (fov.x * px_um)
                fov_stage_coord_y = well_stage_coord_top + (fov.y * px_um)
                # apply rotation matrix
                if rotation_matrix is not None:
                    fov_stage_coord_x, fov_stage_coord_y = apply_rotation_matrix(
                        rotation_matrix,
                        a1_x,
                        a1_y,
                        fov_stage_coord_x,
                        fov_stage_coord_y,
                    )

                fovs.append((fov_stage_coord_x, fov_stage_coord_y))

            # to remove, here it is only to visualize the well center
            if rotation_matrix is not None:
                well_center_x, well_center_y = apply_rotation_matrix(
                    rotation_matrix, a1_x, a1_y, well_center_x, well_center_y
                )
            c.append((well_center_x, well_center_y))

            print()
            print('well center:', well_center_x, well_center_y, 'a1:', a1_x, a1_y)

        return fovs, c
