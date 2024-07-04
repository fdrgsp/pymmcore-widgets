from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication
from useq import WellPlate

from pymmcore_widgets.hcs._calibration_widget._calibration_widget import (
    CalibrationData,
    PlateCalibrationWidget,
)

try:
    from rich import print as rich_print
except ImportError:
    rich_print = print


plate = WellPlate(
    rows=1, columns=1, well_spacing=(0, 0), well_size=(22, 22), circular_wells=False
)

app = QApplication([])

mmc = CMMCorePlus.instance()
mmc.loadSystemConfiguration()

cb = PlateCalibrationWidget(mmcore=mmc)

cb.setValue(
    CalibrationData(
        plate=plate,
        calibration_positions_a1=[(-100, 100), (100, -100)],
    )
)
cb.show()

cb.valueChanged.connect(lambda x: rich_print(x))

app.exec()
