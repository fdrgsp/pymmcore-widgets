from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QItemSelection, QSize, Qt, Signal
from qtpy.QtGui import QIcon
from qtpy.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon
from superqt.utils import signals_blocked

from pymmcore_widgets._icons import StandardIcon
from pymmcore_widgets._util import GREEN, YELLOW, use_combo_row_height

from ._util import find_circle_center, find_rectangle_center

if TYPE_CHECKING:
    from collections.abc import Iterator

COMBO_ROLE = Qt.ItemDataRole.UserRole + 1
ICON_PATH = Path(__file__).parent / "icons"
ONE_CIRCLE = QIcon(str(ICON_PATH / "circle-center.svg"))
ONE_SQUARE = QIcon(str(ICON_PATH / "square-center.svg"))
TWO = QIcon(str(ICON_PATH / "square-vertices.svg"))
THREE = QIcon(str(ICON_PATH / "circle-edges.svg"))
FOUR = QIcon(str(ICON_PATH / "square-edges.svg"))
NON_CALIBRATED_ICON = "mdi:circle"
CALIBRATED_ICON = "mdi:check-circle"
PENDING_ICON = "mdi:progress-check"
ICON_SIZE = QSize(30, 30)
# Button icons and colors shared with the MDA tables and MDA run controls.
ICON_GREEN, ICON_RED, ICON_GRAY = "#3A3", "#C33", "#666"
SET_ICON = "mdi:map-marker-plus-outline"
RUN_ICON = "mdi:play-circle-outline"
CANCEL_ICON = "mdi:stop-circle-outline"
TEST_ICON = "mdi:target"


class Mode(NamedTuple):
    """Calibration mode."""

    text: str
    points: int
    icon: QIcon | None = None

    def __eq__(self, value: object) -> bool:
        if not isinstance(value, Mode):
            return NotImplemented
        return (self.text, self.points) == (value.text, value.points)


# mapping of Circular well -> [Modes]
MODES: dict[bool, list[Mode]] = {
    True: [
        Mode("1 Center point", 1, ONE_CIRCLE),
        Mode("3 Edge points", 3, THREE),
    ],
    False: [
        Mode("1 Center point", 1, ONE_SQUARE),
        Mode("2 Corners", 2, TWO),
        Mode("4 Edge points", 4, FOUR),
    ],
}


class _CalibrationModeWidget(QComboBox):
    """Widget to select the calibration mode."""

    modeChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._is_circular = False
        self.currentIndexChanged.connect(self._on_value_changed)

    def _on_value_changed(self, index: int) -> None:
        """Emit the selected mode with valueChanged signal."""
        self.modeChanged.emit(self.currentMode())

    def isCircularMode(self) -> bool:
        """Return True if the well is circular."""
        return self._is_circular

    def setCircularMode(self, circular: bool) -> None:
        self._is_circular = bool(circular)
        with signals_blocked(self):
            self.clear()
            for idx, mode in enumerate(MODES[self._is_circular]):
                self.addItem(mode.icon, mode.text)
                self.setItemData(idx, mode, COMBO_ROLE)
            # default: 3 edge points for round wells, 2 corners for square ones
            self.setCurrentIndex(1)
        self.modeChanged.emit(self.currentMode())

    def currentMode(self) -> Mode:
        """Return the selected calibration mode."""
        return Mode(*self.itemData(self.currentIndex(), COMBO_ROLE))


class _CalibrationTable(QTableWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 2, parent)

        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        use_combo_row_height(self)
        self.verticalHeader().sectionResized.connect(self.updateGeometry)
        self.horizontalHeader().geometriesChanged.connect(self.updateGeometry)

        hdr = self.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setSectionResizeMode(hdr.ResizeMode.Stretch)

        self.setHorizontalHeaderLabels(["X [µm]", "Y [µm]"])
        self.selectionModel().selectionChanged.connect(self._on_selection_changed)

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        size.setHeight(
            self.horizontalHeader().sizeHint().height()
            + self.verticalHeader().length()
            + 2 * self.frameWidth()
        )
        return size

    def minimumSizeHint(self) -> QSize:
        size = super().minimumSizeHint()
        size.setHeight(self.sizeHint().height())
        return size

    def _on_selection_changed(
        self, selected: QItemSelection, deselected: QItemSelection
    ) -> None:
        # ensure something is always selected
        if selected.count() == 0 and deselected.count() > 0:
            sel_model = self.selectionModel()
            for item in deselected.indexes():
                sel_model.select(item, sel_model.SelectionFlag.Select)

    def positions(self) -> Iterator[tuple[int, float, float]]:
        """Return the list of non-null (row, x, y) points."""
        for row in range(self.rowCount()):
            if (
                (x_item := self.item(row, 0))
                and (y_item := self.item(row, 1))
                and (x_text := x_item.text())
                and (y_text := y_item.text())
            ):
                yield (row, float(x_text), float(y_text))

    def set_selected(self, x: float, y: float) -> None:
        """Assign (x, y) to the currently selected row in the table."""
        if not (indices := self.selectedIndexes()):
            return  # pragma: no cover

        selected_row = indices[0].row()
        for row, *p in self.positions():
            if p == [x, y] and row != selected_row:
                QMessageBox.critical(
                    self,
                    "Duplicate position",
                    f"Position ({x}, {y}) is already in the list.",
                )
                return

        self._set_row(selected_row, x, y)
        if selected_row < self.rowCount() - 1:
            self.setCurrentCell(selected_row + 1, 0)

    def _set_row(self, row: int, x: str | float, y: str | float) -> None:
        """Emit only one itemChanged signal when setting the item."""
        itemx = QTableWidgetItem(f"{x:.2f}")
        itemx.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        itemy = QTableWidgetItem(f"{y:.2f}")
        itemy.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        with signals_blocked(self):
            self.setItem(row, 0, itemx)
        self.setItem(row, 1, itemy)

    def clear_selected(self) -> None:
        """Remove the selected position from the table."""
        if items := self.selectedItems():
            with signals_blocked(self):
                for item in items:
                    item.setText("")
            self.itemChanged.emit(item)

    def clear_all(self) -> None:
        self.resetRowCount(self.rowCount())

    def resetRowCount(self, num: int) -> None:
        with signals_blocked(self):
            self.clearContents()
            self.setRowCount(num)
            # select the first row
            self.setCurrentCell(0, 0)
        self.itemChanged.emit(self.item(0, 0))
        self.updateGeometry()


class WellCalibrationWidget(QWidget):
    calibrationChanged = Signal(bool)

    def __init__(
        self, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent)

        self._mmc = mmcore or CMMCorePlus.instance()
        self._well_center: tuple[float, float] | None = None

        # WIDGETS -------------------------------------------------------------

        # Well label
        self.well_label = QLabel("Well A1")
        font = self.well_label.font()
        font.setBold(True)
        font.setPixelSize(16)
        self.well_label.setFont(font)

        # Icon for calibration status
        self._calibration_icon = QLabel()
        icn = QIconifyIcon(NON_CALIBRATED_ICON, color=YELLOW)
        self._calibration_icon.setPixmap(icn.pixmap(ICON_SIZE))

        # calibration mode
        self._calibration_mode_wdg = _CalibrationModeWidget(self)
        # instructions are a tooltip: hover (or click) the info button to read them
        self._info_button = QToolButton()
        self._info_button.setIcon(QIconifyIcon("mdi:information-outline"))
        self._info_button.setAutoRaise(True)
        self._info_button.setAccessibleName("Calibration Instructions")
        self._info_button.clicked.connect(self._show_instructions)
        # one-line purpose under the title; details live in the info tooltip
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        # wrap to the panel width instead of widening it
        policy = self._summary.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self._summary.setSizePolicy(policy)
        self.setInstructions(
            "Record points on the selected well to find its center.",
            "First, select the well on the plate diagram and choose a method. "
            "Then move the stage to the <b>well's center</b>, <b>edge points</b>, or "
            "<b>opposite corners</b>, as required by that method, "
            "and click <b>Set</b> to record each point.",
        )

        # calibration table and buttons
        self._table = _CalibrationTable(self)

        # add and remove buttons
        self._set_button = QPushButton("Set")
        self._set_button.setIcon(QIconifyIcon(SET_ICON, color=ICON_GREEN))
        self._set_button.setToolTip("Record the current stage position.")
        self._clear_button = QPushButton("Clear")
        self._clear_button.setIcon(StandardIcon.DELETE.icon(ICON_RED))
        self._clear_button.setToolTip("Clear the selected point.")
        self._clear_all_button = QPushButton("Clear All")
        self._clear_all_button.setIcon(StandardIcon.DELETE_ALL.icon(ICON_RED))
        self._clear_all_button.setToolTip("Clear all points.")
        # no focus, so the dialog's Enter key and default highlight skip them
        for button in (self._set_button, self._clear_button, self._clear_all_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setAutoDefault(False)

        # LAYOUT --------------------------------------------------------------

        labels = QHBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.addWidget(self._calibration_icon)
        labels.addWidget(self.well_label)
        labels.addStretch()
        labels.addWidget(self._info_button)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Method:"))
        mode_row.addWidget(self._calibration_mode_wdg, 1)

        remove_btns = QHBoxLayout()
        remove_btns.setContentsMargins(0, 0, 0, 0)
        remove_btns.addWidget(self._clear_button)
        remove_btns.addWidget(self._clear_all_button)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(5)
        main_layout.addLayout(labels)
        main_layout.addWidget(self._summary)
        main_layout.addSpacing(10)
        main_layout.addLayout(mode_row)
        main_layout.addSpacing(10)
        main_layout.addWidget(self._table)
        main_layout.addWidget(self._set_button)
        main_layout.addLayout(remove_btns)
        main_layout.addStretch()

        # CONNECTIONS ---------------------------------------------------------

        self._calibration_mode_wdg.modeChanged.connect(self._on_mode_changed)
        self._set_button.clicked.connect(self._on_set_clicked)
        self._clear_button.clicked.connect(self._table.clear_selected)
        self._clear_all_button.clicked.connect(self._table.clear_all)
        self._table.itemChanged.connect(self._validate_calibration)

    def setInstructions(self, summary: str, details: str) -> None:
        """Set the one-line summary and the info-button tooltip instructions."""
        self._summary.setText(summary)
        # <p> makes Qt treat the tooltip as rich text, which word-wraps.
        self._info_button.setToolTip(f"<p>{details}</p>")

    def _show_instructions(self) -> None:
        QToolTip.showText(
            self._info_button.mapToGlobal(self._info_button.rect().bottomLeft()),
            self._info_button.toolTip(),
            self._info_button,
        )

    def wellCenter(self) -> tuple[float, float] | None:
        """Return the center of the well, or None if not calibrated."""
        return self._well_center

    def setWellCenter(self, center: tuple[float, float] | None) -> None:
        """Set the calibration icon and emit the calibrationChanged signal."""
        if self._well_center == center:
            return

        self._well_center = center
        if center is None:
            icn = QIconifyIcon(NON_CALIBRATED_ICON, color=YELLOW)
        else:
            icn = QIconifyIcon(CALIBRATED_ICON, color=GREEN)
        self._calibration_icon.setPixmap(icn.pixmap(ICON_SIZE))
        self.calibrationChanged.emit(center is not None)

    def setCenterPoint(self, center: tuple[float, float]) -> None:
        """Populate the direct-center mode from a measured well center."""
        self._calibration_mode_wdg.setCurrentIndex(0)
        self._table.resetRowCount(1)
        self._table._set_row(0, *center)
        self.setWellCenter(center)

    def setCircularWell(self, circular: bool) -> None:
        """Update the calibration widget for circular or rectangular wells."""
        self._calibration_mode_wdg.setCircularMode(circular)

    def circularWell(self) -> bool:
        """Return True if the well is circular."""
        return self._calibration_mode_wdg.isCircularMode()

    def calibrationMode(self) -> Mode:
        """Return the selected calibration method."""
        return self._calibration_mode_wdg.currentMode()

    def setCalibrationMode(self, mode: Mode) -> None:
        """Select the calibration method (ignored if not valid for this well shape)."""
        for idx in range(self._calibration_mode_wdg.count()):
            if Mode(*self._calibration_mode_wdg.itemData(idx, COMBO_ROLE)) == mode:
                self._calibration_mode_wdg.setCurrentIndex(idx)
                return

    def hasPoints(self) -> bool:
        """Return True if any calibration point has been recorded."""
        return any(True for _ in self._table.positions())

    def _on_set_clicked(self) -> None:
        x, y = self._mmc.getXYPosition()
        self._table.set_selected(round(x, 2), round(y, 2))

    def _on_mode_changed(self, mode: Mode) -> None:
        """Update the rows in the calibration table."""
        self._table.resetRowCount(mode.points)
        self.setWellCenter(None)

    def _validate_calibration(self) -> None:
        """Validate the calibration points added to the table."""
        # get the current (x, y) positions in the table
        points = [p[1:] for p in self._table.positions()]
        needed_points = self._calibration_mode_wdg.currentMode().points

        # if the number of points is not yet satisfied, do nothing
        if len(points) < needed_points:
            self.setWellCenter(None)
            return

        # if the number of points is 1, well is already calibrated
        if needed_points == 1:
            self.setWellCenter(points[0])
            return

        # if the number of points is correct, try to calculate the calibration
        try:
            # TODO: allow additional sanity checks for min/max radius, width/height
            if self.circularWell():
                x, y, _radius = find_circle_center(points)
            else:
                x, y, _width, _height = find_rectangle_center(points)
        except Exception as e:  # pragma: no cover
            self.setWellCenter(None)
            QMessageBox.critical(
                self,
                "Calibration error",
                f"Could not calculate the center of the well.\n\n{e}",
            )
        else:
            self.setWellCenter((x, y))
