from pathlib import Path

from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication, QPushButton
from rich import print

from pymmcore_widgets._hcs_widget._fov_widget import Center, _FOVSelectrorWidget
from pymmcore_widgets._hcs_widget._well_plate_model import load_database

database_path = (
    Path(__file__).parent.parent.parent / "tests" / "plate_database_for_tests.json"
)
database = load_database(database_path)


app = QApplication([])

mmc = CMMCorePlus.instance()
mmc.loadSystemConfiguration()

fov_selector_wdg = _FOVSelectrorWidget(mmcore=mmc)

fov_selector_wdg.setValue(database["coverslip 22mm"], Center())

btn = QPushButton("Value")
btn.clicked.connect(lambda: print(fov_selector_wdg.value()))
fov_selector_wdg.layout().addWidget(btn, 7, 0, 1, 2)

fov_selector_wdg.show()

app.exec_()
