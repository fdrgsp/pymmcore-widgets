"""Cards, icons and tab controls shared by the MDA axis containers."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from qtpy.QtCore import QEvent, QRectF, QSize, Qt, Signal
from qtpy.QtGui import QColor, QPainter, QPalette, QPen
from qtpy.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from qtpy.QtGui import QHoverEvent, QIcon, QPaintEvent

MDA_ICONS: dict[str, str] = {
    "channels": "mdi:palette-outline",
    "stage_positions": "mdi:map-marker-multiple-outline",
    "grid_plan": "mdi:grid",
    "z_plan": "mdi:arrow-up-down",
    "time_plan": "mdi:clock-time-four-outline",
    "autofocus": "mdi:focus-auto",
}


class _CardFrame(QFrame):
    """A rounded card that stays visible when its content is disabled.

    Painted rather than styled: native styles may omit a QGroupBox's frame.
    """

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        text = self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
        fill = QColor(text)
        fill.setAlpha(14)
        border = QColor(text)
        border.setAlpha(140)
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 6, 6)


def _wrap_in_card(widget: QWidget) -> _CardFrame:
    """Return a card holding `widget`."""
    card = _CardFrame()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(5, 5, 5, 5)
    layout.addWidget(widget)
    return card


class _AxisTabButton(QWidget):
    """Tab button: a checkbox and the axis icon on top, the axis name below."""

    toggled = Signal(bool)

    def __init__(self, title: str, icon: QIcon, *, checked: bool = False) -> None:
        super().__init__()
        self._checkbox = QCheckBox()
        self._checkbox.setChecked(checked)
        self._checkbox.toggled.connect(self.toggled)
        icon_label = QLabel()
        icon_label.setPixmap(icon.pixmap(20, 20))

        top = QHBoxLayout()
        top.setSpacing(7)
        top.addStretch()
        top.addWidget(self._checkbox)
        top.addWidget(icon_label)
        top.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        layout.addLayout(top)
        layout.addWidget(QLabel(title), 0, Qt.AlignmentFlag.AlignCenter)

    def isChecked(self) -> bool:
        return bool(self._checkbox.isChecked())

    def setChecked(self, checked: bool) -> None:
        self._checkbox.setChecked(checked)


class _AxisTabBar(QTabBar):
    """Flat tab bar sized from its `_AxisTabButton`s instead of native icon/text.

    It paints its own background, so no native tab shape shows behind the buttons.
    """

    # room for the native style's inset before a LeftSide tab button
    _BUTTON_LEFT_INSET = 26
    _UNDERLINE = 2
    # washes of the text color, which keep the fill subtle in any theme
    _BORDER_ALPHA = 50
    _HOVER_ALPHA = 20
    _SELECTED_ALPHA = 38

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setDrawBase(False)
        self.setExpanding(False)
        self._hovered = -1

    def tabSizeHint(self, index: int) -> QSize:
        if btn := self.tabButton(index, QTabBar.ButtonPosition.LeftSide):
            hint = btn.sizeHint()
            return QSize(hint.width() + self._BUTTON_LEFT_INSET, hint.height() + 8)
        return super().tabSizeHint(index)  # pragma: no cover

    def minimumTabSizeHint(self, index: int) -> QSize:
        return self.tabSizeHint(index)

    def event(self, a0: QEvent | None) -> bool:
        if a0 is not None:
            if a0.type() in (QEvent.Type.HoverMove, QEvent.Type.HoverEnter):
                pos = cast("QHoverEvent", a0).position().toPoint()
                if (hovered := self.tabAt(pos)) != self._hovered:
                    self._hovered = hovered
                    self.update()
            elif a0.type() == QEvent.Type.HoverLeave and self._hovered != -1:
                self._hovered = -1
                self.update()
        return bool(super().event(a0))

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        """Paint the background and underline; child widgets paint the buttons."""
        painter = QPainter(self)
        text = self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
        current = self.currentIndex()

        for index in range(self.count()):
            if index == current:
                alpha = self._SELECTED_ALPHA
            elif index == self._hovered:
                alpha = self._HOVER_ALPHA
            else:
                continue
            wash = QColor(text)
            wash.setAlpha(alpha)
            painter.fillRect(self.tabRect(index), wash)

        border = QColor(text)
        border.setAlpha(self._BORDER_ALPHA)
        painter.fillRect(0, self.height() - 1, self.width(), 1, border)

        if current >= 0:
            rect = self.tabRect(current)
            painter.fillRect(
                rect.left(),
                self.height() - self._UNDERLINE,
                rect.width(),
                self._UNDERLINE,
                self.palette().color(QPalette.ColorRole.Highlight),
            )
