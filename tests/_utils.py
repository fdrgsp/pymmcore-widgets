from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import Mock

from qtpy.QtCore import QEvent, QPointF, Qt
from qtpy.QtGui import QMouseEvent

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pytestqt.qtbot import QtBot
    from qtpy.QtCore import SignalInstance

    from pymmcore_widgets.control._stage_explorer._stage_viewer import StageViewer


def wait_for_stage_viewer(qtbot: QtBot, viewer: StageViewer) -> None:
    """Wait for image processing, zoom debounce, and GUI batches to finish."""
    timers = (
        viewer._overview_timer,
        viewer._processing_timer,
        viewer._display_timer,
        viewer._downsample_timer,
    )
    qtbot.waitUntil(
        lambda: not any(timer.isActive() for timer in timers), timeout=10000
    )


def right_press() -> QMouseEvent:
    """A press of the right mouse button."""
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(2, 2),
        QPointF(2, 2),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )


@contextmanager
def wait_signal(
    qtbot: QtBot, signal: SignalInstance, timeout: int = 2000
) -> Iterator[None]:
    """Context manager to wait for a signal.

    In some (as of yet understood) cases, qtbot.waitSignal() causes segfaults with
    PySide6.  Using manual polling like this seems to work around the issue.
    """
    mock = Mock()
    signal.connect(mock)
    try:
        yield
        qtbot.waitUntil(lambda: mock.call_count > 0, timeout=timeout)
    finally:
        signal.disconnect(mock)
