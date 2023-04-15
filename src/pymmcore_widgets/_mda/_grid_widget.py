from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal, cast

from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from useq import AnyGridPlan, GridFromEdges, GridRelative, MDASequence, NoGrid
from useq._grid import OrderMode, RelativeTo

if TYPE_CHECKING:
    from typing_extensions import Required, TypedDict

    class GridDict(TypedDict, total=False):
        """Grid dictionary."""

        overlap: Required[float | tuple[float, float]]
        mode: Required[OrderMode | str]
        rows: int
        columns: int
        relative_to: RelativeTo | str
        top: float
        left: float
        bottom: float
        right: float


fixed_sizepolicy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class _SpinboxWidget(QWidget):
    valueChanged = Signal()

    def __init__(
        self,
        label: Literal["top", "bottom", "left", "right", "corner1", "corner2"],
        mmcore: CMMCorePlus,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._mmc = mmcore
        self._label = label

        self._corners = label in {"corner1", "corner2"}

        layout = QHBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        self.label = QLabel(text=f"{self._label}:")
        self.label.setSizePolicy(fixed_sizepolicy)

        self.set_button = QPushButton(text="Set")
        self.set_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.set_button.setMinimumWidth(75)
        self.set_button.setSizePolicy(fixed_sizepolicy)
        self.set_button.clicked.connect(self._on_click)

        layout.addWidget(self.label)

        if self._corners:
            x_label = QLabel(text="x:")
            x_label.setSizePolicy(fixed_sizepolicy)
            self.x_spinbox = self._doublespinbox()
            y_label = QLabel(text="y:")
            y_label.setSizePolicy(fixed_sizepolicy)
            self.y_spinbox = self._doublespinbox()
            layout.addWidget(x_label)
            layout.addWidget(self.x_spinbox)
            layout.addWidget(y_label)
            layout.addWidget(self.y_spinbox)
        else:
            self.spinbox = self._doublespinbox()
            layout.addWidget(self.spinbox)

        layout.addWidget(self.set_button)

    def _doublespinbox(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setMaximum(1000000)
        spin.setMinimum(-1000000)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.valueChanged.connect(self.valueChanged)
        return spin

    def _on_click(self) -> None:
        if not self._mmc.getXYStageDevice():
            return
        if self._corners:
            self.x_spinbox.setValue(self._mmc.getXPosition())
            self.y_spinbox.setValue(self._mmc.getYPosition())
        elif self._label in {"top", "bottom"}:
            self.spinbox.setValue(self._mmc.getYPosition())
        elif self._label in {"left", "right"}:
            self.spinbox.setValue(self._mmc.getXPosition())


class GridWidget(QDialog):
    """A subwidget to setup the acquisition of a grid of images.

    The `value()` method returns a dictionary with the current state of the widget, in a
    format that matches one of the [useq-schema Grid Plan
    specifications](https://pymmcore-plus.github.io/useq-schema/schema/axes/#grid-plans).

    Parameters
    ----------
    parent : QWidget | None
        Optional parent widget, by default None.
    current_stage_pos : tuple[float | None, float | None]
        Optional current xy stage position. By default None.
    mmcore : CMMCorePlus | None
        Optional [`pymmcore_plus.CMMCorePlus`][] micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new)
        [`CMMCorePlus.instance`][pymmcore_plus.core._mmcore_plus.CMMCorePlus.instance].
    """

    valueChanged = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        current_stage_pos: tuple[float | None, float | None] = (None, None),
        mmcore: CMMCorePlus | None = None,
    ) -> None:
        super().__init__(parent=parent)

        self._mmc = mmcore or CMMCorePlus.instance()

        self._current_stage_pos = current_stage_pos

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        self.setLayout(layout)

        tab = self._create_tab()
        layout.addWidget(tab)

        overlap_and_size = self._create_overlap_and_ordermode()
        layout.addWidget(overlap_and_size)

        move_to = self._create_move_to_widget()
        layout.addWidget(move_to)

        label_info = self._create_label_info()
        layout.addWidget(label_info)

        button = self._create_add_button()
        layout.addWidget(button)

        self.setFixedHeight(self.sizeHint().height())

        self._update_info()

        self._mmc.events.systemConfigurationLoaded.connect(self._update_info)
        self._mmc.events.pixelSizeChanged.connect(self._update_info)

        self.destroyed.connect(self._disconnect)

    def _create_tab(self) -> QWidget:
        wdg = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        wdg.setLayout(layout)
        self.tab = QTabWidget()
        self.tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        rowcol = self._create_row_cols_wdg()
        self.tab.addTab(rowcol, "Rows x Columns")

        edges = self._create_edges_grid_wdg()
        self.tab.addTab(edges, "Grid from Edges")

        corners = self._create_corners_grid_wdg()
        self.tab.addTab(corners, "Grid from Corners")

        layout.addWidget(self.tab)

        self.tab.currentChanged.connect(self._update_info)
        return wdg

    def _create_row_cols_wdg(self) -> QWidget:
        group = QWidget()
        group_layout = QGridLayout()
        group_layout.setSpacing(10)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group.setLayout(group_layout)

        # rows
        row_wdg = QWidget()
        row_wdg_lay = QHBoxLayout()
        row_wdg_lay.setSpacing(10)
        self.n_rows = self._general_label_spin_wdg(row_wdg, row_wdg_lay, "Rows:")
        # cols
        col_wdg = QWidget()
        col_wdg_lay = QHBoxLayout()
        col_wdg_lay.setSpacing(10)
        self.n_columns = self._general_label_spin_wdg(col_wdg, col_wdg_lay, "Columns:")

        # relative to combo
        relative_wdg = QWidget()
        relative_layout = QHBoxLayout()
        relative_layout.setSpacing(10)
        relative_layout.setContentsMargins(0, 0, 0, 0)
        relative_wdg.setLayout(relative_layout)
        relative_lbl = QLabel("Relative to:")
        relative_lbl.setSizePolicy(fixed_sizepolicy)
        self.relative_combo = QComboBox()
        self.relative_combo.addItems([r.value for r in RelativeTo])
        relative_layout.addWidget(relative_lbl)
        relative_layout.addWidget(self.relative_combo)

        group_layout.addWidget(row_wdg, 0, 0)
        group_layout.addWidget(col_wdg, 1, 0)
        group_layout.addWidget(relative_wdg, 0, 1)

        return group

    def _general_label_spin_wdg(
        self, wdg: QWidget, layout: QLayout, text: str
    ) -> QSpinBox:
        layout.setContentsMargins(0, 0, 0, 0)
        wdg.setLayout(layout)
        label = QLabel(text=text)
        label.setSizePolicy(fixed_sizepolicy)
        label.setMinimumWidth(65)
        spin = QSpinBox()
        spin.setMinimum(1)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.valueChanged.connect(self._update_info)
        layout.addWidget(label)
        layout.addWidget(spin)
        return spin

    def _create_edges_grid_wdg(self) -> QWidget:
        group = QWidget()
        group_layout = QGridLayout()
        group_layout.setSpacing(10)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group.setLayout(group_layout)

        self.top = _SpinboxWidget("top", mmcore=self._mmc)
        self.top.valueChanged.connect(self._update_info)
        self.bottom = _SpinboxWidget("bottom", mmcore=self._mmc)
        self.bottom.valueChanged.connect(self._update_info)
        self.top.label.setMinimumWidth(self.bottom.label.sizeHint().width())
        self.left = _SpinboxWidget("left", mmcore=self._mmc)
        self.left.valueChanged.connect(self._update_info)
        self.right = _SpinboxWidget("right", mmcore=self._mmc)
        self.right.valueChanged.connect(self._update_info)

        self.top.label.setFixedWidth(self.bottom.label.sizeHint().width() + 5)
        self.bottom.label.setFixedWidth(self.bottom.label.sizeHint().width() + 5)
        self.right.label.setFixedWidth(self.right.label.sizeHint().width() + 5)
        self.left.label.setFixedWidth(self.right.label.sizeHint().width() + 5)

        group_layout.addWidget(self.top, 0, 0)
        group_layout.addWidget(self.bottom, 1, 0)
        group_layout.addWidget(self.left, 0, 1)
        group_layout.addWidget(self.right, 1, 1)

        return group

    def _general_wdg_with_label(self, label_text: str) -> QWidget:
        wdg = QWidget()
        layout = QHBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)
        wdg.setLayout(layout)
        label = QLabel(text=label_text)
        label.setSizePolicy(fixed_sizepolicy)
        layout.addWidget(label)
        return wdg

    def _create_overlap_spinbox(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setMinimumWidth(100)
        spin.setMaximum(100)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.valueChanged.connect(self._update_info)
        return spin

    def _create_overlap_and_ordermode(self) -> QGroupBox:
        group = QGroupBox()
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        group_layout = QGridLayout()
        group_layout.setSpacing(15)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group.setLayout(group_layout)

        # overlap x
        wdg_x = self._general_wdg_with_label("Overlap x (%):")
        self.overlap_spinbox_x = self._create_overlap_spinbox()
        wdg_x.layout().addWidget(self.overlap_spinbox_x)
        group_layout.addWidget(wdg_x, 0, 0)

        # overlap y
        wdg_y = self._general_wdg_with_label("Overlap y (%):")
        self.overlap_spinbox_y = self._create_overlap_spinbox()
        wdg_y.layout().addWidget(self.overlap_spinbox_y)
        group_layout.addWidget(wdg_y, 1, 0)

        # order mode
        wdg_mode = self._general_wdg_with_label("Order mode:")
        self.ordermode_combo = QComboBox()
        self.ordermode_combo.addItems([mode.value for mode in OrderMode])
        self.ordermode_combo.setCurrentText("snake_row_wise")
        wdg_mode.layout().addWidget(self.ordermode_combo)
        group_layout.addWidget(wdg_mode, 0, 1)

        return group

    def _create_corners_grid_wdg(self) -> QWidget:
        group = QWidget()
        group_layout = QVBoxLayout()
        group_layout.setSpacing(10)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group.setLayout(group_layout)

        self.corner_1 = _SpinboxWidget("corner1", mmcore=self._mmc)
        self.corner_1.valueChanged.connect(self._update_info)
        self.corner_2 = _SpinboxWidget("corner2", mmcore=self._mmc)
        self.corner_2.valueChanged.connect(self._update_info)

        self.corner_1.label.setFixedWidth(self.corner_2.label.sizeHint().width() + 5)
        self.corner_2.label.setFixedWidth(self.corner_2.label.sizeHint().width() + 5)

        group_layout.addWidget(self.corner_1)
        group_layout.addWidget(self.corner_2)

        return group

    def _create_label_info(self) -> QGroupBox:
        group = QGroupBox()
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        group_layout = QHBoxLayout()
        group_layout.setSpacing(0)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group.setLayout(group_layout)

        self.info_lbl = QLabel()
        self.info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        group_layout.addWidget(self.info_lbl)

        return group

    def _create_move_to_widget(self) -> QGroupBox:
        group = QGroupBox()
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        group_layout = QHBoxLayout()
        group_layout.setSpacing(10)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group.setLayout(group_layout)

        lbl_policy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row_lbl = QLabel("Row:")
        row_lbl.setSizePolicy(lbl_policy)
        col_lbl = QLabel("Column:")
        col_lbl.setSizePolicy(lbl_policy)

        self._move_to_row = QComboBox()
        self._move_to_row.setEditable(True)
        self._move_to_row.lineEdit().setReadOnly(True)
        self._move_to_row.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._move_to_col = QComboBox()
        self._move_to_col.setEditable(True)
        self._move_to_col.lineEdit().setReadOnly(True)
        self._move_to_col.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._move_button = QPushButton("Go")
        self._move_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._move_button.clicked.connect(self._move_to_row_col)

        group_layout.addWidget(row_lbl)
        group_layout.addWidget(self._move_to_row)
        group_layout.addWidget(col_lbl)
        group_layout.addWidget(self._move_to_col)
        group_layout.addWidget(self._move_button)

        return group

    def _move_to_row_col(self) -> None:
        grid: AnyGridPlan = NoGrid()
        curr_x, curr_y = self._current_stage_pos

        if self.tab.currentIndex() == 0:
            if curr_x is None or curr_y is None:
                return
            grid = GridRelative(**self.value())
        else:
            grid = GridFromEdges(**self.value())

        _move_to_row = int(self._move_to_row.currentText())
        _move_to_col = int(self._move_to_col.currentText())

        row = _move_to_row - 1 if _move_to_row > 0 else 0
        col = _move_to_col - 1 if _move_to_col > 0 else 0

        _, _, width, height = self._mmc.getROI(self._mmc.getCameraDevice())
        width = int(width * self._mmc.getPixelSizeUm())
        height = int(height * self._mmc.getPixelSizeUm())

        for pos in grid.iter_grid_positions(width, height):
            if pos.row == row and pos.col == col:
                if isinstance(grid, GridRelative):
                    xpos = cast(float, curr_x) + pos.x
                    ypos = cast(float, curr_y) + pos.y
                else:
                    xpos = pos.x
                    ypos = pos.y
                self._mmc.setXYPosition(xpos, ypos)
                return

    def _create_add_button(self) -> QWidget:
        wdg = QWidget()
        wdg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        wdg_layout = QHBoxLayout()
        wdg_layout.setSpacing(20)
        wdg_layout.setContentsMargins(0, 0, 0, 0)
        wdg.setLayout(wdg_layout)

        spacer = QSpacerItem(
            5, 5, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        wdg_layout.addSpacerItem(spacer)

        self.add_button = QPushButton(text="Add Grid")
        self.add_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.add_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_button.clicked.connect(self._emit_grid_positions)
        wdg_layout.addWidget(self.add_button)

        return wdg

    def _update_info(self) -> None:
        if not self._mmc.getPixelSizeUm():
            self.info_lbl.setText(
                "Height: _ mm    Width: _ mm    (Rows: _    Columns: _)"
            )
            return

        _, _, width, height = self._mmc.getROI(self._mmc.getCameraDevice())
        width = int(width * self._mmc.getPixelSizeUm())
        height = int(height * self._mmc.getPixelSizeUm())
        overlap_percentage_x = self.overlap_spinbox_x.value()
        overlap_percentage_y = self.overlap_spinbox_y.value()
        overlap_x = width * overlap_percentage_x / 100
        overlap_y = height * overlap_percentage_y / 100

        if self.tab.currentIndex() == 0:
            rows = self.n_rows.value()
            cols = self.n_columns.value()
            x = ((width - overlap_x) * cols) / 1000
            y = ((height - overlap_y) * rows) / 1000
        else:
            top, bottom, left, right = self._get_edges()

            rows = math.ceil((abs(top - bottom) + height) / height)
            cols = math.ceil((abs(right - left) + width) / width)

            x = (abs(left - right) + width) / 1000
            y = (abs(top - bottom) + height) / 1000

        self.info_lbl.setText(
            f"Height: {round(y, 3)} mm    Width: {round(x, 3)} mm    "
            f"(Rows: {rows}    Columns: {cols})"
        )

        self._move_to_row.clear()
        self._move_to_row.addItems([str(r) for r in range(1, rows + 1)])
        self._move_to_col.clear()
        self._move_to_col.addItems([str(r) for r in range(1, cols + 1)])

    def _get_edges(self) -> tuple[int, int, int, int]:
        top = (
            self.top.spinbox.value()
            if self.tab.currentIndex() == 1
            else max(self.corner_1.y_spinbox.value(), self.corner_2.y_spinbox.value())
        )
        bottom = (
            self.bottom.spinbox.value()
            if self.tab.currentIndex() == 1
            else min(self.corner_1.y_spinbox.value(), self.corner_2.y_spinbox.value())
        )
        left = (
            self.left.spinbox.value()
            if self.tab.currentIndex() == 1
            else min(self.corner_1.x_spinbox.value(), self.corner_2.x_spinbox.value())
        )
        right = (
            self.right.spinbox.value()
            if self.tab.currentIndex() == 1
            else max(self.corner_1.x_spinbox.value(), self.corner_2.x_spinbox.value())
        )

        return top, bottom, left, right

    def value(self) -> GridDict:
        """Return the current GridPlan settings.

        Note that output dict will match the Channel from useq schema:
        <https://pymmcore-plus.github.io/useq-schema/schema/axes/#grid-plans>
        """
        value: GridDict = {
            "overlap": (
                self.overlap_spinbox_x.value(),
                self.overlap_spinbox_y.value(),
            ),
            "mode": self.ordermode_combo.currentText(),
        }
        if self.tab.currentIndex() == 0:
            value["rows"] = self.n_rows.value()
            value["columns"] = self.n_columns.value()
            value["relative_to"] = self.relative_combo.currentText()
        else:
            (
                value["top"],
                value["bottom"],
                value["left"],
                value["right"],
            ) = self._get_edges()

        return value

    def set_state(self, grid: dict | AnyGridPlan) -> None:
        """Set the state of the widget from a useq AnyGridPlan or dictionary."""
        grid_plan = MDASequence(grid_plan=grid).grid_plan

        over_x, over_y = grid_plan.overlap
        self.overlap_spinbox_x.setValue(over_x)
        self.overlap_spinbox_y.setValue(over_y)
        self.ordermode_combo.setCurrentText(grid_plan.mode.value)

        if isinstance(grid_plan, GridRelative):
            self.tab.setCurrentIndex(0)
            self.n_rows.setValue(grid_plan.rows)
            self.n_columns.setValue(grid_plan.columns)
            self.relative_combo.setCurrentText(grid_plan.relative_to.value)

        elif isinstance(grid_plan, GridFromEdges):
            self.tab.setCurrentIndex(1)
            self.top.spinbox.setValue(grid_plan.top)
            self.bottom.spinbox.setValue(grid_plan.bottom)
            self.left.spinbox.setValue(grid_plan.left)
            self.right.spinbox.setValue(grid_plan.right)

    def _emit_grid_positions(self) -> None:
        if self._mmc.getPixelSizeUm() <= 0:
            raise ValueError("Pixel Size Not Set.")

        if self.tab.currentIndex() > 0 and any(int(v) == 0 for v in self._get_edges()):
            self._show_warning()
        else:
            self.valueChanged.emit(self.value())

    def _show_warning(self) -> None:
        if self.tab.currentIndex() == 1:
            msg = (
                "Did you set all four 'top',  'bottom',  'left', and  'right'  "
                "positions?"
            )
        elif self.tab.currentIndex() == 2:
            msg = "Did you set the both corner positions?"

        msgBox = QMessageBox()
        msgBox.setIcon(QMessageBox.Warning)
        msgBox.setText(msg)
        msgBox.setWindowTitle("Grid from Edges")
        msgBox.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)

        if msgBox.exec() == QMessageBox.Ok:
            self.valueChanged.emit(self.value())

    def _disconnect(self) -> None:
        self._mmc.events.systemConfigurationLoaded.disconnect(self._update_info)
        self._mmc.events.pixelSizeChanged.disconnect(self._update_info)
