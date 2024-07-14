from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication

from pymmcore_widgets.groups_presets_widget._group_preset_table_widget import (
    GroupPresetTableWidget,
)

app = QApplication([])

mmc = CMMCorePlus.instance()
oc = GroupPresetTableWidget(mmcore=mmc)
oc.show()

mmc.loadSystemConfiguration("/Users/fdrgsp/Desktop/CFG.cfg")
app.exec()
