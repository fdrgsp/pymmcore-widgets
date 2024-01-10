from pathlib import Path
from typing import NamedTuple

import matplotlib.patches as patches
import matplotlib.pyplot as plt
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QDialog,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)
from rich import print
from useq import (
    GridRowsColumns,
    Position,
    RandomPoints,
)

from pymmcore_widgets._mda import PositionTable

from ._calibration_widget import CalibrationData, CalibrationInfo, _CalibrationWidget
from ._fov_widget import Center, FOVSelectorWidget
from ._graphics_items import WellInfo
from ._plate_widget import WellPlateInfo, _PlateWidget
from ._util import apply_rotation_matrix, get_well_center
from ._well_plate_model import PLATE_DB_PATH, WellPlate, load_database

EXPANDING = (QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


class HCSInfo(NamedTuple):
    plate: WellPlate
    wells: list[str] | None
    calibration: CalibrationData | None
    fov_mode: Center | RandomPoints | GridRowsColumns
    positions: list[Position] | None = None


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

    def value(self) -> WellPlateInfo:
        """Return the selected well plate and the selected wells."""
        return self._plate_widget.value()

    def setValue(self, plateinfo: WellPlateInfo) -> None:
        """Set the current plate and the selected wells.

        `value` is a list of (well_name, row, column).
        """
        self._plate_widget.setValue(plateinfo)


class PlateCalibrationPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Plate Calibration")

        self._calibration = _CalibrationWidget()

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(self._calibration)

        self.setButtonText(QWizard.WizardButton.NextButton, "FOVs >")

    def value(self) -> CalibrationInfo:
        """Return the calibration info."""
        return self._calibration.value()

    def setValue(self, calibration_info: CalibrationInfo) -> None:
        """Set the calibration info."""
        self._calibration.setValue(calibration_info)


class FOVSelectorPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Field of View Selection")

        self._fov_widget = FOVSelectorWidget()

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addItem(QSpacerItem(0, 0, *EXPANDING))
        self.layout().addWidget(self._fov_widget)
        self.layout().addItem(QSpacerItem(0, 0, *EXPANDING))

        # rename finish button
        # self.setButtonText(QWizard.WizardButton.FinishButton, "Run")

    def value(self) -> tuple[WellPlate | None, Center | RandomPoints | GridRowsColumns]:
        """Return the list of FOVs."""
        return self._fov_widget.value()

    def setValue(
        self, plate: WellPlate, mode: Center | RandomPoints | GridRowsColumns
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

        self._on_plate_combo_changed(
            self.plate_page._plate_widget.plate_combo.currentText()
        )

        # important to set the correct size of the FOVs
        self.fov_page._fov_widget._on_px_size_changed()

        # this is just for testing, remove later ______________
        self.pt = PT(self)
        # ______________________________________________________

    def accept(self) -> None:
        """Override QWizard default accept method."""
        self.valueChanged.emit(self.value())

        # this is just for testing, remove later ______________
        print(self.value())
        pos = self.value().positions
        if pos is not None:
            self.pt.set_state(pos)
        self.pt.show()
        # ______________________________________________________

    def _on_plate_combo_changed(self, plate_id: str) -> None:
        plate = self._plate_db[plate_id]
        self.calibration_page._calibration.setValue(CalibrationInfo(plate, None))
        self.fov_page._fov_widget.setValue(plate, Center(x=0, y=0))

    def value(self) -> HCSInfo:
        plate, well_list = self.plate_page.value()
        wells = [well.name for well in well_list] if well_list else None
        _, calibration_data = self.calibration_page.value()
        # TODO: add warning if not calibtared
        _, fov_mode = self.fov_page.value()
        positions = self._get_positions()
        return HCSInfo(plate, wells, calibration_data, fov_mode, positions)

    def _get_positions(self) -> list[Position] | None:
        wells_centers = self._get_well_center_in_stage_coordinates()
        if wells_centers is None:
            return None
        return self._get_fovs_in_stage_coords(wells_centers)

    def _get_well_center_in_stage_coordinates(
        self,
    ) -> list[tuple[WellInfo, float, float]] | None:
        plate, _ = self.plate_page.value()
        _, calibration = self.calibration_page.value()

        _, wells = self.plate_page.value()

        if wells is None or calibration is None:
            return None

        a1_x, a1_y = (calibration.well_A1_center_x, calibration.well_A1_center_y)
        wells_center_stage_coords = []
        for well in wells:
            x, y = get_well_center(plate, well, a1_x, a1_y)
            if calibration.rotation_matrix is not None:
                x, y = apply_rotation_matrix(
                    calibration.rotation_matrix,
                    calibration.well_A1_center_x,
                    calibration.well_A1_center_y,
                    x,
                    y,
                )
            wells_center_stage_coords.append((well, x, y))

        return wells_center_stage_coords

    def _get_fovs_in_stage_coords(
        self, wells_centers: list[tuple[WellInfo, float, float]]
    ) -> list[Position]:
        """Get the calibrated stage coords of each FOV of the selected wells."""
        _, mode = self.fov_page.value()

        positions: list[Position] = []

        for well, well_center_x, well_center_y in wells_centers:
            if isinstance(mode, Center):
                positions.append(
                    Position(x=well_center_x, y=well_center_y, name=f"{well.name}")
                )
            else:
                for idx, fov in enumerate(mode):
                    x = (fov.x * 1000) + well_center_x
                    y = (fov.y * 1000) + well_center_y
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


class PT(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._pt = PositionTable()

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(self._pt)

    def set_state(self, positions: list[Position]) -> None:
        self._pt.set_state(positions)


# _______________________________________________________________
