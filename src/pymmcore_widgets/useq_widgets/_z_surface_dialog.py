from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import cmap
import numpy as np
from pydantic import ValidationError
from qtpy.QtCore import QPointF, QRectF, QSize, Qt
from qtpy.QtGui import QImage, QPainter, QPainterPath, QPen, QPolygonF
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
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
COLORMAP = cmap.Colormap("viridis")
MAP_SIZE = 128  # pixels per side of the evaluated Z map
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

        They share rows, so `body` always has the table's height, also when the
        table is resized with its grip.
        """
        layout = cast("QVBoxLayout", self.layout())
        grid = QGridLayout()
        grid.setVerticalSpacing(layout.spacing())
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for row, (left, right) in enumerate(
            [(self._toolbar, header), (self._table, body)]
        ):
            layout.removeWidget(left)
            grid.addWidget(left, row, 0)
            grid.addWidget(right, row, 1)
        layout.removeWidget(self._resize_grip)
        grid.addWidget(self._resize_grip, 2, 0)
        layout.addLayout(grid, 1)
        # a table made shorter with the grip leaves the extra space below it
        layout.addStretch()

    def _add_row(self) -> None:
        super()._add_row()
        self._set_from_stage(self.table().rowCount() - 1)

    def _set_from_stage(self, row: int, col: int = 0) -> None:
        if self._capture is not None:
            x, y, z = self._capture()
            self.table().setRowData(row, {"x": x, "y": y, "z": z})


class _SurfaceMap(QWidget):
    """Color-coded map of the surface Z, with the numbered reference points."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # the height follows the reference table's
        self.setMinimumWidth(280)
        self.setMouseTracking(True)
        self._surface: useq.FocusSurface | None = None
        self._xy = np.empty((0, 2))
        self._lo = self._hi = np.zeros(2)
        self._image: QImage | None = None
        self._hull = QPolygonF()  # the measured area, in µm
        self._z_range = (0.0, 0.0)
        bar = COLORMAP(np.linspace(1, 0, 256)[:, None], bytes=True)
        self._colorbar = _qimage(bar)

    def setSurface(self, surface: ZSurface) -> None:
        self._surface = None if surface.problem() else surface.focus_surface()
        self._xy = np.array(surface.points).reshape(-1, 3)[:, :2]
        self._image = None
        self._hull = QPolygonF()
        if len(self._xy):
            # pad the points' bounds, and keep a thin spread from becoming a strip
            span = np.ptp(self._xy, axis=0)
            size = max(span.max(), 1.0)
            half = np.maximum(span, size / 4) / 2 + size * 0.05
            center = (self._xy.min(axis=0) + self._xy.max(axis=0)) / 2
            self._lo, self._hi = center - half, center + half
        if self._surface is not None:
            # top image row is the largest Y: Y points up, as in the stage explorer
            xs = np.linspace(self._lo[0], self._hi[0], MAP_SIZE)
            ys = np.linspace(self._hi[1], self._lo[1], MAP_SIZE)
            z = self._surface.evaluate(np.stack(np.meshgrid(xs, ys), axis=-1))
            z0, z1 = z.min(), z.max()
            norm = (z - z0) / (z1 - z0) if z1 > z0 else np.full_like(z, 0.5)
            self._image = _qimage(
                COLORMAP(norm.reshape(MAP_SIZE, MAP_SIZE), bytes=True)
            )
            self._z_range = (z0, z1)
            self._hull = QPolygonF([QPointF(x, y) for x, y in self._surface.hull()])
        self.update()

    def _canvas_rect(self) -> QRectF:
        """Return the widget area left of the colorbar."""
        text_w = self.fontMetrics().horizontalAdvance("-0000.00 µm")
        return QRectF(self.rect()).adjusted(1, 1, -(text_w + 28), -1)

    def _plot_rect(self) -> QRectF:
        """Return the canvas area showing the points' bounds at their aspect ratio."""
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        canvas, rect = self._canvas_rect(), self._plot_rect()
        palette = self.palette()
        painter.fillRect(canvas, palette.base())
        painter.setPen(palette.mid().color())
        painter.drawRect(canvas)
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
            painter.setPen(palette.mid().color())
            bar = QRectF(canvas.right() + 8, canvas.top(), 12, canvas.height())
            painter.drawImage(bar, self._colorbar)
            painter.drawRect(bar)
            painter.setPen(palette.text().color())
            z0, z1 = self._z_range
            flags = Qt.AlignmentFlag.AlignLeft
            text_rect = bar.adjusted(bar.width() + 4, 0, 200, 0)
            painter.drawText(
                text_rect, flags | Qt.AlignmentFlag.AlignTop, f"{z1:.2f} µm"
            )
            painter.drawText(
                text_rect, flags | Qt.AlignmentFlag.AlignBottom, f"{z0:.2f} µm"
            )
        painter.setPen(palette.text().color())
        painter.setBrush(palette.base())
        for i, (x, y) in enumerate(self._xy, start=1):
            point = self._to_widget(rect, x, y)
            painter.drawEllipse(point, 4, 4)
            painter.drawText(point + QPointF(6, -6), str(i))
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if self._surface is None or event is None:
            return
        rect = self._plot_rect()
        pos = event.position()
        if not rect.contains(pos):
            QToolTip.hideText()
            return
        fx = (pos.x() - rect.left()) / rect.width()
        fy = (rect.bottom() - pos.y()) / rect.height()
        x, y = self._lo + np.array([fx, fy]) * (self._hi - self._lo)
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
    from it after focusing manually.
    """

    def __init__(
        self,
        surface: ZSurface,
        capture: Callable[[], tuple[float, float, float]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Surface Focus")

        self.points = _ReferenceTable(capture, self)

        self.method = QComboBox()
        for i, (key, label) in enumerate(METHODS.items()):
            self.method.addItem(label, key)
            self.method.setItemData(
                i, METHOD_TOOLTIPS[key], Qt.ItemDataRole.ToolTipRole
            )
        self.map = _SurfaceMap()

        self._status_icon = QLabel()
        self._summary = QLabel()
        self._summary.setWordWrap(True)

        save_btn = QPushButton("Save")
        save_btn.setIcon(QIconifyIcon("mdi:content-save-outline"))
        save_btn.setToolTip("Save the reference points to a file.")
        save_btn.clicked.connect(self.save)
        load_btn = QPushButton("Load")
        load_btn.setIcon(QIconifyIcon("material-symbols:upload-rounded"))
        load_btn.setToolTip("Load reference points from a file.")
        load_btn.clicked.connect(self.load)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        method_row = QWidget()
        form = QFormLayout(method_row)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Method:", self.method)
        self.points.setSidePanel(method_row, self.map)

        status = QHBoxLayout()
        status.addWidget(self._status_icon, 0, Qt.AlignmentFlag.AlignTop)
        status.addWidget(self._summary, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(save_btn)
        bottom.addWidget(load_btn)
        bottom.addStretch()
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self.points, 1)
        layout.addLayout(status)
        layout.addLayout(bottom)

        self.method.currentIndexChanged.connect(self._update_summary)
        self.points.valueChanged.connect(self._update_summary)
        self.setValue(surface)
        self.resize(820, 480)

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
        text = problem or _describe(surface)
        self._status_icon.setPixmap(QIconifyIcon(glyph, color=color).pixmap(ICON_SIZE))
        self._summary.setText(text)


def _describe(surface: ZSurface) -> str:
    xyz = np.array(surface.points)
    (x0, y0, z0), (x1, y1, z1) = xyz.min(axis=0), xyz.max(axis=0)
    text = (
        f"{len(xyz)} points over X {x0:.0f} to {x1:.0f} µm, Y {y0:.0f} to "
        f"{y1:.0f} µm; Z {z0:.2f} to {z1:.2f} µm."
    )
    if surface.method == "plane":
        residuals = xyz[:, 2] - surface.focus_surface().evaluate(xyz[:, :2])
        text += (
            f"\nPlane fit residuals: RMS {np.sqrt(np.mean(residuals**2)):.2f} "
            f"µm, max {np.abs(residuals).max():.2f} µm."
        )
    return text + "\nOutside the dashed outline of the points, Z is extrapolated."
