from __future__ import annotations

from math import ceil
from pathlib import Path
from typing import TYPE_CHECKING, cast

import cmap
import numpy as np
from pydantic import ValidationError
from qtpy.QtCore import QPointF, QRectF, QSize, QSizeF, Qt, Signal
from qtpy.QtGui import (
    QAction,
    QCursor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon

from pymmcore_widgets._util import GREEN, RED
from pymmcore_widgets._z_surface import METHODS, ZSurface

from ._column_info import ButtonColumn
from ._data_table import DataTableWidget
from ._positions import PositionTable

if TYPE_CHECKING:
    from collections.abc import Callable

    import useq
    from qtpy.QtGui import QMouseEvent, QPaintEvent

METHOD_TOOLTIPS = {
    "linear": (
        "Interpolate linearly between the three nearest reference points.\n"
        "Follows local changes of the sample height, e.g. a bowed plate."
    ),
    "plane": (
        "Fit one plane through all the reference points.\n"
        "For a flat but tilted sample: averages out focusing errors."
    ),
}
METHOD_TOOLTIP = (
    "How Z is computed from the reference points.\n\n"
    "Local linear surface: the points are joined into triangles, and inside each\n"
    "triangle Z is interpolated linearly between its 3 corners. The surface passes\n"
    "exactly through every point, so it follows local changes of the sample height\n"
    "(e.g. a bowed plate), but also any focusing error of a point.\n\n"
    "Fitted plane: the one plane closest to all the points (least squares). It\n"
    "does not pass exactly through them: it averages out focusing errors, but\n"
    "cannot follow a curved sample. For a flat, possibly tilted, sample.\n\n"
    "Extrapolated Z: the dashed outline on the map joins the outermost points.\n"
    "Outside it there are no points around to compute Z from, so Z is extended\n"
    "beyond them: the fitted plane simply continues, and the local linear surface\n"
    "continues from the nearest edge of the outline with the fitted plane's slope.\n"
    "The farther from the outline, the less reliable Z: place points around the\n"
    "whole area to image."
)
TEST_TOOLTIP = (
    "Move Z to the focus surface's Z at the current stage XY.\n\n"
    "To test the surface: in live mode, move Z away and press this button:\n"
    "Z should come back to the surface's Z at this position, the one shown on\n"
    "the map. Move the stage elsewhere and test again."
)
MOVE_HINT = "Double-click to move here, at the surface Z"
COLORMAP = cmap.Colormap("viridis")
MAP_SIZE = 128  # pixels along the longer side of the evaluated Z map
MAP_PADDING = 0.25  # around the points, as a fraction of their larger spread
COLORBAR_WIDTH = 20
WIDEST_LABEL = "-00000.00 µm"  # the widest Z label the map leaves room for
MAP_MIN_WIDTH = 280
ICON_SIZE = QSize(24, 24)
VALID = ("mdi:check-circle", GREEN)
ERROR = ("mdi:close-circle", RED)


class _ReferenceTable(DataTableWidget):
    X, Y, Z = PositionTable.X, PositionTable.Y, PositionTable.Z

    def __init__(
        self,
        capture: Callable[[], tuple[float, float, float]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(0, parent)
        self._capture = capture
        table = self.table()
        # numbered like the points on the map and in the messages
        table.verticalHeader().setVisible(True)
        # every reference point is used: there is no row selection
        self.act_check_all.setVisible(False)
        self.act_check_none.setVisible(False)
        if capture is not None:
            self.act_add_row.setToolTip("Add the current stage position")
            xyz_btn = ButtonColumn(
                key="xyz_btn", glyph="mdi:arrow-right", on_click=self._set_from_stage
            )
            table.addColumn(xyz_btn, table.indexOf(self.X))

    def setSidePanel(self, header: QWidget, body: QWidget) -> None:
        """Show `header` beside the toolbar and `body` beside the table.

        They share a row, so `body` always has the table's height. Both grow with
        the dialog: its own edges resize them, so the table's grip, which would fix
        the table's height, is hidden.
        """
        layout = cast("QVBoxLayout", self.layout())
        grid = QGridLayout()
        grid.setVerticalSpacing(layout.spacing())
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)
        for row, (left, right) in enumerate(
            [(self._toolbar, header), (self._table, body)]
        ):
            layout.removeWidget(left)
            grid.addWidget(left, row, 0)
            grid.addWidget(right, row, 1)
        self._resize_grip.hide()
        layout.addLayout(grid, 1)

    def _add_row(self) -> None:
        super()._add_row()
        self._set_from_stage(self.table().rowCount() - 1)

    def _set_from_stage(self, row: int, col: int = 0) -> None:
        if self._capture is not None:
            x, y, z = self._capture()
            self.table().setRowData(row, {"x": x, "y": y, "z": z})


class _SurfaceMap(QWidget):
    """Color-coded map of the surface Z, with the numbered reference points."""

    doubleClicked = Signal(float, float)  # the stage XY double-clicked, in µm

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # the height follows the reference table's
        self.setMouseTracking(True)
        self._surface: useq.FocusSurface | None = None
        self._xy = np.empty((0, 2))
        self._lo = self._hi = np.zeros(2)
        self._image: QImage | None = None
        self._hull = QPolygonF()  # the measured area, in µm
        self._z_range = (0.0, 0.0)
        bar = COLORMAP(np.linspace(1, 0, 256)[:, None], bytes=True)
        self._colorbar = _qimage(bar)
        self._colorbar_anchor: Callable[[], QWidget | None] = lambda: None
        self._anchor_offset = 0.0  # from the right edge, as last laid out
        self._view_size = QSizeF()
        self._hint = ""

    def setHint(self, text: str) -> None:
        """Show `text` above the map while there is a surface."""
        self._hint = text
        self.updateGeometry()
        self.update()

    def setColorbarAnchor(self, anchor: Callable[[], QWidget | None]) -> None:
        """Center the colorbar under the widget `anchor` returns, if any."""
        self._colorbar_anchor = anchor
        self.updateGeometry()
        self.update()

    def minimumSizeHint(self) -> QSize:
        """Wide enough for the hint above the canvas, beside the colorbar's labels.

        Where the colorbar sits depends on its anchor, e.g. a dialog button, whose
        place depends on the platform's button order.
        """
        if not self._hint:
            return QSize(MAP_MIN_WIDTH, 0)
        hint_w = QFontMetrics(self._hint_font()).horizontalAdvance(self._hint)
        label_w = self._label_width(WIDEST_LABEL)
        # labels are centered on the colorbar, but kept inside the widget
        offset = self._colorbar_offset()
        colorbar_room = max(offset + label_w / 2, label_w, offset + COLORBAR_WIDTH / 2)
        # the canvas' 1 px border, and its gap to the labels
        return QSize(max(MAP_MIN_WIDTH, ceil(hint_w + 9 + colorbar_room)), 0)

    def setSurface(self, surface: ZSurface) -> None:
        self._surface = None if surface.problem() else surface.focus_surface()
        self._xy = np.array(surface.points).reshape(-1, 3)[:, :2]
        self._hull = QPolygonF()
        if self._surface is not None:
            self._hull = QPolygonF([QPointF(x, y) for x, y in self._surface.hull()])
        self._fit_view()
        self.update()

    def _fit_view(self) -> None:
        """Fill the canvas with the area around the points, and its Z map."""
        self._image = None
        if len(self._xy):
            self._evaluate(self._canvas_rect())
            # the Z labels' widths set the canvas: once they changed, fit it again
            if self._view_changed():
                self._evaluate(self._canvas_rect())

    def _ensure_view(self) -> None:
        """Fit the view again if the canvas changed size since it was fitted.

        The canvas follows the widget, but also the colorbar's anchor buttons and
        the Z labels, so this is checked before painting rather than on resize.
        """
        if self._view_changed():
            self._fit_view()
        # the minimum width depends on where the anchor is: once it moved, e.g.
        # when first laid out, ask for a new layout
        if abs((offset := self._colorbar_offset()) - self._anchor_offset) >= 1:
            self._anchor_offset = offset
            self.updateGeometry()

    def _view_changed(self) -> bool:
        """Whether the canvas changed size by a pixel or more since the fit."""
        size, fitted = self._canvas_rect().size(), self._view_size
        dw, dh = size.width() - fitted.width(), size.height() - fitted.height()
        return bool(abs(dw) >= 1 or abs(dh) >= 1)

    def _evaluate(self, canvas: QRectF) -> None:
        """Set the view to fill `canvas`, padded around the points, and its Z map."""
        self._view_size = canvas.size()
        span = np.ptp(self._xy, axis=0)
        half = span / 2 + max(span.max(), 1.0) * MAP_PADDING
        # widen the narrower axis to the canvas' aspect ratio
        aspect = canvas.width() / canvas.height() if canvas.height() > 0 else 1.0
        half = np.maximum(half, (half[1] * aspect, half[0] / aspect))
        center = (self._xy.min(axis=0) + self._xy.max(axis=0)) / 2
        self._lo, self._hi = center - half, center + half
        if self._surface is None:
            return
        # MAP_SIZE pixels along the longer side; the top row is the largest Y, so
        # Y points up, as in the stage explorer
        nx, ny = np.maximum(np.round(MAP_SIZE * half / half.max()), 2).astype(int)
        xs = np.linspace(self._lo[0], self._hi[0], nx)
        ys = np.linspace(self._hi[1], self._lo[1], ny)
        z = self._surface.evaluate(np.stack(np.meshgrid(xs, ys), axis=-1))
        z0, z1 = z.min(), z.max()
        norm = (z - z0) / (z1 - z0) if z1 > z0 else np.full_like(z, 0.5)
        self._image = _qimage(COLORMAP(norm.reshape(ny, nx), bytes=True))
        self._z_range = (z0, z1)

    def _label_width(self, text: str) -> int:
        return int(self.fontMetrics().horizontalAdvance(text)) + 4

    def _labels(self) -> tuple[str, str] | tuple[()]:
        """Return the Z labels above and below the colorbar, if there is a surface."""
        if self._image is None:
            return ()
        z0, z1 = self._z_range
        return f"{z1:.2f} µm", f"{z0:.2f} µm"

    def _label_left(self, text: str) -> float:
        """Return the left of `text`: centered on the colorbar, inside the widget."""
        w = self._label_width(text)
        return float(min(self._colorbar_x() - w / 2, self.rect().right() - w))

    def _colorbar_offset(self) -> float:
        """Return the distance of the colorbar's center from the right edge."""
        # by default, centered in the room a typical label needs
        offset = self._label_width("-0000.00 µm") / 2
        if (anchor := self._colorbar_anchor()) is not None:
            window = self.window()
            center = anchor.mapTo(window, anchor.rect().center())
            offset = self.rect().right() - self.mapFrom(window, center).x()
        return float(max(offset, COLORBAR_WIDTH / 2))

    def _colorbar_x(self) -> float:
        """Return the X of the colorbar's center."""
        return float(self.rect().right() - self._colorbar_offset())

    def _canvas_rect(self) -> QRectF:
        """Return the widget area left of the colorbar and its labels."""
        lefts = [self._label_left(text) for text in self._labels()]
        lefts.append(self._colorbar_x() - COLORBAR_WIDTH / 2)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        rect.setRight(min(lefts) - 8)
        # room for the hint above, kept without a surface too: no jump when it shows
        rect.setTop(rect.top() + self._hint_height())
        return rect

    def _hint_font(self) -> QFont:
        font = self.font()
        font.setPointSizeF(font.pointSizeF() * 0.9)
        return font

    def _hint_height(self) -> int:
        # with a gap below, for the labels of points at the canvas top
        return int(QFontMetrics(self._hint_font()).height()) + 12 if self._hint else 0

    def _colorbar_rect(self) -> QRectF:
        """Return the colorbar: beside the canvas, under its anchor.

        It leaves a Z label's height above and below it, within the canvas height.
        """
        canvas = self._canvas_rect()
        text_h = self._label_height()
        bar = QRectF(0, 0, COLORBAR_WIDTH, max(canvas.height() - 2 * text_h, 0))
        bar.moveCenter(QPointF(self._colorbar_x(), canvas.center().y()))
        return bar

    def _label_height(self) -> int:
        return int(self.fontMetrics().height()) + 4

    def _label_rect(self, text: str, top: bool) -> QRectF:
        """Return where `text` is drawn above (`top`) or below the colorbar.

        Centered on the colorbar, but kept inside the widget.
        """
        bar, w, h = self._colorbar_rect(), self._label_width(text), self._label_height()
        y = bar.top() - h if top else bar.bottom()
        return QRectF(self._label_left(text), y, w, h)

    def _plot_rect(self) -> QRectF:
        """Return the canvas area showing the view, at its aspect ratio."""
        canvas = self._canvas_rect()
        w, h = (self._hi - self._lo) if len(self._xy) else (1.0, 1.0)
        scale = min(canvas.width() / w, canvas.height() / h)
        rect = QRectF(0, 0, w * scale, h * scale)
        rect.moveCenter(canvas.center())
        return rect

    def _to_widget(self, rect: QRectF, x: float, y: float) -> QPointF:
        fx, fy = (np.array([x, y]) - self._lo) / (self._hi - self._lo)
        return QPointF(
            rect.left() + fx * rect.width(), rect.bottom() - fy * rect.height()
        )

    def paintEvent(self, event: QPaintEvent | None) -> None:
        self._ensure_view()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        canvas, rect = self._canvas_rect(), self._plot_rect()
        palette = self.palette()
        painter.fillRect(canvas, palette.base())
        painter.setPen(palette.mid().color())
        painter.drawRect(canvas)
        # the colorbar shows the colormap even without a surface, unlabeled
        painter.setPen(palette.mid().color())
        bar = self._colorbar_rect()
        painter.drawImage(bar, self._colorbar)
        painter.drawRect(bar)
        if self._image is not None:
            painter.drawImage(rect, self._image)
            # dim the extrapolated area, outside the measured one
            measured = QPainterPath()
            measured.addPolygon(
                QPolygonF([self._to_widget(rect, p.x(), p.y()) for p in self._hull])
            )
            measured.closeSubpath()
            extrapolated = QPainterPath()
            extrapolated.addRect(rect)
            dim = palette.base().color()
            dim.setAlpha(120)
            painter.fillPath(extrapolated.subtracted(measured), dim)
            painter.setPen(QPen(palette.text().color(), 1, Qt.PenStyle.DashLine))
            painter.drawPath(measured)
            painter.setPen(palette.text().color())
            center = Qt.AlignmentFlag.AlignCenter
            for text, top in zip(self._labels(), (True, False), strict=False):
                painter.drawText(self._label_rect(text, top), center, text)
        if self._hint and self._image is not None:
            self._draw_hint(painter, canvas)
        painter.setPen(palette.text().color())
        painter.setBrush(palette.base())
        for i, (x, y) in enumerate(self._xy, start=1):
            point = self._to_widget(rect, x, y)
            painter.drawEllipse(point, 4, 4)
            painter.drawText(point + QPointF(6, -6), str(i))
        painter.end()

    def _draw_hint(self, painter: QPainter, canvas: QRectF) -> None:
        """Draw the hint centered above the canvas."""
        box = QRectF(canvas.left(), 0, canvas.width(), canvas.top() - 6)
        font = self._hint_font()
        text = QFontMetrics(font).elidedText(
            self._hint, Qt.TextElideMode.ElideRight, int(box.width())
        )
        painter.save()
        painter.setFont(font)
        painter.setPen(self.palette().placeholderText().color())
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _stage_xy(self, pos: QPointF) -> tuple[float, float] | None:
        """Return the stage XY at `pos` on the map, if there is one."""
        self._ensure_view()
        rect = self._plot_rect()
        if self._surface is None or not rect.contains(pos):
            return None
        fx = (pos.x() - rect.left()) / rect.width()
        fy = (rect.bottom() - pos.y()) / rect.height()
        x, y = self._lo + np.array([fx, fy]) * (self._hi - self._lo)
        return float(x), float(y)

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        if event is not None and (xy := self._stage_xy(event.position())):
            self.doubleClicked.emit(*xy)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if self._surface is None or event is None:
            return
        if (xy := self._stage_xy(event.position())) is None:
            QToolTip.hideText()
            return
        x, y = xy
        z = self._surface.evaluate([(x, y)])[0]
        fill = Qt.FillRule.OddEvenFill
        note = (
            "" if self._hull.containsPoint(QPointF(x, y), fill) else " (extrapolated)"
        )
        text = f"X {x:.0f} µm, Y {y:.0f} µm\nZ {z:.2f} µm{note}"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)


def _qimage(rgba: np.ndarray) -> QImage:
    h, w = rgba.shape[:2]
    data = np.ascontiguousarray(rgba)
    fmt = QImage.Format.Format_RGBA8888
    return QImage(data.data, w, h, 4 * w, fmt).copy()


class ZSurfaceDialog(QDialog):
    """Edit the reference points and method of a `ZSurface`.

    `capture` returns the current stage XYZ: given, points can be added or updated
    from it after focusing manually. `move_z` moves the focus stage: given too, the
    surface can be tested by moving Z to it at the current XY. `move_xy` moves the
    XY stage: given with `move_z`, double-clicking the map moves the stage there,
    with Z on the surface.
    """

    def __init__(
        self,
        surface: ZSurface,
        capture: Callable[[], tuple[float, float, float]] | None = None,
        move_z: Callable[[float], object] | None = None,
        move_xy: Callable[[float, float], object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Surface Focus")
        self._capture = capture
        self._move_z = move_z
        self._move_xy = move_xy

        self.points = _ReferenceTable(capture, self)

        self.method = QComboBox()
        for i, (key, label) in enumerate(METHODS.items()):
            self.method.addItem(label, key)
            self.method.setItemData(
                i, METHOD_TOOLTIPS[key], Qt.ItemDataRole.ToolTipRole
            )
        self.method.setToolTip(METHOD_TOOLTIP)
        self.map = _SurfaceMap()
        if move_xy is not None and move_z is not None:
            self.map.setHint(MOVE_HINT)
            self.map.doubleClicked.connect(self.move_to)

        self._status_icon = QLabel()
        self._summary = QLabel()
        self._summary.setWordWrap(True)

        # in the table's toolbar, left of the spacer pinning its buttons right
        gray = "#666"
        self.act_save = QAction(
            QIconifyIcon("mdi:content-save-outline", color=gray),
            "Save the reference points to a file",
            self,
        )
        self.act_save.triggered.connect(self.save)
        self.act_load = QAction(
            QIconifyIcon("material-symbols:upload-rounded", color=gray),
            "Load reference points from a file",
            self,
        )
        self.act_load.triggered.connect(self.load)
        toolbar = self.points.toolBar()
        toolbar.insertActions(toolbar.actions()[0], [self.act_save, self.act_load])

        self.test_btn = QPushButton("Test")
        self.test_btn.setIcon(QIconifyIcon("mdi:target"))
        self.test_btn.setToolTip(TEST_TOOLTIP)
        # Enter must not move the stage
        self.test_btn.setAutoDefault(False)
        self.test_btn.setVisible(capture is not None and move_z is not None)
        self.test_btn.clicked.connect(self.test)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._buttons = buttons
        self.map.setColorbarAnchor(self._colorbar_anchor)

        method_row = QWidget()
        method_layout = QHBoxLayout(method_row)
        method_layout.setContentsMargins(0, 0, 0, 0)
        method_layout.setSpacing(0)
        method_layout.addWidget(QLabel("Method:"))
        method_layout.addSpacing(6)
        method_layout.addWidget(self.method, 1)
        method_layout.addWidget(self.test_btn)
        self.points.setSidePanel(method_row, self.map)

        bottom = QHBoxLayout()
        bottom.addWidget(self._status_icon)
        bottom.addWidget(self._summary, 1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self.points, 1)
        layout.addLayout(bottom)

        self.method.currentIndexChanged.connect(self._update_summary)
        self.points.valueChanged.connect(self._update_summary)
        self.setValue(surface)
        self.resize(820, 480)

    def _colorbar_anchor(self) -> QWidget | None:
        """Return the button to center the map's colorbar under.

        The Test button, or else the rightmost dialog button: OK on macOS, Cancel
        where the platform puts OK first.
        """
        if self.test_btn.isVisibleTo(self):
            return self.test_btn
        visible = [b for b in self._buttons.buttons() if b.isVisibleTo(self)]
        return max(
            visible, key=lambda b: b.mapTo(self, b.rect().topRight()).x(), default=None
        )

    def value(self) -> ZSurface:
        """Return the surface being edited."""
        points = [(r["x"], r["y"], r["z"]) for r in self.points.value()]
        return ZSurface(method=self.method.currentData(), points=points)

    def setValue(self, surface: ZSurface) -> None:
        """Show `surface` for editing."""
        self.method.setCurrentIndex(self.method.findData(surface.method))
        rows = [dict(zip("xyz", p, strict=True)) for p in surface.points]
        self.points.setValue(rows)
        self._update_summary()

    def save(self, file: str | Path | None = None) -> None:
        """Save the surface to a JSON file."""
        if not isinstance(file, (str, Path)):
            file, _ = QFileDialog.getSaveFileName(
                self, "Save Surface", "", "json(*.json)"
            )
            if not file:
                return  # pragma: no cover
        Path(file).write_text(self.value().model_dump_json(indent=2))

    def test(self) -> None:
        """Move Z to the surface at the current stage XY."""
        if self._capture is None or self._move_z is None:
            return  # pragma: no cover
        surface = self.value()
        if surface.problem():
            return  # pragma: no cover
        x, y, _ = self._capture()
        z, note = _surface_z(surface, x, y)
        try:
            self._move_z(z)
        except RuntimeError as e:
            QMessageBox.warning(
                self, "Test Surface", f"Cannot move Z to {z:.2f} µm:\n\n{e}"
            )
            return
        text = f"Z moved to {z:.2f} µm{note}\nat X {x:.0f} µm, Y {y:.0f} µm."
        pos = self.test_btn.mapToGlobal(self.test_btn.rect().topLeft())
        QToolTip.showText(pos, text, self.test_btn)

    def move_to(self, x: float, y: float) -> None:
        """Move the stage to `x`, `y`, with Z on the surface."""
        if self._move_xy is None or self._move_z is None:
            return  # pragma: no cover
        surface = self.value()
        if surface.problem():
            return  # pragma: no cover
        z, note = _surface_z(surface, x, y)
        try:
            self._move_xy(x, y)
            self._move_z(z)
        except RuntimeError as e:
            QMessageBox.warning(
                self,
                "Move Stage",
                f"Cannot move to X {x:.0f} µm, Y {y:.0f} µm, Z {z:.2f} µm:\n\n{e}",
            )
            return
        text = f"Moved to X {x:.0f} µm, Y {y:.0f} µm\nZ {z:.2f} µm{note}."
        QToolTip.showText(QCursor.pos(), text, self.map)

    def load(self, file: str | Path | None = None) -> None:
        """Load a surface from a JSON file."""
        if not isinstance(file, (str, Path)):
            file, _ = QFileDialog.getOpenFileName(
                self, "Load Surface", "", "json(*.json)"
            )
            if not file:
                return  # pragma: no cover
        try:
            surface = ZSurface.model_validate_json(Path(file).read_text())
        except (OSError, ValidationError) as e:
            QMessageBox.warning(self, "Load Surface", f"Cannot load {file}:\n\n{e}")
            return
        self.setValue(surface)

    def _update_summary(self) -> None:
        surface = self.value()
        self.map.setSurface(surface)
        problem = surface.problem()
        glyph, color = ERROR if problem else VALID
        text = problem or f"Surface computed from {len(surface.points)} points."
        self._status_icon.setPixmap(QIconifyIcon(glyph, color=color).pixmap(ICON_SIZE))
        self._summary.setText(text)
        self._summary.setToolTip("" if problem else _residuals(surface))
        self.test_btn.setEnabled(not problem)


def _surface_z(surface: ZSurface, x: float, y: float) -> tuple[float, str]:
    """Return the surface Z at `x`, `y`, and a note if it is extrapolated there."""
    focus_surface = surface.focus_surface()
    z = float(focus_surface.evaluate([(x, y)])[0])
    hull = QPolygonF([QPointF(*p) for p in focus_surface.hull()])
    inside = hull.containsPoint(QPointF(x, y), Qt.FillRule.OddEvenFill)
    return z, "" if inside else " (extrapolated)"


def _residuals(surface: ZSurface) -> str:
    """Return the plane fit residuals, if there are any."""
    # 3 points fit a plane exactly, and a linear surface passes through all its
    # points: residuals are always 0 then
    if surface.method != "plane" or len(surface.points) <= 3:
        return ""
    xyz = np.array(surface.points)
    residuals = xyz[:, 2] - surface.focus_surface().evaluate(xyz[:, :2])
    return (
        f"Plane fit residuals: RMS {np.sqrt(np.mean(residuals**2)):.2f} µm, "
        f"max {np.abs(residuals).max():.2f} µm."
    )
