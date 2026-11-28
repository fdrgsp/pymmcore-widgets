from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import qtpy
from qtpy import QtGui
from qtpy.QtCore import QPoint, QPointF, Qt
from qtpy.QtGui import QIcon, QWheelEvent
from qtpy.QtWidgets import QApplication, QStyleFactory, QTabBar

from pymmcore_widgets.useq_widgets._axis_tabs import _AxisTabBar, _AxisTabButton

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


@pytest.mark.skipif(qtpy.QT5, reason="Pixel-scroll input devices require Qt6")
@pytest.mark.parametrize("direction", [Qt.LeftToRight, Qt.RightToLeft])
@pytest.mark.parametrize("overflow", [True, False])
def test_pixel_scroll_keeps_tab_buttons_aligned(
    qtbot: QtBot, direction: Qt.LayoutDirection, overflow: bool
) -> None:
    bar = _AxisTabBar()
    qtbot.addWidget(bar)
    style = QStyleFactory.create("Fusion")
    assert style is not None
    style.setParent(bar)
    bar.setStyle(style)
    bar.setLayoutDirection(direction)
    buttons = []
    for title in ("Channels", "Positions", "Z Stack", "Time Series", "Grid/Tiles"):
        index = bar.addTab("")
        button = _AxisTabButton(title, QIcon())
        bar.setTabButton(index, QTabBar.LeftSide, button)
        buttons.append(button)
    bar.resize(250 if overflow else 1500, bar.sizeHint().height())
    bar.show()
    QApplication.processEvents()

    device = QtGui.QPointingDevice(
        "touchpad",
        1,
        QtGui.QInputDevice.DeviceType.TouchPad,
        QtGui.QPointingDevice.PointerType.Finger,
        QtGui.QInputDevice.Capability.Position
        | QtGui.QInputDevice.Capability.PixelScroll,
        2,
        0,
    )
    initial_rect = bar.tabRect(0)
    offsets = [
        button.pos() - bar.tabRect(i).topLeft() for i, button in enumerate(buttons)
    ]
    moved = False
    for delta in (-30, -40, -10000, -30, 30, 10000):
        if direction == Qt.RightToLeft:
            delta = -delta
        pos = bar.rect().center()
        event = QWheelEvent(
            QPointF(pos),
            QPointF(bar.mapToGlobal(pos)),
            QPoint(delta, 0),
            QPoint(delta, 0),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
            device=device,
        )
        QApplication.sendEvent(bar, event)
        QApplication.processEvents()
        moved |= bar.tabRect(0) != initial_rect
        assert bar.currentIndex() == 0
        for i, button in enumerate(buttons):
            assert button.pos() - bar.tabRect(i).topLeft() == offsets[i]
    assert moved == overflow
    assert bar.tabRect(0) == initial_rect
