from __future__ import annotations

import math
import string
import warnings
from typing import Sequence

import numpy as np
from fonticon_mdi6 import MDI6
from pymmcore_plus import CMMCorePlus
from pymmcore_plus._logger import logger
from qtpy.QtCore import QSize, Qt, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from superqt.fonticon import icon
from superqt.utils import signals_blocked

from .._util import PLATE_FROM_CALIBRATION
from ._well_plate_model import PLATE_DB, WellPlate

AlignCenter = Qt.AlignmentFlag.AlignCenter

ALPHABET = string.ascii_uppercase


class _PlateCalibration(QWidget):
    """Widget to calibrate the sample plate."""

    valueChanged = Signal(tuple)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        mmcore: CMMCorePlus | None = None,
    ) -> None:
        super().__init__(parent)

        self._mmc = mmcore or CMMCorePlus.instance()

        self.plate: WellPlate | None = None
        self.A1_well: tuple[str, float, float] | None = None
        self.plate_rotation_matrix: np.ndarray | None = None
        self.plate_angle_deg: float = 0.0
        self.is_calibrated = False
        self.A1_stage_coords_center: tuple = ()
        self._calculated_well_size_x: float | None = None
        self._calculated_well_size_y: float | None = None
        self._calculated_well_spacing_x: float | None = None
        self._calculated_well_spacing_y: float | None = None

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        self.info_lbl = QLabel()
        self.info_lbl.setAlignment(AlignCenter)
        self.layout().addWidget(self.info_lbl)

        wdg = QWidget()
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(5)
        wdg.setLayout(mode_layout)
        lbl = QLabel(text="Wells for the calibration:")
        lbl.setSizePolicy(QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed))
        self._calibration_combo = QComboBox()
        self._calibration_combo.addItems(["1 Well (A1)"])
        self._calibration_combo.currentTextChanged.connect(self._on_combo_changed)
        mode_layout.addWidget(lbl)
        mode_layout.addWidget(self._calibration_combo)
        self.layout().addWidget(wdg)

        group = QGroupBox()
        self.group_layout = QVBoxLayout()
        self.group_layout.setSpacing(15)
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        group.setLayout(self.group_layout)
        layout.addWidget(group)
        self._create_tables(n_tables=1)

        bottom_group = QGroupBox()
        bottom_group_layout = QHBoxLayout()
        bottom_group_layout.setSpacing(10)
        bottom_group_layout.setContentsMargins(10, 10, 10, 10)
        bottom_group.setLayout(bottom_group_layout)

        cal_state_wdg = QWidget()
        cal_state_wdg_layout = QHBoxLayout()
        cal_state_wdg_layout.setAlignment(AlignCenter)
        cal_state_wdg_layout.setSpacing(0)
        cal_state_wdg_layout.setContentsMargins(0, 0, 0, 0)
        cal_state_wdg.setLayout(cal_state_wdg_layout)
        self.icon_lbl = QLabel()
        self.icon_lbl.setSizePolicy(QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed))
        self.icon_lbl.setPixmap(
            icon(MDI6.close_octagon_outline, color="magenta").pixmap(QSize(30, 30))
        )
        self.cal_lbl = QLabel()
        self.cal_lbl.setText("Plate non Calibrated!")
        cal_state_wdg_layout.addWidget(self.icon_lbl)
        cal_state_wdg_layout.addWidget(self.cal_lbl)

        calibrate_btn = QPushButton(text="Calibrate Plate")
        calibrate_btn.clicked.connect(self._calibrate_plate)

        bottom_group_layout.addWidget(calibrate_btn)
        bottom_group_layout.addWidget(cal_state_wdg)

        layout.addWidget(bottom_group)

        test_calibration = QGroupBox(title="Test Calibration")
        test_cal_layout = QHBoxLayout()
        test_cal_layout.setSpacing(10)
        test_cal_layout.setContentsMargins(10, 10, 10, 10)
        test_calibration.setLayout(test_cal_layout)
        # connections to this test_calibration widgets are in _main_hcs_widget.py
        lbl = QLabel("Move to edge of well:")
        lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._well_letter_combo = QComboBox()
        self._well_letter_combo.setEditable(True)
        self._well_letter_combo.lineEdit().setReadOnly(True)
        self._well_letter_combo.lineEdit().setAlignment(AlignCenter)

        self._well_number_combo = QComboBox()
        self._well_number_combo.setEditable(True)
        self._well_number_combo.lineEdit().setReadOnly(True)
        self._well_number_combo.lineEdit().setAlignment(AlignCenter)

        self._test_button = QPushButton("Test")
        self._test_button.setEnabled(False)

        test_cal_layout.addWidget(lbl)
        test_cal_layout.addWidget(self._well_letter_combo)
        test_cal_layout.addWidget(self._well_number_combo)
        test_cal_layout.addWidget(self._test_button)

        spacer = QSpacerItem(10, 30, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addSpacerItem(spacer)
        layout.addWidget(test_calibration)

    def _reset_calibration_variables(self, set_to_none: bool) -> None:
        if set_to_none or not self.plate:
            self._calculated_well_size_x = None
            self._calculated_well_size_y = None
            self._calculated_well_spacing_x = None
            self._calculated_well_spacing_y = None
        else:
            self._calculated_well_size_x = self.plate.well_size_x * 1000
            self._calculated_well_size_y = self.plate.well_size_y * 1000
            self._calculated_well_spacing_x = self.plate.well_spacing_x * 1000
            self._calculated_well_spacing_y = self.plate.well_spacing_y * 1000

    def _create_tables(self, n_tables: int) -> None:
        self.table_1 = CalibrationTable()
        self.table_2 = CalibrationTable()
        self.group_layout.addWidget(self.table_1)
        self.group_layout.addWidget(self.table_2)

        self._show_hide_tables(n_tables)

    def _show_hide_tables(self, n_tables: int, well_list: Sequence[str] = ()) -> None:
        self.table_1._rename_well_column("Well A1")
        self.table_1.show()
        if n_tables == 1:
            self.table_2.hide()
        else:
            if well_list:
                self.table_2._rename_well_column(f"Well {well_list[0]}")
            self.table_2.show()

    def _clear_tables(self) -> None:
        self.table_1._clear_table()
        self.table_2._clear_table()

    def _on_combo_changed(self, combo_txt: str) -> None:
        if not self.plate:
            raise RuntimeError("Plate not defined!")
        self._update_gui(self.plate.id, combo_txt=combo_txt)

    def _update_gui(self, plate_id: str, combo_txt: str = "") -> None:
        if self.plate and self.plate.id == plate_id and not combo_txt:
            return

        self._set_calibrated(False)
        self._clear_tables()

        try:
            self.plate = PLATE_DB[plate_id]
        except KeyError:
            self.plate = None
            warnings.warn(f'Plate ID "{plate_id}" not found in database.', stacklevel=2)
            return

        with signals_blocked(self._calibration_combo):
            well_list = self._get_calibration_wells(combo_txt)

        if not well_list:
            return

        n_tables = self._calibration_combo.currentText()[0]
        self._show_hide_tables(int(n_tables), well_list)

        if self._calibration_combo.currentText()[0] == "1":
            wells_to_calibrate = self._calibration_combo.currentText()[8:-1]
        else:
            wells_to_calibrate = self._calibration_combo.currentText()[9:-1]

        if self.plate.circular:
            text = (
                f"Calibrate Wells: {wells_to_calibrate}\n"
                "\n"
                "Add 3 points on the circonference of the round well "
                "and click on 'Calibrate Plate'."
            )
        else:
            text = (
                f"Calibrate Wells: {wells_to_calibrate}\n"
                "\n"
                "Add 2 points (opposite vertices) "
                "or 4 points (1 point per side) "
                "and click on 'Calibrate Plate'."
            )
        self.info_lbl.setText(text)

        self._update_well_combos(plate_id)

    def _update_well_combos(self, plate_id: str) -> None:
        """Update the well combo boxes with the correct letters/number of wells."""
        self._well_letter_combo.clear()
        letters = [ALPHABET[letter] for letter in range(PLATE_DB[plate_id].rows)]
        self._well_letter_combo.addItems(letters)

        self._well_number_combo.clear()
        numbers = [str(c) for c in range(1, PLATE_DB[plate_id].cols + 1)]
        self._well_number_combo.addItems(numbers)

    def _get_calibration_wells(self, combo_txt: str) -> list[str] | None:
        self._calibration_combo.clear()

        if not self.plate:
            return None

        rows = self.plate.rows
        cols = self.plate.cols
        well = ALPHABET[rows - 1]

        cal_well_list: list[str] = []

        if (
            self.plate.id != PLATE_FROM_CALIBRATION
            and rows == 1
            and cols == 1
            or self.plate.id == PLATE_FROM_CALIBRATION
        ):
            self._calibration_combo.addItem("1 Well (A1)")
        elif rows == 1:
            cal_well_list.append(f"A{cols}")
            self._calibration_combo.addItems(["1 Well (A1)", f"2 Wells (A1,  A{cols})"])

        elif cols == 1:
            cal_well_list.append(f"{well}{rows}")
            self._calibration_combo.addItems(
                ["1 Well (A1)", f"2 Wells (A1, {well}{rows})"]
            )
        else:
            cal_well_list.extend((f"A{cols}", f"{well}{1}", f"{well}{cols}"))
            self._calibration_combo.addItems(
                [
                    "1 Well (A1)",
                    f"2 Wells (A1,  A{cols})",
                ]
            )

        if combo_txt:
            self._calibration_combo.setCurrentText(combo_txt)

        return cal_well_list

    def _set_calibrated(self, state: bool) -> None:
        self._set_to_calibrated() if state else self._set_to_not_calibrated()

    def _set_to_calibrated(self) -> None:
        self.is_calibrated = True
        self.icon_lbl.setPixmap(
            icon(MDI6.check_bold, color=(0, 255, 0)).pixmap(QSize(20, 20))
        )
        self.cal_lbl.setText("Plate Calibrated!")
        self._test_button.setEnabled(True)

    def _set_to_not_calibrated(self) -> None:
        self.is_calibrated = False
        self.A1_well = None
        self.plate_rotation_matrix = None
        self.plate_angle_deg = 0.0
        self.A1_stage_coords_center = ()
        self._reset_calibration_variables(set_to_none=True)
        self.icon_lbl.setPixmap(
            icon(MDI6.close_octagon_outline, color="magenta").pixmap(QSize(30, 30))
        )
        self.cal_lbl.setText("Plate non Calibrated!")
        self._test_button.setEnabled(False)

    def _get_circle_center_(
        self, a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
    ) -> tuple[float, float]:
        """Find the center of a round well given 3 edge points."""
        # eq circle (x - x1)^2 + (y - y1)^2 = r^2
        # for point a: (x - ax)^2 + (y - ay)^2 = r^2
        # for point b: = (x - bx)^2 + (y - by)^2 = r^2
        # for point c: = (x - cx)^2 + (y - cy)^2 = r^2
        xc, yc = _get_circle_from_3_points(a, b, c)
        return float(xc), float(yc)

    def _get_rect_center(self, *args: tuple[float, ...]) -> tuple[float, float]:
        """
        Find the center of a rectangle/square well.

        (given two opposite verices coordinates or 4 points on the edges).
        """
        # add block if wrong coords!!!
        x_list, y_list = list(zip(*args))
        x_max, x_min = (max(x_list), min(x_list))
        y_max, y_min = (max(y_list), min(y_list))

        if x_max == x_min or y_max == y_min:
            raise ValueError("Invalid Coordinates!")

        x_val = abs(x_min) if x_min < 0 else 0
        y_val = abs(y_min) if y_min < 0 else 0

        x1, y1 = (x_min + x_val, y_max + y_val)
        x2, y2 = (x_max + x_val, y_min + y_val)

        x_max_, x_min_ = (max(x1, x2), min(x1, x2))
        y_max_, y_min_ = (max(y1, y2), min(y1, y2))

        xc = ((x_max_ - x_min_) / 2) - x_val
        yc = ((y_max_ - y_min_) / 2) - y_val

        if x_min > 0:
            xc += x_min
        if y_min > 0:
            yc += y_min

        return xc, yc

    def _calibrate_plate(self) -> None:
        self._set_calibrated(False)

        if not self._mmc.getPixelSizeUm():
            warnings.warn("Pixel Size not defined! Set pixel size first.", stacklevel=2)
            return

        if self._mmc.isSequenceRunning():
            self._mmc.stopSequenceAcquisition()

        if not self.plate:
            raise RuntimeError("Plate not defined!")

        self._reset_calibration_variables(set_to_none=False)

        if self.table_1._handle_error(circular_well=self.plate.circular):
            return
        if not self.table_2.isHidden() and self.table_2._handle_error(
            circular_well=self.plate.circular
        ):
            return

        xc_w1, yc_w1 = self._get_well_center(self.table_1.tb)
        self.A1_stage_coords_center = (xc_w1, yc_w1)
        xy_coords: list[tuple] = [(xc_w1, yc_w1)]
        if not self.table_2.isHidden():
            xc_w2, yc_w2 = self._get_well_center(self.table_2.tb)
            xy_coords.append((xc_w2, yc_w2))
            self._calculate_and_set_well_spacing(xc_w1, yc_w1, xc_w2, yc_w2)

        if len(xy_coords) > 1:
            self._calculate_plate_rotation_matrix(xy_coords)

        self._set_calibrated(True)

        if self.plate.id == PLATE_FROM_CALIBRATION:
            pos = self._get_pos_from_table(self.table_1.tb)
            self.valueChanged.emit(pos)

    def _calculate_and_set_well_spacing(
        self, xc_w1: float, yc_w1: float, xc_w2: float, yc_w2: float
    ) -> None:
        if not self.plate:
            raise RuntimeError("Plate not defined!")

        if yc_w1 == yc_w2:
            spacing_x = (xc_w2 - xc_w1) / self.plate.cols
            spacing_y = 0.0
        else:
            dist_x = xc_w2 - xc_w1
            dist_y = yc_w2 - yc_w1
            spacing_x = math.sqrt(dist_x**2 + dist_y**2)
            if self.plate.well_spacing_x == self.plate.well_spacing_y:
                spacing_y = spacing_x
            else:
                # to be changed by adding more calibration wells (for now only 2)
                spacing_y = self.plate.well_spacing_y * 1000

        self._calculated_well_spacing_x = spacing_x
        self._calculated_well_spacing_y = spacing_y

    def _calculate_plate_rotation_matrix(self, xy_coord_list: list[tuple]) -> None:
        if len(xy_coord_list) == 2:
            x_1, y_1 = xy_coord_list[0]
            x_2, y_2 = xy_coord_list[1]

            m = (y_2 - y_1) / (x_2 - x_1)  # slope from y = mx + q
            plate_angle_rad = -np.arctan(m)
            self.plate_angle_deg = np.rad2deg(plate_angle_rad)
            self.plate_rotation_matrix = np.array(
                [
                    [np.cos(plate_angle_rad), -np.sin(plate_angle_rad)],
                    [np.sin(plate_angle_rad), np.cos(plate_angle_rad)],
                ]
            )

            logger.debug(f"plate angle: {self.plate_angle_deg} deg.")
            logger.debug(f"rotation matrix: \n{self.plate_rotation_matrix}.")

    def _get_pos_from_table(
        self, table: QTableWidget
    ) -> tuple[tuple[float, float], ...]:
        pos = []
        _range = table.rowCount()
        for r in range(_range):
            x = table.cellWidget(r, 1).value()
            y = table.cellWidget(r, 2).value()
            pos.append((x, y))
        return tuple(pos)

    def _get_well_center(self, table: QTableWidget) -> tuple[float, float]:
        if self.plate is None:
            raise RuntimeError("Plate not defined!")

        pos = self._get_pos_from_table(table)

        size_x: float = self.plate.well_size_x
        size_y: float = self.plate.well_size_y

        if self.plate.circular:
            xc, yc = self._get_circle_center_(*pos)

            # calculated well_size_x (diameter)
            x0 = pos[0][0]
            x_max, x_min = max(xc, x0), min(xc, x0)
            size_x = (x_max - x_min) * 2
            size_y = size_x
            logger.debug(
                f"{self.plate.id}\n"
                f"calculated diameter: {size_x} vs "
                f"stored plate well_size_x: {self.plate.well_size_x * 1000}"
            )

        else:
            xc, yc = self._get_rect_center(*pos)

            # calculated well_size_x and well_size_y
            if self.plate.id != PLATE_FROM_CALIBRATION:
                if len(pos) == 4:
                    x0, y0 = pos[0][0], pos[0][1]
                    x_max, x_min = max(xc, x0), min(xc, x0)
                    y_max, y_min = max(yc, y0), min(yc, y0)
                    size_x = (x_max - x_min) * 2
                    size_y = (y_max - y_min) * 2
                elif len(pos) == 2:
                    x0, y0 = pos[0][0], pos[0][1]
                    x1, y1 = pos[1][0], pos[1][1]
                    size_x = abs(x0) + abs(x1)
                    size_y = abs(y0) + abs(y1)
                logger.debug(
                    f"{self.plate.id}\n"
                    f"calculated well_size_x: {size_x}, "
                    f"calculated well_size_y: {size_y} vs "
                    f"stored plate well_size_x: {self.plate.well_size_x * 1000}, "
                    f"stored plate well_size_y: {self.plate.well_size_y * 1000}"
                )

        if table == self.table_1.tb:
            self.A1_well = ("A1", xc, yc)
            self._calculated_well_size_x = size_x
            self._calculated_well_size_y = size_y

        if self.plate.id == PLATE_FROM_CALIBRATION:
            self.valueChanged.emit(pos)

        return xc, yc


class CalibrationTable(QWidget):
    """Table for the calibration widget."""

    def __init__(self, *, mmcore: CMMCorePlus | None = None) -> None:
        super().__init__()

        self._mmc = mmcore or CMMCorePlus.instance()

        self._well_name = ""

        self._create_wdg()

    def _create_wdg(self) -> None:
        layout = QGridLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 0, 0, 0)
        self.setLayout(layout)

        self.tb = QTableWidget()
        self.tb.setMinimumHeight(150)
        hdr = self.tb.horizontalHeader()
        hdr.setSectionResizeMode(hdr.Stretch)
        self.tb.verticalHeader().setVisible(False)
        self.tb.setTabKeyNavigation(True)
        self.tb.setColumnCount(3)
        self.tb.setRowCount(0)
        self.tb.setHorizontalHeaderLabels(["Well", "X", "Y"])
        self.tb.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.tb, 0, 0, 3, 1)

        btn_sizepolicy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        min_size = 100
        add_btn = QPushButton(text="Add")
        add_btn.clicked.connect(self._add_pos)
        add_btn.setMinimumWidth(min_size)
        add_btn.setSizePolicy(btn_sizepolicy)
        remove_btn = QPushButton(text="Remove")
        remove_btn.clicked.connect(self._remove_position_row)
        remove_btn.setMinimumWidth(min_size)
        remove_btn.setSizePolicy(btn_sizepolicy)
        clear_btn = QPushButton(text="Clear")
        clear_btn.clicked.connect(self._clear_table)
        clear_btn.setMinimumWidth(min_size)
        clear_btn.setSizePolicy(btn_sizepolicy)
        layout.addWidget(add_btn, 0, 1, 1, 1)
        layout.addWidget(remove_btn, 1, 1, 1, 2)
        layout.addWidget(clear_btn, 2, 1, 1, 2)

    def _add_pos(self) -> None:
        if not self._mmc.getXYStageDevice():
            warnings.warn("XY Stage not selected!", stacklevel=2)
            return

        if len(self._mmc.getLoadedDevices()) <= 1:
            return

        row = self._add_position_row()
        name = QTableWidgetItem(f"{self._well_name}_pos{row:03d}")
        name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.tb.setItem(row, 0, name)
        self._add_table_value(self._mmc.getXPosition(), row, 1)
        self._add_table_value(self._mmc.getYPosition(), row, 2)

    def _add_position_row(self) -> int:
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

    def _remove_position_row(self) -> None:
        rows = {r.row() for r in self.tb.selectedIndexes()}
        for idx in sorted(rows, reverse=True):
            self.tb.removeRow(idx)

        self._rename_positions()

    def _rename_positions(self) -> None:
        pos_list = []
        name = ""
        for r in range(self.tb.rowCount()):
            curr_name = self.tb.item(r, 0).text()

            if r == 0:
                name = curr_name.split("_")[0]

            curr_pos = int(curr_name[-3:])
            pos_list.append(curr_pos)

        missing = [x for x in range(pos_list[0], pos_list[-1] + 1) if x not in pos_list]

        full = sorted(missing + pos_list)[: self.tb.rowCount()]

        for r in range(self.tb.rowCount()):
            new_name = f"{name}_pos{full[r]:03d}"
            item = QTableWidgetItem(new_name)
            item.setTextAlignment(int(Qt.AlignHCenter | Qt.AlignVCenter))
            self.tb.setItem(r, 0, item)

    def _clear_table(self) -> None:
        self.tb.clearContents()
        self.tb.setRowCount(0)

    def _rename_well_column(self, well_name: str) -> None:
        self._well_name = well_name
        well = QTableWidgetItem(well_name)
        self.tb.setHorizontalHeaderItem(0, well)

    def _handle_error(self, circular_well: bool) -> bool:
        if circular_well:
            if self.tb.rowCount() < 3:
                warnings.warn(
                    f"Not enough points for {self._well_name}. "
                    "Add 3 points to the table.",
                    stacklevel=2,
                )
                return True
            elif self.tb.rowCount() > 3:
                warnings.warn("Add only 3 points to the table.", stacklevel=2)
                return True

        elif self.tb.rowCount() < 2 or self.tb.rowCount() == 3:
            warnings.warn(
                f"Not enough points for {self._well_name}. "
                "Add 2 or 4 points to the table.",
                stacklevel=2,
            )
            return True
        elif self.tb.rowCount() > 4:
            warnings.warn("Add 2 or 4 points to the table.", stacklevel=2)
            return True

        return False


def _get_circle_from_3_points(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> tuple[float, float]:
    """Return the center of the circle passing through three points."""
    A = np.array([[*a, 1], [*b, 1], [*c, 1]])
    B = np.array([a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2])
    x, y, _ = np.linalg.solve(A, B)
    return x / 2, y / 2
