"""Paint a working overlay before a blocking operation takes the GUI thread."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from qtpy.QtCore import QEventLoop, QRectF, Qt
from qtpy.QtGui import QPainter, QPalette
from qtpy.QtWidgets import QApplication, QWidget

if TYPE_CHECKING:
    from collections.abc import Iterator

    from qtpy.QtGui import QPaintEvent


_BAR_WIDTH = 240
_BAR_HEIGHT = 6
_BAR_GAP = 10

# the overlay of the innermost `busy` block, which `progress` reports to
_active: BusyOverlay | None = None


class BusyOverlay(QWidget):
    """Dim the parent widget and show a centered message, and progress if known."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._message = ""
        self._fraction: float | None = None
        self.hide()

    def start(self, message: str) -> None:
        """Cover the parent with `message` and repaint immediately."""
        self._message = message
        self._fraction = None
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

    def setProgress(self, done: int, total: int) -> None:
        """Show a bar of `done` out of `total` steps, repainting at each percent."""
        fraction = min(done / total, 1.0) if total > 0 else None
        old, self._fraction = self._fraction, fraction
        if (
            fraction is not None
            and old is not None
            and int(fraction * 100) == int(old * 100)
        ):
            return
        if self.isVisible():
            # no event processing: the caller is mid-operation
            self.repaint()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        p = QPainter(self)
        palette = self.palette()
        backdrop = palette.color(QPalette.ColorRole.Window)
        backdrop.setAlpha(200)
        p.fillRect(self.rect(), backdrop)
        p.setFont(self.font())
        p.setPen(palette.color(QPalette.ColorRole.WindowText))
        if self._fraction is None:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            p.end()
            return

        # the message, with the bar under it, centered together
        text_height = self.fontMetrics().height()
        top = (self.height() - text_height - _BAR_GAP - _BAR_HEIGHT) / 2
        text_rect = QRectF(0, top, self.width(), text_height)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._message)
        width = min(_BAR_WIDTH, self.width() - 40)
        bar = QRectF(
            (self.width() - width) / 2,
            text_rect.bottom() + _BAR_GAP,
            width,
            _BAR_HEIGHT,
        )
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        radius = _BAR_HEIGHT / 2
        p.setBrush(palette.color(QPalette.ColorRole.Mid))
        p.drawRoundedRect(bar, radius, radius)
        if self._fraction > 0:
            bar.setWidth(max(bar.width() * self._fraction, _BAR_HEIGHT))
            p.setBrush(palette.color(QPalette.ColorRole.Highlight))
            p.drawRoundedRect(bar, radius, radius)
        p.end()


def progress(done: int, total: int) -> None:
    """Report `done` out of `total` steps of the operation a `busy` block covers.

    Does nothing outside one, so that code can report its progress regardless.
    """
    if _active is not None:
        _active.setProgress(done, total)


@contextmanager
def busy(overlay: BusyOverlay, message: str) -> Iterator[None]:
    """Show `overlay` for the duration of a blocking operation.

    Does nothing if the overlay's parent isn't visible yet (e.g. during
    construction), where painting would be pointless and processEvents unsafe.
    """
    global _active
    active = (parent := overlay.parentWidget()) is not None and parent.isVisible()
    previous = _active
    if active:
        overlay.start(message)
        _active = overlay
    try:
        yield
    finally:
        if active:
            overlay.stop()
            _active = previous
