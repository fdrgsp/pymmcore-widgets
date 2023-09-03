from pathlib import Path
from typing import NamedTuple

import matplotlib.pyplot as plt
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import Qt
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
from useq import (  # type: ignore
    GridRelative,
    MDASequence,
    Position,
    RandomPoints,
)

from pymmcore_widgets._mda import PositionTable

from ._calibration_widget import CalibrationData, CalibrationInfo, _CalibrationWidget
from ._fov_widget import Center, _FOVSelectrorWidget
from ._graphics_items import WellInfo
from ._plate_widget import WellPlateInfo, _PlateWidget
from ._util import apply_rotation_matrix, get_well_center
from ._well_plate_model import PLATE_DB_PATH, WellPlate, load_database

EXPANDING = (QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


class HCSInfo(NamedTuple):
    plate: WellPlate
    wells: list[str] | None
    calibration: CalibrationData | None
    fov_mode: Center | RandomPoints | GridRelative


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

        self._fov_widget = _FOVSelectrorWidget()

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addItem(QSpacerItem(0, 0, *EXPANDING))
        self.layout().addWidget(self._fov_widget)
        self.layout().addItem(QSpacerItem(0, 0, *EXPANDING))

        # rename finish button
        # self.setButtonText(QWizard.WizardButton.FinishButton, "Run")

    def value(self) -> tuple[WellPlate | None, Center | RandomPoints | GridRelative]:
        """Return the list of FOVs."""
        return self._fov_widget.value()

    def setValue(
        self, plate: WellPlate, mode: Center | RandomPoints | GridRelative
    ) -> None:
        """Set the list of FOVs."""
        self._fov_widget.setValue(plate, mode)


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

        _finish = self.button(QWizard.WizardButton.FinishButton)
        # _finish.disconnect(self)  # disconnect default behavior
        _finish.disconnect()  # disconnect default behavior
        _finish.clicked.connect(self._on_finish_clicked)

        self._on_plate_combo_changed(
            self.plate_page._plate_widget.plate_combo.currentText()
        )

        # this is just for testing, remove later ______________
        self.pt = PT(self)
        # ______________________________________________________

    def _on_plate_combo_changed(self, plate_id: str) -> None:
        plate = self._plate_db[plate_id]
        self.calibration_page._calibration.setValue(CalibrationInfo(plate, None))
        self.fov_page._fov_widget.setValue(plate, Center())

    def value(self) -> HCSInfo:
        plate, well_list = self.plate_page.value()
        wells = [well.name for well in well_list] if well_list else None
        _, calibration_data = self.calibration_page.value()
        # TODO: add warning if not calibtared
        _, fov_mode = self.fov_page.value()
        return HCSInfo(plate, wells, calibration_data, fov_mode)

    def _on_finish_clicked(self) -> None:
        print(self.value())

        well_centers = self._get_well_center_in_stage_coordinates()
        if well_centers is None:
            return
        positions = self._get_fovs_in_stage_coords(well_centers)

        print("__________________________")
        print(positions)
        print("__________________________")

        # this is just for testing, remove later ______________
        self.pt.set_state(positions)
        self.pt.show()
        # ______________________________________________________

    def _get_well_center_in_stage_coordinates(
        self,
    ) -> list[tuple[WellInfo, float, float]] | None:
        plate, _, calibration, _ = self.value()
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
        self, wells_center: list[tuple[WellInfo, float, float]], _show: bool = True
    ) -> list[Position]:
        """Get the calibrated stage coords of each FOV of the selected wells."""
        _, _, _, mode = self.value()

        positions: list[Position] = []

        for well, well_center_x, well_center_y in wells_center:
            if isinstance(mode, Center):
                positions.append(
                    Position(x=well_center_x, y=well_center_y, name=f"{well.name}")
                )
                plt.plot(well_center_x, well_center_y, "mo")

            elif isinstance(mode, GridRelative):
                positions.append(
                    Position(
                        x=well_center_x,
                        y=well_center_y,
                        name=f"{well.name}",
                        sequence=MDASequence(grid_plan=mode),
                    )
                )
                for idx, fov in enumerate(mode):
                    cl = "yo" if idx == 0 else "go"
                    plt.plot((fov.x * 1000) + well_center_x, (fov.y * 1000) + well_center_y, cl)  # type: ignore  # noqa: E501
                plt.plot(well_center_x, well_center_y, "mo")

            elif isinstance(mode, RandomPoints):
                for idx, fov in enumerate(mode):
                    x, y = (fov.x * 1000) + well_center_x, (fov.y * 1000) + well_center_y  # type: ignore  # noqa: E501
                    positions.append(Position(x=x, y=y, name=f"{well.name}_{idx:04d}"))
                    plt.plot(x, y, "go")
                plt.plot(well_center_x, well_center_y, "mo")

        plt.axis("equal")
        if _show:
            plt.show()

        return positions


# this is just for testing, remove later _______________________
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
