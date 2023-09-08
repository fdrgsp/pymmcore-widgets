from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import (
    QApplication,
)

from pymmcore_widgets._hcs_widget._main_wizard_widget import HCSWizard

app = QApplication([])
mmc = CMMCorePlus.instance()
mmc.loadSystemConfiguration()
w = HCSWizard()
w.show()
app.exec_()