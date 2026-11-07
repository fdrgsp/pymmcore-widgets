from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import numpy as np
import pytest
import useq
from qtpy.QtCore import QEvent, Qt
from qtpy.QtGui import QFontMetrics, QMouseEvent, QPainter
from qtpy.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QToolTip,
    QWidget,
)
from superqt.iconify import QIconifyIcon

from pymmcore_widgets._z_surface import ZSurface
from pymmcore_widgets.useq_widgets import PositionTable
from pymmcore_widgets.useq_widgets._positions import (
    INCLUDE_Z_SURFACE_TOOLTIP,
    SURFACE_HAF_TOOLTIP,
    SURFACE_INCLUDE_Z_TOOLTIP,
    SURFACE_SETTINGS_TOOLTIP,
)
from pymmcore_widgets.useq_widgets._z_surface_dialog import (
    ERROR,
    ICON_SIZE,
    MAP_MIN_WIDTH,
    METHOD_TOOLTIP,
    MOVE_HINT,
    VALID,
    ZSurfaceDialog,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytestqt.qtbot import QtBot

SURFACE = ZSurface(method="plane", points=[(0, 0, 0), (100, 0, 1), (0, 100, 2)])


def _accept(dialog: ZSurfaceDialog) -> int:
    dialog.setValue(SURFACE)
    return QDialog.DialogCode.Accepted


def test_dialog_value(qtbot: QtBot) -> None:
    dialog = ZSurfaceDialog(SURFACE)
    qtbot.addWidget(dialog)
    assert dialog.value() == SURFACE
    assert dialog.method.toolTip() == METHOD_TOOLTIP
    # 3 points fit a plane exactly: no residuals to show
    assert dialog._summary.text() == "Surface computed from 3 points."
    assert dialog._summary.toolTip() == ""
    dialog.setValue(ZSurface(method="plane", points=[*SURFACE.points, (100, 100, 4)]))
    # the residuals are in the tooltip
    assert dialog._summary.text() == "Surface computed from 4 points."
    assert dialog._summary.toolTip() == (
        "Plane fit residuals: RMS 0.25 µm, max 0.25 µm."
    )
    # a linear surface passes through all its points
    dialog.method.setCurrentIndex(dialog.method.findData("linear"))
    assert dialog._summary.text() == "Surface computed from 4 points."
    assert dialog._summary.toolTip() == ""
    dialog.setValue(SURFACE)
    assert dialog.map._image is not None
    # no stage: no column to set a point from it, nor a Test button
    assert dialog.points.table().columnCount() == 3
    assert dialog.test_btn.isHidden()

    dialog.points.table().removeRow(2)
    assert dialog._summary.text() == "At least 3 reference points are needed (2 set)."
    assert dialog.map._image is None
    dialog.method.setCurrentIndex(dialog.method.findData("linear"))
    assert dialog.value().method == "linear"


@pytest.mark.parametrize(
    "points, glyph",
    [
        (SURFACE.points, VALID),
        (SURFACE.points[:2], ERROR),
        ([*SURFACE.points[:2], (0, 0, 1)], ERROR),
    ],
)
def test_dialog_status_icon(qtbot: QtBot, points: list, glyph: tuple) -> None:
    dialog = ZSurfaceDialog(ZSurface(points=points))
    qtbot.addWidget(dialog)
    expected = QIconifyIcon(glyph[0], color=glyph[1]).pixmap(ICON_SIZE).toImage()
    assert dialog._status_icon.pixmap().toImage() == expected


def test_dialog_map(qtbot: QtBot) -> None:
    dialog = ZSurfaceDialog(SURFACE)
    qtbot.addWidget(dialog)
    dialog.show()
    surface_map = dialog.map
    assert not surface_map.grab().isNull()  # fits the view to the laid out canvas
    # the view: the points padded by a quarter of their spread, at least, and
    # widened to fill the canvas
    lo, hi = surface_map._lo, surface_map._hi
    assert (lo <= (-25, -25)).all() and (hi >= (125, 125)).all()
    # only the narrower axis widened
    assert np.isclose(lo, -25).any() and np.isclose(hi, 125).any()
    canvas = surface_map._canvas_rect()
    w, h = hi - lo
    assert w / h == pytest.approx(canvas.width() / canvas.height(), rel=0.01)
    # the Z range of the whole view, extrapolated around the points too
    plane_z = 0.01 * np.array([lo[0], hi[0]]) + 0.02 * np.array([lo[1], hi[1]])
    assert surface_map._z_range == pytest.approx(tuple(plane_z), abs=0.1)
    assert not surface_map.grab().isNull()
    # the colorbar is right of the canvas, its labels above and below it
    bar, canvas = surface_map._colorbar_rect(), surface_map._canvas_rect()
    z0, z1 = surface_map._z_range
    high, low = surface_map._labels()
    assert (high, low) == (f"{z1:.2f} µm", f"{z0:.2f} µm")
    top = surface_map._label_rect(high, top=True)
    bottom = surface_map._label_rect(low, top=False)
    assert top.bottom() == bar.top() and bottom.top() == bar.bottom()
    assert top.top() >= canvas.top() and bottom.bottom() <= canvas.bottom()
    assert top.center().x() == pytest.approx(bar.center().x())
    # the canvas ends just before the widest label
    assert canvas.right() == min(top.left(), bottom.left()) - 8
    # a wide label stays inside the widget, and the canvas clear of it
    dialog.setValue(ZSurface(points=[(0, 0, -10000), (100, 0, 0), (0, 100, 10000)]))
    canvas = surface_map._canvas_rect()
    for text, top_ in zip(surface_map._labels(), (True, False), strict=True):
        rect = surface_map._label_rect(text, top_)
        assert canvas.right() < rect.left()
        assert rect.right() <= surface_map.rect().right()
    # without a surface, it ends just before the colorbar
    dialog.setValue(ZSurface())
    bar, canvas = surface_map._colorbar_rect(), surface_map._canvas_rect()
    assert surface_map._labels() == ()
    assert canvas.right() == bar.left() - 8
    dialog.setValue(SURFACE)

    rect = surface_map._plot_rect()
    no_button, no_mod = Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier
    texts = []
    for xy in [(10, 10), (90, 90)]:
        pos = surface_map._to_widget(rect, *xy)
        event = QMouseEvent(
            QEvent.Type.MouseMove, pos, pos, no_button, no_button, no_mod
        )
        with patch.object(QToolTip, "showText") as show:
            surface_map.mouseMoveEvent(event)
        texts.append(show.call_args.args[1])
    assert texts == [
        "X 10 µm, Y 10 µm\nZ 0.30 µm",
        "X 90 µm, Y 90 µm\nZ 2.70 µm (extrapolated)",
    ]


@pytest.mark.parametrize("stage", [True, False])
def test_dialog_colorbar_under_the_buttons(qtbot: QtBot, stage: bool) -> None:
    kwargs = {"capture": lambda: (0.0, 0.0, 0.0), "move_z": print} if stage else {}
    dialog = ZSurfaceDialog(SURFACE, **kwargs)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    dialog.show()
    # under the Test button, or OK without a stage to test with
    ok = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
    anchor = dialog.test_btn if stage else ok
    for size in ((820, 480), (1000, 600)):
        dialog.resize(*size)
        qtbot.waitUntil(lambda: dialog.map.width() > 0)

        def _x(widget: QWidget, x: float) -> float:
            return widget.mapTo(dialog, widget.rect().topLeft()).x() + x

        bar_x = _x(dialog.map, dialog.map._colorbar_rect().center().x())
        assert bar_x == pytest.approx(_x(anchor, anchor.width() / 2), abs=1)


def test_dialog_table_and_map_follow_the_dialog(qtbot: QtBot) -> None:
    dialog = ZSurfaceDialog(SURFACE)
    qtbot.addWidget(dialog)
    dialog.show()
    table = dialog.points.table()
    assert dialog.points._resize_grip.isHidden()
    heights = []
    for size in ((820, 480), (1000, 800), (700, 400)):
        dialog.resize(*size)
        qtbot.waitUntil(lambda: _rows(dialog.map) == _rows(table))
        qtbot.waitUntil(lambda: dialog.map.width() > 0)
        heights.append((table.height(), dialog.map.width()))
    # both grow and shrink with the dialog
    assert heights[1][0] > heights[0][0] > heights[2][0]
    assert heights[1][1] > heights[0][1] > heights[2][1]


def _rows(widget: QWidget) -> tuple[int, int]:
    return widget.geometry().top(), widget.geometry().bottom()


def test_dialog_capture(qtbot: QtBot) -> None:
    xyz = iter([(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])
    dialog = ZSurfaceDialog(SURFACE, capture=lambda: next(xyz))
    qtbot.addWidget(dialog)
    table = dialog.points

    table.act_add_row.trigger()
    assert dialog.value().points[-1] == (1, 2, 3)
    table.table().cellWidget(0, table.table().indexOf("")).click()
    assert dialog.value().points[0] == (4, 5, 6)


def test_dialog_test_button(qtbot: QtBot) -> None:
    moves: list[float] = []
    dialog = ZSurfaceDialog(
        SURFACE, capture=lambda: (10.0, 10.0, 99.0), move_z=moves.append
    )
    qtbot.addWidget(dialog)
    btn = dialog.test_btn
    assert not btn.isHidden() and btn.isEnabled()
    assert not btn.autoDefault()

    with patch.object(QToolTip, "showText") as show:
        btn.click()
    assert moves == [pytest.approx(0.3)]
    assert show.call_args.args[1] == "Z moved to 0.30 µm\nat X 10 µm, Y 10 µm."

    def fail(z: float) -> None:
        raise RuntimeError("out of range")

    dialog._move_z = fail
    with patch.object(QMessageBox, "warning") as warning:
        btn.click()
    assert "out of range" in warning.call_args.args[2]

    # without a valid surface there is nothing to test
    dialog.points.table().removeRow(2)
    assert not btn.isEnabled()


def test_dialog_map_without_surface(qtbot: QtBot) -> None:
    dialog = ZSurfaceDialog(ZSurface())
    qtbot.addWidget(dialog)
    dialog.show()
    # the colorbar, without Z labels
    with patch.object(QPainter, "drawText") as draw_text:
        assert not dialog.map.grab().isNull()
    draw_text.assert_not_called()


def test_dialog_double_click_moves_the_stage(qtbot: QtBot) -> None:
    moves: list[tuple] = []
    dialog = ZSurfaceDialog(
        SURFACE,
        move_z=lambda z: moves.append(("z", z)),
        move_xy=lambda x, y: moves.append(("xy", x, y)),
    )
    qtbot.addWidget(dialog)
    dialog.show()
    surface_map = dialog.map
    assert surface_map._hint == MOVE_HINT
    assert not surface_map.grab().isNull()  # with the hint drawn

    rect = surface_map._plot_rect()
    pos = surface_map._to_widget(rect, 10, 10)
    with qtbot.waitSignal(surface_map.doubleClicked) as blocker:
        with patch.object(QToolTip, "showText") as show:
            qtbot.mouseDClick(surface_map, Qt.MouseButton.LeftButton, pos=pos.toPoint())
    x, y = blocker.args
    # XY first, then Z on the surface there: 0.01 x + 0.02 y
    assert moves == [("xy", x, y), ("z", pytest.approx(0.01 * x + 0.02 * y))]
    assert (x, y) == pytest.approx((10, 10), abs=1)
    assert show.call_args.args[1].startswith(f"Moved to X {x:.0f} µm, Y {y:.0f} µm")

    # outside the map: nothing
    moves.clear()
    outside = surface_map.rect().bottomRight()
    qtbot.mouseDClick(surface_map, Qt.MouseButton.LeftButton, pos=outside)
    assert moves == []

    def fail(x: float, y: float) -> None:
        raise RuntimeError("out of range")

    dialog._move_xy = fail
    with patch.object(QMessageBox, "warning") as warning:
        dialog.move_to(10, 10)
    assert "out of range" in warning.call_args.args[2]
    assert moves == []

    # no surface: no hint, nor moving
    dialog.setValue(ZSurface())
    with patch.object(QPainter, "drawText") as draw_text:
        surface_map.grab()
    draw_text.assert_not_called()


def test_dialog_hint_fits_above_the_map(qtbot: QtBot) -> None:
    dialog = ZSurfaceDialog(
        ZSurface(points=[(0, 0, -10000), (100, 0, 0), (0, 100, 10000)]),
        move_z=print,
        move_xy=print,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.resize(1, 1)  # as small as it gets
    surface_map = dialog.map
    qtbot.waitUntil(lambda: surface_map.width() == surface_map.minimumWidth())
    canvas = surface_map._canvas_rect()
    hint_w = QFontMetrics(surface_map._hint_font()).horizontalAdvance(MOVE_HINT)
    assert canvas.width() >= hint_w
    # in its own room above the canvas
    assert canvas.top() >= QFontMetrics(surface_map._hint_font()).height()


def test_dialog_without_xy_stage_has_no_hint(qtbot: QtBot) -> None:
    dialog = ZSurfaceDialog(SURFACE, move_z=print)
    qtbot.addWidget(dialog)
    assert dialog.map._hint == ""
    assert dialog.map.minimumWidth() == MAP_MIN_WIDTH
    assert dialog.map._canvas_rect().top() == 1


def test_dialog_save_load(qtbot: QtBot, tmp_path: Path) -> None:
    dialog = ZSurfaceDialog(SURFACE)
    qtbot.addWidget(dialog)
    # icons left of the toolbar's spacer, which pins the table's buttons right
    actions = dialog.points.toolBar().actions()
    assert actions[:2] == [dialog.act_save, dialog.act_load]
    with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")):
        dialog.act_save.trigger()  # cancelled: nothing saved
    dialog.save(file := tmp_path / "surface.json")
    dialog.setValue(ZSurface())
    dialog.load(file)
    assert dialog.value() == SURFACE

    file.write_text('{"method": "cubic"}')
    with patch.object(QMessageBox, "warning") as warning:
        dialog.load(file)
    warning.assert_called_once()
    assert dialog.value() == SURFACE


def test_position_table_surface_focus(qtbot: QtBot) -> None:
    table = PositionTable()
    qtbot.addWidget(table)
    sf = table.surface_focus
    assert table.surface() == ZSurface()
    gear = table._surface_settings
    # checked by default: "Include Z" rules it out, and setting it up
    assert not sf.isEnabled()
    assert sf.toolTip() == SURFACE_INCLUDE_Z_TOOLTIP
    assert not gear.isEnabled()
    assert gear.toolTip() == SURFACE_INCLUDE_Z_TOOLTIP
    table.include_z.setChecked(False)
    assert gear.isEnabled()
    assert gear.toolTip() == SURFACE_SETTINGS_TOOLTIP
    # without a valid surface it is enabled, but cannot be checked
    assert sf.isEnabled() and not sf.isCheckable()
    assert ZSurface().problem() in sf.toolTip()

    with qtbot.waitSignal(table.valueChanged):
        table.setSurface(SURFACE)
    assert sf.isCheckable()
    assert "Fitted plane through 3 points." in sf.toolTip()
    # checked, it unchecks and disables "Include Z", until it is unchecked
    sf.setChecked(True)
    inc_z = table.include_z
    assert not inc_z.isChecked() and not inc_z.isEnabled()
    assert inc_z.toolTip() == INCLUDE_Z_SURFACE_TOOLTIP
    sf.setChecked(False)
    assert inc_z.isEnabled() and not inc_z.isChecked()
    assert inc_z.toolTip() == ""

    # loaded positions with Z keep it, unused while Surface Focus is checked
    sf.setChecked(True)
    table.setValue([useq.Position(x=0, y=0, z=1)])
    assert sf.isChecked() and not inc_z.isChecked()
    sf.setChecked(False)
    inc_z.setChecked(True)
    assert table.value()[0].z == 1
    # checked, "Include Z" disables Surface Focus, keeping the surface
    assert not sf.isEnabled() and sf.toolTip() == SURFACE_INCLUDE_Z_TOOLTIP
    assert table.surface() == SURFACE
    inc_z.setChecked(False)
    assert sf.isEnabled()

    table.af_per_position.setChecked(True)
    assert sf.toolTip() == SURFACE_HAF_TOOLTIP
    assert not gear.isEnabled() and gear.toolTip() == SURFACE_HAF_TOOLTIP
    table.af_per_position.setChecked(False)
    assert gear.isEnabled()

    table.setSurfaceFocusConflict("No.")
    assert not sf.isEnabled() and sf.toolTip() == "No."
    table.setSurfaceFocusConflict("")
    assert sf.isEnabled()


def test_position_table_edit_surface(qtbot: QtBot) -> None:
    table = PositionTable()
    qtbot.addWidget(table)
    table.include_z.setChecked(False)

    with patch.object(ZSurfaceDialog, "exec", _accept):
        table._surface_settings.click()
    assert table.surface() == SURFACE

    with patch.object(ZSurfaceDialog, "exec", return_value=0):
        table._surface_settings.click()
    assert table.surface() == SURFACE


def test_surface_focus_click_sets_up_the_surface(qtbot: QtBot) -> None:
    table = PositionTable()
    qtbot.addWidget(table)
    table.include_z.setChecked(False)
    sf = table.surface_focus

    with patch.object(ZSurfaceDialog, "exec", return_value=0) as exec_:
        sf.click()
    exec_.assert_called_once()
    assert not sf.isChecked()

    with patch.object(ZSurfaceDialog, "exec", _accept):
        sf.click()
    assert sf.isChecked()
    assert table.surface() == SURFACE

    # once set up, a click just toggles it
    with patch.object(ZSurfaceDialog, "exec") as exec_:
        sf.click()
    exec_.assert_not_called()
    assert not sf.isChecked()
