from pathlib import Path

from qtpy.QtWidgets import QApplication
from rich import print

from pymmcore_widgets.hcs_widget._plate_widget import _PlateWidget

database_path = (
    Path(__file__).parent.parent.parent / "tests" / "plate_database_for_tests.json"
)

app = QApplication([])

plate_selector = _PlateWidget(plate_database_path=database_path)
plate_selector.valueChanged.connect(lambda: print(plate_selector.value()))

plate_selector.show()

app.exec_()
