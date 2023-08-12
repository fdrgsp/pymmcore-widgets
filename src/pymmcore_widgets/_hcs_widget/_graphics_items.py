import string
from typing import Any, NamedTuple

from qtpy.QtCore import QRectF, Qt
from qtpy.QtGui import QBrush, QFont, QPainter, QPen, QTextOption
from qtpy.QtWidgets import QGraphicsItem

ALPHABET = string.ascii_uppercase
POINT_SIZE = 5


class WellInfo(NamedTuple):
    """Tuple to store the well name, row and column."""

    well_name: str
    row: int
    col: int
    well_shape: QRectF


class _Well(QGraphicsItem):
    """QGraphicsItem to draw a well of a plate."""

    def __init__(
        self,
        rect: QRectF,
        row: int,
        col: int,
        circular: bool,
        text_size: float | None,
        brush: QBrush | None = None,
        pen: QPen | None = None,
    ) -> None:
        super().__init__()

        self._row = row
        self._col = col
        self._text_size = text_size
        self._circular = circular

        self._brush = brush or QBrush(Qt.GlobalColor.green)

        default_pen = QPen(Qt.GlobalColor.black)
        default_pen.setWidth(1)
        self._pen = pen or default_pen

        self._well_shape = rect

        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable, True)

    @property
    def brush(self) -> QBrush:
        return self._brush

    @brush.setter
    def brush(self, brush: QBrush) -> None:
        self._brush = brush
        self.update()

    @property
    def pen(self) -> QPen:
        return self._pen

    @pen.setter
    def pen(self, pen: QPen) -> None:
        self._pen = pen
        self.update()

    def boundingRect(self) -> QRectF:
        return self._well_shape

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.setBrush(self._brush)
        painter.setPen(self._pen)
        # draw a circular or rectangular well
        if self._circular:
            painter.drawEllipse(self._well_shape)
        else:
            painter.drawRect(self._well_shape)

        # write the well name
        if self._text_size is None:
            return
        font = QFont("Helvetica", int(self._text_size))
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        well_name = f"{ALPHABET[self._row]}{self._col + 1}"
        painter.drawText(
            self._well_shape, well_name, QTextOption(Qt.AlignmentFlag.AlignCenter)
        )

    def value(self) -> WellInfo:
        """Return the well name, row and column in a tuple."""
        row = self._row
        col = self._col
        well = f"{ALPHABET[self._row]}{self._col + 1}"
        return WellInfo(well_name=well, row=row, col=col, well_shape=self._well_shape)


class _WellArea(QGraphicsItem):
    """QGraphicsItem to draw the single well area for the _SelectFOV widget."""

    def __init__(self, rect: QRectF, circular: bool, pen_width: int) -> None:
        super().__init__()

        self._circular = circular
        self._pen_width = pen_width
        self._rect = rect

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(self, painter: QPainter, *args: Any) -> None:
        pen = QPen(Qt.GlobalColor.magenta)
        pen.setWidth(self._pen_width)
        painter.setPen(pen)
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
        fov_width: float,
        fov_height: float,
        bounding_rect: QRectF,
        pen: QPen | None = None,
    ) -> None:
        super().__init__()

        self._rect = bounding_rect
        # center of the FOV in scene px
        self._center_x = center_x
        self._center_y = center_y

        self.fov_width = POINT_SIZE if fov_width == 1 else fov_width
        self.fov_height = POINT_SIZE if fov_height == 1 else fov_height
        self.pen = pen or QPen(Qt.GlobalColor.white)
        self.pen.setWidth(2)
        self._use_brush = fov_width == 1 or fov_height == 1

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(self, painter: QPainter, *args) -> None:  # type: ignore
        painter.setPen(self.pen)
        if self._use_brush:
            painter.setBrush(QBrush(Qt.GlobalColor.white))
        start_x = self._center_x - (self.fov_width / 2)
        start_y = self._center_y - (self.fov_height / 2)
        painter.drawRect(QRectF(start_x, start_y, self.fov_width, self.fov_height))

    def value(self) -> tuple[float, float]:
        """Return the center of the FOV."""
        return self._center_x, self._center_y
