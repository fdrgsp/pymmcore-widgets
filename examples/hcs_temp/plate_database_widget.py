from pathlib import Path

from qtpy.QtWidgets import QApplication

from pymmcore_widgets.hcs._plate_database_widget import PlateDatabaseWidget

database_path = (
    Path(__file__).parent.parent.parent / "tests" / "plate_database_for_tests.json"
)

app = QApplication([])

db = PlateDatabaseWidget(plate_database_path=database_path)
db.show()

app.exec_()
