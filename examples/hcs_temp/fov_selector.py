from pathlib import Path

from qtpy.QtWidgets import QApplication

from pymmcore_widgets.hcs._fov_widget import Center, FOVSelectorWidget
from pymmcore_widgets.hcs._well_plate_model import load_database

database_path = (
    Path(__file__).parent.parent.parent / "tests" / "plate_database_for_tests.json"
)
database = load_database(database_path)


app = QApplication([])

fs = FOVSelectorWidget(plate=database["standard 96 wp"], mode=Center(0, 0, 512, 512))

fs.valueChanged.connect(lambda x: print(x))

fs.show()

app.exec_()
