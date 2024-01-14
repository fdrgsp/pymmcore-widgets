from pathlib import Path

from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication, QPushButton

from pymmcore_widgets.hcs._calibration_widget import (
    CalibrationInfo,
    PlateCalibrationWidget,
)
from pymmcore_widgets.hcs._well_plate_model import load_database

database_path = (
    Path(__file__).parent.parent.parent / "tests" / "plate_database_for_tests.json"
)
database = load_database(database_path)


app = QApplication([])

mmc = CMMCorePlus.instance()
mmc.loadSystemConfiguration()

calibration_wdg = PlateCalibrationWidget(mmcore=mmc)

calibration_wdg.setValue(CalibrationInfo(database["standard 96 wp"], None))

btn = QPushButton("Value")
btn.clicked.connect(lambda: print(calibration_wdg.value()))
calibration_wdg.layout().addWidget(btn)

calibration_wdg.show()

app.exec_()
