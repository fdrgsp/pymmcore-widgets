import string
from typing import Any, Optional, Tuple

from qtpy.QtCore import QRectF, Qt
from qtpy.QtGui import QBrush, QColor, QFont, QPainter, QPen, QTextOption
from qtpy.QtWidgets import QGraphicsItem

from .._util import FOV_GRAPHICS_VIEW_SIZE

ALPHABET = string.ascii_uppercase


class _Well(QGraphicsItem):
    """QGraphicsItem to draw a well of a plate."""

    def __init__(
        self,
        x: float,
        y: float,
        size_x: float,
        size_y: float,
        row: int,
        col: int,
        text_size: float,
        circular: bool,
        text_color: str = "",
    ) -> None:
        super().__init__()
        self._x = x
        self._y = y
        self._size_x = size_x
        self._size_y = size_y
        self._row = row
        self._col = col
        self._text_size = text_size
        self.circular = circular
        self.text_color = text_color

        self.brush = QBrush(Qt.green)
        self.well_shape = QRectF(self._x, self._y, self._size_x, self._size_y)

        self.setFlag(self.ItemIsSelectable, True)

    def boundingRect(self) -> QRectF:
        return self.well_shape

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.setBrush(self.brush)
        painter.setPen(QPen(Qt.black))
        if self.circular:
            painter.drawEllipse(self.well_shape)
        else:
            painter.drawRect(self.well_shape)

        font = QFont("Helvetica", int(self._text_size))
        font.setWeight(QFont.Bold)
        pen = QPen(QColor(self.text_color))
        painter.setPen(pen)
        painter.setFont(font)
        well_name = f"{ALPHABET[self._row]}{self._col + 1}"
        painter.drawText(self.well_shape, well_name, QTextOption(Qt.AlignCenter))

    def set_well_color(self, brush: QBrush) -> None:
        """Set the QBrush of the well to change the well color."""
        self.brush = brush
        self.update()

    def get_name_row_col(self) -> Tuple[str, int, int]:
        """Return the well name, row and column."""
        row = self._row
        col = self._col
        well = f"{ALPHABET[self._row]}{self._col + 1}"
        return well, row, col


class _WellArea(QGraphicsItem):
    """QGraphicsItem to draw the single well area for the _SelectFOV widget."""

    def __init__(
        self,
        circular: bool,
        start_x: float,
        start_y: float,
        width: float,
        height: float,
        pen: QPen,
    ) -> None:
        super().__init__()

        self._view_size = FOV_GRAPHICS_VIEW_SIZE  # size of _SelectFOV QGraphicsView

        self._circular = circular
        self._start_x = start_x
        self._start_y = start_y
        self._w = width
        self._h = height
        self._pen = pen

        self.rect = QRectF(self._start_x, self._start_y, self._w, self._h)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._view_size, self._view_size)

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.setPen(self._pen)
        if self._circular:
            painter.drawEllipse(self.rect)
        else:
            painter.drawRect(self.rect)


class _FOVPoints(QGraphicsItem):
    """QGraphicsItem to draw the the positions of each FOV in the _SelectFOV widget."""

    def __init__(
        self,
        center_x: float,
        center_y: float,
        scene_size_x: int,
        scene_size_y: int,
        plate_size_x: float,
        plate_size_y: float,
        image_size_mm_x: float,
        image_size_mm_y: float,
        fov_row: Optional[int] = None,
        fov_col: Optional[int] = None,
    ) -> None:
        super().__init__()

        self._view_size = FOV_GRAPHICS_VIEW_SIZE  # size of _SelectFOV QGraphicsView

        self.center_x = center_x
        self.center_y = center_y
        self.fov_row = fov_row
        self.fov_col = fov_col

        # fov width and height in scene px
        self._x_size = (scene_size_x * image_size_mm_x) / plate_size_x
        self._y_size = (scene_size_y * image_size_mm_y) / plate_size_y

        self.scene_width = scene_size_x
        self.scene_width = scene_size_y

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._view_size, self._view_size)

    def paint(self, painter: QPainter, *args) -> None:  # type: ignore
        pen = QPen()
        pen.setWidth(2)
        painter.setPen(pen)

        start_x = self.center_x - (self._x_size / 2)
        start_y = self.center_y - (self._y_size / 2)
        painter.drawRect(QRectF(start_x, start_y, self._x_size, self._y_size))

    def get_center_and_size(self) -> Tuple[float, float, int, int]:
        """Return the center and size of the FOV."""
        return self.center_x, self.center_y, self.scene_width, self.scene_width
