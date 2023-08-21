from __future__ import annotations

from itertools import product
from typing import TYPE_CHECKING

import numpy as np
from qtpy.QtCore import QRectF, Qt
from qtpy.QtWidgets import (
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from ._graphics_items import WellInfo, _Well

if TYPE_CHECKING:
    from qtpy.QtGui import QBrush, QPen, QResizeEvent

    from ._well_plate_model import WellPlate


class ResizingGraphicsView(QGraphicsView):
    """A QGraphicsView that resizes the scene to fit the view."""

    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


def draw_well_plate(
    view: QGraphicsView,
    scene: QGraphicsScene,
    plate: WellPlate,
    brush: QBrush | None,
    pen: QPen | None,
    opacity: float = 1.0,
    text: bool = True,
) -> None:
    """Draw all wells of the plate."""
    # setting a custom well size in scene px. Using 10 times the well size in mm
    # gives a good resolution in the viewer.
    well_scene_size = plate.well_size_x * 10

    scene.clear()

    if not plate.well_size_x or not plate.well_size_y:
        return

    # calculate the width and height of the well in scene px
    if plate.well_size_x == plate.well_size_y:
        well_width = well_height = well_scene_size
    elif plate.well_size_x > plate.well_size_y:
        well_width = well_scene_size
        # keep the ratio between well_size_x and well_size_y
        well_height = int(well_scene_size * plate.well_size_y / plate.well_size_x)
    else:
        # keep the ratio between well_size_x and well_size_y
        well_width = int(well_scene_size * plate.well_size_x / plate.well_size_y)
        well_height = well_scene_size

    # calculate the spacing between wells
    dx = plate.well_spacing_x - plate.well_size_x if plate.well_spacing_x else 0
    dy = plate.well_spacing_y - plate.well_size_y if plate.well_spacing_y else 0

    # convert the spacing between wells in pixels
    dx_px = dx * well_width / plate.well_size_x if plate.well_spacing_x else 0
    dy_px = dy * well_height / plate.well_size_y if plate.well_spacing_y else 0

    # the text size is the well_height of the well divided by 3
    text_size = well_height / 3 if text else None

    # draw the wells and place them in their correct row/column position
    for row, col in product(range(plate.rows), range(plate.columns)):
        _x = (well_width * col) + (dx_px * col)
        _y = (well_height * row) + (dy_px * row)
        rect = QRectF(_x, _y, well_width, well_height)
        w = _Well(rect, row, col, plate.circular, text_size)
        w.brush = brush
        w.pen = pen
        w.setOpacity(opacity)
        scene.addItem(w)

    # # set the scene size
    plate_width = (well_width * plate.columns) + (dx_px * (plate.columns - 1))
    plate_height = (well_height * plate.rows) + (dy_px * (plate.rows - 1))

    # add some offset to the scene rect to leave some space around the plate
    offset = 20
    scene.setSceneRect(
        -offset, -offset, plate_width + (offset * 2), plate_height + (offset * 2)
    )

    # fit scene in view
    view.fitInView(scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


def get_well_center(
    plate: WellPlate, well: WellInfo, a1_center_x: float, a1_center_y: float
) -> tuple[float, float]:
    """Calculate the x, y stage coordinates of a well."""
    a1_x, a1_y = (a1_center_x, a1_center_y)
    spacing_x = plate.well_spacing_x * 1000  # µm
    spacing_y = plate.well_spacing_y * 1000  # µm

    if well.name == "A1":
        x, y = (a1_x, a1_y)
    else:
        x = a1_x + (spacing_x * well.column)
        y = a1_y - (spacing_y * well.row)
    return x, y


def apply_rotation_matrix(
    rotation_matrix: np.ndarray, center_x: float, center_y: float, x: float, y: float
) -> tuple[float, float]:
    """Apply rotation matrix to x, y coordinates."""
    center = np.array([[center_x], [center_y]])
    coords = [[x], [y]]
    transformed = np.linalg.inv(rotation_matrix).dot(coords - center) + center
    x_rotated, y_rotated = transformed
    return x_rotated[0], y_rotated[0]
