from __future__ import annotations

from itertools import groupby, product
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

from .._util import GRAPHICS_VIEW_HEIGHT, GRAPHICS_VIEW_WIDTH
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
        self.originQPoint = event.screenPos()
        self.currentQRubberBand = QRubberBand(QRubberBand.Rectangle)
        self.originCropPoint = event.scenePos()

        self._selected_wells = [item for item in self.items() if item.isSelected()]

        for item in self._selected_wells:
            item = cast("_Well", item)
            item.set_well_color(SELECTED_COLOR)

        if well := self.itemAt(self.originCropPoint, QTransform()):
            well = cast("_Well", well)
            if well.isSelected():
                well.set_well_color(UNSELECTED_COLOR)
                well.setSelected(False)
            else:
                well.set_well_color(SELECTED_COLOR)
                well.setSelected(True)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.currentQRubberBand.setGeometry(QRect(self.originQPoint, event.screenPos()))
        self.currentQRubberBand.show()
        selection = self.items(QRectF(self.originCropPoint, event.scenePos()))
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
        self.currentQRubberBand.hide()

    def _clear_selection(self) -> None:
        """Clear the selection of all wells."""
        for item in self.items():
            item = cast("_Well", item)
            if item.isSelected():
                item.setSelected(False)
                item.set_well_color(UNSELECTED_COLOR)

    def _draw_plate_wells(self, plate: WellPlate) -> None:
        """Draw the well plate."""
        start_x, start_y, size_x, size_y, text_size = self._calculate_plate_info(plate)
        x = start_x
        y = start_y
        for row, col in product(range(plate.rows), range(plate.cols)):
            _x = x + size_x * col
            _y = y + size_y * row
            self.addItem(
                _Well(_x, _y, size_x, size_y, row, col, text_size, plate.circular)
            )

    def _calculate_plate_info(
        self, plate: WellPlate
    ) -> tuple[float, float, float, float, float]:
        """Calculate the size of the plate and the size of the wells to then draw it."""
        max_w = GRAPHICS_VIEW_WIDTH - 10
        max_h = GRAPHICS_VIEW_HEIGHT - 10

        start_y = 0.0
        if plate.rows == 1 and plate.cols > 1:
            size_x = size_y = max_w / plate.cols
            start_y = (max_h / 2) - (size_y / 2)
        elif plate.cols == 1 and plate.rows > 1:
            size_y = size_x = max_h / plate.rows
        else:
            size_y = max_h / plate.rows
            size_x = (
                size_y
                if plate.circular or plate.well_size_x == plate.well_size_y
                else (max_w / plate.cols)
            )

        width = size_x * plate.cols
        start_x = (
            max((self.width() - width) / 2, 0)
            if width != self.width() and self.width() > 0
            else 0
        )

        text_size = size_y / 3

        return start_x, start_y, size_x, size_y, text_size

    def get_wells_positions(self) -> list[tuple[str, int, int]] | None:
        """Return a list of (well, row, column) for each well selected.

        ...in a snake-like order.
        """
        if not self.items():
            return None

        well_list_to_order = [
            item.get_name_row_col() for item in self.items() if item.isSelected()
        ]
        return self._arrange_in_snake_order(well_list_to_order)

    def _arrange_in_snake_order(
        self, well_list_to_order: list[tuple[str, int, int]]
    ) -> list[tuple[str, int, int]]:
        """Reorder the list of tuples in a snake-like order."""
        sorted_lst = sorted(well_list_to_order, key=lambda t: (t[1], t[2]))

        result: list[tuple[str, int, int]] = []
        for _, group in groupby(enumerate(sorted_lst), key=lambda t: t[1][1]):
            group_list = list(group)
            if group_list[0][1][1] % 2 == 0:
                result.extend(t for _, t in group_list)
            else:
                result.extend(t for _, t in group_list[::-1])

        return result
