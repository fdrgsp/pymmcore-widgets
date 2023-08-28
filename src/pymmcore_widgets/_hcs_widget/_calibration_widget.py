from __future__ import annotations

import math
import string
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, cast

import matplotlib.pyplot as plt
import numpy as np
from fonticon_mdi6 import MDI6
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QSize, Qt, Signal
from qtpy.QtGui import QIcon, QPixmap
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QAction,
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

from ._graphics_items import WellInfo
from ._util import apply_rotation_matrix, get_well_center

if TYPE_CHECKING:
    from ._well_plate_model import WellPlate

AlignCenter = Qt.AlignmentFlag.AlignCenter
FixedSizePolicy = (QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


ALPHABET = string.ascii_uppercase
ROLE = Qt.ItemDataRole.UserRole + 1
ICON_PATH = Path(__file__).parent / "icons"
ICON_SIZE = 22
CIRCLE_ICON = QIcon(str(ICON_PATH / "circle-outline.svg"))
SIDES_ICON = QIcon(str(ICON_PATH / "square-outline_s.svg"))
VERTICES_ICON = QIcon(str(ICON_PATH / "square-outline_v.svg"))
CIRCLE_ITEM = "3 points : add 3 points on the circonference of the well"
SIDES_ITEM = "4 points: add 4 points, 1 per side of the rectangular/square well"
VERTICES_ITEM = (
    "2 points: add 2 points at 2 opposite vertices of the rectangular/square well"
)
CIRCLE_MODE_POINTS = 3
SIDES_MODE_POINTS = 4
VERTICES_MODE_POINTS = 2
LABEL_STYLE = """
    background: rgb(0, 255, 0);
    font-size: 16pt; font-weight:bold;
    color : black;
    border: 1px solid black;
    border-radius: 5px;
"""


class ThreePoints(NamedTuple):
    icon: QIcon = CIRCLE_ICON
    item: str = CIRCLE_ITEM
    points: int = CIRCLE_MODE_POINTS


class FourPoints(NamedTuple):
    icon: QIcon = SIDES_ICON
    item: str = SIDES_ITEM
    points: int = SIDES_MODE_POINTS


class TwoPoints(NamedTuple):
    icon: QIcon = VERTICES_ICON
    item: str = VERTICES_ITEM
    points: int = VERTICES_MODE_POINTS


class CalibrationInfo(NamedTuple):
    """Calibration information for the plate."""

    well_A1_center_x: float
    well_A1_center_y: float
    rotation_matrix: np.ndarray | None


def _find_circle_center(
    point1: tuple[float, float],
    point2: tuple[float, float],
    point3: tuple[float, float],
) -> tuple[float, float]:
    """
    Calculate the center of a circle passing through three given points.

    The function uses the formula for the circumcenter of a triangle to find
    the center of the circle that passes through the given points.
    """
    x1, y1 = point1
    x2, y2 = point2
    x3, y3 = point3

    # Calculate determinant D
    D = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))

    # Calculate x and y coordinates of the circle's center
    x = (
        ((x1**2 + y1**2) * (y2 - y3))
        + ((x2**2 + y2**2) * (y3 - y1))
        + ((x3**2 + y3**2) * (y1 - y2))
    ) / D
    y = (
        ((x1**2 + y1**2) * (x3 - x2))
        + ((x2**2 + y2**2) * (x1 - x3))
        + ((x3**2 + y3**2) * (x2 - x1))
    ) / D

    # this is to test only, should be removed_____________________________
    plt.plot(x1, y1, "mo")
    plt.plot(x2, y2, "mo")
    plt.plot(x3, y3, "mo")
    plt.plot(x, y, "go")
    plt.axis("equal")
    plt.gca().invert_yaxis()
    plt.show()
    # ____________________________________________________________________

    return x, y


def _find_rectangle_center(*args: tuple[float, ...]) -> tuple[float, float]:
    """
    Find the center of a rectangle/square well.

    ...given two opposite verices coordinates or 4 points on the edges.
    """
    x_list, y_list = list(zip(*args))

    if len(args) == 4:
        # get corner x and y coordinates
        x_list = (max(x_list), min(x_list))
        y_list = (max(y_list), min(y_list))

    # get center coordinates
    x = sum(x_list) / 2
    y = sum(y_list) / 2
    # this is to test only, should be removed_____________________________
    plt.plot(x_list, y_list, "o")
    plt.plot(x, y, "o")
    plt.axis("equal")
    plt.gca().invert_yaxis()
    plt.show()
    # ____________________________________________________________________
    return x, y


def _get_plate_rotation_matrix(
    xy_well_1: tuple[float, float], xy_well_2: tuple[float, float]
) -> np.ndarray:
    """Get the rotation matrix to align the plate along the x axis."""
    x1, y1 = xy_well_1
    x2, y2 = xy_well_2

    m = (y2 - y1) / (x2 - x1)  # slope from y = mx + q
    # plate_angle_rad = -np.arctan(m)
    plate_angle_rad = np.arctan(m)
    # this is to test only, should be removed_____________________________
    print(f"plate_angle: {np.rad2deg(plate_angle_rad)}")
    # ____________________________________________________________________
    return np.array(
        [
            [np.cos(plate_angle_rad), -np.sin(plate_angle_rad)],
            [np.sin(plate_angle_rad), np.cos(plate_angle_rad)],
        ]
    )


def _get_random_circle_edge_point(
    xc: float, yc: float, radius: float
) -> tuple[float, float]:
    """Get random edge point of a circle.

    ...with center (xc, yc) and radius `radius`.
    """
    # random angle
    angle = 2 * math.pi * np.random.random()
    # coordinates of the edge point using trigonometry
    x = radius * math.cos(angle) + xc
    y = radius * math.sin(angle) + yc

    return x, y


def _get_random_rectangle_edge_point(
    xc: float, yc: float, well_size_x: float, well_size_y: float
) -> tuple[float, float]:
    """Get random edge point of a rectangle.

    ...with center (xc, yc) and size (well_size_x, well_size_y).
    """
    x_left, y_top = xc - (well_size_x / 2), yc + (well_size_y / 2)
    x_right, y_bottom = xc + (well_size_x / 2), yc - (well_size_y / 2)

    # random 4 edge points
    edge_points = [
        (x_left, np.random.uniform(y_top, y_bottom)),  # left
        (np.random.uniform(x_left, x_right), y_top),  # top
        (x_right, np.random.uniform(y_top, y_bottom)),  # right
        (np.random.uniform(x_left, x_right), y_bottom),  # bottom
    ]
    return edge_points[np.random.randint(0, 4)]


class _CalibrationModeWidget(QGroupBox):
    valueChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._mode_combo = QComboBox()
        self._mode_combo.currentIndexChanged.connect(self._on_value_changed)

        lbl = QLabel(text="Calibration Mode:")
        lbl.setSizePolicy(*FixedSizePolicy)

        self.setLayout(QHBoxLayout())
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().setSpacing(10)
        self.layout().addWidget(lbl)
        self.layout().addWidget(self._mode_combo)

    def _on_value_changed(self) -> None:
        """Emit the selected mode with valueChanged signal."""
        mode = self._mode_combo.itemData(self._mode_combo.currentIndex(), ROLE)
        self.valueChanged.emit(mode)

    def setValue(self, modes: list[ThreePoints | FourPoints | TwoPoints]) -> None:
        """Set the available modes."""
        self._mode_combo.clear()
        for idx, mode in enumerate(modes):
            self._mode_combo.addItem(mode.icon, mode.item)
            self._mode_combo.setItemData(idx, mode, ROLE)

    def value(self) -> ThreePoints | FourPoints | TwoPoints:
        """Return the selected calibration mode."""
        return self._mode_combo.itemData(self._mode_combo.currentIndex(), ROLE)  # type: ignore  # noqa E501


class _CalibrationTable(QWidget):
    """Table for the calibration widget."""

    def __init__(self, *, mmcore: CMMCorePlus | None = None) -> None:
        super().__init__()

        self._mmc = mmcore or CMMCorePlus.instance()

        self._plate: WellPlate | None = None
        self._calibration_mode: ThreePoints | FourPoints | TwoPoints | None = None

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
        self.act_clear.triggered.connect(self._clear)

        self._toolbar.addWidget(spacer_1)
        self._toolbar.addWidget(self._well_label)
        self._toolbar.addWidget(spacer_2)
        self._toolbar.addAction(self.act_add_row)
        self._toolbar.addAction(self.act_remove_row)
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

    @property
    def plate(self) -> WellPlate | None:
        return self._plate

    @plate.setter
    def plate(self, plate: WellPlate) -> None:
        self._plate = plate

    @property
    def calibration_mode(self) -> ThreePoints | FourPoints | TwoPoints | None:
        return self._calibration_mode

    @calibration_mode.setter
    def calibration_mode(self, mode: ThreePoints | FourPoints | TwoPoints) -> None:
        self._calibration_mode = mode

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

    def _clear(self) -> None:
        self.tb.clearContents()
        self.tb.setRowCount(0)

    def _get_table_values(self) -> list[tuple[float, float]]:
        _range = self.tb.rowCount()
        pos: list[tuple[float, float]] = [
            (self.tb.cellWidget(r, 0).value(), self.tb.cellWidget(r, 1).value())
            for r in range(_range)
        ]
        return pos

    def _update(
        self,
        plate: WellPlate,
        calibration_mode: ThreePoints | FourPoints | TwoPoints,
        well_name: str,
    ) -> None:
        """Update the widget with the given plate calibration mode and well name."""
        self._clear()
        self._plate = plate
        self._calibration_mode = calibration_mode
        self._well_label.setText(well_name)

    def setValue(self, list_of_points: list[tuple[float, float]]) -> None:
        self._clear()
        for x, y in list_of_points:
            row = self._add_row()
            self._add_table_value(x, row, 0)
            self._add_table_value(y, row, 1)

    def value(self) -> list[tuple[float, float]] | None:
        if self.plate is None or self._calibration_mode is None:
            return None
        return self._get_table_values()


class _TestCalibrationWidget(QGroupBox):
    def __init__(
        self, parent: QWidget | None = None, *, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent)
        self.setTitle("Test Calibration")

        self._mmc = mmcore or CMMCorePlus.instance()

        # test calibration groupbox
        lbl = QLabel("Move to the edge of well:")
        lbl.setSizePolicy(*FixedSizePolicy)
        # combo to select plate
        self._letter_combo = QComboBox()
        self._letter_combo.setEditable(True)
        self._letter_combo.lineEdit().setReadOnly(True)
        self._letter_combo.lineEdit().setAlignment(AlignCenter)
        # combo to select well number
        self._number_combo = QComboBox()
        self._number_combo.setEditable(True)
        self._number_combo.lineEdit().setReadOnly(True)
        self._number_combo.lineEdit().setAlignment(AlignCenter)
        # test button
        self._test_button = QPushButton("Go")
        self._test_button.setEnabled(False)
        # groupbox
        test_calibration = QWidget()
        test_calibration.setLayout(QHBoxLayout())
        test_calibration.layout().setSpacing(10)
        test_calibration.layout().setContentsMargins(10, 10, 10, 10)
        test_calibration.layout().addWidget(lbl)
        test_calibration.layout().addWidget(self._letter_combo)
        test_calibration.layout().addWidget(self._number_combo)
        test_calibration.layout().addWidget(self._test_button)

        # main
        self.setLayout(QHBoxLayout())
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().addWidget(test_calibration)

    def _update(self, plate: WellPlate) -> None:
        self._letter_combo.clear()
        letters = [ALPHABET[letter] for letter in range(plate.rows)]
        self._letter_combo.addItems(letters)

        self._number_combo.clear()
        numbers = [str(c) for c in range(1, plate.columns + 1)]
        self._number_combo.addItems(numbers)

    def value(self) -> WellInfo:
        """Return the selected test well as `WellInfo` object."""
        return WellInfo(
            self._letter_combo.currentText() + self._number_combo.currentText(),
            self._letter_combo.currentIndex(),
            self._number_combo.currentIndex(),
        )

    def setValue(self, well: WellInfo) -> None:
        """Set the selected test well."""
        self._letter_combo.setCurrentIndex(well.row)
        self._number_combo.setCurrentIndex(well.column)


class _CalibrationLabel(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Calibration Status")

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
        self.layout().setSpacing(5)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(self._icon_lbl)
        self.layout().addWidget(self._text_lbl)

    def setValue(self, pixmap: QPixmap, text: str) -> None:
        """Set the icon and text of the labels."""
        self._icon_lbl.setPixmap(pixmap)
        self._text_lbl.setText(text)


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
        self._calibration_info: CalibrationInfo | None = None

        # calibration mode
        self._calibration_mode = _CalibrationModeWidget()

        # calibration tables
        self._table_a1 = _CalibrationTable()
        # self._table_a1._write_to_label(colunm=0)
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
        self.layout().addWidget(self._calibration_mode)
        self.layout().addWidget(_table_and_btn_wdg)
        self.layout().addWidget(_bottom_group)

        # connect
        self._calibration_mode.valueChanged.connect(self._on_calibration_mode_changed)
        self._calibrate_button.clicked.connect(self._on_calibrate_button_clicked)
        self._test_calibration._test_button.clicked.connect(self._move_to_well_edge)

    @property
    def plate(self) -> WellPlate | None:
        return self._plate

    @plate.setter
    def plate(self, plate: WellPlate) -> None:
        self._plate = plate

    @property
    def calibration_info(self) -> CalibrationInfo | None:
        return self._calibration_info

    @calibration_info.setter
    def calibration_info(self, info: CalibrationInfo) -> None:
        self._calibration_info = info

    def _on_calibration_mode_changed(
        self, calibration_mode: ThreePoints | FourPoints | TwoPoints
    ) -> None:
        self._table_a1.calibration_mode = calibration_mode
        self._table_an.calibration_mode = calibration_mode

    def _update(self, plate: WellPlate) -> None:
        self.plate = plate
        # reset calibration state
        self._reset_calibration()
        # update calibration mode
        calibration_mode: list[ThreePoints | FourPoints | TwoPoints] = (
            [ThreePoints()] if plate.circular else [TwoPoints(), FourPoints()]
        )
        self._calibration_mode.setValue(calibration_mode)
        # update tables
        self._update_tables(self.plate, self._calibration_mode.value())
        # update test calibration
        self._test_calibration._update(self.plate)

    def _update_tables(
        self, plate: WellPlate, calibration_mode: ThreePoints | FourPoints | TwoPoints
    ) -> None:
        """Update the calibration tables."""
        self._table_a1._update(plate, calibration_mode, " Well A1 ")
        self._table_an._update(plate, calibration_mode, f" Well A{plate.columns} ")
        self._table_an.show() if plate.columns > 1 else self._table_an.hide()

    def _reset_calibration(self) -> None:
        """Reset to not calibrated state."""
        self._set_calibration_label(False)
        self._calibration_info = None

    def _on_calibrate_button_clicked(self) -> None:
        """Calibrate the plate."""
        if self.plate is None:
            self._reset_calibration()
            return

        # get calibration well centers
        a1_center, an_center = self._find_calibration_well_centers()

        # return if any of the necessary well centers are None
        if None in a1_center or (None in an_center and self.plate.columns > 1):
            self._reset_calibration()
            # TODO: add warning
            return

        # get plate rotation matrix
        rotation_matrix = (
            None
            if None in an_center
            else _get_plate_rotation_matrix(a1_center, an_center)  # type: ignore
        )

        # set calibration_info property
        a1_x, a1_y = cast(tuple[float, float], a1_center)
        self.calibration_info = CalibrationInfo(a1_x, a1_y, rotation_matrix)

        # update calibration label
        self._set_calibration_label(True)

    def _find_calibration_well_centers(
        self,
    ) -> tuple[tuple[float | None, float | None], tuple[float | None, float | None]]:
        """Find the centers in stage coordinates of the calibration wells."""
        if self.plate is None:
            return (None, None), (None, None)

        a1_x, a1_y = self._find_well_center(self._table_a1)
        an_x, an_y = (
            self._find_well_center(self._table_an)
            if self.plate.columns > 1
            else (None, None)
        )
        return (a1_x, a1_y), (an_x, an_y)

    def _find_well_center(
        self, table: _CalibrationTable
    ) -> tuple[float | None, float | None]:
        """Find the well center from the calibration table."""
        pos = table.value()

        if pos is None or table.calibration_mode is None:
            return None, None

        points = table.calibration_mode.points
        if len(pos) != points:
            self._reset_calibration()
            raise ValueError(
                "Invalid number of points for "
                f"'{table._well_label.text().replace(' ', '')}'. "
                f"Expected {points}, got {len(pos)}."
            )

        return (
            _find_circle_center(*pos)
            if points == CIRCLE_MODE_POINTS
            else _find_rectangle_center(*pos)
        )

    def _move_to_well_edge(self) -> None:
        """Move to the edge of the selected well to test the calibratiion."""
        if self.plate is None:
            return
        cal_info = self.calibration_info
        if cal_info is None:
            return

        well = self._test_calibration.value()
        a1_x, a1_y = cal_info.well_A1_center_x, cal_info.well_A1_center_y
        cx, cy = get_well_center(self.plate, well, a1_x, a1_y)

        if cal_info.rotation_matrix is not None:
            cx, cy = apply_rotation_matrix(cal_info.rotation_matrix, a1_x, a1_y, cx, cy)

        self._mmc.waitForDevice(self._mmc.getXYStageDevice())

        if self.plate.circular:
            x, y = _get_random_circle_edge_point(
                cx, cy, self.plate.well_size_x * 1000 / 2
            )
        else:
            x, y = _get_random_rectangle_edge_point(
                cx, cy, self.plate.well_size_x * 1000, self.plate.well_size_y * 1000
            )

        self._mmc.setXYPosition(x, y)
        # this is only for testing, remove later____________________________________
        plt.plot(a1_x, a1_y, "mo")
        plt.plot(cx, cy, "mo")
        plt.plot(x, y, "go")
        for _ in range(50):
            if self.plate.circular:
                x, y = _get_random_circle_edge_point(
                    cx, cy, self.plate.well_size_x * 1000 / 2
                )
            else:
                x, y = _get_random_rectangle_edge_point(
                    cx,
                    cy,
                    self.plate.well_size_x * 1000,
                    self.plate.well_size_y * 1000,
                )
            plt.plot(x, y, "ko")
        plt.axis("equal")
        plt.gca().invert_yaxis()
        plt.show()
        # ______________________________________________________________________________

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
        self._test_calibration._test_button.setEnabled(state)

    def value(self) -> CalibrationInfo | None:
        """Get the calibration information."""
        return self.calibration_info

    def setValue(self, value: CalibrationInfo | None) -> None:
        """Set the calibration information."""
        if value is None:
            self.calibration_info = value
            self._set_calibration_label(True)
