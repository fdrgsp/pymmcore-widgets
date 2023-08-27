from __future__ import annotations

import contextlib
import math
import warnings
from typing import Any, NamedTuple, cast

import numpy as np
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QRectF, Qt, Signal
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
from useq import GridRelative, RandomArea, RandomPoints  # type: ignore
from useq._grid import OrderMode

from ._graphics_items import FOV, _FOVGraphicsItem, _WellAreaGraphicsItem
from ._util import ResizingGraphicsView
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


class Center(NamedTuple):
    """Center of the well as FOV of the plate."""

    scene_center_x: float = FOV_GRAPHICS_VIEW_SIZE / 2
    scene_center_y: float = FOV_GRAPHICS_VIEW_SIZE / 2
    scene_rect: QRectF = QRectF(0, 0, FOV_GRAPHICS_VIEW_SIZE, FOV_GRAPHICS_VIEW_SIZE)


class FOVInfo(NamedTuple):
    plate: WellPlate
    mode: Center | RandomPoints | GridRelative


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
    valueChanged = Signal(object)

    """Widget to select random FOVVs per well of the plate."""

    def __init__(self, parent: QWidget | None = None, *, plate: WellPlate) -> None:
        super().__init__(parent)

        self._plate = plate
        self._random_seed: int = np.random.randint(0, 2**32 - 1)

        # well area doublespinbox along x
        self.plate_area_x = QDoubleSpinBox()
        self.plate_area_x.setAlignment(AlignCenter)
        self.plate_area_x.setMinimum(0.01)
        self.plate_area_x.setSingleStep(0.1)
        plate_area_label_x = _create_label("Area x (mm):")
        _plate_area_x = _make_wdg_with_label(plate_area_label_x, self.plate_area_x)
        self.plate_area_x.valueChanged.connect(self.valueChanged.emit)

        # well area doublespinbox along y
        self.plate_area_y = QDoubleSpinBox()
        self.plate_area_y.setAlignment(AlignCenter)
        self.plate_area_y.setMinimum(0.01)
        self.plate_area_y.setSingleStep(0.1)
        plate_area_label_y = _create_label("Area y (mm):")
        _plate_area_y = _make_wdg_with_label(plate_area_label_y, self.plate_area_y)
        self.plate_area_y.valueChanged.connect(self.valueChanged.emit)

        # number of FOVs spinbox
        self.number_of_FOV = QSpinBox()
        self.number_of_FOV.setAlignment(AlignCenter)
        self.number_of_FOV.setMinimum(1)
        self.number_of_FOV.setMaximum(100)
        number_of_FOV_label = _create_label("FOVs:")
        nFOV = _make_wdg_with_label(number_of_FOV_label, self.number_of_FOV)
        self.number_of_FOV.valueChanged.connect(self.valueChanged.emit)

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

        # connect
        self.plate_area_x.valueChanged.connect(self._update_plate_area_y)

        # self._update(self.plate)

    @property
    def plate(self) -> WellPlate:
        """Return the well plate."""
        return self._plate

    @plate.setter
    def plate(self, well_plate: WellPlate) -> None:
        """Set the well plate."""
        self._plate = well_plate

    @property
    def random_seed(self) -> int:
        """Return the random seed."""
        return self._random_seed

    @random_seed.setter
    def random_seed(self, seed: int) -> None:
        """Set the random seed."""
        self._random_seed = seed

    def _update(self, plate: WellPlate) -> None:
        """Update the widget."""
        self.plate = plate
        # with signals_blocked(self):
        self.plate_area_x.setMaximum(self.plate.well_size_x)
        self.plate_area_x.setValue(self.plate.well_size_x)
        self.plate_area_y.setMaximum(self.plate.well_size_y)
        self.plate_area_y.setValue(self.plate.well_size_y)
        self.plate_area_y.setEnabled(not self.plate.circular)
        self.plate_area_y.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
            if self.plate.circular
            else QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )

    def _update_plate_area_y(self, value: float) -> None:
        """Update the plate area y value if the plate has circular wells."""
        if not self.plate.circular:
            return
        self.plate_area_y.setValue(value)

    def value(self) -> RandomPoints | None:
        """Return the values of the widgets."""
        return RandomPoints(
            circular=self.plate.circular,
            nFOV=self.number_of_FOV.value(),
            random_seed=self.random_seed,
            area=RandomArea(
                x=0,
                y=0,
                width=self.plate_area_x.value(),
                height=self.plate_area_y.value(),
            ),
        )

    def setValue(self, value: RandomPoints) -> None:
        """Set the values of the widgets."""
        self._radio_btn.setChecked(True)
        self.random_seed = value.random_seed
        self.number_of_FOV.setValue(value.nFOV)
        self.plate_area_x.setValue(value.area.width)
        self.plate_area_y.setValue(value.area.width)


class _GridFovWidget(QWidget):
    """Widget to select a grid FOV per well of the plate."""

    valueChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.rows = QSpinBox()
        self.rows.setAlignment(AlignCenter)
        self.rows.setMinimum(1)
        rows_lbl = _create_label("Rows:")
        _rows = _make_wdg_with_label(rows_lbl, self.rows)
        self.rows.valueChanged.connect(self.valueChanged.emit)

        self.cols = QSpinBox()
        self.cols.setAlignment(AlignCenter)
        self.cols.setMinimum(1)
        cols_lbl = _create_label("Columns:")
        _cols = _make_wdg_with_label(cols_lbl, self.cols)
        self.cols.valueChanged.connect(self.valueChanged.emit)

        self.overlap_x = QDoubleSpinBox()
        self.overlap_x.setAlignment(AlignCenter)
        self.overlap_x.setMinimum(-10000)
        self.overlap_x.setMaximum(100)
        self.overlap_x.setSingleStep(1.0)
        self.overlap_x.setValue(0)
        overlap_x_lbl = _create_label("Overlap x (%):")
        _overlap_x = _make_wdg_with_label(overlap_x_lbl, self.overlap_x)
        self.overlap_x.valueChanged.connect(self.valueChanged.emit)

        self.overlap_y = QDoubleSpinBox()
        self.overlap_y.setAlignment(AlignCenter)
        self.overlap_y.setMinimum(-10000)
        self.overlap_y.setMaximum(100)
        self.overlap_y.setSingleStep(1.0)
        self.overlap_y.setValue(0)
        spacing_y_lbl = _create_label("Overlap y (%):")
        _overlap_y = _make_wdg_with_label(spacing_y_lbl, self.overlap_y)
        self.overlap_y.valueChanged.connect(self.valueChanged.emit)

        self.order_combo = QComboBox()
        self.order_combo.addItems([mode.value for mode in OrderMode])
        self.order_combo.setCurrentText(OrderMode.row_wise_snake.value)
        order_combo_lbl = _create_label("Grid Order:")
        _order_combo = _make_wdg_with_label(order_combo_lbl, self.order_combo)
        self.order_combo.currentTextChanged.connect(self.valueChanged.emit)

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

        self._plate: WellPlate = WellPlate()
        self._reference_well_area: QRectF = QRectF()

        # graphics scene to draw the well and the fovs
        self.scene = QGraphicsScene()
        self.view = ResizingGraphicsView(self.scene, self)
        self.view.setStyleSheet("background:grey; border-radius: 5px;")
        self.view.setMinimumSize(FOV_GRAPHICS_VIEW_SIZE, FOV_GRAPHICS_VIEW_SIZE)
        self.view.setSceneRect(0, 0, FOV_GRAPHICS_VIEW_SIZE, FOV_GRAPHICS_VIEW_SIZE)
        # contral, random and grid widgets
        self.center_wdg = _CenterFOVWidget()
        self.random_wdg = _RandomFOVWidget(plate=self._plate)
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
        self.random_wdg.valueChanged.connect(self._on_random_changed)
        self.random_wdg.random_button.clicked.connect(self._on_random_changed)
        self.grid_wdg.valueChanged.connect(self._on_grid_changed)

        # self._update(plate=self.plate)

    @property
    def plate(self) -> WellPlate:
        """Return the well plate."""
        return self._plate

    @plate.setter
    def plate(self, well_plate: WellPlate) -> None:
        """Set the well plate."""
        self._plate = well_plate

    def _update(self, plate: WellPlate) -> None:
        """Load the information of the well plate.

        This method get the GUI ready to select the FOVs of the well plate.
        """
        self.scene.clear()

        self.plate = plate

        # update the random widget
        self.random_wdg.plate = plate
        self.random_wdg._update(plate)

        # set the size of the well in pixel maintaining the ratio between
        # the well size x and y. The offset is used to leave some space between the
        # well plate and the border of the scene (scene SceneRect set in __init__).
        well_size_px = FOV_GRAPHICS_VIEW_SIZE - OFFSET
        if plate.well_size_x == plate.well_size_y:
            size_x = size_y = well_size_px
        elif plate.well_size_x > plate.well_size_y:
            size_x = well_size_px
            # keep the ratio between well_size_x and well_size_y
            size_y = int(well_size_px * plate.well_size_y / plate.well_size_x)
        else:
            # keep the ratio between well_size_x and well_size_y
            size_x = int(well_size_px * plate.well_size_x / plate.well_size_y)
            size_y = well_size_px

        # set the position of the well plate in the scene using the center of the view
        # QRectF as reference
        x = self.view.sceneRect().center().x() - (size_x / 2)
        y = self.view.sceneRect().center().y() - (size_y / 2)
        w = size_x
        h = size_y

        self._reference_well_area = QRectF(x, y, w, h)

        # draw the well
        pen = QPen(Qt.GlobalColor.green)
        pen.setWidth(PEN_WIDTH)
        if plate.circular:
            self.scene.addEllipse(self._reference_well_area, pen=pen)
        else:
            self.scene.addRect(self._reference_well_area, pen=pen)

        # update the scene with the new pl,ate information
        self._update_scene(self._get_mode())

    def _get_mode(self) -> str | None:
        """Return the current mode."""
        for btn in self._mode_btn_group.buttons():
            if btn.isChecked():
                return cast(str, btn.objectName())
        return None

    def _remove_items(self, item_types: Any | tuple[Any]) -> None:
        """Remove all items of `item_types` from the scene."""
        for item in self.scene.items():
            if isinstance(item, item_types):
                self.scene.removeItem(item)
        self.scene.update()

    def _on_px_size_changed(self) -> None:
        """Update the scene when the pixel size is changed."""
        with contextlib.suppress(AttributeError):
            self._remove_items(
                (_WellAreaGraphicsItem, _FOVGraphicsItem, QGraphicsLineItem)
            )
            self._update_scene(self._get_mode())

    def _on_radiobutton_toggled(self, radio_btn: QRadioButton) -> None:
        """Update the scene when the tab is changed."""
        self._remove_items((_WellAreaGraphicsItem, _FOVGraphicsItem, QGraphicsLineItem))
        if radio_btn.isChecked():
            self._update_scene(self._get_mode())

    def _on_random_changed(self) -> None:
        self._remove_items((_WellAreaGraphicsItem, _FOVGraphicsItem, QGraphicsLineItem))
        # reset the random seed
        self.random_wdg.random_seed = np.random.randint(0, 2**32 - 1)
        self._update_random_fovs(self.random_wdg.value())

    def _on_grid_changed(self) -> None:
        self._remove_items((_FOVGraphicsItem, QGraphicsLineItem))
        self._update_grid_fovs(self.grid_wdg.value())

    def _update_scene(self, mode: str | None) -> None:
        """Update the scene depending on the selected tab."""
        if mode == CENTER:
            self._update_center_fov(self.center_wdg.value())
        elif mode == RANDOM:
            self._update_random_fovs(self.random_wdg.value())
        elif mode == GRID:
            self._update_grid_fovs(self.grid_wdg.value())
        else:
            return

    def _update_center_fov(self, value: Center) -> None:
        """Update the _CenterWidget scene."""
        points = self._get_center_point(value)
        self._draw_fovs(points)

    def _update_random_fovs(self, value: RandomPoints) -> None:
        """Update the _RandomWidget scene."""
        points = self._get_random_points(value)
        self._draw_fovs(points)

    def _update_grid_fovs(self, value: GridRelative) -> None:
        """Update the _GridWidget scene."""
        points = self._get_grid_points(value)
        self._draw_fovs(points)

    def _get_center_point(self, mode: Center) -> list[FOV]:
        return [FOV(mode.scene_center_x, mode.scene_center_y, mode.scene_rect)]

    def _get_random_points(self, mode: RandomPoints) -> list[FOV]:
        """Create the points for the mode scene.

        They can be either mode points in a circle or in a square/rectangle depending
        on the well shape.
        """
        # convert the well area from mm to px depending on the image size and the well
        # reference area (size of the well in pixel in the scene)
        well_area_x_px = (
            self._reference_well_area.width() * mode.area.width / self.plate.well_size_x
        )
        well_area_y_px = (
            self._reference_well_area.height()
            * mode.area.height
            / self.plate.well_size_y
        )

        # calculate the starting point of the well area
        x = self._reference_well_area.center().x() - (well_area_x_px / 2)
        y = self._reference_well_area.center().y() - (well_area_y_px / 2)
        rect = QRectF(x, y, well_area_x_px, well_area_y_px)
        # draw the well area
        area = _WellAreaGraphicsItem(rect, self.plate.circular, PEN_WIDTH)
        self.scene.addItem(area)

        random_area = RandomArea(
            x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height()
        )
        mode = mode.replace(area=random_area)

        points = list(mode.iterate_random_points())
        return [FOV(x, y, rect) for x, y in points]

    def _get_grid_points(self, mode: GridRelative) -> list[FOV]:
        """Create the points for the grid scene."""
        # camera fov size in scene pixels
        fov_width_px, fov_height_px = self._get_image_size_in_px()
        # update the grid with the camera fov size in px
        mode = mode.replace(fov_width=fov_width_px, fov_height=fov_height_px)

        # x and y center coords of the scene in px
        x, y = (
            self.scene.sceneRect().center().x(),
            self.scene.sceneRect().center().y(),
        )
        rect = self._reference_well_area

        # create a list of FOV points by shifting the grid by the center coords
        # and invert the y axis (because (0,0) in the scene is the top left corner)
        return [FOV(g.x + x, (g.y - y) * (-1), rect) for g in mode]  # type: ignore

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

        max_well_size_mm = max(self.plate.well_size_x, self.plate.well_size_y)

        # calculating the image size in scene px
        image_width_px = (FOV_GRAPHICS_VIEW_SIZE * image_width_mm) / max_well_size_mm
        image_height_px = (FOV_GRAPHICS_VIEW_SIZE * image_height_mm) / max_well_size_mm

        return image_width_px, image_height_px

    def _draw_fovs(self, points: list[FOV]) -> None:
        """Draw the fovs in the scene.

        The scene will have fovs as `_FOVPoints` and lines conncting the fovs that
        represent the fovs acquidition order.
        """
        self._remove_items((_FOVGraphicsItem, QGraphicsLineItem))
        line_pen = QPen(Qt.GlobalColor.black)
        line_pen.setWidth(2)
        x = y = None
        for idx, fov in enumerate(points):
            # set the pen color to black for the first fov if the tab is the random one
            pen = self._get_pen(idx)
            # draw the fovs
            img_w, img_h = self._get_image_size_in_px()
            fovs = _FOVGraphicsItem(fov.x, fov.y, img_w, img_h, fov.bounding_rect, pen)
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

    # def _get_random_points(self, random: RandomPoints) -> list[FOV]:
    #     """Create the points for the _RandomWidget scene.

    #     They can be either random points in a circle or in a square/rectangle depending  # noqa: E501
    #     on the well shape.
    #     """
    #     # convert the well area from mm to px depending on the image size and the well
    #     # reference area (size of the well in pixel in the scene)
    #     well_area_x_px = (
    #         self._reference_well_area.width()
    #         * random.area.width
    #         / self.plate.well_size_x
    #     )
    #     well_area_y_px = (
    #         self._reference_well_area.height()
    #         * random.area.height
    #         / self.plate.well_size_y
    #     )

    #     # calculate the starting point of the well area
    #     x = self._reference_well_area.center().x() - (well_area_x_px / 2)
    #     y = self._reference_well_area.center().y() - (well_area_y_px / 2)
    #     rect = QRectF(x, y, well_area_x_px, well_area_y_px)
    #     # draw the well area
    #     area = _WellAreaGraphicsItem(rect, self.plate.circular, PEN_WIDTH)
    #     self.scene.addItem(area)

    #     random_area = RandomArea(
    #         x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height()
    #     )
    #     random = random.replace(area=random_area)

    #     points = list(random.iterate_random_points())
    #     return [FOV(x, y, rect) for x, y in points]

    # # minimum distance between the fovs in px depending on the image size
    # image_width_mm, image_height_mm = self._get_image_size_in_mm()
    # if image_width_mm is None or image_height_mm is None:
    #     min_dist_px_x = min_dist_px_y = 0.0
    # else:
    #     min_dist_px_x = (FOV_GRAPHICS_VIEW_SIZE * image_width_mm) / mode.area_x_mm
    #     min_dist_px_y = (FOV_GRAPHICS_VIEW_SIZE * image_height_mm) / mode.area_y_mm  # noqa: E501

    # generate random points
    # points = self._generate_random_points(
    #     mode.nFOV, rect, min_dist_px_x, min_dist_px_y
    # )

    # return self._order_points(points)

    # def _generate_random_points(
    #     self,
    #     nFOV: int,
    #     rect: QRectF,
    #     min_dist_x: float,
    #     min_dist_y: float,
    # ) -> list[FOV]:
    #     """Generate a list of random points in a circle or in a rectangle."""
    #     points: list[FOV] = []

    #     t = time.time()
    #     while len(points) < nFOV:
    #         # random point in circle
    #         if self.plate.circular:
    #             x, y = self._random_point_in_circle(rect)
    #         else:
    #             x, y = self._random_point_in_rectangle(rect)
    #         point = FOV(x, y, rect)
    #         if self.is_a_valid_point(point, points, min_dist_x, min_dist_y):
    #             points.append(point)
    #         # raise a warning if it takes longer than 200ms to generate the points.
    #         if time.time() - t > 0.25:
    #             self._raise_points_warning(nFOV, len(points))
    #             return points

    #     return points

    # def _random_point_in_circle(self, rect: QRectF) -> tuple[float, float]:
    #     """Generate a random point in a circle."""
    #     radius = rect.width() / 2
    #     angle = np.random.uniform(0, 2 * math.pi)
    #     x = rect.center().x() + np.random.uniform(0, radius) * math.cos(angle)
    #     y = rect.center().y() + np.random.uniform(0, radius) * math.sin(angle)
    #     return (x, y)

    # def _random_point_in_rectangle(self, rect: QRectF) -> tuple[float, float]:
    #     """Generate a random point in a rectangle."""
    #     x = np.random.uniform(rect.left(), rect.right())
    #     y = np.random.uniform(rect.top(), rect.bottom())
    #     return (x, y)

    # def is_a_valid_point(
    #     self,
    #     new_point: FOV,
    #     existing_points: list[FOV],
    #     min_dist_x: float,
    #     min_dist_y: float,
    # ) -> bool:
    #     """Check if the distance between the `new point` and the `existing_points` is
    #     greater than the minimum disrtance required.
    #     """
    #     return not any(
    #         (
    #             _distance(new_point, point) < min_dist_x
    #             or _distance(new_point, point) < min_dist_y
    #         )
    #         for point in existing_points
    #     )

    # def _on_random_button_pressed(self) -> None:
    #     self._remove_items((_FOVGraphicsItem, QGraphicsLineItem))
    #     self._update_random_fovs(self.random_wdg.value())

    # def _raise_points_warning(self, nFOV: int, points: int) -> None:
    #     """Display a warning the set number of points cannot be generated."""
    #     warnings.warn(
    #         f"Unable to generate {nFOV} fovs. Only {points} were generated.",
    #         stacklevel=2,
    #     )
    #     with signals_blocked(self.random_wdg.number_of_FOV):
    #         self.random_wdg.number_of_FOV.setValue(points or 1)

    def _order_points(self, fovs: list[FOV]) -> list[FOV]:
        """Orders a list of points starting from the top-left and then moving towards
        the nearest point.
        """  # noqa: D205
        # TODO: find a better way
        top_left = min(fovs, key=lambda fov: (fov.x, fov.y))
        ordered_points = [top_left]
        fovs.remove(top_left)

        while fovs:
            nearest_fov = min(fovs, key=lambda fov: _distance(ordered_points[-1], fov))
            fovs.remove(nearest_fov)
            ordered_points.append(nearest_fov)

        return ordered_points

    def value(self) -> FOVInfo:
        mode_name = self._get_mode()
        if mode_name == RANDOM:
            mode = self.random_wdg.value()
        elif mode_name == GRID:
            mode = self.grid_wdg.value()
        else:  # mode_name == CENTER
            mode = self.center_wdg.value()
        return FOVInfo(self.plate, mode)

    # def setValue(self, fovs: FOVs, plate: WellPlate | None = None) -> None:
    def setValue(self, mode: Center | RandomPoints | GridRelative) -> None:
        """Set the value of the widget."""
        self._remove_items((_FOVGraphicsItem, QGraphicsLineItem))

        # in case the radio button is already checked, call `_update_scene()`
        # to directly update the scene
        radio_btn = self._mode_btn_group.checkedButton().objectName()

        if isinstance(mode, Center):
            self.center_wdg.setValue(mode)
            if radio_btn == CENTER:
                self._update_scene(CENTER)

        elif isinstance(mode, RandomPoints):
            self.random_wdg.setValue(mode)
            if radio_btn == RANDOM:
                self._update_scene(RANDOM)

        elif isinstance(mode, GridRelative):
            self.grid_wdg.setValue(mode)
            if radio_btn == GRID:
                self._update_scene(GRID)
