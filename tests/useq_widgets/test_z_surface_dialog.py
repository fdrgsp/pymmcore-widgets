from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import useq
from qtpy.QtCore import QEvent, Qt
from qtpy.QtGui import QMouseEvent
from qtpy.QtWidgets import QDialog, QMessageBox, QToolTip, QWidget
from superqt.iconify import QIconifyIcon

from pymmcore_widgets._z_surface import ZSurface
from pymmcore_widgets.useq_widgets import PositionTable
from pymmcore_widgets.useq_widgets._positions import (
    INCLUDE_Z_SURFACE_TOOLTIP,
    SURFACE_HAF_TOOLTIP,
    SURFACE_INCLUDE_Z_TOOLTIP,
)
from pymmcore_widgets.useq_widgets._z_surface_dialog import (
    ERROR,
    ICON_SIZE,
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
    assert "3 points" in dialog._summary.text()
    assert "residuals" in dialog._summary.text()
    assert dialog.map._image is not None
    # no stage: no column to set a point from it
    assert dialog.points.table().columnCount() == 3

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
    # the whole view, extrapolated around the points too
    assert surface_map._z_range == pytest.approx((-0.15, 3.15))
    assert not surface_map.grab().isNull()

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


def test_dialog_map_follows_the_table(qtbot: QtBot) -> None:
    dialog = ZSurfaceDialog(SURFACE)
    qtbot.addWidget(dialog)
    dialog.show()
    table = dialog.points.table()
    for height in (None, 150, 500):
        if height is not None:
            table.setFixedHeight(height)  # what dragging the table's grip does
        qtbot.waitUntil(lambda: _rows(dialog.map) == _rows(table))


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


def test_dialog_save_load(qtbot: QtBot, tmp_path: Path) -> None:
    dialog = ZSurfaceDialog(SURFACE)
    qtbot.addWidget(dialog)
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
    # checked by default: "Include Z" rules it out
    assert not sf.isEnabled()
    assert sf.toolTip() == SURFACE_INCLUDE_Z_TOOLTIP
    table.include_z.setChecked(False)
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
    table.af_per_position.setChecked(False)

    table.setSurfaceFocusConflict("No.")
    assert not sf.isEnabled() and sf.toolTip() == "No."
    table.setSurfaceFocusConflict("")
    assert sf.isEnabled()


def test_position_table_edit_surface(qtbot: QtBot) -> None:
    table = PositionTable()
    qtbot.addWidget(table)

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
