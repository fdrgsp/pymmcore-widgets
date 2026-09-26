"""Sidebar-navigation presentation of the core-connected MDA widget.

``MDAWidgetSidebar`` is a drop-in [`MDAWidget`][pymmcore_widgets.MDAWidget] that
presents the acquisition axes as a left-hand navigation list (icon, title, and
an enable toggle switch per row) with the selected dimension's editor shown on
the right, rather than as collapsible sections or a checkable tab widget. It
subclasses ``MDAWidget`` and reuses all of its behavior (``value``/``setValue``,
``prepare_mda``/``run_mda``, ``save``/``load``, editor enablement, and core
awareness); only the presentation differs.

A disabled dimension stays selectable -- its row is dimmed (toggle off) but
clicking it still shows its editor on the right, just disabled, matching the
collapsible presentation's "click to view, independent of enabled state".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from qtpy.QtCore import QRectF, Qt, Signal
from qtpy.QtGui import QColor, QPainter, QPaintEvent, QPalette
from qtpy.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from superqt import QToggleSwitch
from superqt.iconify import QIconifyIcon

from pymmcore_widgets.control._camera_roi_widget import CameraRoiWidget
from pymmcore_widgets.useq_widgets import PYMMCW_METADATA_KEY

from ._collapsible_mda import (
    _STATUS_ON_COLOR,
    CAMERA_ROI_METADATA_KEY,
    MDA_ICONS,
    _CardFrame,
    _clear_layout,
    _ClickableFrame,
)
from ._core_mda import CoreMDATabs, MDAWidget

_ICON_SIZE = 18

if TYPE_CHECKING:
    import useq
    from pymmcore_plus import CMMCorePlus
    from pymmcore_plus.mda import SingleOutput

    from ._save_widget import SaveGroupBox


class _SidebarRowFrame(_ClickableFrame):
    """The clickable background of a sidebar row.

    Paints a subtle highlight fill when selected -- painted rather than set via
    a stylesheet or palette role, for the same reason as _CardFrame: it must
    survive a downstream application that clears stylesheets or overrides
    widget palettes.
    """

    _FILL_ALPHA = 45

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected = False

    def set_selected(self, selected: bool) -> None:
        selected = bool(selected)
        if selected != self._selected:
            self._selected = selected
            self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        if self._selected:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            fill = QColor(self.palette().color(QPalette.ColorRole.Highlight))
            fill.setAlpha(self._FILL_ALPHA)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.drawRoundedRect(rect, 4, 4)
        super().paintEvent(a0)


class SidebarRow(QWidget):
    """A single sidebar navigation row: icon, title, and optional toggle switch."""

    checkedChanged = Signal(bool)
    clicked = Signal()

    def __init__(
        self,
        title: str,
        *,
        checked: bool | None = None,
        icon: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title

        self._frame = _SidebarRowFrame()
        self._frame.setObjectName("mdaSidebarRow")
        self._frame.clicked.connect(self.clicked)
        row_layout = QHBoxLayout(self._frame)
        row_layout.setContentsMargins(10, 8, 10, 8)
        row_layout.setSpacing(8)

        if icon:
            icon_label = QLabel()
            icon_label.setPixmap(QIconifyIcon(icon).pixmap(_ICON_SIZE, _ICON_SIZE))
            icon_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            row_layout.addWidget(icon_label)

        # A fixed width (set uniformly across every row by
        # SidebarMDATabs._align_row_titles, once every row exists) keeps the
        # toggle switches lined up in a column close to the text, rather than
        # each row's toggle drifting left/right with its own title length.
        self._title_label = title_label = QLabel(title)
        title_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row_layout.addWidget(title_label)

        # A QToggleSwitch's own mousePressEvent accepts the click (it's a real
        # button), so -- unlike the plain QLabels above, which don't accept
        # their press and let it bubble up to _frame -- clicking it toggles
        # the switch only, without also selecting the row via _frame.clicked.
        self._checkbox: QToggleSwitch | None
        if checked is None:
            self._checkbox = None
        else:
            checkbox = self._checkbox = QToggleSwitch()
            checkbox.setChecked(checked)
            checkbox.setAccessibleName(f"Use {title} in the acquisition")
            checkbox.toggled.connect(self.checkedChanged)
            checkbox.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            checkbox.onColor = QColor(_STATUS_ON_COLOR)
            row_layout.addWidget(checkbox)

        # Absorbs any leftover row width so icon/title/toggle stay left-packed
        # as a group instead of Qt spreading them out (see the collapsible
        # section's header for the same fix, and why it's needed).
        row_layout.addStretch()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._frame)

    @property
    def title(self) -> str:
        """Return the displayed row title."""
        return self._title

    @property
    def checked(self) -> bool | None:
        """Return the acquisition checkbox state, or ``None`` when absent."""
        return self._checkbox.isChecked() if self._checkbox is not None else None

    @property
    def checkbox(self) -> QToggleSwitch | None:
        """Return the acquisition enable toggle, if this row has one."""
        return self._checkbox

    def set_checked(self, checked: bool) -> None:
        """Set the acquisition state."""
        if self._checkbox is None:
            raise TypeError(f"{self._title!r} is not a checkable row")
        self._checkbox.setChecked(checked)

    def set_checkbox_enabled(self, enabled: bool) -> None:
        """Enable or disable the acquisition checkbox."""
        if self._checkbox is not None:
            self._checkbox.setEnabled(enabled)

    def set_selected(self, selected: bool) -> None:
        """Highlight (or unhighlight) this row as the current selection."""
        self._frame.set_selected(selected)


class SidebarMDATabs(CoreMDATabs):
    """Core-aware MDA axis container presented as a left sidebar navigation list.

    This remains a ``CoreMDATabs`` subclass so upstream ``MDAWidget`` logic
    continues to use its established API. The five widgets created by
    ``CoreMDATabs.create_subwidgets`` are moved intact from their temporary tabs
    into a shared ``QStackedWidget``, one row per widget in the sidebar list.
    """

    _AXES = (
        ("c", "Channels", "channels", 4),
        ("p", "Positions", "stage_positions", 1),
        ("g", "Grid / Tile Scan", "grid_plan", 2),
        ("z", "Z Stack", "z_plan", 3),
        ("t", "Time Series", "time_plan", 0),
    )
    _LIST_WIDTH = 190
    _STACK_MARGIN = 10

    def __init__(
        self, parent: QWidget | None = None, core: CMMCorePlus | None = None
    ) -> None:
        self._sections_ready = False
        self._row_by_widget: dict[QWidget, SidebarRow] = {}
        self._visual_index_by_widget: dict[QWidget, int] = {}
        self._logical_index_by_widget: dict[QWidget, int] = {}
        self._widget_by_logical_index: dict[int, QWidget] = {}
        self._supporting_sections_added = False
        self._editor_enabled = True
        self._restoring_roi_section = False
        super().__init__(parent, core)

        # ``_sections_ready`` is still False, so ``self.isChecked`` routes to the
        # base ``CheckableTabWidget`` implementation (see the override below).
        initial_states = {
            widget: bool(self.isChecked(widget)) for widget in self._axis_widgets()
        }

        while QTabWidget.count(self):
            QTabWidget.removeTab(self, 0)
        for checkbox in self._cboxes:
            checkbox.deleteLater()
        self._cboxes.clear()

        self._list_container = QWidget()
        self._list_container.setObjectName("mdaSidebarListContent")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)

        self._list_scroll = QScrollArea()
        self._list_scroll.setObjectName("mdaSidebarListScrollArea")
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._list_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._list_scroll.setFixedWidth(self._LIST_WIDTH)
        self._list_scroll.setWidget(self._list_container)

        self._stack = QStackedWidget()
        self._stack.setObjectName("mdaSidebarStack")

        for _axis, title, attr, logical_index in self._AXES:
            widget = cast("QWidget", getattr(self, attr))
            wrap = attr in ("grid_plan", "z_plan")
            self._add_row(
                widget,
                title=title,
                checked=initial_states[widget],
                icon=MDA_ICONS.get(attr),
                object_name=f"mda{attr.title().replace('_', '')}Row",
                wrap_in_card=wrap,
                logical_index=logical_index,
            )
            widget.setEnabled(initial_states[widget])
            # The grip lets a table trade vertical space with its neighbors in
            # the collapsible presentation's shared scroll area. Here each
            # table gets its own full-height stack page instead, so there are
            # no neighbors to negotiate with and the grip is just clutter.
            if (grip := getattr(widget, "_resize_grip", None)) is not None:
                grip.hide()

        # Give the selected dimension's editor some breathing room instead of
        # sitting flush against the widget's border (the sidebar list opts out
        # of this via its own padded row margins instead).
        stack_container = QWidget()
        stack_container.setObjectName("mdaSidebarStackContainer")
        stack_layout = QVBoxLayout(stack_container)
        stack_layout.setContentsMargins(
            self._STACK_MARGIN,
            self._STACK_MARGIN,
            self._STACK_MARGIN,
            self._STACK_MARGIN,
        )
        stack_layout.addWidget(self._stack)

        central = QWidget()
        central.setObjectName("mdaSidebarCentral")
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._list_scroll)
        central_layout.addWidget(stack_container, 1)

        # Wrap the sidebar + selected editor (but not the footer, which is
        # outside this tab entirely -- see MDAWidgetSidebar._install_layout)
        # in a scroll area, same as the collapsible presentation, so shrinking
        # the window past what the content needs scrolls it instead of
        # squeezing everything down into overlapping widgets.
        self._central_scroll = QScrollArea()
        self._central_scroll.setObjectName("mdaSidebarCentralScrollArea")
        self._central_scroll.setWidgetResizable(True)
        self._central_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Vertical-only: a table/editor that's too tall should scroll, but one
        # that's merely too wide already handles that itself (e.g. the tables'
        # own horizontal scrollbars) -- an outer horizontal scrollbar here
        # would fight with a QStackedWidget page whose minimumSizeHint is the
        # max across *all* pages, not just the visible one.
        self._central_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._central_scroll.setWidget(central)
        QTabWidget.addTab(self, self._central_scroll, "")
        if tab_bar := self.tabBar():
            tab_bar.hide()
        self.setDocumentMode(True)

        self._sections_ready = True
        for widget in self._logical_index_by_widget:
            row = self._row_by_widget[widget]
            row.checkedChanged.connect(
                lambda checked, axis_widget=widget: self._on_row_checked(
                    axis_widget, checked
                )
            )

        self.select_axis(self.channels)

    @staticmethod
    def _wrap_in_card(widget: QWidget, margin: int = 5) -> _CardFrame:
        """Wrap ``widget`` in the same bordered card style used elsewhere.

        A ``QGroupBox`` won't do here: on macOS's native style it draws only a
        title and a thin separator line, never a surrounding rectangle -- even
        with no title it draws nothing at all. ``_CardFrame`` is painted rather
        than relying on native/stylesheet chrome, so it renders the same
        rectangle across styles and themes.

        The card's own trailing stretch (after ``widget``) absorbs any extra
        height the card itself is stretched to (see ``_add_row``), so the
        card's *border* extends to fill the page while ``widget`` stays
        pinned to its top instead of drifting down into that empty space.
        """
        card = _CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.addWidget(widget)
        layout.addStretch()
        # QTabWidget explicitly hides pages as they are removed; reparenting
        # alone does not clear that state, so mark the widget visible again.
        widget.show()
        return card

    def _add_row(
        self,
        widget: QWidget,
        *,
        title: str,
        checked: bool | None,
        icon: str | None,
        object_name: str,
        wrap_in_card: bool = False,
        logical_index: int | None = None,
    ) -> SidebarRow:
        row = SidebarRow(title, checked=checked, icon=icon, parent=self._list_container)
        row.setObjectName(object_name)
        row.clicked.connect(lambda axis_widget=widget: self.select_axis(axis_widget))
        self._list_layout.addWidget(row)

        if wrap_in_card:
            # The QStackedWidget stretches whichever page is current to fill
            # its full height; a resizable QScrollArea's viewport widget gets
            # the same treatment, stretching the card to match -- which is
            # exactly what we want here, so the card's border extends the full
            # height of the page (its own trailing stretch, see
            # _wrap_in_card, keeps its content pinned to the top instead of
            # drifting into that extra space).
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(self._wrap_in_card(widget))
            page: QWidget = scroll
        else:
            page = widget
        widget.show()
        index = self._stack.count()
        self._stack.addWidget(page)

        self._row_by_widget[widget] = row
        self._visual_index_by_widget[widget] = index
        if logical_index is not None:
            self._logical_index_by_widget[widget] = logical_index
            self._widget_by_logical_index[logical_index] = widget
        return row

    def select_axis(self, widget: QWidget) -> None:
        """Show ``widget``'s editor on the right and highlight its row."""
        index = self._visual_index_by_widget.get(widget)
        if index is None:
            return
        self._stack.setCurrentIndex(index)
        for row_widget, row in self._row_by_widget.items():
            row.set_selected(row_widget is widget)

    @property
    def rows(self) -> tuple[SidebarRow, ...]:
        """Return all sidebar rows in visual order."""
        rows: list[SidebarRow] = []
        for index in range(self._list_layout.count()):
            item = self._list_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, SidebarRow):
                rows.append(widget)
        return tuple(rows)

    def row(self, key: str | QWidget | int) -> SidebarRow:
        """Return the row associated with an axis key, widget, or index."""
        widget = self._resolve_widget(key)
        if widget is None:
            raise ValueError(f"Unknown MDA axis: {key!r}")
        return self._row_by_widget[widget]

    def indexOf(self, widget: QWidget | None) -> int:
        """Return the original logical tab index for an axis widget."""
        if self._sections_ready and widget in self._logical_index_by_widget:
            return self._logical_index_by_widget[widget]
        return int(QTabWidget.indexOf(self, widget))

    def removeTab(self, index: int) -> None:
        """Remove a logical axis from the sidebar presentation.

        ``MDATabs`` consumers use ``removeTab(indexOf(axis_widget))`` to omit an
        axis entirely, notably the per-position sub-sequence editor. The
        sidebar presentation has only one physical ``QTabWidget`` page, so
        route logical axis indices to their row instead.
        """
        if not self._sections_ready:
            super().removeTab(index)
            return
        if (widget := self._widget_by_logical_index.get(index)) is not None:
            row = self._row_by_widget[widget]
            row.set_checked(False)
            row.hide()
            return
        QTabWidget.removeTab(self, index)

    def isChecked(
        self,
        key: str | int | QWidget,
        position: QTabBar.ButtonPosition = QTabBar.ButtonPosition.LeftSide,
    ) -> bool | None:
        """Return whether an MDA axis participates in the sequence."""
        if not self._sections_ready:
            return super().isChecked(cast("int | QWidget", key), position)
        widget = self._resolve_widget(key)
        if widget is None:
            return None
        return bool(self._row_by_widget[widget].checked)

    def setChecked(
        self,
        key: str | int | QWidget,
        checked: bool,
        position: QTabBar.ButtonPosition = QTabBar.ButtonPosition.LeftSide,
    ) -> None:
        """Set whether an MDA axis participates in the sequence."""
        if not self._sections_ready:
            super().setChecked(cast("int | QWidget", key), checked, position)
            return
        widget = self._resolve_widget(key)
        if widget is None:
            raise ValueError(f"Unknown MDA axis: {key!r}")
        self._row_by_widget[widget].set_checked(checked)

    def add_supporting_sections(
        self,
        *,
        camera_roi: CameraRoiWidget,
        save_info: SaveGroupBox,
    ) -> None:
        """Append Camera ROI and Saving rows after the five axes.

        Global settings (axis order, keep shutter open, autofocus axis) are
        not a sidebar row here -- ``MDAWidgetSidebar`` places them inline in
        its own footer instead (see ``MDAWidgetSidebar._install_layout``).
        """
        if self._supporting_sections_added:
            return

        camera_roi.setEnabled(False)
        self._camera_roi = camera_roi
        self.roi_row = self._add_row(
            camera_roi,
            title="Camera ROI",
            checked=False,
            icon=MDA_ICONS.get("camera_roi"),
            object_name="mdaRoiRow",
            wrap_in_card=True,
        )
        self.roi_row.checkedChanged.connect(self._on_roi_section_checked)

        self._save_info = save_info
        self.saving_row = self._add_row(
            save_info,
            title="Saving",
            checked=save_info.isChecked(),
            icon=MDA_ICONS.get("saving"),
            object_name="mdaSavingRow",
            wrap_in_card=True,
        )
        self.saving_row.checkedChanged.connect(save_info.setChecked)
        save_info.toggled.connect(self.saving_row.set_checked)

        self._list_layout.addStretch()
        self._supporting_sections_added = True
        self._align_row_titles()
        self.apply_save_body_style()

    def _align_row_titles(self) -> None:
        """Give every row's title the same fixed width.

        Keeps the toggle switches lined up in a column close to the text
        (see SidebarRow.__init__), rather than each row's toggle drifting
        left/right with its own title length.
        """
        rows = self.rows
        if not rows:
            return
        width = max(row._title_label.sizeHint().width() for row in rows)
        for row in rows:
            row._title_label.setFixedWidth(width)

    def apply_save_body_style(self) -> None:
        """Hide the duplicated native QGroupBox header inside Saving."""
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

    def _on_roi_section_checked(self, checked: bool) -> None:
        self._camera_roi.setEnabled(self._editor_enabled and checked)
        if not checked and not self._restoring_roi_section:
            # Mirrors MDAWidgetSidebar._apply_camera_roi's prepare_mda-time
            # reset, just triggered immediately so Live reflects "not using an
            # ROI" right away instead of waiting for the next MDA run's own
            # preflight. The planned ROI is restored into the editor right after,
            # so re-checking still shows exactly what was configured before --
            # only the real hardware moves, not the widget's remembered plan.
            planned_roi = self._camera_roi.roiValue()
            try:
                self._camera_roi.applyFullFrame()
            finally:
                self._camera_roi.setRoiValue(planned_roi)

    def set_editor_enabled(self, enabled: bool) -> None:
        """Enable or disable MDA editing while retaining row-selection access."""
        self._editor_enabled = enabled
        for widget, row in self._row_by_widget.items():
            row.set_checkbox_enabled(enabled)
            if widget in self._logical_index_by_widget:
                widget.setEnabled(enabled and bool(row.checked))
        if self._supporting_sections_added:
            self.roi_row.set_checkbox_enabled(enabled)
            self._camera_roi.setEnabled(enabled and bool(self.roi_row.checked))
            self.saving_row.set_checkbox_enabled(enabled)
            self._save_info.setEnabled(enabled)

    def _enable_tabs(self, enable: bool) -> None:
        """Implement the upstream acquisition enable/disable contract."""
        self.set_editor_enabled(enable)

    def _axis_widgets(self) -> tuple[QWidget, ...]:
        return (
            self.time_plan,
            self.stage_positions,
            self.grid_plan,
            self.z_plan,
            self.channels,
        )

    def _resolve_widget(self, key: str | int | QWidget) -> QWidget | None:
        if isinstance(key, str):
            return {
                "c": self.channels,
                "p": self.stage_positions,
                "g": self.grid_plan,
                "z": self.z_plan,
                "t": self.time_plan,
            }.get(key[0].lower() if key else "")
        if isinstance(key, int):
            return self._widget_by_logical_index.get(key)
        return key if key in self._row_by_widget else None

    def _on_row_checked(self, widget: QWidget, checked: bool) -> None:
        widget.setEnabled(self._editor_enabled and checked)
        self.tabChecked.emit(self._logical_index_by_widget[widget], checked)


class MDAWidgetSidebar(MDAWidget):
    """`MDAWidget` that presents the acquisition axes as a sidebar navigation list.

    Behaves exactly like [`MDAWidget`][pymmcore_widgets.MDAWidget] — same
    ``value``/``setValue``, ``prepare_mda``/``run_mda``, ``save``/``load``, and
    core awareness — but lays the axes out as a left-hand navigation list (icon,
    title, enable toggle switch) with the selected dimension's editor on the
    right, instead of a checkable tab widget or collapsible sections.
    """

    roiSelectionRequested = Signal(bool)

    def __init__(
        self, *, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent=parent, mmcore=mmcore)
        self.camera_roi = CameraRoiWidget(
            parent=self,
            mmcore=self._mmc,
            show_auto_snap=True,
        )
        self.camera_roi.snap_checkbox.setChecked(True)
        self.camera_roi.roiChanged.connect(lambda *_args: self.valueChanged.emit())
        self.camera_roi.roiSelectionRequested.connect(self.roiSelectionRequested.emit)
        self._install_layout()

    def _create_tab_widget(self) -> CoreMDATabs:
        return SidebarMDATabs(None, self._mmc)

    @property
    def tabs(self) -> SidebarMDATabs:
        """Return the sidebar axis container."""
        tabs = self.tab_wdg
        if not isinstance(tabs, SidebarMDATabs):  # pragma: no cover
            raise RuntimeError("MDAWidgetSidebar has the wrong axis container")
        return tabs

    def value(self) -> useq.MDASequence:
        """Return the sequence with the planned camera ROI in widget metadata."""
        value = super().value()
        meta: dict = value.metadata.setdefault(PYMMCW_METADATA_KEY, {})
        meta[CAMERA_ROI_METADATA_KEY] = {
            "enabled": self.tabs.roi_row.checked,
            **self.camera_roi.roiValue(),
        }
        return value

    def setValue(self, value: useq.MDASequence) -> None:
        """Restore the sequence and its planned ROI without changing hardware."""
        super().setValue(value)
        raw = value.metadata.get(PYMMCW_METADATA_KEY, {}).get(CAMERA_ROI_METADATA_KEY)
        enabled = False
        if isinstance(raw, Mapping):
            try:
                self.camera_roi.setRoiValue(raw)
            except ValueError:
                pass
            else:
                enabled = bool(raw.get("enabled", False))
        # Restoring a saved sequence must not reach out and change live hardware
        # as a side effect of setting the checkbox -- only an interactive uncheck
        # should do that (see SidebarMDATabs._on_roi_section_checked).
        self.tabs._restoring_roi_section = True
        try:
            self.tabs.roi_row.set_checked(enabled)
        finally:
            self.tabs._restoring_roi_section = False

    def prepare_mda(self) -> bool | SingleOutput | None:
        """Validate the MDA and apply its camera ROI once before acquisition."""
        output = super().prepare_mda()
        if isinstance(output, bool):
            return output
        self._apply_camera_roi()
        return output

    def _apply_camera_roi(self) -> None:
        if not self.tabs.roi_row.checked:
            planned_roi = self.camera_roi.roiValue()
            try:
                self.camera_roi.applyFullFrame()
            finally:
                # Full frame is a hardware preflight state, not a change to the
                # ROI the user has configured for the next enabled acquisition.
                self.camera_roi.setRoiValue(planned_roi)
            return
        roi = self.camera_roi.roiValue()
        camera = roi["camera"]
        if not camera:
            return
        requested = (roi["x"], roi["y"], roi["width"], roi["height"])
        if tuple(self._mmc.getROI(camera)) != requested:
            self._mmc.setROI(camera, *requested)

    def _enable_widgets(self, enable: bool) -> None:
        """Disable editors during an acquisition while keeping controls usable."""
        self.tabs.set_editor_enabled(enable)
        self._settings_group.setEnabled(enable)
        self._save_button.setEnabled(enable)
        self._load_button.setEnabled(enable)

    def _install_layout(self) -> None:
        """Move the global/save/footer controls into the sidebar presentation."""
        tabs = self.tabs
        layout = self.layout()
        if layout is None:  # pragma: no cover
            raise RuntimeError("MDAWidget has no layout")
        _clear_layout(layout)

        tabs.add_supporting_sections(
            camera_roi=self.camera_roi, save_info=self.save_info
        )

        footer = QFrame()
        footer.setObjectName("mdaExecutionFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(8, 4, 8, 8)

        # Global settings live inline here, in a group box that only extends
        # horizontally (its own natural, non-stretched height), instead of as
        # their own sidebar row -- there's room for them here, and unlike a
        # dimension they have no on/off state that would benefit from a
        # disclosure affordance.
        axis_row = QHBoxLayout()
        axis_row.setContentsMargins(0, 0, 0, 0)
        axis_row.addWidget(self._axis_order_label)
        self.axis_order.setMaximumWidth(100)
        axis_row.addWidget(self.axis_order)
        axis_row.addStretch()

        self._settings_group = settings_group = QGroupBox("Settings")
        settings_layout = QVBoxLayout(settings_group)
        settings_layout.addLayout(axis_row)
        settings_layout.addWidget(self.keep_shutter_open)
        settings_layout.addWidget(self.af_axis)
        footer_layout.addWidget(settings_group)

        estimate_row = QHBoxLayout()
        estimate_row.addWidget(self._time_warning)
        estimate_row.addWidget(self._duration_label, 1)
        footer_layout.addLayout(estimate_row)

        actions_row = QHBoxLayout()
        actions_row.addWidget(self._save_button)
        actions_row.addWidget(self._load_button)
        actions_row.addStretch()
        actions_row.addWidget(self.control_btns)
        footer_layout.addLayout(actions_row)

        box = cast("QVBoxLayout", layout)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(tabs, 1)
        box.addWidget(footer)


__all__ = [
    "MDAWidgetSidebar",
    "SidebarMDATabs",
    "SidebarRow",
]
