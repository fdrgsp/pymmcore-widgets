from pathlib import Path
from typing import NamedTuple

import matplotlib.pyplot as plt
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
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
    RandomArea,
    RandomPoints,
)

from ._calibration_widget import CalibrationInfo, _CalibrationWidget
from ._fov_widget import Center, _FOVSelectrorWidget
from ._graphics_items import WellInfo
from ._plate_widget import WellPlateInfo, _PlateWidget
from ._util import apply_rotation_matrix, get_well_center
from ._well_plate_model import PLATE_DB_PATH, WellPlate, load_database


class HCSInfo(NamedTuple):
    plate: WellPlate
    wells: list[WellInfo] | None
    calibration: CalibrationInfo | None
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

    def value(self) -> CalibrationInfo | None:
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

        spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addItem(spacer)
        self.layout().addWidget(self._fov_widget)
        self.layout().addItem(spacer)

        # self.setButtonText(QWizard.WizardButton.FinishButton, "Run")

    def value(self) -> Center | RandomPoints | GridRelative:
        # def value(self) -> tuple[Center | RandomPoints | GridRelative, list[FOV]]:
        """Return the list of FOVs."""
        return self._fov_widget.value()

    def setValue(self, mode: Center | RandomPoints | GridRelative) -> None:
        """Set the list of FOVs."""
        self._fov_widget.setValue(mode)


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

        _finish = self.button(
            QWizard.WizardButton.FinishButton
        )  # name set in self.page3
        # _finish.disconnect(self)  # disconnect default behavior
        _finish.disconnect()  # disconnect default behavior
        _finish.clicked.connect(self._on_finish_clicked)

        self._on_plate_combo_changed(
            self.plate_page._plate_widget.plate_combo.currentText()
        )

    def _on_plate_combo_changed(self, plate_id: str) -> None:
        plate = self._plate_db[plate_id]
        self.calibration_page._calibration._update(plate)
        self.fov_page._fov_widget._update(plate)

    def value(self) -> HCSInfo:
        plate, wells = self.plate_page.value()
        calibration = self.calibration_page.value()
        fov_mode = self.fov_page.value()
        return HCSInfo(plate, wells, calibration, fov_mode)

    def _on_finish_clicked(self) -> None:
        print(self.value())

        well_centers = self._get_well_center_in_stage_coordinates()
        if well_centers is None:
            return
        positions = self._get_fovs_in_stage_coords(well_centers)

        print("__________________________")
        print(positions)
        print("__________________________")

    def _get_well_center_in_stage_coordinates(
        self,
    ) -> list[tuple[WellInfo, float, float]] | None:
        plate, wells, calibration, _ = self.value()

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
        self, wells_center: list[tuple[WellInfo, float, float]]
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
                plt.plot(well_center_x, well_center_y, "mo")
                for fov in mode:
                    plt.plot(fov.x + well_center_x, fov.y + well_center_y, "go")  # type: ignore  # noqa: E501

            elif isinstance(mode, RandomPoints):
                # shift the area to the well center in stage coords
                mode = mode.replace(
                    area=RandomArea(
                        x=well_center_x - (mode.area.width / 2),
                        y=well_center_y - (mode.area.height / 2),
                        width=mode.area.width,
                        height=mode.area.height,
                    )
                )
                plt.plot(well_center_x, well_center_y, "mo")
                for idx, (x, y) in enumerate(mode):
                    positions.append(Position(x=x, y=y, name=f"{well.name}_{idx:04d}"))
                    plt.plot(x, y, "go")

        plt.axis("equal")
        plt.gca().invert_yaxis()
        plt.show()

        return positions
