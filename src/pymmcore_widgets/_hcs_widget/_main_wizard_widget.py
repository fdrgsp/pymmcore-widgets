from pathlib import Path

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

from ._calibration_widget import _CalibrationWidget
from ._fov_widget import _FOVSelectrorWidget
from ._plate_widget import _PlateWidget
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

        self.page1 = PlatePage(
            plate_database_path=self._plate_db_path, plate_database=self._plate_db
        )
        self.page1._plate_widget.plate_combo.currentTextChanged.connect(
            self._on_plate_combo_changed
        )

        self.page2 = PlateCalibrationPage()

        self.page3 = FOVSelectorPage()

        self.addPage(self.page1)
        self.addPage(self.page2)
        self.addPage(self.page3)

        _run = self.button(QWizard.WizardButton.FinishButton)  # name set in self.page3
        _run.disconnect()  # disconnect default behavior
        _run.clicked.connect(self._on_finish_clicked)

        self._on_plate_combo_changed(self.page1._plate_widget.plate_combo.currentText())

        self.page3._fov_widget.center_wdg._radio_btn.setChecked(True)

    def _on_plate_combo_changed(self, plate_id: str) -> None:
        plate = self._plate_db[plate_id]
        self.page2._calibration._update(plate)
        self.page3._fov_widget._update(plate)

    def _on_finish_clicked(self) -> None:
        print("__________________________")
        print(self.page1._plate_widget.value())
        print(self.page3._fov_widget.value())
        print("__________________________")
