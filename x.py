from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication

from pymmcore_widgets import GroupPresetDialog

app = QApplication([])

mmc = CMMCorePlus.instance()
mmc.loadSystemConfiguration("/Users/fdrgsp/Desktop/CFG.cfg")

# oc = GroupPresetTableWidget(mmcore=mmc)
oc = GroupPresetDialog(mmcore=mmc)
oc.show()

# l = _LightPathGroupBox(mmcore=mmc)
# l.show()

# app.exec()
