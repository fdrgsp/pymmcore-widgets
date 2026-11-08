"""Cards, icons and tab controls shared by the MDA axis containers."""

from __future__ import annotations

from typing import cast

from qtpy.QtCore import QEvent, QRectF, QSize, Qt, Signal
from qtpy.QtGui import (
    QColor,
    QHoverEvent,
    QIcon,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
)
from qtpy.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

MDA_ICONS: dict[str, str] = {
    "channels": "mdi:palette-outline",
    "stage_positions": "mdi:map-marker-multiple-outline",
    "grid_plan": "mdi:grid",
    "z_plan": "mdi:arrow-up-down",
    "time_plan": "mdi:clock-time-four-outline",
    "autofocus": "mdi:focus-auto",
    "saving": "mdi:content-save-outline",
}


def _paint_card(widget: QWidget, painter: QPainter, rect: QRectF) -> None:
    """Draw the shared card fill and border."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    text = widget.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
    fill = QColor(text)
    fill.setAlpha(14)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawRoundedRect(rect, 6, 6)
    border = QColor(text)
    border.setAlpha(140)
    painter.setPen(QPen(border, 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, 6, 6)
    painter.restore()


class _CardFrame(QFrame):
    """Paint a rounded card that remains visible when the section is disabled."""

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        _paint_card(self, painter, rect)


def _wrap_in_card(
    widget: QWidget, margin: int = 5, *, stretch: bool = False
) -> _CardFrame:
    """Wrap ``widget`` in a card; ``stretch`` keeps it aligned to the top.

    Painted borders also work with native styles that omit a QGroupBox's frame.
    """
    card = _CardFrame()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.addWidget(widget)
    if stretch:
        layout.addStretch()
    # Reparenting does not clear a removed tab page's explicitly hidden state.
    widget.show()
    return card


class _AxisTabButton(QWidget):
    """Tab button: axis icon centered on top, checkbox + name label below."""

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        icon: QIcon,
        *,
        checked: bool = False,
    ) -> None:
        super().__init__()

        self._icon_label = icon_label = QLabel()
        icon_label.setPixmap(icon.pixmap(20, 20))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(7)
        top.addStretch()
        self._checkbox = QCheckBox()
        self._checkbox.setChecked(checked)
        self._checkbox.toggled.connect(self.toggled)
        top.addWidget(self._checkbox)
        top.addWidget(icon_label)
        top.addStretch()
        layout.addLayout(top)
        lbl = QLabel(title)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)

    def isChecked(self) -> bool:
        return bool(self._checkbox.isChecked())

    def set_icon(self, icon: QIcon) -> None:
        """Replace the displayed icon (e.g. with a re-tinted version)."""
        self._icon_label.setPixmap(icon.pixmap(20, 20))

    def setChecked(self, checked: bool) -> None:
        self._checkbox.setChecked(checked)


class _AxisTabBar(QTabBar):
    """Flat tab bar sized from its custom buttons instead of native icon/text.

    Painting the background here avoids native tab shapes behind those buttons.
    """

    # Reserve space for the native style's inset before a LeftSide tab button.
    _BUTTON_LEFT_INSET = 26
    _UNDERLINE = 2
    _BORDER_ALPHA = 50
    _HOVER_ALPHA = 20
    # Tint with the text color to keep the fill subtle across themes.
    _SELECTED_ALPHA = 38

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setDrawBase(False)
        self._hovered = -1

    def tabSizeHint(self, index: int) -> QSize:
        if btn := self.tabButton(index, QTabBar.ButtonPosition.LeftSide):
            hint = btn.sizeHint()
            return QSize(hint.width() + self._BUTTON_LEFT_INSET, hint.height() + 8)
        return super().tabSizeHint(index)

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
