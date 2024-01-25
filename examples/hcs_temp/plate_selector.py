from pathlib import Path

from qtpy.QtWidgets import QApplication

from pymmcore_widgets.hcs._plate_widget import PlateSelectorWidget

database_path = (
    Path(__file__).parent.parent.parent / "tests" / "plate_database_for_tests.json"
)

app = QApplication([])

ps = PlateSelectorWidget(plate_database_path=database_path)
ps.valueChanged.connect(lambda: print(ps.value()))

ps.show()

app.exec_()
