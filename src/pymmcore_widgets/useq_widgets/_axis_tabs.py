"""Presentation pieces shared by the MDA axis containers.

The bordered card the editors sit in, the icon each axis is shown with, and the
tab button and tab bar ``MDATabs`` uses to show a checkbox and icon on top of
each tab's name.
"""

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

# Icon (iconify string) shown on each tab (MDATabs) or before each section's
# title (CollapsibleCoreMDATabs). Edit freely to change an axis' icon; keys are
# the axis attribute names plus "saving"/"settings".
MDA_ICONS: dict[str, str] = {
    "channels": "mdi:palette-outline",
    "stage_positions": "mdi:map-marker-multiple-outline",
    "grid_plan": "mdi:grid",
    "z_plan": "mdi:arrow-up-down",
    "time_plan": "mdi:clock-time-four-outline",
    "saving": "mdi:content-save-outline",
    "settings": "mdi:cog-outline",
}


class _CardFrame(QFrame):
    """A frame that paints a subtle rounded border around a section.

    The border color is derived from the palette's ``Text`` role at a low alpha
    so it reads in both light and dark themes. It is painted (rather than set
    via a stylesheet or a border palette role) so it survives a downstream
    application that clears stylesheets or overrides widget palettes.

    Always reads the palette's ``Active`` group, regardless of this widget's
    own enabled state: the card is a structural outline around a section, not
    content, and should not fade the way ``Text`` does automatically when a
    widget is disabled (``Active`` still resolves correctly for an enabled
    card -- there is only a visible difference when the card itself has been
    disabled). This matters for a presentation that disables the *card* itself
    as the enable control for its content (e.g. `MDATabs`'s checkable
    tabs, whose auto-wiring disables the tab's page widget directly) --
    without it, the border would fade to near-invisible right when it matters
    most, on an unchecked-but-still-viewable tab.
    """

    _RADIUS = 6
    _BORDER_ALPHA = 140
    _FILL_ALPHA = 14
    _BORDER_WIDTH = 1.5

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
        border = QColor(text)
        border.setAlpha(self._BORDER_ALPHA)
        fill = QColor(text)
        fill.setAlpha(self._FILL_ALPHA)
        painter.setPen(QPen(border, self._BORDER_WIDTH))
        painter.setBrush(fill)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)


def _wrap_in_card(
    widget: QWidget, margin: int = 5, *, stretch: bool = False
) -> _CardFrame:
    """Wrap ``widget`` in the bordered card used for sections, tabs and settings.

    A ``QGroupBox`` won't do here: on macOS's native style it draws only a
    title and a thin separator line, never a surrounding rectangle -- even
    with no title it draws nothing at all. ``_CardFrame`` is painted rather
    than relying on native/stylesheet chrome, so it renders the same
    rectangle across styles and themes.

    ``stretch`` keeps ``widget`` at the top of a card that is taller than it
    (e.g. one filling a scroll area's viewport).
    """
    card = _CardFrame()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.addWidget(widget)
    if stretch:
        layout.addStretch()
    # `widget` may still carry QTabWidget's "explicitly hidden" state from
    # before it was moved out of its original tab page -- reparenting into
    # this card doesn't clear that on its own.
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
        checked: bool | None = False,
    ) -> None:
        super().__init__()

        self._icon_label = icon_label = QLabel()
        icon_label.setPixmap(icon.pixmap(20, 20))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        self._checkbox: QCheckBox | None = None
        if checked is not None:
            top = QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
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
        else:
            layout.addWidget(icon_label)
            lbl = QLabel(title)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(lbl)

    def isChecked(self) -> bool | None:
        if self._checkbox is None:
            return None
        return bool(self._checkbox.isChecked())

    def set_icon(self, icon: QIcon) -> None:
        """Replace the displayed icon (e.g. with a re-tinted version)."""
        self._icon_label.setPixmap(icon.pixmap(20, 20))

    def setChecked(self, checked: bool) -> None:
        if self._checkbox is not None:
            self._checkbox.setChecked(checked)


class _AxisTabBar(QTabBar):
    """Flat, self-painted tab bar sized from each tab's custom button widget.

    Every tab here has empty native icon/text (see ``MDATabs._add_tab``): the whole
    visible tab content is a ``_AxisTabButton`` set as the tab's ``LeftSide``
    button instead. That breaks both halves of the native tab machinery, so
    this bar replaces both:

    *Sizing* -- the base ``QTabBar`` computes ``tabSizeHint`` from the native
    icon/text alone, so with both empty it reserves a tiny nominal rect, far
    smaller than a checkbox-icon-label button needs, and the button overflows
    into its neighbors. ``tabSizeHint`` returns the button's own hint instead.

    *Painting* -- a native tab shape drawn behind a button that already fills
    the tab is chrome this presentation never asked for; the macOS style, for
    one, draws its segmented-control look and tints the selected segment with
    the system accent, which reads as a colored pill sitting behind (and
    clashing with) the button. Painting the bar here keeps it flat and
    palette-driven, so it looks the same on every style and theme.
    """

    # The style reserves a fixed left inset before a LeftSide tab button (on
    # top of the button's own width) that isn't reflected in the button's
    # sizeHint -- measured empirically at ~15px on this style/platform, so
    # padding generously here keeps the button from overflowing into the
    # next tab regardless of small per-platform variation.
    _BUTTON_LEFT_INSET = 26
    _UNDERLINE = 2
    _BORDER_ALPHA = 50
    _HOVER_ALPHA = 20
    # Both washes are the palette's own text color at low alpha rather than a
    # concrete role like Base: a tab's label is painted by a child QLabel in
    # WindowText, and a role picked independently of it can land arbitrarily
    # close to that (macOS hands out Window #323232 with WindowText #111111
    # and Base #171717, where a Base fill leaves the selected tab's label
    # near-invisible). Tinting toward the text color instead always moves
    # *away* from it, so the label keeps its contrast in any palette.
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
        """Paint a flat bar: selected tab filled + underlined, nothing else.

        Deliberately does not call ``super().paintEvent`` -- that is what
        would draw the native tab shapes this presentation replaces. The tab
        buttons are real child widgets, so they still paint themselves.
        """
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
