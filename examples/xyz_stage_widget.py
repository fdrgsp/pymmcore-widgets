"""Example usage of the XYZStageWidget class.

Unlike StageWidget, this widget does not target a specific device: it always
follows whichever devices are currently set as the Core's default XY stage
and focus device, showing a placeholder for either axis if no default device
is set.
"""

from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication

from pymmcore_widgets import XYZStageWidget

app = QApplication([])

mmc = CMMCorePlus().instance()
mmc.loadSystemConfiguration()

wdg = XYZStageWidget()
wdg.show()

app.exec()
