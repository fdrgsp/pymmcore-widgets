"""Core-connected containers for the MDA axis editors.

``CoreMDATabs`` is the plain checkable tab widget of core-connected axis
editors (the position sub-sequence popup uses it as is). ``TopbarMDATabs`` is
the presentation [`MDAWidget`][pymmcore_widgets.MDAWidget] uses: the same
checkable tabs, with an icon-on-top / title-below button per tab and Saving as
a sixth tab. ``CollapsibleCoreMDATabs`` (in ``_collapsible_mda``) is the other
presentation, used by ``MDAWidgetCollapsible``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pymmcore_plus import CMMCorePlus
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
    QScrollArea,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon

from pymmcore_widgets._util import QWIDGETSIZE_MAX
from pymmcore_widgets.useq_widgets._mda_sequence import MDATabs
from pymmcore_widgets.useq_widgets._time import TimePlanWidget

from ._core_channels import CoreConnectedChannelTable
from ._core_grid import CoreConnectedGridPlanWidget
from ._core_positions import CoreConnectedPositionTable
from ._core_z import CoreConnectedZPlanWidget

if TYPE_CHECKING:
    import useq

    from ._save_widget import SaveGroupBox


# Icon (iconify string) shown on each tab (TopbarMDATabs) or before each section's
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


class _CoreConnectedPositionTable(CoreConnectedPositionTable):
    def __init__(
        self,
        rows: int = 0,
        mmcore: CMMCorePlus | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(rows, mmcore, parent)

        # subclassing to disconnect the signals from this widget so we use the ones
        # we will implement in the MDAWidget that take into account both the autofocus
        # device mand the z plan
        ev = self._mmc.events
        ev.systemConfigurationLoaded.disconnect(self._on_sys_config_loaded)
        ev.propertyChanged.disconnect(self._on_property_changed)


class CoreMDATabs(MDATabs):
    channels: CoreConnectedChannelTable

    def __init__(
        self, parent: QWidget | None = None, core: CMMCorePlus | None = None
    ) -> None:
        self._mmc = core or CMMCorePlus.instance()
        super().__init__(parent)
        self.z_plan.setStackActive(bool(self.isChecked(self.z_plan)))

    def create_subwidgets(self) -> None:
        self.time_plan = TimePlanWidget(0)
        self.stage_positions = _CoreConnectedPositionTable(0, self._mmc)
        self.z_plan = CoreConnectedZPlanWidget(self._mmc)
        self.grid_plan = CoreConnectedGridPlanWidget(self._mmc)
        self.channels = CoreConnectedChannelTable(0, self._mmc)

    def _on_tab_checked(self, idx: int, checked: bool) -> None:
        # The base class shows/hides Do Stack and Acquire Every purely on their
        # own axis's state. Here both are further gated behind the channel
        # table's "Advanced" toggle, so route each axis's state through the
        # table (which recomputes visibility from both inputs).
        super()._on_tab_checked(idx, checked)
        if idx == self.indexOf(self.z_plan):
            self.z_plan.setStackActive(checked)
            self.channels.setZStackActive(checked)
        elif idx == self.indexOf(self.time_plan):
            self.channels.setTimePlanActive(checked)

    def _enable_tabs(self, enable: bool) -> None:
        """Enable or disable the tab checkboxes and their contents.

        However, we can still mover through the tabs and see their contents.
        """
        # disable tab checkboxes
        for cbox in self._cboxes:
            cbox.setEnabled(enable)
        # disable tabs contents (the content of an unchecked tab stays disabled).
        # Toggle the editors, not the tab pages: a page may wrap its editor (e.g. a
        # scroll area or a card), and a disabled page would keep the editor disabled
        # after its tab is checked again.
        for wdg in (
            self.time_plan,
            self.stage_positions,
            self.z_plan,
            self.grid_plan,
            self.channels,
        ):
            wdg.setEnabled(enable and self.isChecked(wdg) is not False)

    def add_supporting_sections(self, *, save_info: SaveGroupBox) -> None:
        """Take in the Saving editor, after the five axes.

        ``MDAWidget`` calls this once while building its layout; each
        presentation decides where Saving goes (a tab, a section, ...). The
        plain tabs have no place for it, so they cannot back an ``MDAWidget``.
        """
        raise NotImplementedError(
            f"{type(self).__name__} cannot host the MDAWidget's Saving editor"
        )


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
    as the enable control for its content (e.g. `TopbarMDATabs`'s checkable
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


class _TopbarTabBar(QTabBar):
    """Flat, self-painted tab bar sized from each tab's custom button widget.

    Every tab here has empty native icon/text (see ``_add_tab``): the whole
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


class TopbarMDATabs(CoreMDATabs):
    """Core-aware MDA axis container presented as native tabs.

    The default presentation of [`MDAWidget`][pymmcore_widgets.MDAWidget].
    Structurally much closer to the base ``CoreMDATabs`` than the collapsible
    presentation: this *is* a plain, themed ``QTabWidget``, using the
    checkbox-per-tab mechanism ``CheckableTabWidget.addTab`` already
    provides. The only differences from the base widget are the tab order
    (matching the collapsible presentation: channels, positions, grid, z,
    time), a checkbox-and-icon-on-top / title-below button on every tab (see
    `_add_tab`, `_AxisTabButton`), and one further tab after the axes --
    Saving, checkable the same as an axis.

    Grid, Z Stack and Saving are each wrapped in the same bordered card the
    collapsible presentation uses (see ``_wrap_in_card``) --
    Channels/Positions/Time Series are left unwrapped since their table
    editors already carry their own frame.
    """

    _AXES = (
        ("c", "Channels", "channels"),
        ("p", "Positions", "stage_positions"),
        ("g", "Grid/Tiles", "grid_plan"),
        ("z", "Z Stack", "z_plan"),
        ("t", "Time Series", "time_plan"),
    )
    # Inset every page's content so no editor sits flush against the tab's
    # edges (see `_add_tab`).
    _PAGE_MARGIN = 9

    def __init__(
        self, parent: QWidget | None = None, core: CMMCorePlus | None = None
    ) -> None:
        self._sections_ready = False
        self._supporting_sections_added = False
        # index -> the plain (undyed) icon a tab was given, so a theme sweep
        # can re-tint from a stable source instead of compounding tints.
        self._tab_icons: dict[int, QIcon] = {}
        # A card-wrapped axis/section's real widget -> the scroll+card page
        # actually registered with the tab widget (see `_add_tab`). `indexOf`
        # and `setCurrentWidget` translate through this so every other method
        # -- including inherited ones like `CoreMDATabs._on_tab_checked`, and
        # `CheckableTabWidget.isChecked`/`setChecked`, which call `self.
        # indexOf` internally -- can keep referring to the real widget (e.g.
        # `self.z_plan`) without knowing it is wrapped.
        self._page_for: dict[QWidget, QWidget] = {}
        super().__init__(parent, core)

        # The base MDATabs.__init__ already added the five axis tabs, in its
        # own order/labels and with no icon. Undo that and re-add them the way
        # the collapsible presentation orders and labels them.
        initial_states = {
            widget: bool(self.isChecked(widget)) for widget in self._axis_widgets()
        }
        while self.count():
            QTabWidget.removeTab(self, 0)
        for cbox in self._cboxes:
            cbox.deleteLater()
        self._cboxes.clear()

        # Every tab's real content is a custom button widget (see _add_tab),
        # not native icon/text -- only _TopbarTabBar sizes and paints tabs
        # correctly for that (see its docstring). Set once no tabs remain on
        # the old bar, so none need to be transferred across. Document mode
        # drops the native frame the tab widget would otherwise draw around
        # the page, which _TopbarTabBar's own flat border replaces.
        self.setTabBar(_TopbarTabBar(self))
        self.setDocumentMode(True)

        for _axis, title, attr in self._AXES:
            widget = cast("QWidget", getattr(self, attr))
            # Matches the collapsible presentation: table editors
            # (channels/positions/time) already carry their own frame, so only
            # grid_plan/z_plan get the bordered card.
            card = attr in ("grid_plan", "z_plan")
            self._add_tab(
                widget, title, attr, checked=initial_states[widget], card=card
            )
            # The grip lets a table trade vertical space with its neighbours
            # in the collapsible presentation's shared scroll area. Here each
            # table gets its own full-height tab page instead, so there are no
            # neighbours to negotiate with and the grip is just clutter.
            if (grip := getattr(widget, "_resize_grip", None)) is not None:
                grip.hide()
        self.setCurrentWidget(self.channels)
        if tab_bar := self.tabBar():
            tab_bar.setExpanding(False)

        self._sections_ready = True

    def _add_tab(
        self,
        widget: QWidget,
        title: str,
        icon_key: str,
        *,
        checked: bool | None,
        card: bool = False,
    ) -> int:
        """Add a tab with an icon-on-top / title-below button widget.

        Every page is a container holding ``widget`` inset by ``_PAGE_MARGIN``,
        so no editor sits flush against the tab's edges; ``card=True`` puts a
        bordered card inside a scroll area in between. ``widget`` is stored in
        ``_page_for`` either way, so all other methods address the real widget
        rather than whichever container it ended up in.
        """
        icon = QIconifyIcon(MDA_ICONS[icon_key])
        index = int(self.count())
        self._tab_icons[index] = icon

        widget.setEnabled(True)  # clear WA_ForceDisabled set by a prior insertTab

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(
            self._PAGE_MARGIN, self._PAGE_MARGIN, self._PAGE_MARGIN, self._PAGE_MARGIN
        )
        if card:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(_wrap_in_card(widget, stretch=True))
            page_layout.addWidget(scroll)
        else:
            page_layout.addWidget(widget)
            # An editor that caps its own height (SaveGroupBox does) cannot
            # fill the page, and a lone item in a box layout is centered in
            # the space it does not take -- leaving the form floating
            # mid-tab. Absorb the remainder below it instead.
            if widget.maximumHeight() < QWIDGETSIZE_MAX:
                page_layout.addStretch()
        self._page_for[widget] = page

        # `QTabWidget.removeTab` explicitly hides the page it removes, and
        # reparenting alone does not clear that -- without this, every axis
        # rebuilt in __init__ stays hidden and its tab comes up blank.
        widget.show()

        QTabWidget.addTab(self, page, "")

        btn = _AxisTabButton(title, icon, checked=checked)
        if checked is not None:
            # `_cboxes` is typed for QCheckBox by the base class, but every
            # consumer (`CoreMDATabs._enable_tabs`, the clear-loop above) only
            # ever calls `.setEnabled()`/`.deleteLater()` on its contents --
            # both of which `_AxisTabButton` supports as a plain QWidget.
            self._cboxes.append(btn)
            # Disable the editor itself rather than its page: `_enable_tabs`
            # re-enables editors by name (e.g. `self._save_info`), which a
            # disabled *parent* would silently override. `indexOf` resolves
            # the widget back to its page, so the inherited handler still
            # finds the right tab.
            btn.toggled.connect(lambda c, w=widget: self._on_tab_checkbox_toggled(c, w))
            if not checked:
                widget.setEnabled(False)
        if tab_bar := self.tabBar():
            tab_bar.setTabButton(index, QTabBar.ButtonPosition.LeftSide, btn)
            tab_bar.setTabToolTip(index, title)

        return index

    def indexOf(self, widget: QWidget | None) -> int:
        """Return the tab index for ``widget``, resolving through any card page."""
        if widget is not None and widget in self._page_for:
            widget = self._page_for[widget]
        return int(QTabWidget.indexOf(self, widget))

    def setCurrentWidget(self, widget: QWidget | None) -> None:
        """Select the tab for ``widget``, resolving through any card page."""
        if widget is None:
            return
        QTabWidget.setCurrentWidget(self, self._page_for.get(widget, widget))

    def tabIcon(self, index: int) -> QIcon:
        """Return the stored icon; icons live in the button widget, not the tab."""
        return self._tab_icons.get(index, QIcon())

    def setValue(self, value: useq.MDASequence) -> None:
        """Restore a sequence without letting it change the current tab.

        Every axis the sequence uses is checked in turn, and a tab checkbox
        selects its own tab (``change_tab_on_check``, upstream's default for
        an *interactive* click). Restoring saved state is not navigation
        though, so whichever axis happened to be checked last would otherwise
        decide what the user is looking at -- the widget would open on
        Positions rather than the Channels tab it starts on.
        """
        current = self.currentIndex()
        super().setValue(value)
        self.setCurrentIndex(current)

    def set_tab_icon(self, index: int, icon: QIcon) -> None:
        """Replace a tab's rendered icon (its button widget's, not the native tab's).

        A downstream theme sweep uses this to re-tint from the stable source
        `tab_icon` returns, rather than `QTabWidget.setTabIcon`: the native
        tab icon is deliberately left empty (see `_add_tab`), so setting one
        would add an icon nothing in this presentation displays -- and native
        tab styles are free to decorate a tab that suddenly has both an icon
        and a corner button in ways this presentation never asked for.
        """
        if tab_bar := self.tabBar():
            btn = tab_bar.tabButton(index, QTabBar.ButtonPosition.LeftSide)
            if isinstance(btn, _AxisTabButton):
                btn.set_icon(icon)

    def currentWidget(self) -> QWidget | None:
        """Return the axis/section widget, not its wrapping page container."""
        page = QTabWidget.currentWidget(self)
        for widget, p in self._page_for.items():
            if p is page:
                return widget
        return page

    def isChecked(
        self,
        key: int | QWidget,
        position: QTabBar.ButtonPosition = QTabBar.ButtonPosition.LeftSide,
    ) -> bool | None:
        """Return the tab's checkbox state, or None if it has no checkbox."""
        idx = self.indexOf(key) if isinstance(key, QWidget) else key
        if tab_bar := self.tabBar():
            btn = tab_bar.tabButton(idx, position)
            if isinstance(btn, _AxisTabButton):
                return btn.isChecked()
        return None

    def setChecked(
        self,
        key: int | QWidget,
        checked: bool,
        position: QTabBar.ButtonPosition = QTabBar.ButtonPosition.LeftSide,
    ) -> None:
        """Set the axis checkbox state."""
        idx = self.indexOf(key) if isinstance(key, QWidget) else key
        if tab_bar := self.tabBar():
            btn = tab_bar.tabButton(idx, position)
            if isinstance(btn, _AxisTabButton):
                btn.setChecked(checked)

    def add_supporting_sections(self, *, save_info: SaveGroupBox) -> None:
        """Append the Saving tab after the five axes.

        It is checkable like an axis. Saving's tab checkbox is synced with
        ``SaveGroupBox``'s own checkable state (``_on_saving_tab_checked`` /
        ``_on_save_info_toggled``), and the native title is then hidden
        (``apply_save_body_style``) so the tab checkbox is the only enable
        control shown, not two redundant ones.

        Global settings (axis order, keep shutter open, autofocus axis) are
        not a tab: they have no on/off state of their own and always apply, so
        ``MDAWidget`` keeps them in a card below the tabs instead (see
        ``MDAWidget._install_layout``).
        """
        if self._supporting_sections_added:
            return

        self._save_info = save_info
        self._add_tab(
            save_info, "Saving", "saving", checked=save_info.isChecked(), card=True
        )
        self.tabChecked.connect(self._on_saving_tab_checked)
        save_info.toggled.connect(self._on_save_info_toggled)

        self._supporting_sections_added = True
        self.apply_save_body_style()

    def _on_saving_tab_checked(self, index: int, checked: bool) -> None:
        """Keep the Saving tab's checkbox and `SaveGroupBox`'s own state in sync."""
        if index != self.indexOf(self._save_info):
            return
        self._save_info.setChecked(checked)

    def _on_save_info_toggled(self, checked: bool) -> None:
        """The reverse direction of `_on_saving_tab_checked`.

        `QCheckBox.setChecked` only emits `toggled` on an actual change, so
        the two directions naturally settle after one bounce rather than
        looping.
        """
        self.setChecked(self._save_info, checked)

    def tab_icon(self, index: int) -> QIcon | None:
        """Return the plain (undyed) icon a tab was created with, if any."""
        return self._tab_icons.get(index)

    def apply_save_body_style(self) -> None:
        """Hide the duplicated native QGroupBox header inside Saving.

        The Saving tab's own checkbox is the enable control (kept in sync by
        `_on_saving_tab_checked`/`_on_save_info_toggled`), so the native
        checkable title `SaveGroupBox` draws by default would just be a
        second, redundant toggle.
        """
        if not self._supporting_sections_added:
            return
        self._save_info.setTitle("")
        self._save_info.setFlat(True)
        self._save_info.setObjectName("mdaSaveInfoBody")
        self._save_info.setStyleSheet(
            "QGroupBox#mdaSaveInfoBody {"
            " border: 0px; margin-top: 0px; padding-top: 0px;"
            "}"
            "QGroupBox#mdaSaveInfoBody::title {"
            " width: 0px; height: 0px; padding: 0px; margin: 0px;"
            "}"
            "QGroupBox#mdaSaveInfoBody::indicator {"
            " image: none; border: 0px; background: transparent;"
            " width: 0px; height: 0px;"
            "}"
        )

    def _enable_tabs(self, enable: bool) -> None:
        """Enable or disable every tab's checkbox and content.

        Extends the base class's five axes with Saving, checkable like an
        axis. The settings cards are not tabs; ``MDAWidget._enable_widgets``
        handles them itself.
        """
        super()._enable_tabs(enable)
        if self._supporting_sections_added:
            for cbox in self._cboxes[len(self._axis_widgets()) :]:
                cbox.setEnabled(enable)
            self._save_info.setEnabled(enable)

    def _axis_widgets(self) -> tuple[QWidget, ...]:
        return (
            self.channels,
            self.stage_positions,
            self.grid_plan,
            self.z_plan,
            self.time_plan,
        )


__all__ = ["CoreMDATabs", "TopbarMDATabs"]
