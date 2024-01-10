from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget

from pymmcore_widgets import MDAWidget
from pymmcore_widgets.hcs import HCSWizard
from pymmcore_widgets.hcs._main_wizard_widget import HCSInfo


class MDA(QWidget):
    """..."""

    def __init__(self) -> None:
        super().__init__()
        # get the CMMCore instance and load the default config
        self.mmc = CMMCorePlus.instance()
        self.mmc.loadSystemConfiguration()

        # instantiate the MDAWidget and the HCS wizard
        self.mda = MDAWidget()
        self.hcs = HCSWizard(parent=self)

        # create a button to show the HCS wizard
        hcs_button = QPushButton("HCS Wizard")
        hcs_button.setToolTip("Open the HCS wizard.")
        # connect the button to the show_hcs method
        hcs_button.clicked.connect(self._show_hcs)

        # add the button to the MDAWidget layout in the Positions tab
        pos_table_layout = self.mda.stage_positions.layout().itemAt(2)
        pos_table_layout.insertWidget(2, hcs_button)

        # add the MDAWidget to the main layout
        main_layout = QHBoxLayout(self)
        main_layout.addWidget(self.mda)

        # connect the HCS wizard valueChanged signal to populate
        # the MDAWidget's Positions table
        self.hcs.valueChanged.connect(self._add_to_positios_table)

    def _show_hcs(self) -> None:
        # if hcs is open, raise it, otherwise show it
        if self.hcs.isVisible():
            self.hcs.raise_()
        else:
            self.hcs.show()

    def _add_to_positios_table(self, value: HCSInfo) -> None:
        """Add a list of positions to the Positions table."""
        self.mda.stage_positions.setValue(value.positions)


if __name__ == "__main__":
    app = QApplication([])
    mda = MDA()
    mda.show()
    app.exec_()
