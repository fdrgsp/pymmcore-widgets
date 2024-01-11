from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication
from rich import print

from pymmcore_widgets.hcs import HCSWizard

app = QApplication([])
mmc = CMMCorePlus.instance()
mmc.loadSystemConfiguration()
w = HCSWizard()
w.valueChanged.connect(lambda: print(w.value()))
w.show()
app.exec_()
