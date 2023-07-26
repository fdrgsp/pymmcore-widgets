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

from .._util import GRAPHICS_VIEW_HEIGHT, GRAPHICS_VIEW_WIDTH
from ._graphics_items import _Well

if TYPE_CHECKING:
    from ._well_plate_database import WellPlate

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
            item.setBrush(SELECTED_COLOR)

        if well := self.itemAt(self.originCropPoint, QTransform()):
            well = cast("_Well", well)
            if well.isSelected():
                well.setBrush(UNSELECTED_COLOR)
                well.setSelected(False)
            else:
                well.setBrush(SELECTED_COLOR)
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
        item.setBrush(SELECTED_COLOR if state else UNSELECTED_COLOR)
        item.setSelected(state)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.currentQRubberBand.hide()

    def _clear_selection(self) -> None:
        """Clear the selection of all wells."""
        for item in self.items():
            item = cast("_Well", item)
            if item.isSelected():
                item.setSelected(False)
                item.setBrush(UNSELECTED_COLOR)

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
        """Calculate the size of the plate and the size of the wells to draw it."""
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

    def _get_plate_positions(self) -> list[tuple[str, int, int]] | None:
        """Return a list of (well, row, column) for each well selected.

        ...in a snake-like order.
        """
        if not self.items():
            return None

        well_list_to_order = [
            item._getPos() for item in reversed(self.items()) if item.isSelected()
        ]

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

        # def row_wise_snake(self, lst):
        """Arrange the list of tuples in a row snake direction."""
        # num_cols = max(item[1] for item in lst) + 1
        # sorted_lst = sorted(lst, key=lambda t: (t[0], -t[1] if t[0] % 2 else t[1]))
        # return [
        #     sorted_lst[i : i + num_cols]
        #     if i % (2 * num_cols) == 0
        #     else sorted_lst[i : i + num_cols][::-1]
        #     for i in range(0, len(sorted_lst), num_cols)
        # ]

        # for 'snake' acquisition
