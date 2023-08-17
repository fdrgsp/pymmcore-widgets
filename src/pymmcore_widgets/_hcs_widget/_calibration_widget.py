from __future__ import annotations

import string
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from fonticon_mdi6 import MDI6
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QSize, Qt
from qtpy.QtGui import QAction, QIcon, QPixmap
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QTableWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from superqt.fonticon import icon

if TYPE_CHECKING:
    from ._well_plate_model import WellPlate

AlignCenter = Qt.AlignmentFlag.AlignCenter
FixedSizePolicy = (QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


ALPHABET = string.ascii_uppercase
ROLE = Qt.ItemDataRole.UserRole + 1
ICON_PATH = Path(__file__).parent / "icons"
ICON_SIZE = 22
CIRCLE_ICON = QIcon(str(ICON_PATH / "circle-outline.svg"))
SQUARE_ICON_SIDES = QIcon(str(ICON_PATH / "square-outline_s.svg"))
SQUARE_ICON_VERTICES = QIcon(str(ICON_PATH / "square-outline_v.svg"))
CIRCLE_ITEM = "3 points: add 3 points on the circonference of the well"
SQUARE_ITEM_SIDES = "4 points: add 4 points, 1 per side of the rectangular/square well"
SQUARE_ITEM_VERTICES = (
    "2 points: add 2 points at 2 opposite vertices of the rectangular/square well"
)

LABEL_STYLE = """
    background: rgb(0, 255, 0);
    font-size: 16pt; font-weight:bold;
    color : black;
    border: 1px solid black;
    border-radius: 5px;
"""


class _CalibrationMode(QGroupBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._mode_combo = QComboBox()
        # self._mode_combo.setIconSize(QSize(30, 30))
        # self._mode_combo.setStyleSheet("QComboBox {font-size: 40px;}")
        # align text to center
        # self._mode_combo.setEditable(True)
        # self._mode_combo.lineEdit().setReadOnly(True)
        # self._mode_combo.lineEdit().setAlignment(Qt.AlignmentFlag.AlignVCenter)

        lbl = QLabel(text="Calibration Mode:")
        lbl.setSizePolicy(*FixedSizePolicy)

        self.setLayout(QHBoxLayout())
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().setSpacing(10)
        self.layout().addWidget(lbl)
        self.layout().addWidget(self._mode_combo)

        self.setValue(False)

    def setValue(self, circle: bool) -> None:
        self._mode_combo.clear()

        if circle:
            items = [(CIRCLE_ITEM, CIRCLE_ICON, 3)]
        else:
            items = [
                (SQUARE_ITEM_VERTICES, SQUARE_ICON_VERTICES, 2),
                (SQUARE_ITEM_SIDES, SQUARE_ICON_SIDES, 4),
            ]
        for idx, (_text, _icon, _pos) in enumerate(items):
            self._mode_combo.addItem(_icon, _text)
            self._mode_combo.setItemData(idx, _pos, ROLE)

    def value(self) -> int:
        """Return the number of points necessary for the calibration."""
        return int(self._mode_combo.itemData(self._mode_combo.currentIndex(), ROLE))


class _CalibrationTable(QWidget):
    """Table for the calibration widget."""

    def __init__(self, *, mmcore: CMMCorePlus | None = None) -> None:
        super().__init__()

        self._mmc = mmcore or CMMCorePlus.instance()

        self._toolbar = QToolBar()
        self._toolbar.setFloatable(False)
        self._toolbar.setIconSize(QSize(ICON_SIZE, ICON_SIZE))

        spacer_1 = QWidget()
        spacer_1.setFixedWidth(5)
        spacer_1.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._well_label = QLabel()
        self._well_label.setFixedHeight(ICON_SIZE)
        self._well_label.setStyleSheet(LABEL_STYLE)
        self._well_label.setAlignment(AlignCenter)

        spacer_2 = QWidget()
        spacer_2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.act_add_row = QAction(
            icon(MDI6.plus_thick, color=(0, 255, 0)), "Add new row", self
        )
        self.act_add_row.triggered.connect(self._add_position)
        self.act_remove_row = QAction(
            icon(MDI6.close_box_outline, color="magenta"), "Remove selected row", self
        )
        self.act_remove_row.triggered.connect(self._remove_position)
        self.act_clear = QAction(
            icon(MDI6.close_box_multiple_outline, color="magenta"), "Clear", self
        )
        self.act_clear.triggered.connect(self._clear_table)

        self._toolbar.addWidget(spacer_1)
        self._toolbar.addWidget(self._well_label)
        self._toolbar.addWidget(spacer_2)
        # self._toolbar.addSeparator()
        self._toolbar.addAction(self.act_add_row)
        # self._toolbar.addSeparator()
        self._toolbar.addAction(self.act_remove_row)
        # self._toolbar.addSeparator()
        self._toolbar.addAction(self.act_clear)

        # table
        self.tb = QTableWidget()
        hdr = self.tb.horizontalHeader()
        hdr.setSectionResizeMode(hdr.ResizeMode.Stretch)
        self.tb.verticalHeader().setVisible(False)
        self.tb.setTabKeyNavigation(True)
        self.tb.setColumnCount(2)
        self.tb.setRowCount(0)
        self.tb.setHorizontalHeaderLabels(["X", "Y"])

        # main
        self.setLayout(QVBoxLayout())
        self.setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)
        self.layout().addWidget(self._toolbar)
        self.layout().addWidget(self.tb)

    def _add_position(self) -> None:
        if not self._mmc.getXYStageDevice():
            warnings.warn("XY Stage not selected!", stacklevel=2)
            return

        if len(self._mmc.getLoadedDevices()) <= 1:
            return

        row = self._add_row()
        self._add_table_value(self._mmc.getXPosition(), row, 0)
        self._add_table_value(self._mmc.getYPosition(), row, 1)

    def _add_row(self) -> int:
        idx = self.tb.rowCount()
        self.tb.insertRow(idx)
        return int(idx)

    def _add_table_value(self, value: float, row: int, col: int) -> None:
        spin = QDoubleSpinBox()
        spin.setAlignment(AlignCenter)
        spin.setMaximum(1000000.0)
        spin.setMinimum(-1000000.0)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setValue(value)
        # block mouse scroll
        spin.wheelEvent = lambda event: None
        self.tb.setCellWidget(row, col, spin)

    def _remove_position(self) -> None:
        rows = {r.row() for r in self.tb.selectedIndexes()}
        for idx in sorted(rows, reverse=True):
            self.tb.removeRow(idx)

    def _clear_table(self) -> None:
        self.tb.clearContents()
        self.tb.setRowCount(0)

    def _write_to_label(self, colunm: int) -> None:
        self._well_label.setText(f" Well A{colunm + 1} ")


class _CalibrationLabel(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # icon
        self._icon_lbl = QLabel()
        self._icon_lbl.setSizePolicy(*FixedSizePolicy)
        self._icon_lbl.setPixmap(
            icon(MDI6.close_octagon_outline, color="magenta").pixmap(QSize(30, 30))
        )
        # text
        self._text_lbl = QLabel(text="Plate Not Calibrated!")
        self._text_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")

        # main
        self.setLayout(QHBoxLayout())
        self.layout().setAlignment(AlignCenter)
        self.layout().setSpacing(0)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(self._icon_lbl)
        self.layout().addWidget(self._text_lbl)

    def setValue(self, pixmap: QPixmap, text: str) -> None:
        """Set the icon and text of the labels."""
        self._icon_lbl.setPixmap(pixmap)
        self._text_lbl.setText(text)


class _TestCalibrationWidget(QWidget):
    def __init__(
        self, parent: QWidget | None = None, *, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent)

        self._mmc = mmcore or CMMCorePlus.instance()

        # test calibration groupbox
        lbl = QLabel("Move to the edge of well:")
        lbl.setSizePolicy(*FixedSizePolicy)
        # combo to select plate
        self._well_letter_combo = QComboBox()
        self._well_letter_combo.setEditable(True)
        self._well_letter_combo.lineEdit().setReadOnly(True)
        self._well_letter_combo.lineEdit().setAlignment(AlignCenter)
        # combo to select well number
        self._well_number_combo = QComboBox()
        self._well_number_combo.setEditable(True)
        self._well_number_combo.lineEdit().setReadOnly(True)
        self._well_number_combo.lineEdit().setAlignment(AlignCenter)
        # test button
        self._test_button = QPushButton("Go")
        self._test_button.setEnabled(False)
        # groupbox
        test_calibration = QGroupBox(title="Test Calibration")
        test_calibration.setLayout(QHBoxLayout())
        test_calibration.layout().setSpacing(10)
        test_calibration.layout().setContentsMargins(10, 10, 10, 10)
        test_calibration.layout().addWidget(lbl)
        test_calibration.layout().addWidget(self._well_letter_combo)
        test_calibration.layout().addWidget(self._well_number_combo)
        test_calibration.layout().addWidget(self._test_button)

        # main
        self.setLayout(QHBoxLayout())
        self.layout().setSpacing(10)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(test_calibration)

    def _update(self, plate: WellPlate) -> None:
        self._well_letter_combo.clear()
        letters = [ALPHABET[letter] for letter in range(plate.rows)]
        self._well_letter_combo.addItems(letters)

        self._well_number_combo.clear()
        numbers = [str(c) for c in range(1, plate.columns + 1)]
        self._well_number_combo.addItems(numbers)


class _CalibrationWidget(QWidget):
    """Widget to calibrate the sample plate."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        mmcore: CMMCorePlus | None = None,
    ) -> None:
        super().__init__(parent)

        self._mmc = mmcore or CMMCorePlus.instance()
        self._plate: WellPlate | None = None

        # calibration mode
        self._mode = _CalibrationMode()

        # calibration tables
        self._table_a1 = _CalibrationTable()
        self._table_a1._write_to_label(colunm=0)
        self._table_an = _CalibrationTable()
        _table_group = QGroupBox()
        _table_group.setLayout(QHBoxLayout())
        _table_group.layout().setContentsMargins(0, 0, 0, 0)
        _table_group.layout().setSpacing(10)
        _table_group.layout().addWidget(self._table_a1)
        _table_group.layout().addWidget(self._table_an)
        # calibration buttons
        self._calibrate_button = QPushButton(text="Calibrate Plate")
        self._calibrate_button.setIcon(icon(MDI6.target_variant, color="darkgrey"))
        self._calibrate_button.setIconSize(QSize(30, 30))
        spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        _calibrate_btn_wdg = QWidget()
        _calibrate_btn_wdg.setLayout(QHBoxLayout())
        _calibrate_btn_wdg.layout().setSpacing(10)
        _calibrate_btn_wdg.layout().setContentsMargins(0, 0, 0, 0)
        _calibrate_btn_wdg.layout().addItem(spacer)
        _calibrate_btn_wdg.layout().addWidget(self._calibrate_button)
        # calibration tabls and calibration button group
        _table_and_btn_wdg = QGroupBox()
        _table_and_btn_wdg.setLayout(QVBoxLayout())
        _table_and_btn_wdg.layout().setSpacing(10)
        _table_and_btn_wdg.layout().setContentsMargins(10, 10, 10, 10)
        _table_and_btn_wdg.layout().addWidget(_table_group)
        _table_and_btn_wdg.layout().addWidget(_calibrate_btn_wdg)

        # test calibration
        self._test_calibration = _TestCalibrationWidget()
        # calibration label
        self._calibration_label = _CalibrationLabel()
        # test calibration and calibration label group
        _bottom_group = QWidget()
        _bottom_group.setLayout(QHBoxLayout())
        _bottom_group.layout().setSpacing(10)
        _bottom_group.layout().setContentsMargins(10, 10, 10, 10)
        _bottom_group.layout().addWidget(self._test_calibration)
        _bottom_group.layout().addWidget(self._calibration_label)

        # main
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(10)
        self.layout().addWidget(self._mode)
        self.layout().addWidget(_table_and_btn_wdg)
        self.layout().addWidget(_bottom_group)

        # connect
        self._calibrate_button.clicked.connect(self._on_calibrate_button_clicked)

    def _clear(self) -> None:
        """Clear the calibration tables."""
        self._table_a1._clear_table()
        self._table_an._clear_table()

    def _update(self, plate: WellPlate) -> None:
        self._plate = plate
        # update calibration mode
        self._mode.setValue(plate.circular)
        # update tables
        self._clear()
        if plate.columns > 1:
            self._table_an._write_to_label(colunm=plate.columns - 1)
        self._table_an.show() if plate.columns > 1 else self._table_an.hide()
        # update calibration label
        self._set_calibration_label(False)
        # update test calibration
        self._test_calibration._update(plate)

    def _on_calibrate_button_clicked(self) -> None:
        """Calibrate the plate."""
        # get calibration points
        # a1 = self._table_a1._get_calibration_point()
        # an = self._table_an._get_calibration_point()

        # update calibration label
        self._set_calibration_label(True)

    def _set_calibration_label(self, state: bool) -> None:
        """Set the calibration label."""
        lbl_icon = MDI6.check_bold if state else MDI6.close_octagon_outline
        lbl_icon_size = QSize(20, 20) if state else QSize(30, 30)
        lbl_icon_color = (0, 255, 0) if state else "magenta"
        text = "Plate Calibrated!" if state else "Plate Not Calibrated!"
        self._calibration_label.setValue(
            pixmap=icon(lbl_icon, color=lbl_icon_color).pixmap(lbl_icon_size),
            text=text,
        )
