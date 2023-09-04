from pathlib import Path

from qtpy.QtWidgets import QApplication, QPushButton
from rich import print

from pymmcore_widgets._hcs_widget._plate_widget import _PlateWidget

database_path = (
    Path(__file__).parent.parent.parent / "tests" / "plate_database_for_tests.json"
)

app = QApplication([])

plate_selector = _PlateWidget(plate_database_path=database_path)

btn = QPushButton("Value")
btn.clicked.connect(lambda: print(plate_selector.value()))
plate_selector.layout().addWidget(btn)

plate_selector.show()

app.exec_()
