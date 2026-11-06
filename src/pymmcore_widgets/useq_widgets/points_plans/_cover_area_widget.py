from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked
from useq import GridFromShape, GridRowsColumns, OrderMode, Shape

from ._common import LabelAligner, OverlapWidget, labeled_row
from ._grid_row_column_widget import GridRowColumnWidget

if TYPE_CHECKING:
    from collections.abc import Mapping

# relative tolerance when testing whether an area fits inside the well
_FIT_TOL = 1e-9
COVERAGE_TAB, GRID_TAB = 0, 1
# overlap range of the grid tab (negative overlap spaces the FOVs apart); coverage
# requires overlap >= 0 so that the area is actually covered
_MIN_GRID_OVERLAP, _MAX_OVERLAP = -10000.0, 99.0
# maximum area size (µm) when the well size is unknown
_MAX_AREA = 1e6
# area used when the well size is unknown
_DEFAULT_AREA = (Shape.ELLIPSE, 1000.0, 1000.0)


def area_fits_in_well(
    area: tuple[Shape, float, float], well: tuple[Shape, float, float]
) -> bool:
    """Return True if a centered (shape, width, height) area fits inside the well.

    Both are centered at the origin and share the same axes. The test depends on the
    shapes: e.g. a rectangle as wide as a circular well does not fit in the circle.
    """
    a_shape, aw, ah = area
    w_shape, ww, wh = well
    tol = 1 + _FIT_TOL
    # Any centered area fits in a rectangle iff its bounding box does, and a centered
    # axis-aligned ellipse fits in another one iff both of its radii do.
    if w_shape == Shape.RECTANGLE or a_shape == Shape.ELLIPSE:
        return aw <= ww * tol and ah <= wh * tol
    # rectangle in an ellipse: its corners must be inside the ellipse
    return (aw / ww) ** 2 + (ah / wh) ** 2 <= tol


class CoverAreaWidget(QWidget):
    """Widget to tile the well with FOVs, either covering an area or as a grid.

    Two tabs define the tiles; the overlap and scan order below them are shared:

    - **Coverage** (`useq.GridFromShape`): cover a centered area of the well. The
      area starts as the full well (see `setWellGeometry`) and cannot be larger than
      it. The number of FOVs derives from the area, the FOV size and the overlap.
      With "Keep FOVs Inside Area" checked, only FOVs entirely inside the area are
      kept (`placement="inside"`): its boundary may then be left unimaged.
    - **Grid** (`useq.GridRowsColumns`): a grid with a given number of rows and
      columns (the embedded `grid_wdg`).
    """

    valueChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # NON-GUI attributes
        self._fov_size: tuple[float | None, float | None] = (None, None)
        # (shape, width µm, height µm) of the well; None when unknown
        self._well: tuple[Shape, float, float] | None = None
        # (shape, width µm, height µm) of the area to cover
        self._area: tuple[Shape, float, float] = _DEFAULT_AREA
        self._rotation: float = 0.0
        self._error: str = ""

        # title
        title = QLabel(text="Cover Area")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # COVERAGE TAB -----------------------------------------------------

        self.area_width = _spin(1, _MAX_AREA, "Area width (µm)")
        self.area_height = _spin(1, _MAX_AREA, "Area height (µm)")
        self.shape = QComboBox()
        self.shape.addItems([s.value for s in Shape])
        self.shape.setAccessibleName("Area shape")

        # label/field rows (labels share one fixed width, see LabelAligner)
        self._labels: list[QLabel] = []
        coverage_tab = QWidget()
        coverage_layout = QVBoxLayout(coverage_tab)
        coverage_layout.setContentsMargins(6, 4, 6, 4)
        coverage_layout.setSpacing(4)
        for text, field in (
            ("Width (µm):", self.area_width),
            ("Height (µm):", self.area_height),
            ("Shape:", self.shape),
        ):
            coverage_layout.addLayout(labeled_row(text, field, self._labels))
        coverage_layout.addStretch()

        # GRID TAB ---------------------------------------------------------

        self.grid_wdg = GridRowColumnWidget()
        self.grid_wdg.setEmbedded(True)
        grid_tab = QWidget()
        grid_layout = QVBoxLayout(grid_tab)
        grid_layout.setContentsMargins(6, 4, 6, 4)
        grid_layout.addWidget(self.grid_wdg)
        grid_layout.addStretch()

        self.tabs = QTabWidget()
        self.tabs.addTab(coverage_tab, "Coverage")
        self.tabs.addTab(grid_tab, "Grid")
        self.tabs.setTabToolTip(COVERAGE_TAB, "Cover the well, or an area of it.")
        self.tabs.setTabToolTip(GRID_TAB, "A grid with a set number of rows/columns.")

        # SHARED SETTINGS --------------------------------------------------

        # one overlap for x and y; right-click it to set separate x and y overlaps
        self.overlap_wdg = OverlapWidget(0, _MAX_OVERLAP)
        self.overlap_x = self.overlap_wdg.overlap_x
        self.overlap_y = self.overlap_wdg.overlap_y
        self.mode = QComboBox()
        self.mode.addItems([mode.value for mode in OrderMode])
        self.mode.setCurrentText(OrderMode.row_wise_snake.value)
        self.mode.setAccessibleName("Scan order")
        self.inside_area = QCheckBox("Keep FOVs Inside Area")
        self.inside_area.setToolTip(
            "Only use FOVs that are entirely inside the area (e.g. the well): the "
            "area boundary may be left unimaged.\nWhen unchecked, the whole area is "
            "covered and edge FOVs can extend outside of it."
        )
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("Cover Area error")
        self.error_label.hide()

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(4)
        main_layout.setContentsMargins(6, 4, 6, 4)
        main_layout.addWidget(title)
        main_layout.addWidget(self.tabs)
        main_layout.addLayout(
            labeled_row("Overlap (%):", self.overlap_wdg, self._labels)
        )
        main_layout.addLayout(labeled_row("Scan Order:", self.mode, self._labels))
        main_layout.addWidget(self.inside_area)
        main_layout.addWidget(self.error_label)
        self._label_aligner = LabelAligner(self, self.labels())

        # CONNECTIONS ------------------------------------------------------

        self.shape.currentTextChanged.connect(self._on_area_edited)
        self.area_width.valueChanged.connect(self._on_area_edited)
        self.area_height.valueChanged.connect(self._on_area_edited)
        self.overlap_wdg.valueChanged.connect(self._on_common_changed)
        self.mode.currentTextChanged.connect(self._on_common_changed)
        self.inside_area.toggled.connect(self._on_value_changed)
        self.grid_wdg.valueChanged.connect(self._on_value_changed)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self._sync_common_to_grid()
        self._update_area_controls()
        self._update_status()

    # _________________________PUBLIC METHODS_________________________ #

    def labels(self) -> list[QLabel]:
        """Return the row labels, including the Grid tab's ones."""
        return [*self._labels, *self.grid_wdg.labels()]

    def planType(self) -> type[GridFromShape] | type[GridRowsColumns]:
        """Return the type of plan of the current tab."""
        if self.tabs.currentIndex() == GRID_TAB:
            return GridRowsColumns
        return GridFromShape

    @property
    def fov_size(self) -> tuple[float | None, float | None]:
        """Return the FOV size (width, height) in µm."""
        return self._fov_size

    @fov_size.setter
    def fov_size(self, size: tuple[float | None, float | None]) -> None:
        """Set the FOV size (width, height) in µm (does not emit valueChanged)."""
        self._fov_size = (size[0] or None, size[1] or None)
        self.grid_wdg.fov_size = self._fov_size
        self._update_status()

    @property
    def overlap(self) -> tuple[float, float]:
        """Return the overlap along x and y, in percent."""
        return self.overlap_x.value(), self.overlap_y.value()

    def setWellGeometry(
        self, shape: Shape | str | None, width_um: float | None, height_um: float | None
    ) -> None:
        """Set the well shape and size (in µm).

        A new well geometry resets the area to the full well, and bounds its size.
        Setting the same geometry again (e.g. when only the selected wells change)
        preserves all user input. Passing None for any argument clears the well
        geometry.
        """
        well = None
        if shape is not None and width_um and height_um:
            well = (Shape(shape), float(width_um), float(height_um))
        if well == self._well:
            return
        self._well = well
        if well is not None:
            self._area = well
        self._update_area_controls()
        self._on_value_changed()

    def wellGeometry(self) -> tuple[Shape, float, float] | None:
        """Return the (shape, width µm, height µm) of the well, if set."""
        return self._well

    def setRotation(self, rotation: float | None) -> None:
        """Set the rotation (degrees) of the covered area, e.g. the plate rotation."""
        rotation = float(rotation or 0)
        if rotation != self._rotation:
            self._rotation = rotation
            self._on_value_changed()

    def errorMessage(self) -> str:
        """Return why the current settings cannot produce a plan ('' if valid)."""
        return self._error

    def isValid(self) -> bool:
        """Return True if the current settings produce a valid plan."""
        return not self._error

    def value(self) -> GridFromShape | GridRowsColumns:
        """Return the plan for the current tab."""
        if self.tabs.currentIndex() == GRID_TAB:
            return self.grid_wdg.value()
        shape, width, height = self._area
        kwargs: dict[str, Any] = {"rotation": self._rotation} if self._rotation else {}
        if self.inside_area.isChecked():
            kwargs["placement"] = "inside"
        fov_w, fov_h = self._fov_size
        return GridFromShape(
            shape=shape,
            width=width,
            height=height,
            overlap=self.overlap,
            mode=self.mode.currentText(),
            fov_width=fov_w,
            fov_height=fov_h,
            **kwargs,
        )

    def setValue(self, value: GridFromShape | GridRowsColumns | Mapping) -> None:
        """Set the widget from a coverage or grid plan, selecting the matching tab.

        A coverage area larger than the well is clamped to the well size.
        """
        if not isinstance(value, (GridFromShape, GridRowsColumns)):
            value = (
                GridFromShape.model_validate(value)
                if "shape" in value
                else GridRowsColumns.model_validate(value)
            )
        with signals_blocked(self):
            if isinstance(value, GridRowsColumns):
                self.tabs.setCurrentIndex(GRID_TAB)
                self.grid_wdg.setValue(value)
            else:
                self.tabs.setCurrentIndex(COVERAGE_TAB)
                self._area = (value.shape, value.width, value.height)
                self._rotation = value.rotation
                with signals_blocked(self.inside_area):
                    self.inside_area.setChecked(value.placement == "inside")
                self._update_area_controls()
            self._update_tab_controls()
            with signals_blocked(self.overlap_wdg):
                self.overlap_wdg.setValue(value.overlap)
            with signals_blocked(self.mode):
                self.mode.setCurrentText(value.mode.value)
            self._sync_common_to_grid()
            self._fov_size = (value.fov_width, value.fov_height)
            self.grid_wdg.fov_size = self._fov_size
        self._on_value_changed()

    # _________________________PRIVATE METHODS_________________________ #

    def _on_area_edited(self) -> None:
        self._area = (
            Shape(self.shape.currentText()),
            self.area_width.value(),
            self.area_height.value(),
        )
        self._on_value_changed()

    def _on_common_changed(self) -> None:
        self._sync_common_to_grid()
        self._on_value_changed()

    def _sync_common_to_grid(self) -> None:
        """Push the shared overlap/order into the grid widget."""
        with signals_blocked(self.grid_wdg):
            self.grid_wdg.overlap_wdg.setValue(self.overlap_wdg.value())
            self.grid_wdg.mode.setCurrentText(self.mode.currentText())

    def _on_tab_changed(self, index: int) -> None:
        self._update_tab_controls()
        self._on_value_changed()

    def _update_tab_controls(self) -> None:
        """Update the shared controls that depend on the current tab."""
        grid = self.tabs.currentIndex() == GRID_TAB
        # coverage needs overlap >= 0, a grid can space its FOVs apart. Changing the
        # minimum clamps (visibly) a negative grid overlap.
        self.overlap_wdg.setMinimum(_MIN_GRID_OVERLAP if grid else 0.0)
        self._sync_common_to_grid()
        # only coverage plans can keep the FOVs inside the area
        self.inside_area.setEnabled(not grid)

    def _update_area_controls(self) -> None:
        """Show the area, bounded by (and clamped to) the well size.

        Whether the area fits the well's *shape* (a rectangle in a round well) is
        validated separately.
        """
        max_w, max_h = self._well[1:] if self._well else (_MAX_AREA, _MAX_AREA)
        shape, width, height = self._area
        self._area = (shape, min(width, max_w), min(height, max_h))
        with (
            signals_blocked(self.shape),
            signals_blocked(self.area_width),
            signals_blocked(self.area_height),
        ):
            self.area_width.setMaximum(max_w)
            self.area_height.setMaximum(max_h)
            self.shape.setCurrentText(self._area[0].value)
            self.area_width.setValue(self._area[1])
            self.area_height.setValue(self._area[2])

    def _on_value_changed(self) -> None:
        self._update_status()
        self.valueChanged.emit(self.value())

    def _coverage_error(self) -> str:
        fov_w, fov_h = self._fov_size
        if (
            not fov_w
            or not fov_h
            or not (math.isfinite(fov_w) and math.isfinite(fov_h))
        ):
            return (
                "The FOV size is unknown: calibrate the pixel size (or set the FOV "
                "size) to cover the area."
            )
        if self._well is not None and not area_fits_in_well(self._area, self._well):
            shape, ww, wh = self._well
            return (
                f"The area does not fit inside the {ww:g} x {wh:g} µm {shape.value} "
                "well: reduce its size or change its shape."
            )
        try:
            if self.value().num_positions() == 0:
                return (
                    "No FOV fits entirely inside the area: enlarge the area, or "
                    "uncheck 'Keep FOVs Inside Area'."
                )
        except ValueError as e:
            return f"{e} Increase the FOV size or reduce the area."
        return ""

    def _update_status(self) -> None:
        """Validate the settings and update the error label."""
        if self.tabs.currentIndex() == GRID_TAB:
            self._error = ""
        else:
            self._error = self._coverage_error()
        self.error_label.setText(self._error)
        self.error_label.setVisible(bool(self._error))


def _spin(low: float, high: float, name: str) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
    spin.setRange(low, high)
    spin.setDecimals(1)
    spin.setStepType(QDoubleSpinBox.StepType.AdaptiveDecimalStepType)
    spin.setAccessibleName(name)
    spin.setToolTip(name)
    return spin
