from pathlib import Path
from typing import NamedTuple

import matplotlib.patches as patches
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
from useq import (
    GridRowsColumns,
    Position,
    RandomPoints,
)

from ._calibration_widget import (
    CalibrationData,
    PlateCalibrationWidget,
)
from ._fov_widget import Center, FOVSelectorWidget
from ._graphics_items import Well
from ._plate_widget import PlateInfo, PlateSelectorWidget
from ._util import apply_rotation_matrix, get_well_center
from ._well_plate_model import PLATE_DB_PATH, Plate

EXPANDING = (QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


class HCSData(NamedTuple):
    """NamedTuple to store all the info needed to setup an HCS experiment.

    Attributes
    ----------
    plate : Plate
        The selected well plate.
    wells : list[str] | None
        The selected wells.
    mode : Center | RandomPoints | GridRowsColumns | None
        The mode used to select the FOVs.
    calibration : CalibrationData | None
        The data necessary to calibrate the plate.
    positions : list[Position] | None
        The list of FOVs as useq.Positions expressed in stage coordinates.
    """

    plate: Plate | None
    wells: list[Well] | None = None
    mode: Center | RandomPoints | GridRowsColumns | None = None
    calibration: CalibrationData = CalibrationData()
    positions: list[Position] | None = None


class PlatePage(QWizardPage):
    def __init__(
        self,
        plate_database_path: Path | str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.setTitle("Plate and Well Selection")

        self._plate_widget = PlateSelectorWidget(
            plate_database_path=plate_database_path
        )

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(self._plate_widget)

        self.setButtonText(QWizard.WizardButton.NextButton, "Calibration >")

        self.combo = self._plate_widget.plate_combo

    def value(self) -> PlateInfo:
        """Return the selected well plate and the selected wells."""
        return self._plate_widget.value()

    def setValue(self, value: PlateInfo) -> None:
        """Set the current plate and the selected wells.

        `value` is a list of (well_name, row, column).
        """
        self._plate_widget.setValue(value)

    def database(self) -> dict[str, Plate]:
        """Return the current plate database."""
        return self._plate_widget.database()


class PlateCalibrationPage(QWizardPage):
    def __init__(
        self, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent)
        self.setTitle("Plate Calibration")

        self._calibration = PlateCalibrationWidget(mmcore=mmcore)

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(self._calibration)
        self.layout().addItem(QSpacerItem(0, 0, *EXPANDING))

        self.setButtonText(QWizard.WizardButton.NextButton, "FOVs >")

    def value(self) -> CalibrationData:
        """Return the calibration info."""
        return self._calibration.value()

    def setValue(self, value: CalibrationData) -> None:
        """Set the calibration info."""
        self._calibration.setValue(value)


class FOVSelectorPage(QWizardPage):
    def __init__(
        self,
        plate: Plate | None = None,
        mode: Center | RandomPoints | GridRowsColumns | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setTitle("Field of View Selection")

        self._fov_widget = FOVSelectorWidget(plate, mode, parent)

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(self._fov_widget)
        self.layout().addItem(QSpacerItem(0, 0, *EXPANDING))

    def value(
        self,
    ) -> tuple[Plate | None, Center | RandomPoints | GridRowsColumns | None]:
        """Return the list of FOVs."""
        return self._fov_widget.value()

    def setValue(
        self, plate: Plate | None, mode: Center | RandomPoints | GridRowsColumns | None
    ) -> None:
        """Set the list of FOVs."""
        self._fov_widget.setValue(plate, mode)


class HCSWizard(QWizard):
    valueChanged = Signal(object)

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

        # setup plate page)
        self.plate_page = PlatePage(plate_database_path or PLATE_DB_PATH)

        # get currently selected plate
        plate, _ = self.plate_page.value()

        # setup calibration page
        self.calibration_page = PlateCalibrationPage()
        self.calibration_page.setValue(CalibrationData(plate))

        # setup fov page
        fov_w, fov_h = self._get_fov_size()
        mode = Center(0, 0, fov_w, fov_h)
        self.fov_page = FOVSelectorPage(plate, mode)

        # add pages to wizard
        self.addPage(self.plate_page)
        self.addPage(self.calibration_page)
        self.addPage(self.fov_page)

        # connections
        self.plate_page.combo.currentTextChanged.connect(self._on_plate_combo_changed)
        self._mmc.events.pixelSizeChanged.connect(self._on_px_size_changed)
        self._mmc.events.systemConfigurationLoaded.connect(
            self._on_system_config_loaded
        )

    # _________________________PUBLIC METHODS_________________________ #

    def setValue(self, value: HCSData) -> None:
        """Set the values of the wizard.

        Note: the `positions` attribute of the HCSData `value` is not necessary
        and is not used.
        """
        self.plate_page.setValue(PlateInfo(value.plate, value.wells))
        self.calibration_page.setValue(value.calibration)
        self.fov_page.setValue(value.plate, value.mode)

    def value(self) -> HCSData:
        """Return the values of the wizard."""
        plate, well_list = self.plate_page.value()

        calibration_data = self.calibration_page.value()
        assert calibration_data.plate == plate

        fov_plate, mode = self.fov_page.value()
        assert fov_plate == plate

        positions = self._get_positions()

        return HCSData(plate, well_list, mode, calibration_data, positions)

    def accept(self) -> None:
        """Override QWizard default accept method."""
        self.valueChanged.emit(self.value())

    # _________________________PRIVATE METHODS_________________________ #

    def _on_system_config_loaded(self) -> None:
        """Update the scene when the system configuration is loaded."""
        plate, _ = self.plate_page.value()
        self._update_wizard_pages(plate)

    def _on_plate_combo_changed(self, plate_id: str) -> None:
        db = self.plate_page.database()
        plate = db[plate_id] if plate_id else None
        self._update_wizard_pages(plate)

    def _update_wizard_pages(self, plate: Plate | None) -> None:
        self.calibration_page.setValue(CalibrationData(plate))
        fov_w, fov_h = self._get_fov_size()
        self.fov_page.setValue(plate, Center(0, 0, fov_w, fov_h))

    def _on_px_size_changed(self) -> None:
        """Update the scene when the pixel size is changed."""
        plate, mode = self.fov_page.value()

        if plate is None or mode is None:
            return

        # update the mode with the new fov size
        fov_w, fov_h = self._get_fov_size()
        mode = mode.replace(fov_width=fov_w, fov_height=fov_h)

        # update the fov_page with the fov size
        self.fov_page.setValue(plate, mode)

    def _get_fov_size(self) -> tuple[float, float]:
        """Return the image size in mm depending on the camera device."""
        if (
            self._mmc is None
            or not self._mmc.getCameraDevice()
            or not self._mmc.getPixelSizeUm()
        ):
            return (0.0, 0.0)

        _cam_x = self._mmc.getImageWidth()
        _cam_y = self._mmc.getImageHeight()
        image_width_mm = _cam_x * self._mmc.getPixelSizeUm()
        image_height_mm = _cam_y * self._mmc.getPixelSizeUm()

        return image_width_mm, image_height_mm

    def _get_positions(self) -> list[Position] | None:
        wells_centers = self._get_well_center_in_stage_coordinates()
        if wells_centers is None:
            return None
        return self._get_fovs_in_stage_coords(wells_centers)

    def _get_well_center_in_stage_coordinates(
        self,
    ) -> list[tuple[Well, float, float]] | None:
        plate, _ = self.plate_page.value()

        if plate is None:
            return None

        calibration = self.calibration_page.value()

        _, wells = self.plate_page.value()

        if wells is None or calibration.well_A1_center is None:
            return None

        a1_x, a1_y = calibration.well_A1_center
        wells_center_stage_coords = []
        for well in wells:
            x, y = get_well_center(plate, well, a1_x, a1_y)
            if calibration.rotation_matrix is not None:
                x, y = apply_rotation_matrix(
                    calibration.rotation_matrix, a1_x, a1_y, x, y
                )
            wells_center_stage_coords.append((well, x, y))

        return wells_center_stage_coords

    def _get_fovs_in_stage_coords(
        self, wells_centers: list[tuple[Well, float, float]]
    ) -> list[Position]:
        """Get the calibrated stage coords of each FOV of the selected wells."""
        _, mode = self.fov_page.value()

        if mode is None:
            return []

        positions: list[Position] = []

        for well, well_center_x, well_center_y in wells_centers:
            if isinstance(mode, Center):
                positions.append(
                    Position(x=well_center_x, y=well_center_y, name=f"{well.name}")
                )
            else:
                for idx, fov in enumerate(mode):
                    x = fov.x + well_center_x
                    y = fov.y + well_center_y
                    positions.append(Position(x=x, y=y, name=f"{well.name}_{idx:04d}"))

        return positions

    # this is just for testing, remove later _______________________
    def drawPlateMap(self) -> None:
        """Draw the plate map for the current experiment."""
        # get the well centers in stage coordinates
        well_centers = self._get_well_center_in_stage_coordinates()

        if well_centers is None:
            return

        _, ax = plt.subplots()

        plate, _ = self.plate_page.value()

        if plate is None:
            return

        # draw wells
        for _, well_center_x, well_center_y in well_centers:
            plt.plot(well_center_x, well_center_y, "mo")

            if plate.circular:
                sh = patches.Circle(
                    (well_center_x, well_center_y),
                    radius=plate.well_size_x * 1000 / 2,
                    fill=False,
                )
            else:
                w = plate.well_size_x * 1000
                h = plate.well_size_y * 1000
                x = well_center_x - w / 2
                y = well_center_y - h / 2
                sh = patches.Rectangle((x, y), width=w, height=h, fill=False)

            ax.add_patch(sh)

        # draw FOVs
        positions = self._get_positions()
        if positions is None:
            return

        x = [p.x for p in positions]  # type: ignore
        y = [p.y for p in positions]  # type: ignore
        plt.scatter(x, y, color="green")

        ax.axis("equal")
        ax.xaxis.set_visible(False)
        ax.yaxis.set_visible(False)
        plt.show()

    # _______________________________________________________________
