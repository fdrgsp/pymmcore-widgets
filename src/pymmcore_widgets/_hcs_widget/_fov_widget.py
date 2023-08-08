from __future__ import annotations

import contextlib
import math
import random
import time
import warnings
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QRectF, Qt
from qtpy.QtGui import QPen
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDoubleSpinBox,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked

from pymmcore_widgets._util import FOV_GRAPHICS_VIEW_SIZE

from ._graphics_items import _FOVPoints, _WellArea

if TYPE_CHECKING:
    from ._well_plate_model import WellPlate

AlignCenter = Qt.AlignmentFlag.AlignCenter
CENTER = "Center"
RANDOM = "Random"
GRID = "Grid"
CENTER_TAB_INDEX = 0
RANDOM_TAB_INDEX = 1
GRID_TAB_INDEX = 2
FOV_SCENE_MIN = 160
FOV_SCENE_MAX = 180
PEN_WIDTH = 4


class Center(NamedTuple):
    """Center of the well as FOV of the plate."""

    x: float
    y: float


class Random(NamedTuple):
    """Random FOVs per well of the plate."""

    area_x: float
    area_y: float
    nFOV: int


class Grid(NamedTuple):
    """Grid FOV per well of the plate."""

    rows: int
    cols: int
    overlap_x: float
    overlap_y: float
    order: OrderMode


class OrderModeInfo(NamedTuple):
    """Info about the `OrderMode`."""

    name: str
    snake: bool
    row_wise: bool


class OrderMode(Enum):
    """Different ways of ordering the grid positions."""

    row_wise = OrderModeInfo("row_wise", False, True)
    column_wise = OrderModeInfo("column_wise", False, False)
    row_wise_snake = OrderModeInfo("row_wise_snake", True, True)
    column_wise_snake = OrderModeInfo("column_wise_snake", True, False)

    def __repr__(self) -> str:
        return self.value.name


def _create_label(label_text: str) -> QLabel:
    """Create a QLabel with fixed QSizePolicy."""
    lbl = QLabel()
    lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    lbl.setText(label_text)
    return lbl


def _make_wdg_with_label(label: QLabel, wdg: QWidget) -> QWidget:
    """Create a QWidget with a QHBoxLayout with the given label and widget."""
    widget = QWidget()
    widget.setLayout(QHBoxLayout())
    widget.layout().setContentsMargins(0, 0, 0, 0)
    widget.layout().setSpacing(5)
    widget.layout().addWidget(label)
    widget.layout().addWidget(wdg)
    return widget


class _CenterFOVWidget(QWidget):
    """Widget to select the center of the well as FOV of the plate."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # well area doublespinbox along x
        self.plate_area_center_x = QDoubleSpinBox()
        self.plate_area_center_x.setEnabled(False)
        self.plate_area_center_x.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        self.plate_area_center_x.setAlignment(AlignCenter)
        self.plate_area_center_x.setMinimum(1)
        plate_area_label_x = _create_label("Area x (mm):")
        _plate_area_x = _make_wdg_with_label(
            plate_area_label_x, self.plate_area_center_x
        )
        # well area doublespinbox along x
        self.plate_area_center_y = QDoubleSpinBox()
        self.plate_area_center_y.setEnabled(False)
        self.plate_area_center_y.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        self.plate_area_center_y.setAlignment(AlignCenter)
        self.plate_area_center_y.setMinimum(1)
        plate_area_label_y = _create_label("Area y (mm):")
        _plate_area_y = _make_wdg_with_label(
            plate_area_label_y, self.plate_area_center_y
        )
        # fov spinbox
        nFOV_spin = QSpinBox()
        nFOV_spin.setEnabled(False)
        nFOV_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        nFOV_spin.setAlignment(AlignCenter)
        nFOV_spin.setValue(1)
        nFOV_label = _create_label("FOVs:")
        nFOV = _make_wdg_with_label(nFOV_label, nFOV_spin)
        # add widgets layout
        wdg = QWidget()
        wdg.setLayout(QVBoxLayout())
        wdg.layout().setSpacing(0)
        wdg.layout().setContentsMargins(10, 10, 10, 10)
        wdg.layout().addWidget(_plate_area_x)
        wdg.layout().addWidget(_plate_area_y)
        wdg.layout().addWidget(nFOV)

        # set labels sizes
        for lbl in (plate_area_label_x, plate_area_label_y, nFOV_label):
            lbl.setMinimumWidth(plate_area_label_x.sizeHint().width())

        # main
        self.setLayout(QVBoxLayout())
        self.layout().setSpacing(0)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(wdg)

    def value(self) -> Center:
        """Return the values of the widgets."""
        # x and y are the center of the well in view pixels
        return Center(x=FOV_GRAPHICS_VIEW_SIZE / 2, y=FOV_GRAPHICS_VIEW_SIZE / 2)


class _RandomFOVWidget(QWidget):
    """Widget to select random FOVVs per well of the plate."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.plate_area_x = QDoubleSpinBox()
        self.plate_area_x.setAlignment(AlignCenter)
        self.plate_area_x.setMinimum(0.01)
        self.plate_area_x.setSingleStep(0.1)
        plate_area_label_x = _create_label("Area x (mm):")
        _plate_area_x = _make_wdg_with_label(plate_area_label_x, self.plate_area_x)

        self.plate_area_y = QDoubleSpinBox()
        self.plate_area_y.setAlignment(AlignCenter)
        self.plate_area_y.setMinimum(0.01)
        self.plate_area_y.setSingleStep(0.1)
        plate_area_label_y = _create_label("Area y (mm):")
        _plate_area_y = _make_wdg_with_label(plate_area_label_y, self.plate_area_y)

        self.number_of_FOV = QSpinBox()
        self.number_of_FOV.setAlignment(AlignCenter)
        self.number_of_FOV.setMinimum(1)
        self.number_of_FOV.setMaximum(100)
        number_of_FOV_label = _create_label("FOVs:")
        nFOV = _make_wdg_with_label(number_of_FOV_label, self.number_of_FOV)

        spacer = QSpacerItem(0, 10, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.random_button = QPushButton(text="Generate Random FOV(s)")

        # add widgets to wdg layout
        wdg = QWidget()
        wdg.setLayout(QVBoxLayout())
        wdg.layout().setSpacing(5)
        wdg.layout().setContentsMargins(10, 10, 10, 10)
        wdg.layout().addWidget(_plate_area_x)
        wdg.layout().addWidget(_plate_area_y)
        wdg.layout().addWidget(nFOV)
        wdg.layout().addItem(spacer)
        wdg.layout().addWidget(self.random_button)

        # set labels sizes
        for lbl in (plate_area_label_x, plate_area_label_y, number_of_FOV_label):
            lbl.setMinimumWidth(plate_area_label_x.sizeHint().width())

        # main
        self.setLayout(QVBoxLayout())
        self.layout().setSpacing(0)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(wdg)

    def value(self) -> Random:
        """Return the values of the widgets."""
        return Random(
            area_x=self.plate_area_x.value(),
            area_y=self.plate_area_y.value(),
            nFOV=self.number_of_FOV.value(),
        )

    def setValue(self, value: Random) -> None:
        """Set the values of the widgets."""
        self.plate_area_x.setValue(value.area_x)
        self.plate_area_y.setValue(value.area_y)
        self.number_of_FOV.setValue(value.nFOV)


class _GridFovWidget(QWidget):
    """Widget to select a grid FOV per well of the plate."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.rows = QSpinBox()
        self.rows.setAlignment(AlignCenter)
        self.rows.setMinimum(1)
        rows_lbl = _create_label("Rows:")
        _rows = _make_wdg_with_label(rows_lbl, self.rows)

        self.cols = QSpinBox()
        self.cols.setAlignment(AlignCenter)
        self.cols.setMinimum(1)
        cols_lbl = _create_label("Columns:")
        _cols = _make_wdg_with_label(cols_lbl, self.cols)

        self.overlap_x = QDoubleSpinBox()
        self.overlap_x.setAlignment(AlignCenter)
        self.overlap_x.setMinimum(-10000)
        self.overlap_x.setMaximum(100)
        self.overlap_x.setSingleStep(1.0)
        self.overlap_x.setValue(0)
        overlap_x_lbl = _create_label("Overlap x (%):")
        _overlap_x = _make_wdg_with_label(overlap_x_lbl, self.overlap_x)

        self.overlap_y = QDoubleSpinBox()
        self.overlap_y.setAlignment(AlignCenter)
        self.overlap_y.setMinimum(-10000)
        self.overlap_y.setMaximum(100)
        self.overlap_y.setSingleStep(1.0)
        self.overlap_y.setValue(0)
        spacing_y_lbl = _create_label("Overlap y (%):")
        _overlap_y = _make_wdg_with_label(spacing_y_lbl, self.overlap_y)

        self.order_combo = QComboBox()
        self.order_combo.addItems([mode.value.name for mode in OrderMode])
        self.order_combo.setCurrentText(OrderMode.row_wise_snake.value.name)
        order_combo_lbl = _create_label("Grid Order:")
        _order_combo = _make_wdg_with_label(order_combo_lbl, self.order_combo)

        # add widgets to wdg layout
        wdg = QWidget()
        wdg.setLayout(QVBoxLayout())
        wdg.layout().setSpacing(5)
        wdg.layout().setContentsMargins(10, 10, 10, 10)
        wdg.layout().addWidget(_rows)
        wdg.layout().addWidget(_cols)
        wdg.layout().addWidget(_overlap_x)
        wdg.layout().addWidget(_overlap_y)
        wdg.layout().addWidget(_order_combo)

        # set labels sizes
        for lbl in (
            rows_lbl,
            cols_lbl,
            overlap_x_lbl,
            spacing_y_lbl,
            order_combo_lbl,
        ):
            lbl.setMinimumWidth(overlap_x_lbl.sizeHint().width())

        # main
        self.setLayout(QVBoxLayout())
        self.layout().setSpacing(0)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(wdg)

    def value(self) -> Grid:
        """Return the values of the widgets."""
        return Grid(
            rows=self.rows.value(),
            cols=self.cols.value(),
            overlap_x=self.overlap_x.value(),
            overlap_y=self.overlap_y.value(),
            order=OrderMode[self.order_combo.currentText()],
        )

    def setValue(self, value: Grid) -> None:
        """Set the values of the widgets."""
        self.rows.setValue(value.rows)
        self.cols.setValue(value.cols)
        self.overlap_x.setValue(value.overlap_x)
        self.overlap_y.setValue(value.overlap_y)
        self.order_combo.setCurrentText(value.order.value.name)


class _FOVSelectrorWidget(QWidget):
    """Widget to select the FOVVs per well of the plate."""

    def __init__(
        self, parent: QWidget | None = None, *, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent=parent)

        self._mmc = mmcore or CMMCorePlus.instance()

        self._scene_width_px: float = 0.0
        self._scene_height_px: float = 0.0
        self._well_width_mm: float = 0.0
        self._well_height_mm: float = 0.0
        self._is_circular: bool = False

        # contral widget
        self.center_wdg = _CenterFOVWidget()
        # random fov widget
        self.random_wdg = _RandomFOVWidget()
        self.random_wdg.plate_area_x.valueChanged.connect(self._on_random_area_changed)
        self.random_wdg.plate_area_x.valueChanged.connect(self._update_plate_area_y)
        self.random_wdg.plate_area_y.valueChanged.connect(self._on_random_area_changed)
        self.random_wdg.number_of_FOV.valueChanged.connect(self._on_nFOV_changed)
        self.random_wdg.random_button.clicked.connect(self._on_random_button_pressed)
        # grid fovs widget
        self.grid_wdg = _GridFovWidget()
        self.grid_wdg.rows.valueChanged.connect(self._on_grid_changed)
        self.grid_wdg.cols.valueChanged.connect(self._on_grid_changed)
        self.grid_wdg.overlap_x.valueChanged.connect(self._on_grid_changed)
        self.grid_wdg.overlap_y.valueChanged.connect(self._on_grid_changed)
        self.grid_wdg.order_combo.currentIndexChanged.connect(self._on_grid_changed)
        # add widgets in a tab widget
        self.tab_wdg = QTabWidget()
        self.tab_wdg.setMinimumHeight(150)
        self.tab_wdg.addTab(self.center_wdg, CENTER)
        self.tab_wdg.addTab(self.random_wdg, RANDOM)
        self.tab_wdg.addTab(self.grid_wdg, GRID)

        # graphics scene to draw the well and the fovs
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene, self)
        self.view.setStyleSheet("background:grey; border-radius: 5px;")
        self._view_size = FOV_GRAPHICS_VIEW_SIZE
        self.scene.setSceneRect(QRectF(0, 0, self._view_size, self._view_size))
        self.view.setFixedSize(self._view_size + 2, self._view_size + 2)

        # main
        self.setLayout(QHBoxLayout())
        self.layout().setSpacing(10)
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().addWidget(self.tab_wdg)
        self.layout().addWidget(self.view)

        # connect
        self.tab_wdg.currentChanged.connect(self._on_tab_changed)
        self._mmc.events.pixelSizeChanged.connect(self._on_px_size_changed)

    def _on_px_size_changed(self) -> None:
        """Update the scene when the pixel size is changed."""
        with contextlib.suppress(AttributeError):
            for item in self.scene.items():
                if isinstance(item, (_WellArea, _FOVPoints, QGraphicsLineItem)):
                    self.scene.removeItem(item)
            mode = self.tab_wdg.tabText(self.tab_wdg.currentIndex())
            self._update_scene(mode)

    def _on_tab_changed(self, tab_index: int) -> None:
        """Update the scene when the tab is changed."""
        for item in self.scene.items():
            if isinstance(item, (_WellArea, _FOVPoints, QGraphicsLineItem)):
                self.scene.removeItem(item)

        mode = self.tab_wdg.tabText(tab_index)
        self._update_scene(mode)

    def _on_random_area_changed(self) -> None:
        """Update the _RandomWidget scene when the usable plate area is changed."""
        for item in self.scene.items():
            if isinstance(item, (_WellArea, _FOVPoints, QGraphicsLineItem)):
                self.scene.removeItem(item)
        self._update_random_fovs(self.random_wdg.value())

    def _on_nFOV_changed(self) -> None:
        """Update the _RandomWidget scene when the number of FOVVs is changed."""
        for item in self.scene.items():
            if isinstance(item, (_FOVPoints, QGraphicsLineItem)):
                self.scene.removeItem(item)
        self._update_random_fovs(self.random_wdg.value())

    def _on_grid_changed(self) -> None:
        for item in self.scene.items():
            if isinstance(item, (_FOVPoints, QGraphicsLineItem)):
                self.scene.removeItem(item)
        self._update_grid_fovs(self.grid_wdg.value())

    def _update_scene(self, mode: str) -> None:
        """Update the scene depending on the selected tab."""
        if mode == CENTER:
            self._update_center_fov()
        elif mode == RANDOM:
            self._update_random_fovs(self.random_wdg.value())
        elif mode == GRID:
            self._update_grid_fovs(self.grid_wdg.value())

    def _update_center_fov(self) -> None:
        """Update the _CenterWidget scene."""
        points = [(self._view_size / 2, self._view_size / 2)]  # center x and y
        self._draw_fovs(points)

    def _update_random_fovs(self, value: Random) -> None:
        """Update the _RandomWidget scene."""
        nFOV, area_x, area_y = (value.nFOV, value.area_x, value.area_y)
        image_width_mm, image_height_mm = self._get_image_size_in_mm()
        area_pen = QPen(Qt.GlobalColor.magenta)
        area_pen.setWidth(PEN_WIDTH)
        points = self._points_for_random_scene(
            nFOV, area_x, area_y, image_width_mm, image_height_mm, area_pen
        )
        self._draw_fovs(points)

    def _update_grid_fovs(self, value: Grid) -> None:
        """Update the _GridWidget scene."""
        # `dx` and `dy` are expressed in µm and are the wanted distance between the grid
        # FOVs along the x and y dimensions.
        # rows, cols, dx, dy = (value.rows, value.cols, value.dx, value.dy)

        # camera fov size in scene pixels
        fov_width_px, fov_height_px = self._get_image_size_in_px()

        # overlap in scene px
        dx = -value.overlap_x * fov_width_px / 100
        dy = -value.overlap_y * fov_height_px / 100

        # x and y center coords of the scene in px
        x, y = (self._view_size / 2, self._view_size / 2)  # px
        # if we have more than 1 row or column, we need to shift towards the scene
        # top-left corner the starting pixel (x, y) coords for the first fov of the grid
        # depending on the number of rows and columns.
        rows, cols = (value.rows, value.cols)
        if rows != 1 or cols != 1:
            x = x - ((cols - 1) * (fov_width_px / 2)) - ((dx / 2) * (cols - 1))
            y = y - ((rows - 1) * (fov_height_px / 2)) - ((dy / 2) * (rows - 1))

        move_x = fov_width_px + dx
        move_y = fov_height_px + dy

        order = OrderMode[self.grid_wdg.order_combo.currentText()]

        points = self._grid_of_points(
            rows, cols, x, y, move_x, move_y, order.value.snake, order.value.row_wise
        )

        self._draw_fovs(points)

    def _get_image_size_in_mm(self) -> tuple[float | None, float | None]:
        """Return the image size in mm depending on the camera device."""
        if not self._mmc.getCameraDevice():
            warnings.warn("Camera Device not found!", stacklevel=2)
            return None, None

        if not self._mmc.getPixelSizeUm():
            warnings.warn("Pixel Size not defined!", stacklevel=2)
            return None, None

        _cam_x = self._mmc.getImageWidth()
        _cam_y = self._mmc.getImageHeight()
        image_width_mm = (_cam_x * self._mmc.getPixelSizeUm()) / 1000
        image_height_mm = (_cam_y * self._mmc.getPixelSizeUm()) / 1000

        return image_width_mm, image_height_mm

    def _get_image_size_in_px(self) -> tuple[float, float]:
        """Return the image size in px depending on the camera device.

        If no Camera Device is found, the image size is set to 1x1 px.
        """
        image_width_mm, image_height_mm = self._get_image_size_in_mm()

        if image_width_mm is None or image_height_mm is None:
            return 1.0, 1.0

        well_size_mm = max(self._well_width_mm, self._well_height_mm)
        max_scene = max(self._scene_width_px, self._scene_height_px)

        # calculating the image size in scene px
        image_width_px = (max_scene * image_width_mm) / well_size_mm
        image_height_px = (max_scene * image_height_mm) / well_size_mm

        return image_width_px, image_height_px

    def _grid_of_points(
        self,
        rows: int,
        columns: int,
        x: float,
        y: float,
        dx: float,
        dy: float,
        snake: bool = False,
        row_wise: bool = True,
    ) -> list[tuple[float, float]]:
        """Create an ordered grid of points spaced by `dx` and  dy`."""
        # create a meshgrid of arrays with the number of rows and columns
        c, r = np.meshgrid(np.arange(columns), np.arange(rows))
        if snake:
            if row_wise:
                # invert the order of the columns in the odd rows
                c[1::2, :] = c[1::2, :][:, ::-1]
            else:
                # invert the order of the rows in the odd columns
                r[:, 1::2] = r[:, 1::2][::-1, :]
        # _zip is a list of tuples with the row and column indices (ravel flattens the
        # arrays)
        _zip = zip(r.ravel(), c.ravel()) if row_wise else zip(r.T.ravel(), c.T.ravel())
        # create a list of points by shifting the starting point by dx and dy
        return [(x + _c * dx, y + _r * dy) for _r, _c in _zip]

    def _draw_fovs(self, points: list[tuple[float, float]]) -> None:
        """Draw the fovs in the scene.

        The scene will have fovs as `_FOVPoints` and lines conncting the fovs that
        represent the fovs acquidition order.
        """
        line_pen = QPen(Qt.GlobalColor.white)
        line_pen.setWidth(2)
        x = y = None
        for idx, (xc, yc) in enumerate(points):
            # set the pen color to green for the first fov if the tab is the random one
            pen = (
                QPen(Qt.GlobalColor.white)
                if idx == 0
                and self.tab_wdg.currentIndex() == RANDOM_TAB_INDEX
                and self.random_wdg.number_of_FOV.value() > 1
                else None
            )
            # draw the fovs
            self.scene.addItem(_FOVPoints(xc, yc, *self._get_image_size_in_px(), pen))
            # draw the lines connecting the fovs
            if x is not None and y is not None:
                self.scene.addLine(x, y, xc, yc, pen=line_pen)
            x = xc
            y = yc

    def _update_plate_area_y(self, value: float) -> None:
        """Update the plate area y value if the plate has circular wells."""
        if not self._is_circular:
            return
        self.random_wdg.plate_area_y.setValue(value)

    def _points_for_random_scene(
        self,
        nFOV: int,
        area_x_mm: float,
        area_y_mm: float,
        image_width_mm: float | None,
        image_height_mm: float | None,
        well_area_pen: QPen,
    ) -> list[tuple[float, float]]:
        """Create the points for the _RandomWidget scene.

        They can be either random points in a circle or in a square/rectangle depending
        on the well shape.
        """
        well_area_width_px = (self._scene_width_px * area_x_mm) / self._well_width_mm
        well_area_height_px = (self._scene_height_px * area_y_mm) / self._well_height_mm
        scene_center_x = (self._view_size - well_area_width_px) / 2  # px
        scene_center_y = (self._view_size - well_area_height_px) / 2  # px

        # draw the well area
        self.scene.addItem(
            _WellArea(
                self._is_circular,
                scene_center_x,
                scene_center_y,
                well_area_width_px,
                well_area_height_px,
                well_area_pen,
            )
        )

        # minimum distance between the fovs in px
        if image_width_mm is None or image_height_mm is None:
            min_dist_px_x = min_dist_px_y = 0.0
        else:
            min_dist_px_x = (self._scene_width_px * image_width_mm) / area_x_mm
            min_dist_px_y = (self._scene_height_px * image_height_mm) / area_y_mm

        if self._is_circular:
            points = self._random_points_in_circle(
                nFOV,
                well_area_width_px,
                scene_center_x,
                scene_center_y,
                min_dist_px_x,
                min_dist_px_y,
            )

        else:
            well_area_x_px = (self._scene_width_px * area_x_mm) / self._well_width_mm
            well_area_y_px = (self._scene_height_px * area_y_mm) / self._well_height_mm

            points = self._random_points_in_rectangle(
                nFOV,
                well_area_x_px,
                well_area_y_px,
                self._view_size,
                self._view_size,
                min_dist_px_x,
                min_dist_px_y,
            )

        return self._order_points(points)

    def _random_points_in_circle(
        self,
        nFOV: int,
        diameter: float,
        center_x: float,
        center_y: float,
        min_dist_x: float,
        min_dist_y: float,
    ) -> list[tuple[float, float]]:
        """Create a list of random points in a specified circle.

        The points have a minimum distance from each other defined by `min_dist_x` and
        `min_dist_y`.
        """
        radius = diameter / 2
        points: list[tuple[float, float]] = []
        _t = time.time()
        while len(points) < nFOV:
            angle = random.uniform(0, 2 * math.pi)
            x = center_x + radius + random.uniform(0, radius) * math.cos(angle)
            y = center_y + radius + random.uniform(0, radius) * math.sin(angle)
            new_point = (x, y)
            if self.is_a_valid_point(new_point, points, min_dist_x, min_dist_y):
                points.append(new_point)
            # raise a warning if it takes longer than 200ms to generate the points.
            if time.time() - _t > 0.2:
                warnings.warn(
                    f"Area too small to generate {nFOV} fovs. "
                    f"Only {len(points)} were generated.",
                    stacklevel=2,
                )
                with signals_blocked(self.random_wdg.number_of_FOV):
                    self.random_wdg.number_of_FOV.setValue(len(points) or 1)
                return points
        return points

    def is_a_valid_point(
        self,
        new_point: tuple[float, float],
        existing_points: list[tuple[float, float]],
        min_dist_x: float,
        min_dist_y: float,
    ) -> bool:
        """Check if the distance between the `new point` and the `existing_points` is
        greater than the minimum disrtance required.
        """  # noqa: D205

        def distance(point1: tuple[float, float], point2: tuple[float, float]) -> float:
            x1, y1 = point1
            x2, y2 = point2
            return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

        for point in existing_points:
            if (
                distance(new_point, point) < min_dist_x
                or distance(new_point, point) < min_dist_y
            ):
                return False
        return True

    def _random_points_in_rectangle(
        self,
        nFOV: int,
        size_x: float,
        size_y: float,
        max_size_x: int,
        max_size_y: int,
        min_dist_x: float,
        min_dist_y: float,
    ) -> list[tuple[float, float]]:
        """Create a list of random points in a square/rectangle.

        The points have a minimum distance from each other defined by `min_dist_x` and
        `min_dist_y`.
        """
        x_left = (max_size_x - size_x) / 2  # left bound
        x_right = x_left + size_x  # right bound
        y_up = (max_size_y - size_y) / 2  # upper bound
        y_down = y_up + size_y  # lower bound

        points: list[tuple[float, float]] = []

        t = time.time()
        while len(points) < nFOV:
            x = np.random.uniform(x_left, x_right)
            y = np.random.uniform(y_up, y_down)
            if self._distance((x, y), points, (min_dist_x, min_dist_y)):
                points.append((x, y))
            t1 = time.time()
            if t1 - t > 0.5:
                warnings.warn(
                    f"Area too small to generate {nFOV} fovs. "
                    f"Only {len(points)} were generated.",
                    stacklevel=2,
                )
                with signals_blocked(self.random_wdg.number_of_FOV):
                    self.random_wdg.number_of_FOV.setValue(len(points))
                return points
        return points

    def _distance(
        self,
        new_point: tuple[float, float],
        points: list[tuple[float, float]],
        min_distance: tuple[float, float],
    ) -> bool:
        x_new, y_new = new_point[0], new_point[1]
        min_distance_x, min_distance_y = min_distance[0], min_distance[1]
        for point in points:
            x, y = point[0], point[1]
            x_max, x_min = max(x, x_new), min(x, x_new)
            y_max, y_min = max(y, y_new), min(y, y_new)
            if x_max - x_min < min_distance_x and y_max - y_min < min_distance_y:
                return False
        return True

    def _set_spinboxes_values(
        self, spin_x: QDoubleSpinBox, spin_y: QDoubleSpinBox
    ) -> None:
        with signals_blocked(spin_x):
            spin_x.setMaximum(self._well_width_mm)
            spin_x.setValue(self._well_width_mm)
        with signals_blocked(spin_y):
            spin_y.setMaximum(self._well_height_mm)
            spin_y.setValue(self._well_height_mm)

    def _on_random_button_pressed(self) -> None:
        for item in self.scene.items():
            if isinstance(item, (_FOVPoints, QGraphicsLineItem)):
                self.scene.removeItem(item)
        self._update_random_fovs(self.random_wdg.value())

    def _load_plate_info(self, well_plate: WellPlate) -> None:
        self.scene.clear()

        self._well_width_mm = round(well_plate.well_size_x, 3)
        self._well_height_mm = round(well_plate.well_size_y, 3)
        self._is_circular = well_plate.circular

        # set the scene size depending on the well size. Using FOV_SCENE_MIN or
        # FOV_SCENE_MAX to draw any rectangular shaped well.
        self._scene_width_px = (
            FOV_SCENE_MIN
            if self._well_width_mm <= self._well_height_mm
            else FOV_SCENE_MAX
        )
        self._scene_height_px = (
            FOV_SCENE_MIN
            if self._well_height_mm <= self._well_width_mm
            else FOV_SCENE_MAX
        )

        self._scene_start_x = (self._view_size - self._scene_width_px) / 2
        self._scene_start_y = (self._view_size - self._scene_height_px) / 2

        pen = QPen(Qt.GlobalColor.green)
        pen.setWidth(PEN_WIDTH)

        if self._is_circular:
            self.scene.addEllipse(
                self._scene_start_x,
                self._scene_start_y,
                self._scene_width_px,
                self._scene_height_px,
                pen,
            )
        else:
            self.scene.addRect(
                self._scene_start_x,
                self._scene_start_y,
                self._scene_width_px,
                self._scene_height_px,
                pen,
            )

        self._set_spinboxes_values(
            self.random_wdg.plate_area_x, self.random_wdg.plate_area_y
        )
        self._set_spinboxes_values(
            self.center_wdg.plate_area_center_x, self.center_wdg.plate_area_center_y
        )

        self.random_wdg.plate_area_y.setEnabled(not self._is_circular)
        self.random_wdg.plate_area_y.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
            if self._is_circular
            else QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )

        self.random_wdg.plate_area_x.setEnabled(True)

        mode = self.tab_wdg.tabText(self.tab_wdg.currentIndex())
        self._update_scene(mode)

    def _order_points(
        self, fovs: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        """Orders a list of points starting from the top-left and then moving towards
        the nearest point.
        """  # noqa: D205

        def _distance(
            point1: tuple[float, float], point2: tuple[float, float]
        ) -> float:
            """Return the Euclidean distance between two points."""
            x1, y1 = point1
            x2, y2 = point2
            return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

        # Find the top-left point
        top_left = min(fovs, key=lambda p: p[0] + p[1])
        ordered_points = [top_left]
        fovs.remove(top_left)

        while fovs:
            # Find the nearest point to the last ordered point
            nearest_point = min(fovs, key=lambda p: _distance(ordered_points[-1], p))
            ordered_points.append(nearest_point)
            fovs.remove(nearest_point)

        return ordered_points

    def value(
        self,
    ) -> tuple[list[tuple[float, float]], Center | Random | Grid, tuple[float, float]]:
        """Return the center of each FOVs."""
        points = [
            item.value() for item in self.scene.items() if isinstance(item, _FOVPoints)
        ]
        fovs = self._order_points(list(reversed(points)))
        fov_info = self._get_fov_info()
        scene_px_size_mm = (
            self._well_width_mm / self._scene_width_px,
            self._well_height_mm / self._scene_height_px,
        )
        return fovs, fov_info, scene_px_size_mm

    def _get_fov_info(self) -> Center | Random | Grid:
        """Return the information about the FOVs."""
        mode = self.tab_wdg.tabText(self.tab_wdg.currentIndex())
        if mode == RANDOM:
            return self.random_wdg.value()
        elif mode == GRID:
            return self.grid_wdg.value()
        return self.center_wdg.value()

    def setValue(self, fov_info: Center | Random | Grid) -> None:
        """Set the center of each FOVs."""
        # clear the scene
        for item in self.scene.items():
            if isinstance(item, (_FOVPoints, QGraphicsLineItem)):
                self.scene.removeItem(item)

        if isinstance(fov_info, Center):
            self._draw_fovs([(fov_info.x, fov_info.y)])
            tab_idx = CENTER_TAB_INDEX
        elif isinstance(fov_info, Random):
            self.random_wdg.setValue(fov_info)
            tab_idx = RANDOM_TAB_INDEX
        elif isinstance(fov_info, Grid):
            self.grid_wdg.setValue(fov_info)
            tab_idx = GRID_TAB_INDEX

        # doing both because if we are on the tab_idx tab, the fovs are not properly
        # drawn
        with signals_blocked(self.tab_wdg):
            self.tab_wdg.setCurrentIndex(tab_idx) or 0
        self._on_tab_changed(tab_idx or 0)
