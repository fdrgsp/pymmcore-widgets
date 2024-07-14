from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication

from pymmcore_widgets import GroupPresetDialog

app = QApplication([])

mmc = CMMCorePlus.instance()
oc = GroupPresetDialog(mmcore=mmc)
oc.show()

mmc.loadSystemConfiguration("/Users/fdrgsp/Desktop/CFG.cfg")
app.exec()
