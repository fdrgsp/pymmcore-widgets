from __future__ import annotations

import contextlib
import math
import random
import time
import warnings
from typing import Any, NamedTuple, cast

import numpy as np
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QRectF, Qt
from qtpy.QtGui import QPainter, QPaintEvent, QPen
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QGraphicsLineItem,
    QGraphicsScene,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked
from useq import GridRelative
from useq._grid import OrderMode

from pymmcore_widgets._util import ResizingGraphicsView

from ._graphics_items import FOV, _FOVCoordinates, _WellArea
from ._well_plate_model import WellPlate

AlignCenter = Qt.AlignmentFlag.AlignCenter
CENTER = "Center"
CENTER_BTN_ID = 0
RANDOM = "Random"
RANDOM_BTN_ID = 1
GRID = "Grid"
GRID_BTN_ID = 2
FOV_GRAPHICS_VIEW_SIZE = 300
OFFSET = 20
PEN_WIDTH = 4
WELL_PLATE = WellPlate("", True, 0, 0, 0, 0, 0, 0)


class FOVs(NamedTuple):
    """FOVs of the well plate."""

    fov_info: Center | Random | GridRelative | None
    fov_list: list[FOV]


class Center(NamedTuple):
    """Center of the well as FOV of the plate."""

    ...


class Random(NamedTuple):
    """Random FOVs per well of the plate."""

    area_x: float
    area_y: float
    nFOV: int


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


def _distance(point1: FOV, point2: FOV) -> float:
    """Return the Euclidean distance between two points."""
    return math.sqrt((point2.x - point1.x) ** 2 + (point2.y - point1.y) ** 2)


class _CenterFOVWidget(QWidget):
    """Widget to select the center of the well as FOV of the plate."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        lbl = QLabel(text="Center of the Well.")
        lbl.setStyleSheet("font-weight: bold;")
        lbl.setAlignment(AlignCenter)

        # # add widgets layout
        self.wdg = QGroupBox()
        self.wdg.setLayout(QVBoxLayout())
        self.wdg.layout().setSpacing(0)
        self.wdg.layout().setContentsMargins(10, 10, 10, 10)
        self.wdg.layout().addWidget(lbl)
        self.wdg.setEnabled(False)

        self._radio_btn = QRadioButton()
        self._radio_btn.toggled.connect(self.wdg.setEnabled)
        self._radio_btn.setObjectName(CENTER)
        self._radio_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

        # widgets with radio button
        wdg_radio = QWidget()
        wdg_radio.setLayout(QHBoxLayout())
        wdg_radio.layout().setSpacing(10)
        wdg_radio.layout().setContentsMargins(0, 0, 0, 0)
        wdg_radio.layout().addWidget(self._radio_btn)
        wdg_radio.layout().addWidget(self.wdg)

        # main
        self.setLayout(QVBoxLayout())
        self.layout().setSpacing(0)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(wdg_radio)

    def value(self) -> Center:
        """Return the values of the widgets."""
        return Center()

    def setValue(self, center: Center) -> None:
        """Set the values of the widgets."""
        self._radio_btn.setChecked(True)


class _RandomFOVWidget(QWidget):
    """Widget to select random FOVVs per well of the plate."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # well area doublespinbox along x
        self.plate_area_x = QDoubleSpinBox()
        self.plate_area_x.setAlignment(AlignCenter)
        self.plate_area_x.setMinimum(0.01)
        self.plate_area_x.setSingleStep(0.1)
        plate_area_label_x = _create_label("Area x (mm):")
        _plate_area_x = _make_wdg_with_label(plate_area_label_x, self.plate_area_x)

        # well area doublespinbox along y
        self.plate_area_y = QDoubleSpinBox()
        self.plate_area_y.setAlignment(AlignCenter)
        self.plate_area_y.setMinimum(0.01)
        self.plate_area_y.setSingleStep(0.1)
        plate_area_label_y = _create_label("Area y (mm):")
        _plate_area_y = _make_wdg_with_label(plate_area_label_y, self.plate_area_y)

        # number of FOVs spinbox
        self.number_of_FOV = QSpinBox()
        self.number_of_FOV.setAlignment(AlignCenter)
        self.number_of_FOV.setMinimum(1)
        self.number_of_FOV.setMaximum(100)
        number_of_FOV_label = _create_label("FOVs:")
        nFOV = _make_wdg_with_label(number_of_FOV_label, self.number_of_FOV)

        self.random_button = QPushButton(text="Generate Random FOV(s)")

        # add widgets to wdg layout
        self.wdg = QGroupBox()
        self.wdg.setLayout(QVBoxLayout())
        self.wdg.layout().setSpacing(5)
        self.wdg.layout().setContentsMargins(10, 10, 10, 10)
        self.wdg.layout().addWidget(_plate_area_x)
        self.wdg.layout().addWidget(_plate_area_y)
        self.wdg.layout().addWidget(nFOV)
        self.wdg.layout().addWidget(self.random_button)
        self.wdg.setEnabled(False)

        self._radio_btn = QRadioButton()
        self._radio_btn.toggled.connect(self.wdg.setEnabled)
        self._radio_btn.setObjectName(RANDOM)
        self._radio_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

        # widgets with radio button
        wdg_radio = QWidget()
        wdg_radio.setLayout(QHBoxLayout())
        wdg_radio.layout().setSpacing(10)
        wdg_radio.layout().setContentsMargins(0, 0, 0, 0)
        wdg_radio.layout().addWidget(self._radio_btn)
        wdg_radio.layout().addWidget(self.wdg)

        # set labels sizes
        for lbl in (plate_area_label_x, plate_area_label_y, number_of_FOV_label):
            lbl.setMinimumWidth(plate_area_label_x.sizeHint().width())

        title = QLabel(text="Random Fields of Views.")
        title.setStyleSheet("font-weight: bold;")
        title.setAlignment(AlignCenter)

        # main
        self.setLayout(QVBoxLayout())
        self.layout().setSpacing(10)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(title)
        self.layout().addWidget(wdg_radio)

    def value(self) -> Random:
        """Return the values of the widgets."""
        return Random(
            area_x=self.plate_area_x.value(),
            area_y=self.plate_area_y.value(),
            nFOV=self.number_of_FOV.value(),
        )

    def setValue(self, value: Random) -> None:
        """Set the values of the widgets."""
        self._radio_btn.setChecked(True)
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
        self.order_combo.addItems([mode.value for mode in OrderMode])
        self.order_combo.setCurrentText(OrderMode.row_wise_snake.value)
        order_combo_lbl = _create_label("Grid Order:")
        _order_combo = _make_wdg_with_label(order_combo_lbl, self.order_combo)

        # add widgets to wdg layout
        self.wdg = QGroupBox()
        self.wdg.setLayout(QVBoxLayout())
        self.wdg.layout().setSpacing(5)
        self.wdg.layout().setContentsMargins(10, 10, 10, 10)
        self.wdg.layout().addWidget(_rows)
        self.wdg.layout().addWidget(_cols)
        self.wdg.layout().addWidget(_overlap_x)
        self.wdg.layout().addWidget(_overlap_y)
        self.wdg.layout().addWidget(_order_combo)
        self.wdg.setEnabled(False)

        self._radio_btn = QRadioButton()
        self._radio_btn.toggled.connect(self.wdg.setEnabled)
        self._radio_btn.setObjectName(GRID)
        self._radio_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

        # widgets with radio button
        wdg_radio = QWidget()
        wdg_radio.setLayout(QHBoxLayout())
        wdg_radio.layout().setSpacing(10)
        wdg_radio.layout().setContentsMargins(0, 0, 0, 0)
        wdg_radio.layout().addWidget(self._radio_btn)
        wdg_radio.layout().addWidget(self.wdg)

        # set labels sizes
        for lbl in (
            rows_lbl,
            cols_lbl,
            overlap_x_lbl,
            spacing_y_lbl,
            order_combo_lbl,
        ):
            lbl.setMinimumWidth(overlap_x_lbl.sizeHint().width())

        title = QLabel(text="Fields of Views in a Grid.")
        title.setStyleSheet("font-weight: bold;")
        title.setAlignment(AlignCenter)

        # main
        self.setLayout(QVBoxLayout())
        self.layout().setSpacing(10)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().addWidget(title)
        self.layout().addWidget(wdg_radio)

    def value(self) -> GridRelative:
        """Return the values of the widgets."""
        return GridRelative(
            rows=self.rows.value(),
            columns=self.cols.value(),
            overlap=(self.overlap_x.value(), self.overlap_y.value()),
            mode=self.order_combo.currentText(),
        )

    def setValue(self, value: GridRelative) -> None:
        """Set the values of the widgets."""
        self._radio_btn.setChecked(True)
        self.rows.setValue(value.rows)
        self.cols.setValue(value.columns)
        self.overlap_x.setValue(value.overlap[0])
        self.overlap_y.setValue(value.overlap[1])
        self.order_combo.setCurrentText(value.mode.value)


class _SeparatorWidget(QWidget):
    """Widget to separate widgets with a line."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(Qt.GlobalColor.gray, 1, Qt.PenStyle.SolidLine))
        painter.drawLine(self.rect().topLeft(), self.rect().topRight())


class _FOVSelectrorWidget(QWidget):
    """Widget to select the FOVVs per well of the plate."""

    def __init__(
        self, parent: QWidget | None = None, *, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent=parent)

        self._mmc = mmcore or CMMCorePlus.instance()

        self._plate: WellPlate = WELL_PLATE
        self._reference_well_area: QRectF = QRectF()
        self._well_size_x_px: float = 0.0
        self._well_size_y_px: float = 0.0

        # graphics scene to draw the well and the fovs
        self.scene = QGraphicsScene()
        self.view = ResizingGraphicsView(self.scene, self)
        self.view.setStyleSheet("background:grey; border-radius: 5px;")
        self.view.setMinimumSize(FOV_GRAPHICS_VIEW_SIZE, FOV_GRAPHICS_VIEW_SIZE)
        self.view.setSceneRect(0, 0, FOV_GRAPHICS_VIEW_SIZE, FOV_GRAPHICS_VIEW_SIZE)
        # contral, random and grid widgets
        self.center_wdg = _CenterFOVWidget()
        self.random_wdg = _RandomFOVWidget()
        self.grid_wdg = _GridFovWidget()

        # radio buttons group for fov mode selection
        self._mode_btn_group = QButtonGroup()
        self._mode_btn_group.addButton(self.center_wdg._radio_btn, id=CENTER_BTN_ID)
        self._mode_btn_group.addButton(self.random_wdg._radio_btn, id=RANDOM_BTN_ID)
        self._mode_btn_group.addButton(self.grid_wdg._radio_btn, id=GRID_BTN_ID)
        self._mode_btn_group.buttonToggled.connect(self._on_radiobutton_toggled)

        # main
        self.setLayout(QGridLayout())
        self.layout().setSpacing(10)
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().addWidget(_SeparatorWidget(), 0, 0)
        self.layout().addWidget(self.center_wdg, 1, 0)
        self.layout().addWidget(_SeparatorWidget(), 2, 0)
        self.layout().addWidget(self.random_wdg, 3, 0)
        self.layout().addWidget(_SeparatorWidget(), 4, 0)
        self.layout().addWidget(self.grid_wdg, 5, 0)
        self.layout().addWidget(_SeparatorWidget(), 6, 0)
        self.layout().addWidget(self.view, 0, 1, 7, 1)

        # connect
        self._mmc.events.pixelSizeChanged.connect(self._on_px_size_changed)
        self.random_wdg.plate_area_x.valueChanged.connect(self._on_random_area_changed)
        self.random_wdg.plate_area_x.valueChanged.connect(self._update_plate_area_y)
        self.random_wdg.plate_area_y.valueChanged.connect(self._on_random_area_changed)
        self.random_wdg.number_of_FOV.valueChanged.connect(self._on_nFOV_changed)
        self.random_wdg.random_button.clicked.connect(self._on_random_button_pressed)
        self.grid_wdg.rows.valueChanged.connect(self._on_grid_changed)
        self.grid_wdg.cols.valueChanged.connect(self._on_grid_changed)
        self.grid_wdg.overlap_x.valueChanged.connect(self._on_grid_changed)
        self.grid_wdg.overlap_y.valueChanged.connect(self._on_grid_changed)
        self.grid_wdg.order_combo.currentIndexChanged.connect(self._on_grid_changed)

    @property
    def plate(self) -> WellPlate:
        """Return the well plate."""
        return self._plate

    @plate.setter
    def plate(self, well_plate: WellPlate) -> None:
        """Set the well plate."""
        self._plate = well_plate
        self._load_plate_info(well_plate)

    def _load_plate_info(self, well_plate: WellPlate) -> None:
        """Load the information of the well plate.

        This method get the GUI ready to select the FOVs of the well plate.
        """
        self.scene.clear()

        # set the size of the well in pixel maintaining the ratio between
        # the well size x and y. The offset is used to leave some space between the
        # well plate and the border of the scene (scene SceneRect set in __init__).
        well_size_px = FOV_GRAPHICS_VIEW_SIZE - OFFSET
        if well_plate.well_size_x == well_plate.well_size_y:
            size_x = size_y = well_size_px
        elif well_plate.well_size_x > well_plate.well_size_y:
            size_x = well_size_px
            # keep the ratio between well_size_x and well_size_y
            size_y = int(well_size_px * well_plate.well_size_y / well_plate.well_size_x)
        else:
            # keep the ratio between well_size_x and well_size_y
            size_x = int(well_size_px * well_plate.well_size_x / well_plate.well_size_y)
            size_y = well_size_px

        # set the position of the well plate in the scene using the center of the view
        # QRectF as reference
        x = self.view.sceneRect().center().x() - size_x / 2
        y = self.view.sceneRect().center().y() - size_y / 2
        w = size_x
        h = size_y

        self._reference_well_area = QRectF(x, y, w, h)

        # draw the well
        pen = QPen(Qt.GlobalColor.green)
        pen.setWidth(PEN_WIDTH)
        if well_plate.circular:
            self.scene.addEllipse(self._reference_well_area, pen=pen)
        else:
            self.scene.addRect(self._reference_well_area, pen=pen)

        # set variables
        self._well_size_x_px = size_x
        self._well_size_y_px = size_y

        # set the values of the random widget
        self._update_random_wdg()

        # update the scene with the new pl,ate information
        self._update_scene(self._get_mode())

    def _get_mode(self) -> str | None:
        """Return the current mode."""
        for btn in self._mode_btn_group.buttons():
            if btn.isChecked():
                return cast(str, btn.objectName())
        return None

    def _update_random_wdg(self) -> None:
        """Update the random widget."""
        self.random_wdg.plate_area_y.setEnabled(not self._plate.circular)
        # not using self.random_wdg.setValue because we need to block the signals
        with signals_blocked(self.random_wdg.plate_area_x):
            self.random_wdg.plate_area_x.setMaximum(self._plate.well_size_x)
            self.random_wdg.plate_area_x.setValue(self._plate.well_size_x)
        with signals_blocked(self.random_wdg.plate_area_y):
            self.random_wdg.plate_area_y.setMaximum(self._plate.well_size_y)
            self.random_wdg.plate_area_y.setValue(self._plate.well_size_y)
        self.random_wdg.plate_area_y.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
            if self._plate.circular
            else QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )

    def _set_spinboxes_values(
        self, spin_x: QDoubleSpinBox, spin_y: QDoubleSpinBox
    ) -> None:
        with signals_blocked(spin_x):
            spin_x.setMaximum(self._plate.well_size_x)
            spin_x.setValue(self._plate.well_size_x)
        with signals_blocked(spin_y):
            spin_y.setMaximum(self._plate.well_size_y)
            spin_y.setValue(self._plate.well_size_y)

    def _remove_items(self, item_types: Any | tuple[Any]) -> None:
        """Remove all items of `item_types` from the scene."""
        for item in self.scene.items():
            if isinstance(item, item_types):
                self.scene.removeItem(item)

    def _on_px_size_changed(self) -> None:
        """Update the scene when the pixel size is changed."""
        with contextlib.suppress(AttributeError):
            self._remove_items((_WellArea, _FOVCoordinates, QGraphicsLineItem))
            self._update_scene(self._get_mode())

    def _on_radiobutton_toggled(self, radio_btn: QRadioButton) -> None:
        """Update the scene when the tab is changed."""
        self._remove_items((_WellArea, _FOVCoordinates, QGraphicsLineItem))
        if radio_btn.isChecked():
            self._update_scene(self._get_mode())

    def _on_random_area_changed(self) -> None:
        """Update the _RandomWidget scene when the usable plate area is changed."""
        self._remove_items((_WellArea, _FOVCoordinates, QGraphicsLineItem))
        self._update_random_fovs(self.random_wdg.value())

    def _on_nFOV_changed(self) -> None:
        """Update the _RandomWidget scene when the number of FOVVs is changed."""
        self._remove_items((_FOVCoordinates, QGraphicsLineItem))
        self._update_random_fovs(self.random_wdg.value())

    def _on_grid_changed(self) -> None:
        self._remove_items((_FOVCoordinates, QGraphicsLineItem))
        self._update_grid_fovs(self.grid_wdg.value())

    def _update_scene(self, mode: str | None) -> None:
        """Update the scene depending on the selected tab."""
        if mode == CENTER:
            self._update_center_fov()
        elif mode == RANDOM:
            self._update_random_fovs(self.random_wdg.value())
        elif mode == GRID:
            self._update_grid_fovs(self.grid_wdg.value())
        else:
            return

    def _update_center_fov(self) -> None:
        """Update the _CenterWidget scene."""
        self._draw_fovs(
            [
                FOV(
                    self.scene.sceneRect().center().x(),
                    self.scene.sceneRect().center().y(),
                )
            ]
        )

    def _update_random_fovs(self, value: Random) -> None:
        """Update the _RandomWidget scene."""
        nFOV, area_x, area_y = (value.nFOV, value.area_x, value.area_y)
        image_width_mm, image_height_mm = self._get_image_size_in_mm()
        points = self._points_for_random_scene(
            nFOV, area_x, area_y, image_width_mm, image_height_mm
        )
        self._draw_fovs(points)

    def _update_grid_fovs(self, value: GridRelative) -> None:
        """Update the _GridWidget scene."""
        # camera fov size in scene pixels
        fov_width_px, fov_height_px = self._get_image_size_in_px()

        grid = GridRelative(
            rows=value.rows,
            columns=value.columns,
            fov_width=fov_width_px,
            fov_height=fov_height_px,
            overlap=value.overlap,
            mode=value.mode,
        )

        # x and y center coords of the scene in px
        x, y = (
            self.scene.sceneRect().center().x(),
            self.scene.sceneRect().center().y(),
        )

        # create a list of FOV points by shifting the grid by the center coords
        # and invert the y axis (because (0,0) in the scene is the top left corner)
        points = [FOV(g.x + x, (g.y - y) * (-1)) for g in grid]  # type: ignore

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

        max_well_size_mm = max(self._plate.well_size_x, self._plate.well_size_y)
        max_scene = max(self._well_size_x_px, self._well_size_y_px)

        # calculating the image size in scene px
        image_width_px = (max_scene * image_width_mm) / max_well_size_mm
        image_height_px = (max_scene * image_height_mm) / max_well_size_mm

        return image_width_px, image_height_px

    def _draw_fovs(self, points: list[FOV]) -> None:
        """Draw the fovs in the scene.

        The scene will have fovs as `_FOVPoints` and lines conncting the fovs that
        represent the fovs acquidition order.
        """
        self._remove_items((_FOVCoordinates, QGraphicsLineItem))
        line_pen = QPen(Qt.GlobalColor.black)
        line_pen.setWidth(2)
        x = y = None
        for idx, fov in enumerate(points):
            # set the pen color to black for the first fov if the tab is the random one
            pen = self._get_pen(idx)
            # draw the fovs
            img_w, img_h = self._get_image_size_in_px()
            fovs = _FOVCoordinates(
                fov.x, fov.y, img_w, img_h, self.scene.sceneRect(), pen
            )
            self.scene.addItem(fovs)
            # draw the lines connecting the fovs
            if x is not None and y is not None:
                self.scene.addLine(x, y, fov.x, fov.y, pen=line_pen)
            x, y = (fov.x, fov.y)

    def _get_pen(self, index: int) -> QPen:
        """Return the pen for the fovs.

        Return black color for the first fov if the tab is the random one.
        """
        return (
            QPen(Qt.GlobalColor.black)
            if index == 0
            and self.random_wdg._radio_btn.isChecked()
            and self.random_wdg.number_of_FOV.value() > 1
            else None
        )

    def _update_plate_area_y(self, value: float) -> None:
        """Update the plate area y value if the plate has circular wells."""
        if not self._plate.circular:
            return
        self.random_wdg.plate_area_y.setValue(value)

    def _points_for_random_scene(
        self,
        nFOV: int,
        area_x_mm: float,
        area_y_mm: float,
        image_width_mm: float | None,
        image_height_mm: float | None,
    ) -> list[FOV]:
        """Create the points for the _RandomWidget scene.

        They can be either random points in a circle or in a square/rectangle depending
        on the well shape.
        """
        # convert the well area from mm to px depending on the image size ant the well
        # reference area (size of the well in pixel in the scene)
        well_area_x_px = (
            self._reference_well_area.width() * area_x_mm / self._plate.well_size_x
        )
        well_area_y_px = (
            self._reference_well_area.height() * area_y_mm / self._plate.well_size_y
        )

        # calculate the starting point of the well area
        x = self._reference_well_area.center().x() - (well_area_x_px / 2)
        y = self._reference_well_area.center().y() - (well_area_y_px / 2)
        rect = QRectF(x, y, well_area_x_px, well_area_y_px)
        # draw the well area
        area = _WellArea(rect, self._plate.circular, PEN_WIDTH)
        self.scene.addItem(area)

        # minimum distance between the fovs in px depending on the image size
        if image_width_mm is None or image_height_mm is None:
            min_dist_px_x = min_dist_px_y = 0.0
        else:
            min_dist_px_x = (self._well_size_x_px * image_width_mm) / area_x_mm
            min_dist_px_y = (self._well_size_y_px * image_height_mm) / area_y_mm

        # generate random points
        points = self._generate_random_points(
            nFOV, area._rect, min_dist_px_x, min_dist_px_y
        )

        return self._order_points(points)

    def _generate_random_points(
        self, nFOV: int, rect: QRectF, min_dist_x: float, min_dist_y: float
    ) -> list[FOV]:
        """Generate a list of random points in a circle or in a rectangle."""
        # points: list[tuple[float, float]] = []
        points: list[FOV] = []

        t = time.time()
        while len(points) < nFOV:
            # random point in circle
            if self._plate.circular:
                x, y = self._random_point_in_circle(rect)
            else:
                x, y = self._random_point_in_rectangle(rect)

            if self.is_a_valid_point(FOV(x, y), points, min_dist_x, min_dist_y):
                points.append(FOV(x, y))
            # raise a warning if it takes longer than 200ms to generate the points.
            if time.time() - t > 0.25:
                self._raise_points_warning(nFOV, len(points))
                return points

        return points

    def _random_point_in_circle(self, rect: QRectF) -> tuple[float, float]:
        """Generate a random point in a circle."""
        radius = rect.width() / 2
        angle = random.uniform(0, 2 * math.pi)
        x = rect.center().x() + random.uniform(0, radius) * math.cos(angle)
        y = rect.center().y() + random.uniform(0, radius) * math.sin(angle)
        return (x, y)

    def _random_point_in_rectangle(self, rect: QRectF) -> tuple[float, float]:
        """Generate a random point in a rectangle."""
        x = np.random.uniform(rect.left(), rect.right())
        y = np.random.uniform(rect.top(), rect.bottom())
        return (x, y)

    def is_a_valid_point(
        self,
        new_point: FOV,
        existing_points: list[FOV],
        min_dist_x: float,
        min_dist_y: float,
    ) -> bool:
        """Check if the distance between the `new point` and the `existing_points` is
        greater than the minimum disrtance required.
        """  # noqa: D205
        return not any(
            (
                _distance(new_point, point) < min_dist_x
                or _distance(new_point, point) < min_dist_y
            )
            for point in existing_points
        )

    def _raise_points_warning(self, nFOV: int, points: int) -> None:
        """Display a warning the set number of points cannot be generated."""
        warnings.warn(
            f"Unable to generate {nFOV} fovs. Only {points} were generated.",
            stacklevel=2,
        )
        with signals_blocked(self.random_wdg.number_of_FOV):
            self.random_wdg.number_of_FOV.setValue(points or 1)

    def _on_random_button_pressed(self) -> None:
        self._remove_items((_FOVCoordinates, QGraphicsLineItem))
        self._update_random_fovs(self.random_wdg.value())

    def _order_points(self, fovs: list[FOV]) -> list[FOV]:
        """Orders a list of points starting from the top-left and then moving towards
        the nearest point.
        """  # noqa: D205
        top_left = min(fovs, key=lambda fov: (fov.x, fov.y))
        ordered_points = [top_left]
        fovs.remove(top_left)

        while fovs:
            nearest_fov = min(fovs, key=lambda fov: _distance(ordered_points[-1], fov))
            fovs.remove(nearest_fov)
            ordered_points.append(nearest_fov)

        return ordered_points

    def value(
        self,
    ) -> FOVs:
        """Return the center of each FOVs."""
        points = [
            item.value()
            for item in self.scene.items()
            if isinstance(item, _FOVCoordinates)
        ]
        fov_info = self._get_fov_info()
        # if randon, the points are ordered from the top-left one
        fov_list = (
            self._order_points(points)
            if isinstance(fov_info, Random)
            else list(reversed(points))
        )
        return FOVs(fov_info, fov_list)

    def _get_fov_info(self) -> Center | Random | GridRelative | None:
        """Return the information about the FOVs."""
        mode = self._get_mode()
        if mode == RANDOM:
            return self.random_wdg.value()
        elif mode == GRID:
            return self.grid_wdg.value()
        elif mode == CENTER:
            return self.center_wdg.value()
        return None

    def setValue(self, fovs: FOVs) -> None:
        """Set the center of each FOVs."""
        self._remove_items((_FOVCoordinates, QGraphicsLineItem))

        # in case the radio button is already checked, call _update_scene to directly
        # update the scene
        radio_btn = self._mode_btn_group.checkedButton().objectName()

        if isinstance(fovs.fov_info, Center):
            self.center_wdg.setValue(fovs.fov_info)
            if radio_btn == CENTER:
                self._update_scene(CENTER)

        elif isinstance(fovs.fov_info, Random):
            with signals_blocked(self.random_wdg):
                self.random_wdg.setValue(fovs.fov_info)
            # here we want to draw the fovs in the fov_list and not trigger the creation
            # of new fovs
            self._draw_fovs(fovs.fov_list)

        elif isinstance(fovs.fov_info, GridRelative):
            self.grid_wdg.setValue(fovs.fov_info)
            if radio_btn == GRID:
                self._update_scene(GRID)
