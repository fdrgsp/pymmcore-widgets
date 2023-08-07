import string
from typing import Any, Tuple

from qtpy.QtCore import QRectF, Qt
from qtpy.QtGui import QBrush, QFont, QPainter, QPen, QTextOption
from qtpy.QtWidgets import QGraphicsItem

from pymmcore_widgets._util import FOV_GRAPHICS_VIEW_SIZE

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
    ) -> None:
        super().__init__()

        self._row = row
        self._col = col
        self._text_size = text_size
        self._circular = circular

        self._brush = QBrush(Qt.GlobalColor.green)
        self._well_shape = QRectF(x, y, size_x, size_y)

        self.setFlag(self.ItemIsSelectable, True)

    def boundingRect(self) -> QRectF:
        return self._well_shape

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.setBrush(self._brush)
        painter.setPen(QPen(Qt.GlobalColor.black))
        # draw a circular or rectangular well
        if self._circular:
            painter.drawEllipse(self._well_shape)
        else:
            painter.drawRect(self._well_shape)

        # write the well name
        font = QFont("Helvetica", int(self._text_size))
        font.setWeight(QFont.Bold)
        painter.setFont(font)
        well_name = f"{ALPHABET[self._row]}{self._col + 1}"
        painter.drawText(self._well_shape, well_name, QTextOption(Qt.AlignCenter))

    def set_well_color(self, brush: QBrush) -> None:
        """Set the QBrush of the well to change the well color."""
        self._brush = brush
        self.update()

    def get_name_row_col(self) -> Tuple[str, int, int]:
        """Return the well name, row and column in a tuple."""
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

        self._circular = circular
        self._pen = pen
        self._rect = QRectF(start_x, start_y, width, height)

    def boundingRect(self) -> QRectF:
        # FOV_GRAPHICS_VIEW_SIZE is the size of _SelectFOV QGraphicsView
        return QRectF(0, 0, FOV_GRAPHICS_VIEW_SIZE, FOV_GRAPHICS_VIEW_SIZE)

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.setPen(self._pen)
        if self._circular:
            painter.drawEllipse(self._rect)
        else:
            painter.drawRect(self._rect)


class _FOVPoints(QGraphicsItem):
    """QGraphicsItem to draw the the positions of each FOV in the _SelectFOV widget.

    The FOV is drawn as a rectangle which represents the camera FOV.
    """

    def __init__(
        self,
        center_x: float,
        center_y: float,
        # scene_width: int,
        # scene_height: int,
        fov_width: float,
        fov_height: float,
    ) -> None:
        super().__init__()

        self._view_size = FOV_GRAPHICS_VIEW_SIZE  # size of _SelectFOV QGraphicsView

        # center of the FOV in scene px
        self._center_x = center_x
        self._center_y = center_y

        # width and height of the FOV in scene px
        self.fov_width = fov_width
        self.fov_height = fov_height

        # scene width and height in scene px
        # self._scene_width = scene_width
        # self._scene_height = scene_height

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._view_size, self._view_size)

    def paint(self, painter: QPainter, *args) -> None:  # type: ignore
        pen = QPen()
        pen.setWidth(2)
        painter.setPen(pen)

        start_x = self._center_x - (self.fov_width / 2)
        start_y = self._center_y - (self.fov_height / 2)
        painter.drawRect(QRectF(start_x, start_y, self.fov_width, self.fov_height))

    # def get_center_and_size(self) -> Tuple[float, float, int, int]:
    #     """Return the center and size of the FOV."""
    #     return self._center_x, self._center_y, self._scene_width, self._scene_height

    def value(self) -> tuple[float, float]:
        """Return the center of the FOV."""
        return self._center_x, self._center_y
