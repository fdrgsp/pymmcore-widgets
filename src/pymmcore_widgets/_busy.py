"""Paint a working overlay before a blocking operation takes the GUI thread."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from qtpy.QtCore import QEventLoop, Qt
from qtpy.QtGui import QPainter, QPalette
from qtpy.QtWidgets import QApplication, QWidget

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qtpy.QtGui import QPaintEvent


class BusyOverlay(QWidget):
    """Dim the parent widget and show a centered message."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._message = ""
        self.hide()

    def start(self, message: str) -> None:
        """Cover the parent with `message` and repaint immediately."""
        self._message = message
        if (parent := self.parentWidget()) is None:  # pragma: no cover
            return
        self.setGeometry(parent.rect())
        self.raise_()
        self.show()
        self.repaint()
        if app := QApplication.instance():
            # Flush paint/layout events without allowing clicks to re-enter the caller.
            app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def stop(self) -> None:
        self.hide()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        p = QPainter(self)
        palette = self.palette()
        backdrop = palette.color(QPalette.ColorRole.Window)
        backdrop.setAlpha(200)
        p.fillRect(self.rect(), backdrop)
        p.setFont(self.font())
        p.setPen(palette.color(QPalette.ColorRole.WindowText))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
        p.end()


@contextmanager
def busy(overlay: BusyOverlay, message: str) -> Iterator[None]:
    """Show `overlay` for the duration of a blocking operation.

    Does nothing if the overlay's parent isn't visible yet (e.g. during
    construction), where painting would be pointless and processEvents unsafe.
    """
    active = (parent := overlay.parentWidget()) is not None and parent.isVisible()
    if active:
        overlay.start(message)
    try:
        yield
    finally:
        if active:
            overlay.stop()
