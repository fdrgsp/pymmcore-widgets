from pathlib import Path

from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication
from rich import print

from pymmcore_widgets._hcs_widget._fov_widget import Center, FOVSelectrorWidget
from pymmcore_widgets._hcs_widget._well_plate_model import load_database

database_path = (
    Path(__file__).parent.parent.parent / "tests" / "plate_database_for_tests.json"
)
database = load_database(database_path)


app = QApplication([])

mmc = CMMCorePlus.instance()
mmc.loadSystemConfiguration()

fov_selector_wdg = FOVSelectrorWidget(mmcore=mmc)
fov_selector_wdg.valueChanged.connect(lambda x: print(x))

fov_selector_wdg.setValue(database["standard 96 wp"], Center())

fov_selector_wdg.show()

app.exec_()