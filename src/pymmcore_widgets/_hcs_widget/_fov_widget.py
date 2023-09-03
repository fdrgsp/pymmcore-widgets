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
from superqt.utils import signals_blocked
from useq import GridRelative, RandomPoints  # type: ignore
from useq._grid import OrderMode, Shape  # type: ignore

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
VIEW_SIZE = 300
OFFSET = 20
PEN_WIDTH = 4
WELL_PLATE = WellPlate("", True, 0, 0, 0, 0, 0, 0)
RECT = Shape.RECTANGLE
ELLIPSE = Shape.ELLIPSE


class Center(NamedTuple):
    """Center of the well as FOV of the plate."""

    ...


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


def _get_fov_size_mm(
    mmcore: CMMCorePlus | None = None,
) -> tuple[float | None, float | None]:
    """Return the image size in mm depending on the camera device."""
    if mmcore is None or not mmcore.getCameraDevice() or not mmcore.getPixelSizeUm():
        return None, None

    _cam_x = mmcore.getImageWidth()
    _cam_y = mmcore.getImageHeight()
    image_width_mm = (_cam_x * mmcore.getPixelSizeUm()) / 1000
    image_height_mm = (_cam_y * mmcore.getPixelSizeUm()) / 1000

    return image_width_mm, image_height_mm


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

    valueChanged = Signal(object)

    def __init__(
        self, parent: QWidget | None = None, *, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent)

        self._mmc = mmcore or CMMCorePlus.instance()
        self._is_circular: bool = False
        # setting a random seed for point generation reproducibility
        self._random_seed: int = np.random.randint(0, 2**32 - 1, dtype=np.uint32)

        # well area doublespinbox along x
        self.area_x = QDoubleSpinBox()
        self.area_x.setAlignment(AlignCenter)
        self.area_x.setMinimum(0.0)
        self.area_x.setSingleStep(0.1)
        area_label_x = _create_label("Area x (mm):")
        _area_x = _make_wdg_with_label(area_label_x, self.area_x)
        self.area_x.valueChanged.connect(self.valueChanged.emit)

        # well area doublespinbox along y
        self._area_y = QDoubleSpinBox()
        self._area_y.setAlignment(AlignCenter)
        self._area_y.setMinimum(0.0)
        self._area_y.setSingleStep(0.1)
        area_label_y = _create_label("Area y (mm):")
        _area_y = _make_wdg_with_label(area_label_y, self._area_y)
        self._area_y.valueChanged.connect(self.valueChanged.emit)

        # number of FOVs spinbox
        self.number_of_points = QSpinBox()
        self.number_of_points.setAlignment(AlignCenter)
        self.number_of_points.setMinimum(1)
        self.number_of_points.setMaximum(1000)
        number_of_points_label = _create_label("FOVs:")
        _n_of_points = _make_wdg_with_label(
            number_of_points_label, self.number_of_points
        )
        self.number_of_points.valueChanged.connect(self.valueChanged.emit)

        self.random_button = QPushButton(text="Generate Random FOV(s)")

        # add widgets to wdg layout
        self.wdg = QGroupBox()
        self.wdg.setLayout(QVBoxLayout())
        self.wdg.layout().setSpacing(5)
        self.wdg.layout().setContentsMargins(10, 10, 10, 10)
        self.wdg.layout().addWidget(_area_x)
        self.wdg.layout().addWidget(_area_y)
        self.wdg.layout().addWidget(_n_of_points)
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
        for lbl in (area_label_x, area_label_y, number_of_points_label):
            lbl.setMinimumWidth(area_label_x.sizeHint().width())

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
        self.area_x.valueChanged.connect(self._update_plate_area_y)

    @property
    def is_circular(self) -> bool:
        """Return True if the well is circular."""
        return self._is_circular

    @is_circular.setter
    def is_circular(self, circular: bool) -> None:
        """Set True if the well is circular."""
        self._is_circular = circular

    @property
    def random_seed(self) -> int:
        """Return the random seed."""
        return self._random_seed

    @random_seed.setter
    def random_seed(self, seed: int) -> None:
        """Set the random seed."""
        self._random_seed = seed

    def _update_plate_area_y(self, value: float) -> None:
        """Update the plate area y value if the plate has circular wells."""
        if self._is_circular:
            self._area_y.setValue(value)

    def _enable_plate_area_y(self, enable: bool) -> None:
        """Enable or disable the plate area y widget."""
        self._area_y.setEnabled(enable)
        self._area_y.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.UpDownArrows
            if enable
            else QAbstractSpinBox.ButtonSymbols.NoButtons
        )

    def value(self) -> RandomPoints | None:
        """Return the values of the widgets."""
        fov_width, fov_height = _get_fov_size_mm(self._mmc)
        return RandomPoints(
            num_points=self.number_of_points.value(),
            shape=ELLIPSE if self._is_circular else RECT,
            random_seed=self.random_seed,
            max_width=self.area_x.value(),
            max_height=self._area_y.value(),
            allow_overlap=False,
            fov_width=fov_width,
            fov_height=fov_height,
        )

    def setValue(self, value: RandomPoints) -> None:
        """Set the values of the widgets."""
        self._radio_btn.setChecked(True)
        self.is_circular = value.shape == ELLIPSE
        self.random_seed = value.random_seed
        self.number_of_points.setValue(value.num_points)
        self.area_x.setMaximum(value.max_width)
        self.area_x.setValue(value.max_width)
        self._area_y.setMaximum(value.max_height)
        self._area_y.setValue(value.max_height)
        self._enable_plate_area_y(not self.is_circular)


class _GridFovWidget(QWidget):
    """Widget to select a grid FOV per well of the plate."""

    valueChanged = Signal(object)

    def __init__(
        self, parent: QWidget | None = None, *, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent)

        self._mmc = mmcore or CMMCorePlus.instance()

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
        fov_width, fov_height = _get_fov_size_mm(self._mmc)
        return GridRelative(
            rows=self.rows.value(),
            columns=self.cols.value(),
            overlap=(self.overlap_x.value(), self.overlap_y.value()),
            fov_width=fov_width,
            fov_height=fov_height,
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
        self.view.setMinimumSize(VIEW_SIZE, VIEW_SIZE)
        # set the scene rect so that the center is (0, 0)
        self.view.setSceneRect(-VIEW_SIZE / 2, -VIEW_SIZE / 2, VIEW_SIZE, VIEW_SIZE)
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
        self.random_wdg.valueChanged.connect(self._on_random_changed)
        self.random_wdg.random_button.clicked.connect(self._on_random_changed)
        self.grid_wdg.valueChanged.connect(self._on_grid_changed)

    @property
    def plate(self) -> WellPlate:
        """Return the well plate."""
        return self._plate

    @plate.setter
    def plate(self, well_plate: WellPlate) -> None:
        """Set the well plate."""
        self._plate = well_plate

    def _get_mode(self) -> str | None:  # sourcery skip: use-next
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
            self._update_scene()

    def _on_radiobutton_toggled(self, radio_btn: QRadioButton) -> None:
        """Update the scene when the tab is changed."""
        self._remove_items((_WellAreaGraphicsItem, _FOVGraphicsItem, QGraphicsLineItem))
        if radio_btn.isChecked():
            self._update_scene()

    def _on_random_changed(self) -> None:
        self._remove_items((_WellAreaGraphicsItem, _FOVGraphicsItem, QGraphicsLineItem))
        # reset the random seed
        self.random_wdg.random_seed = np.random.randint(0, 2**32 - 1, dtype=np.uint32)
        self._update_random_fovs(self.random_wdg.value())

    def _on_grid_changed(self) -> None:
        self._remove_items((_FOVGraphicsItem, QGraphicsLineItem))
        self._update_grid_fovs(self.grid_wdg.value())

    def _update_scene(self) -> None:
        """Update the scene depending on the selected tab."""
        mode = self._get_mode()
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
        self._remove_items(_WellAreaGraphicsItem)
        # get the well area in scene pixel
        area = self._well_area_in_pixel(value)
        # draw the well area
        self.scene.addItem(area)
        # update the RandomPoints area with the well area in scene pixel and thecamera
        # fov size in scene pixels
        fov_width_px, fov_height_px = self._get_image_size_in_px()
        mode = value.replace(
            max_width=area.boundingRect().width(),
            max_height=area.boundingRect().height(),
            fov_width=fov_width_px,
            fov_height=fov_height_px,
        )
        # get the random points list
        points = self._get_random_points(mode, area.boundingRect())
        # draw the random points
        self._draw_fovs(points)

    def _update_grid_fovs(self, value: GridRelative) -> None:
        """Update the _GridWidget scene."""
        points = self._get_grid_points(value)
        self._draw_fovs(points)

    def _get_center_point(self, center_mode: Center) -> list[FOV]:
        scene_center_x: float = self.scene.sceneRect().center().x()
        scene_center_y: float = self.scene.sceneRect().center().y()
        scene_rect: QRectF = self.view.sceneRect()
        return [FOV(scene_center_x, scene_center_y, scene_rect)]

    def _well_area_in_pixel(self, random_mode: RandomPoints) -> _WellAreaGraphicsItem:
        # convert the well area from mm to px depending on the image size and the well
        # reference area (size of the well in pixel in the scene)

        # avoid ZeroDivisionError
        if (
            self.plate is None
            or not self._reference_well_area
            or not random_mode.max_width
            or not random_mode.max_height
        ):
            return _WellAreaGraphicsItem(QRectF(), False, PEN_WIDTH)

        well_area_x_px = (
            self._reference_well_area.width()
            * random_mode.max_width
            / self.plate.well_size_x
        )
        well_area_y_px = (
            self._reference_well_area.height()
            * random_mode.max_height
            / self.plate.well_size_y
        )

        # calculate the starting point of the well area
        x = self._reference_well_area.center().x() - (well_area_x_px / 2)
        y = self._reference_well_area.center().y() - (well_area_y_px / 2)
        rect = QRectF(x, y, well_area_x_px, well_area_y_px)

        return _WellAreaGraphicsItem(rect, self.plate.circular, PEN_WIDTH)

    def _get_random_points(self, random_mode: RandomPoints, area: QRectF) -> list[FOV]:
        """Create the points for the random scene."""
        # catch the warning raised by the RandomPoints class if the max number of
        # iterations is reached.
        with warnings.catch_warnings(record=True) as w:
            # note: inverting the y axis because in scene, y up is negative and y down
            # is positive.
            points = [FOV(x, y * (-1), area) for x, y, _, _, _ in random_mode]
            with signals_blocked(self.random_wdg.number_of_points):
                self.random_wdg.number_of_points.setValue(len(points))
        if len(w):
            warnings.warn(w[0].message, w[0].category, stacklevel=2)

        return self._order_points(points)

    def _get_grid_points(self, grid_mode: GridRelative) -> list[FOV]:
        """Create the points for the grid scene."""
        # camera fov size in scene pixels
        fov_width_px, fov_height_px = self._get_image_size_in_px()
        # update the grid with the camera fov size in px
        grid_mode = grid_mode.replace(fov_width=fov_width_px, fov_height=fov_height_px)

        # x and y center coords of the scene in px
        x, y = (
            self.scene.sceneRect().center().x(),
            self.scene.sceneRect().center().y(),
        )
        rect = self._reference_well_area

        # create a list of FOV points by shifting the grid by the center coords.
        # note: inverting the y axis because in scene, y up is negative and y down is
        # positive.
        return [FOV(g.x + x, (g.y - y) * (-1), rect) for g in grid_mode]  # type: ignore

    def _get_image_size_in_px(self) -> tuple[float, float]:
        """Return the image size in px depending on the camera device.

        If no Camera Device is found, the image size is set to 1x1 px.
        """
        image_width_mm, image_height_mm = _get_fov_size_mm(self._mmc)
        if image_width_mm is None or image_height_mm is None:
            return 1.0, 1.0

        max_well_size_mm = max(self.plate.well_size_x, self.plate.well_size_y)

        if max_well_size_mm == 0:
            return 1.0, 1.0

        # calculating the image size in scene px
        image_width_px = (VIEW_SIZE * image_width_mm) / max_well_size_mm
        image_height_px = (VIEW_SIZE * image_height_mm) / max_well_size_mm

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
        """Return the pen for the random mode fovs.

        Return the Qt.GlobalColor.black color for the first fov.
        """
        return (
            QPen(Qt.GlobalColor.black)
            if index == 0
            and self.random_wdg._radio_btn.isChecked()
            and self.random_wdg.number_of_points.value() > 1
            else None
        )

    def _order_points(self, fovs: list[FOV]) -> list[FOV]:
        """Orders a list of points starting from the top-left and then moving towards
        the nearest point.
        """  # noqa: D205
        # TODO: maybe find a better way?
        top_left = min(fovs, key=lambda fov: (fov.x, fov.y))
        ordered_points = [top_left]
        fovs.remove(top_left)

        while fovs:
            nearest_fov = min(fovs, key=lambda fov: _distance(ordered_points[-1], fov))
            fovs.remove(nearest_fov)
            ordered_points.append(nearest_fov)

        return ordered_points

    def value(self) -> tuple[WellPlate | None, Center | RandomPoints | GridRelative]:
        mode_name = self._get_mode()
        if mode_name == RANDOM:
            return self.plate, self.random_wdg.value()
        elif mode_name == GRID:
            return self.plate, self.grid_wdg.value()
        else:  # mode_name == CENTER
            return self.plate, self.center_wdg.value()

    def setValue(
        self, plate: WellPlate, mode: Center | RandomPoints | GridRelative
    ) -> None:
        """Set the value of the widget."""
        self.scene.clear()

        self.plate = plate

        if self.plate is None:
            return

        if isinstance(mode, RandomPoints):
            self._handle_assertions_errors(mode)

            # here block the signals to avoid to emit the valueChanged signal and so to
            # avoid resetting the random seed
            with signals_blocked(self.random_wdg):
                self.random_wdg.setValue(mode)
        else:
            # update the randon widget with the well dimensions
            self.random_wdg.setValue(self._plate_to_random(plate))

        if isinstance(mode, Center):
            self.center_wdg.setValue(mode)

        elif isinstance(mode, GridRelative):
            self.grid_wdg.setValue(mode)
            self._plate_to_random(plate)

        self._reload_scene(self.plate)

    def _handle_assertions_errors(self, mode: RandomPoints) -> None:
        # make sure the RandomPoints shape is the same as the plate shape
        assert mode.shape == ELLIPSE if self.plate.circular else mode.shape == RECT, (
            f"Well plate shape is '{'' if self.plate.circular else 'NON '}circular'"
            f", RandomPoints shape is '{mode.shape.value}'."
        )
        # make sure the RandomPoints max width and height are equal or smaller than the
        # well size
        assert mode.max_width <= self.plate.well_size_x, (
            f"RandomPoints max width '{mode.max_width}' is larger than the well width "
            f"'{self.plate.well_size_x}'."
        )
        assert mode.max_height <= self.plate.well_size_y, (
            f"RandomPoints max height '{mode.max_height}' is larger than the well "
            f"height '{self.plate.well_size_y}'."
        )

    def _plate_to_random(self, plate: WellPlate) -> RandomPoints:
        """Convert a WellPlate object to a RandomPoints object."""
        return RandomPoints(
            num_points=self.random_wdg.number_of_points.value(),
            max_width=plate.well_size_x,
            max_height=plate.well_size_y,
            shape=ELLIPSE if plate.circular else RECT,
            random_seed=self.random_wdg.random_seed,
        )

    def _reload_scene(self, plate: WellPlate) -> None:
        """Redraw the scene with the plate and FOV selector mode."""
        # set the size of the well in pixel maintaining the ratio between
        # the well size x and y. The offset is used to leave some space between the
        # well plate and the border of the scene (scene SceneRect set in __init__).
        well_size_px = VIEW_SIZE - OFFSET
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
        self._update_scene()
