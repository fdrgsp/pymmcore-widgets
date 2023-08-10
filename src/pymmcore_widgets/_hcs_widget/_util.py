from itertools import product

from PyQt6.QtWidgets import QWidget
from qtpy.QtCore import Qt
from qtpy.QtGui import QResizeEvent
from qtpy.QtWidgets import QGraphicsScene, QGraphicsView

from ._graphics_items import _Well
from ._well_plate_model import WellPlate

GRAPHICS_VIEW_HEIGHT = 320


class ResizingGraphicsView(QGraphicsView):
    """A QGraphicsView that resizes the scene to fit the view."""

    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


def draw_well_plate(
    view: QGraphicsView, scene: QGraphicsScene, plate: WellPlate, text: bool = True
) -> None:
    """Draw all wells of the plate."""
    scene.clear()
    width = plate.well_size_x
    height = plate.well_size_y

    dx = plate.well_spacing_x - plate.well_size_x if plate.well_spacing_x else 0
    dy = plate.well_spacing_y - plate.well_size_y if plate.well_spacing_y else 0

    # the text size is the height of the well divided by 3
    text_size = height / 3 if text else None

    # draw the wells and place them in their correct row/column position
    for row, col in product(range(plate.rows), range(plate.cols)):
        _x = (width * col) + (dx * col)
        _y = (height * row) + (dy * row)
        well = _Well(_x, _y, width, height, row, col, text_size, plate.circular)
        scene.addItem(well)

    # set the scene size
    plate_width = (width * plate.cols) + (dx * (plate.cols - 1))
    plate_height = (height * plate.rows) + (dy * (plate.rows - 1))
    # adding -5 and +10 to the scene rect to have a bit of space around the plate
    scene.setSceneRect(-3, -3, plate_width + 6, plate_height + 6)
    # fit scene in view
    view.fitInView(view.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
