from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication

from pymmcore_widgets import GroupPresetDialog

core = CMMCorePlus().instance()
core.loadSystemConfiguration()
app = QApplication([])
ocd = GroupPresetDialog()
ocd._load_group("Channel")
ocd.show()

app.exec()
