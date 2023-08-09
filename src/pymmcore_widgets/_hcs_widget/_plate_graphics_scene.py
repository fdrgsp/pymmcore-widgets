from __future__ import annotations

from itertools import product
from typing import TYPE_CHECKING, cast

import numpy as np
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
    from ._graphics_items import WellInfo
    from ._well_plate_model import WellPlate

SELECTED_COLOR = QBrush(Qt.GlobalColor.magenta)
UNSELECTED_COLOR = QBrush(Qt.GlobalColor.green)


class _HCSGraphicsScene(QGraphicsScene):
    """Custom QGraphicsScene to control the plate/well selection.

    To get the list of selected well info, use the `value` method
    that returns a list of snake-row-wise ordered tuples (name, row, column).
    """

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
                    self._set_selected(item, False)
                else:
                    self._set_selected(item, True)
            elif item not in self._selected_wells:
                self._set_selected(item, False)

    def _set_selected(self, item: _Well, state: bool) -> None:
        """Select or deselect the item."""
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
        try:
            x0, y0, width, height, text_size = self._plate_sizes_in_pixel(plate)
        except ZeroDivisionError:
            return

        # draw the wells and place them in their correct row/column position
        for row, col in product(range(plate.rows), range(plate.cols)):
            _x = x0 + (width * col)
            _y = y0 + (height * row)
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
            # if width * plate.cols > max_w, reduce the width and height!!!
            if width * plate.cols > max_w:
                width = max_w / plate.cols
                height = width
                # knowing the plate height (well height * number of rows) we can
                # calculate the starting y so that the plate stays in the middle of
                # the scene.
                plate_height = height * plate.rows
                start_y = (max_h / 2) - (plate_height / 2)

        # knowing the plate width (well width * number of columns) we can calculate
        # the starting x so that the plate stays in the middle of the scene.
        plate_width = width * plate.cols
        start_x = (
            max((self.width() - plate_width) / 2, 0)
            if plate_width != self.width() and self.width() > 0
            else 0
        )

        # the text size is the height of the well divided by 3
        text_size = height / 3

        return start_x, start_y, width, height, text_size

    def setValue(self, value: list[WellInfo]) -> None:
        """Select the wells listed in `value`."""
        self._clear_selection()

        for item in self.items():
            item = cast("_Well", item)
            if item.value() in value:
                self._set_selected(item, True)

    def value(self) -> list[WellInfo] | None:
        """Return the list of tuple (name, row, column) of the selected wells.

        ...in a snake-row-wise order.
        """
        wells = [item.value() for item in reversed(self.items()) if item.isSelected()]

        return self._snake_row_wise_ordered(wells) if wells else None

    def _snake_row_wise_ordered(self, wells: list[WellInfo]) -> list[WellInfo]:
        """Return a snake-row-wise ordered list of the selected wells."""
        max_row = max(wells, key=lambda well: well.row).row + 1
        max_column = max(wells, key=lambda well: well.col).col + 1

        # create an array with the max number of rows and columns
        _c, _r = np.arange(max_column), np.arange(max_row)

        # remove rows and columns that are not in the selected wells
        row_list = [item.row for item in wells]
        col_list = [item.col for item in wells]
        row_list_updated = _r[np.isin(_r, row_list)]
        col_list_updated = _c[np.isin(_c, col_list)]

        # create a meshgrid of the rows and columns
        c, r = np.meshgrid(col_list_updated, row_list_updated)
        # invert the order of the columns in the odd rows
        c[1::2, :] = c[1::2, :][:, ::-1]

        # `list(zip(r.ravel(), c.ravel()))` creates a list of snake-row-wise ordered
        # (row, col). Now we use this (row, col) info to create a list of
        # (name, row, col) in a snake-row-wise order.
        snake_ordered: list[WellInfo] = []
        for row, col in list(zip(r.ravel(), c.ravel())):
            snake_ordered.extend(
                well for well in wells if well.row == row and well.col == col
            )
        return snake_ordered
