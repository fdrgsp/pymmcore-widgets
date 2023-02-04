from __future__ import annotations

import math
from typing import TYPE_CHECKING

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
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from useq import AnyGridPlan  # type: ignore
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
        top: float  # top_left y
        left: float  # top_left x
        bottom: float  # bottom_right y
        right: float  # bottom_right x


fixed_sizepolicy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class _EdgeSpinbox(QWidget):

    valueChanged = Signal()

    def __init__(
        self, label: str, parent: QWidget | None = None, *, mmcore: CMMCorePlus
    ) -> None:
        super().__init__(parent)

        self._mmc = mmcore
        self._label = label

        layout = QHBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        self.label = QLabel(text=f"{self._label}:")
        self.label.setSizePolicy(fixed_sizepolicy)

        self.spinbox = self._doublespinbox()

        self.set_button = QPushButton(text="Set")
        self.set_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.set_button.setMinimumWidth(75)
        self.set_button.setSizePolicy(fixed_sizepolicy)
        self.set_button.clicked.connect(self._on_click)

        layout.addWidget(self.label)
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
        if self._label in {"top", "bottom"}:
            self.spinbox.setValue(self._mmc.getYPosition())
        elif self._label in {"left", "right"}:
            self.spinbox.setValue(self._mmc.getXPosition())


class GridWidget(QDialog):
    """A subwidget to setup the acquisition of a grid of images."""

    valueChanged = Signal(object)

    def __init__(
        self, parent: QWidget | None = None, *, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent=parent)

        self._mmc = mmcore or CMMCorePlus.instance()

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        self.setLayout(layout)

        tab = self._create_tab()
        layout.addWidget(tab)

        overlap_and_size = self._create_overlap_and_ordermode()
        layout.addWidget(overlap_and_size)

        label_info = self._create_label_info()
        layout.addWidget(label_info)

        button = self._create_add_button()
        layout.addWidget(button)

        self.setFixedHeight(self.sizeHint().height())

        self._update_info_label()

        self._mmc.events.systemConfigurationLoaded.connect(self._update_info_label)

        self.destroyed.connect(self._disconnect)

    def _create_tab(self) -> QWidget:
        wdg = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        wdg.setLayout(layout)
        self.tab = QTabWidget()
        self.tab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        rc = self._create_row_cols_wdg()
        self.tab.addTab(rc, "Rows x Columns")

        cr = self._create_edges_grid_wdg()
        self.tab.addTab(cr, "Grid from Edges")

        layout.addWidget(self.tab)

        self.tab.currentChanged.connect(self._update_info_label)
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
        spin.valueChanged.connect(self._update_info_label)
        layout.addWidget(label)
        layout.addWidget(spin)
        return spin

    def _create_edges_grid_wdg(self) -> QWidget:
        group = QWidget()
        group_layout = QGridLayout()
        group_layout.setSpacing(10)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group.setLayout(group_layout)

        self.top = _EdgeSpinbox("top", mmcore=self._mmc)
        self.top.valueChanged.connect(self._update_info_label)
        self.bottom = _EdgeSpinbox("bottom", mmcore=self._mmc)
        self.bottom.valueChanged.connect(self._update_info_label)
        self.top.label.setMinimumWidth(self.bottom.label.sizeHint().width())
        self.left = _EdgeSpinbox("left", mmcore=self._mmc)
        self.left.valueChanged.connect(self._update_info_label)
        self.right = _EdgeSpinbox("right", mmcore=self._mmc)
        self.right.valueChanged.connect(self._update_info_label)

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
        spin.valueChanged.connect(self._update_info_label)
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

    def _create_label_info(self) -> QGroupBox:
        group = QGroupBox()
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        group_layout = QHBoxLayout()
        group_layout.setSpacing(0)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group.setLayout(group_layout)

        self.info_lbl = QLabel(text="Width: _ mm    Height: _ mm")
        self.info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        group_layout.addWidget(self.info_lbl)

        return group

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

    def _update_info_label(self) -> None:
        if not self._mmc.getPixelSizeUm():
            self.info_lbl.setText("Width: _ mm    Height: _ mm")
            return

        px_size = self._mmc.getPixelSizeUm()
        _, _, width, height = self._mmc.getROI(self._mmc.getCameraDevice())
        overlap_percentage_x = self.overlap_spinbox_x.value()
        overlap_percentage_y = self.overlap_spinbox_y.value()
        overlap_x = width * overlap_percentage_x / 100
        overlap_y = height * overlap_percentage_y / 100

        if self.tab.currentIndex() == 0:  # rows and cols
            rows = self.n_rows.value()
            cols = self.n_columns.value()
        else:  # corners
            total_width = (
                abs(self.left.spinbox.value() - self.right.spinbox.value()) + width
            )
            total_height = (
                abs(self.top.spinbox.value() - self.bottom.spinbox.value()) + height
            )
            rows = math.ceil(total_width / width) if total_width > width else 1
            cols = math.ceil(total_height / height) if total_height > height else 1

        x = ((width - overlap_x) * cols) * px_size / 1000
        y = ((height - overlap_y) * rows) * px_size / 1000

        self.info_lbl.setText(f"Width: {round(x, 3)} mm    Height: {round(y, 3)} mm")

    def value(self) -> GridDict:
        # TODO: update docstring when useq GridPlan will be added to the docs.
        """Return the current GridPlan settings."""
        value: GridDict = {
            "overlap": (
                self.overlap_spinbox_x.value(),
                self.overlap_spinbox_y.value(),
            ),
            "mode": self.ordermode_combo.currentText(),
        }
        if self.tab.currentIndex() == 0:  # rows and cols
            value["rows"] = self.n_rows.value()
            value["columns"] = self.n_columns.value()
            value["relative_to"] = self.relative_combo.currentText()
        else:  # corners
            value["top"] = self.top.spinbox.value()
            value["bottom"] = self.bottom.spinbox.value()
            value["left"] = self.left.spinbox.value()
            value["right"] = self.right.spinbox.value()

        return value

    def set_state(self, grid: AnyGridPlan | GridDict) -> None:
        """Set the state of the widget from a useq AnyGridPlan or dictionary."""
        if isinstance(grid, AnyGridPlan):
            grid = grid.dict()

        overlap = grid.get("overlap") or 0.0
        over_x, over_y = overlap if isinstance(overlap, tuple) else (overlap, overlap)
        self.overlap_spinbox_x.setValue(over_x)
        self.overlap_spinbox_y.setValue(over_y)

        ordermode = grid.get("order_mode") or OrderMode.row_wise_snake
        ordermode = ordermode.value if isinstance(ordermode, OrderMode) else ordermode
        self.ordermode_combo.setCurrentText(ordermode)

        try:
            self._set_relative_wdg(grid)
            self.tab.setCurrentIndex(0)
        except TypeError:
            self._set_corner_wdg(grid)
            self.tab.setCurrentIndex(1)

    def _set_relative_wdg(self, grid: GridDict) -> None:
        self.n_rows.setValue(grid.get("rows"))
        self.n_columns.setValue(grid.get("cols"))
        relative = grid.get("relative_to")
        relative = (
            relative.value if isinstance(relative, RelativeTo) else relative or "center"
        )
        self.relative_combo.setCurrentText(relative)

    def _set_corner_wdg(self, grid: GridDict) -> None:
        self.top.spinbox.setValue(grid["top"])
        self.bottom.spinbox.setValue(grid["bottom"])
        self.left.spinbox.setValue(grid["left"])
        self.right.spinbox.setValue(grid["right"])

    def _emit_grid_positions(self) -> AnyGridPlan:
        if self._mmc.getPixelSizeUm() <= 0:
            raise ValueError("Pixel Size Not Set.")
        self.valueChanged.emit(self.value())

    def _disconnect(self) -> None:
        self._mmc.events.systemConfigurationLoaded.disconnect(self._update_info_label)
