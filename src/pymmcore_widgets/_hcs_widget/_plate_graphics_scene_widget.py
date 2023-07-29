from __future__ import annotations

from itertools import product
from typing import TYPE_CHECKING, cast

from qtpy.QtCore import QRect, QRectF, Qt
from qtpy.QtGui import QBrush, QTransform
from qtpy.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QRubberBand,
    QWidget,
)

from pymmcore_widgets._util import GRAPHICS_VIEW_HEIGHT, GRAPHICS_VIEW_WIDTH

from ._graphics_items import _Well

if TYPE_CHECKING:
    from ._well_plate_model import WellPlate

SELECTED_COLOR = QBrush(Qt.magenta)
UNSELECTED_COLOR = QBrush(Qt.green)


class _HCSGraphicsScene(QGraphicsScene):
    """Custom QGraphicsScene to control the plate/well selection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.well_pos = 0
        self.new_well_pos = 0

        self._selected_wells: list[QGraphicsItem] = []

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        # origin point of the SCREEN
        self.origin_point = event.screenPos()
        # rubber band to show the selection
        self.rubber_band = QRubberBand(QRubberBand.Rectangle)
        # origin point of the SCENE
        self.scene_origin_point = event.scenePos()

        # get the selected items
        self._selected_wells = [item for item in self.items() if item.isSelected()]

        # set the color of the selected wells to SELECTED_COLOR if they are within the
        # selection
        for item in self._selected_wells:
            item = cast("_Well", item)
            item.set_well_color(SELECTED_COLOR)

        # if there is an item where the mouse is pressed and it is selected, deselect,
        # otherwise select it.
        if well := self.itemAt(self.scene_origin_point, QTransform()):
            well = cast("_Well", well)
            if well.isSelected():
                well.set_well_color(UNSELECTED_COLOR)
                well.setSelected(False)
            else:
                well.set_well_color(SELECTED_COLOR)
                well.setSelected(True)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        # update the rubber band geometry using the SCREEN origin point and the current
        self.rubber_band.setGeometry(QRect(self.origin_point, event.screenPos()))
        self.rubber_band.show()
        # get the items within the selection (within the rubber band)
        selection = self.items(QRectF(self.scene_origin_point, event.scenePos()))
        # loop through all the items in the scene and select them if they are within
        # the selection or deselect them if they are not (or if the shift key is pressed
        # while moving the movuse).
        for item in self.items():
            item = cast("_Well", item)

            if item in selection:
                # if pressing shift, remove from selection
                if event.modifiers() and Qt.ShiftModifier:
                    self._select(item, False)
                else:
                    self._select(item, True)
            elif item not in self._selected_wells:
                self._select(item, False)

    def _select(self, item: _Well, state: bool) -> None:
        item.set_well_color(SELECTED_COLOR if state else UNSELECTED_COLOR)
        item.setSelected(state)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.rubber_band.hide()

    def _clear_selection(self) -> None:
        """Clear the selection of all wells."""
        for item in self.items():
            item = cast("_Well", item)
            if item.isSelected():
                item.setSelected(False)
                item.set_well_color(UNSELECTED_COLOR)

    def _draw_plate_wells(self, plate: WellPlate) -> None:
        """Draw all wells of the plate."""
        start_x, start_y, width, height, text_size = self._plate_sizes_in_pixel(plate)
        x = start_x
        y = start_y
        # draw the wells and place them in their correct row/column position
        for row, col in product(range(plate.rows), range(plate.cols)):
            _x = x + width * col
            _y = y + height * row
            self.addItem(
                _Well(_x, _y, width, height, row, col, text_size, plate.circular)
            )

    def _plate_sizes_in_pixel(
        self, plate: WellPlate
    ) -> tuple[float, float, float, float, float]:
        """Calculate the size of the plate and the size of the wells to then draw it.

        The sizes are not the real well dimensions (mm) but are the size in pixels for
        the QGraphicsView.

        Returns
        -------
            start_x: the starting pixel x coordinate of the well (x of top left corner).
            start_y: the starting pixel y coordinate of the well (y of top left corner)
            width: the width of the wells.
            height: the height of the wells.
            text_size: the size of the text used to write the name inside the wells.
        """
        max_w = GRAPHICS_VIEW_WIDTH - 10
        max_h = GRAPHICS_VIEW_HEIGHT - 10

        start_y = 0.0
        # if the plate has only one row and more than one column, the wells, width and
        # height are the same and are euqal to the width of the view divided by the
        # number of columns. The y of the starting point is the middle of the view
        # minus half of the height of the well.
        if plate.rows == 1 and plate.cols > 1:
            width = height = max_w / plate.cols
            start_y = (max_h / 2) - (height / 2)
        # if the plate has more than one row and only one column, the wells width
        # and height are the same and are euqal to the height of the view divided by the
        # number of rows.
        elif plate.cols == 1 and plate.rows > 1:
            height = width = max_h / plate.rows
        # if the plate has more than one row and more than one column, the wells height
        # is equal to the height of the view divided by the number of rows. The wells
        # width is equal to the height if the plate is circular or if the well dimension
        # (in mm) in x and y are the same otherwise the width is equal to the width of
        # the view divided by the number of columns.
        else:
            height = max_h / plate.rows
            width = (
                height
                if plate.circular or plate.well_size_x == plate.well_size_y
                else (max_w / plate.cols)
            )

        # knowing the plate width (well width * number of columns) we can calculate
        # the starting x so that it stays in the middle of the scene.
        plate_width = width * plate.cols
        start_x = (
            max((self.width() - plate_width) / 2, 0)
            if plate_width != self.width() and self.width() > 0
            else 0
        )

        # the text size is the height of the well divided by 3
        text_size = height / 3

        return start_x, start_y, width, height, text_size

    def get_wells_positions(self) -> list[tuple[str, int, int]] | None:
        """Return a list of (well, row, column) for each well selected.

        ...in a snake-like order.
        """
        if not self.items():
            return None

        well_list_to_order = [
            item.get_name_row_col()
            for item in reversed(self.items())
            if item.isSelected()
        ]

        # for 'snake' acquisition
        correct_order = []
        to_add = []
        try:
            previous_row = well_list_to_order[0][1]
        except IndexError:
            return None
        current_row = 0
        for idx, wrc in enumerate(well_list_to_order):
            _, row, _ = wrc

            if idx == 0:
                correct_order.append(wrc)
            elif row == previous_row:
                to_add.append(wrc)
                if idx == len(well_list_to_order) - 1:
                    if current_row % 2:
                        correct_order.extend(iter(reversed(to_add)))
                    else:
                        correct_order.extend(iter(to_add))
            else:
                if current_row % 2:
                    correct_order.extend(iter(reversed(to_add)))
                else:
                    correct_order.extend(iter(to_add))
                to_add.clear()
                to_add.append(wrc)
                if idx == len(well_list_to_order) - 1:
                    if current_row % 2:
                        correct_order.extend(iter(reversed(to_add)))
                    else:
                        correct_order.extend(iter(to_add))

                previous_row = row
                current_row += 1

        return correct_order
