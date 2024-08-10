from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication

from pymmcore_widgets import StageWidget
from pymmcore_widgets.hcs._hcs_calibration_widget import HCSCalibrationWidget

app = QApplication([])
mmc = CMMCorePlus.instance()
mmc.loadSystemConfiguration()
wdg = HCSCalibrationWidget(mmcore=mmc)
wdg.setPlate("96-well")
wdg.show()

s = StageWidget("XY")
s.show()

# app.exec()
