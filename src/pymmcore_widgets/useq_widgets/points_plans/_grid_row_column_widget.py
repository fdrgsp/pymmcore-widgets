from __future__ import annotations

from typing import TYPE_CHECKING

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from useq import GridRowsColumns, OrderMode

from ._common import LabelAligner, OverlapWidget, labeled_row

if TYPE_CHECKING:
    from collections.abc import Mapping


class GridRowColumnWidget(QWidget):
    """Widget to generate a grid of FOVs within a specified area."""

    valueChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.fov_width: float | None = None
        self.fov_height: float | None = None
        self._relative_to: str = "center"

        # title
        self._title = title = QLabel(text="Fields of View in a Grid")
        title.setStyleSheet("font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # rows
        self.rows = QSpinBox()
        self.rows.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rows.setMinimum(1)
        self.rows.setValue(3)
        # columns
        self.columns = QSpinBox()
        self.columns.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.columns.setMinimum(1)
        self.columns.setValue(3)
        # overlap: one value for x and y (right-click for separate x and y)
        self.overlap_wdg = OverlapWidget(-10000, 100)
        self.overlap_x = self.overlap_wdg.overlap_x
        self.overlap_y = self.overlap_wdg.overlap_y
        # order combo
        self.mode = QComboBox()
        self.mode.addItems([mode.value for mode in OrderMode])
        self.mode.setCurrentText(OrderMode.row_wise_snake.value)

        # label/field rows: the overlap/order rows are in their own widget, so that
        # containers sharing those settings across plans can hide them
        self._labels: list[QLabel] = []
        self._common = QWidget()
        common_layout = QVBoxLayout(self._common)
        common_layout.setContentsMargins(0, 0, 0, 0)
        common_layout.setSpacing(5)
        common_layout.addLayout(
            labeled_row("Overlap (%):", self.overlap_wdg, self._labels)
        )
        common_layout.addLayout(labeled_row("Grid Order:", self.mode, self._labels))

        # main
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.addWidget(title)
        main_layout.addLayout(labeled_row("Rows:", self.rows, self._labels))
        main_layout.addLayout(labeled_row("Columns:", self.columns, self._labels))
        main_layout.addWidget(self._common)
        self._label_aligner = LabelAligner(self, self._labels)

        # connect
        self.rows.valueChanged.connect(self._on_value_changed)
        self.columns.valueChanged.connect(self._on_value_changed)
        self.overlap_wdg.valueChanged.connect(self._on_value_changed)
        self.mode.currentTextChanged.connect(self._on_value_changed)

    def labels(self) -> list[QLabel]:
        """Return the row labels (e.g. to align them with other widgets')."""
        return list(self._labels)

    def setEmbedded(self, embedded: bool) -> None:
        """Hide the title and the overlap/order controls (owned by a container)."""
        self._title.setVisible(not embedded)
        self._common.setVisible(not embedded)
        margin = 0 if embedded else 10
        self.layout().setContentsMargins(margin, margin, margin, margin)

    def _on_value_changed(self) -> None:
        """Emit the valueChanged signal."""
        self.valueChanged.emit(self.value())

    @property
    def overlap(self) -> tuple[float, float]:
        """Return the overlap along x and y."""
        return self.overlap_x.value(), self.overlap_y.value()

    @property
    def fov_size(self) -> tuple[float | None, float | None]:
        """Return the FOV size in (width, height)."""
        return self.fov_width, self.fov_height

    @fov_size.setter
    def fov_size(self, size: tuple[float | None, float | None]) -> None:
        """Set the FOV size."""
        self.fov_width, self.fov_height = size

    def value(self) -> GridRowsColumns:
        """Return the values of the widgets."""
        return GridRowsColumns(
            rows=self.rows.value(),
            columns=self.columns.value(),
            overlap=self.overlap,
            mode=self.mode.currentText(),
            fov_width=self.fov_width,
            fov_height=self.fov_height,
            relative_to=self._relative_to,
        )

    def setValue(self, value: GridRowsColumns | Mapping) -> None:
        """Set the values of the widgets."""
        value = GridRowsColumns.model_validate(value)
        self.rows.setValue(value.rows)
        self.columns.setValue(value.columns)
        self.overlap_wdg.setValue(value.overlap)
        self.mode.setCurrentText(value.mode.value)
        self.fov_width = value.fov_width
        self.fov_height = value.fov_height
        self._relative_to = value.relative_to.value

    def reset(self) -> None:
        """Reset value to 1x1, row-wise-snake, with 0 overlap."""
        self.rows.setValue(1)
        self.columns.setValue(1)
        self.overlap_wdg.setValue((0, 0))
        self.mode.setCurrentText(OrderMode.row_wise_snake.value)
        self.fov_size = (None, None)
