"""Collapsible-sections presentation of the core-connected MDA widget."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, cast

from qtpy.QtCore import QEvent, Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon

from pymmcore_widgets.useq_widgets._axis_tabs import (
    MDA_ICONS,
    _CardFrame,
    _wrap_in_card,
)

from ._core_mda import MDAWidget
from ._core_tabs import CoreMDATabs

_ICON_SIZE = 18
# Shared left/right inset for the sections, settings cards, and footer.
_MARGIN = 7

if TYPE_CHECKING:
    import useq
    from pymmcore_plus import CMMCorePlus
    from qtpy.QtGui import (
        QMouseEvent,
        QShowEvent,
    )

    from pymmcore_widgets.useq_widgets._mda_sequence import AutofocusAxis

    from ._save_widget import SaveGroupBox


# Semantic status-dot colors shown before a section summary. They read on both
# light and dark themes; they are inline rich-text colors (not a stylesheet), so
# they survive a downstream app that clears stylesheets or overrides palettes.
_STATUS_ON_COLOR = "#4caf50"
_STATUS_OFF_COLOR = "#f44336"


def _status_dot_html(on: bool, text: str) -> str:
    """Return ``text`` prefixed with a small colored status dot as rich text."""
    color = _STATUS_ON_COLOR if on else _STATUS_OFF_COLOR
    return f'<span style="color: {color};">&#9679;</span>&nbsp;{escape(text)}'


@dataclass(frozen=True)
class SectionMetrics:
    """Pixel sizes for the collapsible acquisition sections.

    Defaults suit a standard Qt style; a downstream theme can supply scaled
    values (e.g. for a zoom level) via
    :meth:`MDAWidgetCollapsible.set_section_metrics`.
    """

    header_height: int = 28
    disclosure_width: int = 22
    header_spacing: int = 4
    body_margin_h: int = 8
    body_margin_top: int = 4
    body_margin_bottom: int = 8
    body_spacing: int = 8
    content_spacing: int = 4
    # Shared horizontal inset for sections, settings cards, and the footer.
    footer_margin_h: int = _MARGIN
    footer_margin_top: int = 4
    footer_margin_bottom: int = 8


class _ClickableLabel(QLabel):
    """Section header label, identified by hosts when tinting header icons."""


class _ClickableFrame(QFrame):
    """Make the section header's background clickable too."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev: QMouseEvent | None) -> None:
        if ev is not None and ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            ev.accept()
        else:
            super().mousePressEvent(ev)


class CollapsibleAcquisitionSection(QWidget):
    """A disclosure section with an independent acquisition checkbox."""

    checkedChanged = Signal(bool)

    def __init__(
        self,
        title: str,
        *,
        checked: bool,
        metrics: SectionMetrics | None = None,
        icon: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._expanded = False
        self._metrics = metrics or SectionMetrics()
        self._content_widget: QWidget | None = None

        self._header = _ClickableFrame()
        self._header.setObjectName("mdaSectionHeader")
        self._header.clicked.connect(self.toggle)
        self._header_layout = QHBoxLayout(self._header)

        self._disclosure = QToolButton()
        self._disclosure.setObjectName("mdaSectionDisclosure")
        self._disclosure.setAutoRaise(True)
        self._disclosure.setProperty("variant", "ghost")
        self._disclosure.clicked.connect(self.toggle)
        self._header_layout.addWidget(
            self._disclosure, 0, Qt.AlignmentFlag.AlignVCenter
        )

        # Fixed policies keep the header aligned when its summary is hidden.
        self._checkbox = QCheckBox()
        self._checkbox.setChecked(checked)
        self._checkbox.setAccessibleName(f"Use {title} in the acquisition")
        self._checkbox.toggled.connect(self._on_checkbox_toggled)
        self._checkbox.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._header_layout.addWidget(self._checkbox)
        # Native checkbox styles may omit right-hand padding.
        self._header_layout.addSpacing(self._metrics.header_spacing)

        if icon:
            icon_label = _ClickableLabel()
            icon_label.setPixmap(QIconifyIcon(icon).pixmap(_ICON_SIZE, _ICON_SIZE))
            icon_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._header_layout.addWidget(icon_label)

        self._title_label = title_label = _ClickableLabel(title)
        title_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._header_layout.addWidget(
            title_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        self._summary_text = ""
        self._summary = _ClickableLabel()
        self._summary.setObjectName("mdaSectionSummary")
        self._summary.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._summary.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._header_layout.addWidget(self._summary, 1)

        self._body = QFrame()
        self._body.setObjectName("mdaSectionBody")
        self._body_layout = QVBoxLayout(self._body)

        self._card = _CardFrame()
        self._card.setObjectName("mdaSectionCard")
        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setContentsMargins(0, 0, 0, 0)
        self._card_layout.setSpacing(0)
        self._card_layout.addWidget(self._header)
        self._card_layout.addWidget(self._body)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._card)

        self._apply_metrics()
        self._on_disclosure_toggled(False)

    @property
    def title(self) -> str:
        """Return the displayed section title."""
        return self._title

    @property
    def expanded(self) -> bool:
        """Return whether the section body is visible."""
        return self._expanded

    @property
    def checked(self) -> bool:
        """Return the acquisition checkbox state."""
        return bool(self._checkbox.isChecked())

    @property
    def checkbox(self) -> QCheckBox:
        """Return the acquisition checkbox."""
        return self._checkbox

    @property
    def summary(self) -> str:
        """Return the collapsed summary text (without any status-dot markup)."""
        return self._summary_text

    @property
    def content_widget(self) -> QWidget | None:
        """Return the original widget hosted by this section."""
        return self._content_widget

    def set_content_widget(self, widget: QWidget) -> None:
        """Put ``widget`` directly in the section body."""
        if self._content_widget is not None:
            raise RuntimeError(f"{self._title!r} already has a content widget")
        self._content_widget = widget
        self._body_layout.addWidget(widget)
        # Reparenting does not clear a removed tab page's hidden state.
        widget.show()

    def set_expanded(self, expanded: bool) -> None:
        """Expand or collapse the body without changing acquisition state."""
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._on_disclosure_toggled(expanded)

    def toggle(self) -> None:
        """Toggle only the disclosure state."""
        self.set_expanded(not self.expanded)

    def set_checked(self, checked: bool) -> None:
        """Set the acquisition state without changing disclosure state."""
        self._checkbox.setChecked(checked)

    def _on_checkbox_toggled(self, checked: bool) -> None:
        if not checked:
            self.set_expanded(False)
        self.checkedChanged.emit(checked)

    def set_summary(self, summary: str, *, status: bool | None = None) -> None:
        """Update the derived collapsed summary.

        When ``status`` is given, a small colored dot is shown before the text:
        green for ``True`` (on) and red for ``False`` (off).
        """
        self._summary_text = summary
        if status is None:
            self._summary.setText(summary)
        else:
            self._summary.setText(_status_dot_html(status, summary))

    def set_checkbox_enabled(self, enabled: bool) -> None:
        """Enable or disable the acquisition checkbox."""
        self._checkbox.setEnabled(enabled)

    def apply_metrics(self, metrics: SectionMetrics) -> None:
        """Adopt new pixel sizes and re-apply them."""
        self._metrics = metrics
        self._apply_metrics()

    def changeEvent(self, event: QEvent | None) -> None:
        """Refresh spacing after a style change."""
        if event is not None and event.type() == QEvent.Type.StyleChange:
            self._apply_metrics()
        super().changeEvent(event)

    def _apply_metrics(self) -> None:
        m = self._metrics
        self._header.setMinimumHeight(m.header_height)
        # small horizontal inset so the disclosure/title are not flush against
        # the card border.
        self._header_layout.setContentsMargins(m.header_spacing, 0, m.header_spacing, 0)
        self._header_layout.setSpacing(m.header_spacing)
        self._disclosure.setFixedSize(m.disclosure_width, m.disclosure_width)
        self._body_layout.setContentsMargins(
            m.body_margin_h, m.body_margin_top, m.body_margin_h, m.body_margin_bottom
        )
        self._body_layout.setSpacing(m.body_spacing)

    def _on_disclosure_toggled(self, expanded: bool) -> None:
        self._disclosure.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._disclosure.setAccessibleName(
            f"{'Collapse' if expanded else 'Expand'} {self._title}"
        )
        self._body.setVisible(expanded)
        self._summary.setVisible(not expanded)


class CollapsibleCoreMDATabs(CoreMDATabs):
    """Core-aware MDA axis container presented as collapsible sections.

    This remains a ``CoreMDATabs`` subclass so upstream ``MDAWidget`` logic
    continues to use its established API.  The five widgets created by
    ``CoreMDATabs.create_subwidgets`` are put in section bodies instead of tabs
    (see ``_build_tabs``); each axis keeps its ``MDATabs`` tab index as a logical
    index (``indexOf``, ``tabChecked``).
    """

    # minimum number of table rows kept visible for the row-based editors
    _MIN_TABLE_ROWS = 3

    def __init__(
        self, parent: QWidget | None = None, core: CMMCorePlus | None = None
    ) -> None:
        self._metrics = SectionMetrics()
        self._section_by_widget: dict[QWidget, CollapsibleAcquisitionSection] = {}
        self._logical_index_by_widget: dict[QWidget, int] = {}
        self._widget_by_logical_index: dict[int, QWidget] = {}
        self._editor_enabled = True
        super().__init__(parent, core)

        # a child timer, rather than QTimer.singleShot, so a pending re-run dies
        # with this widget: PySide would otherwise still call it once deleted
        self._min_heights_timer = QTimer(self)
        self._min_heights_timer.setSingleShot(True)
        self._min_heights_timer.setInterval(0)
        self._min_heights_timer.timeout.connect(self._apply_editor_min_heights)

        for _, attr in self._AXES:
            widget = getattr(self, attr)
            section = self._section_by_widget[widget]
            section.checkedChanged.connect(
                lambda checked, axis_widget=widget: self._on_section_checked(
                    axis_widget, checked
                )
            )
            widget.valueChanged.connect(
                lambda *_args, axis_widget=widget: self._update_axis_summary(
                    axis_widget
                )
            )
        self.refresh_summaries()

    def _build_tabs(self) -> None:
        """Put each axis editor, unchecked, in a collapsible section."""
        self._content = QWidget()
        self._content.setObjectName("mdaSectionsContent")
        self._content_layout = QVBoxLayout(self._content)
        margin_h = self._metrics.footer_margin_h
        self._content_layout.setContentsMargins(margin_h, _MARGIN, margin_h, _MARGIN)
        self._content_layout.setSpacing(self._metrics.content_spacing)

        for logical_index, (title, attr) in enumerate(self._AXES):
            widget = cast("QWidget", getattr(self, attr))
            section = CollapsibleAcquisitionSection(
                title,
                checked=False,
                metrics=self._metrics,
                icon=MDA_ICONS.get(attr),
                parent=self._content,
            )
            section.setObjectName(f"mda{attr.title().replace('_', '')}Section")
            if attr in ("grid_plan", "z_plan"):
                section.set_content_widget(_wrap_in_card(widget))
            else:
                section.set_content_widget(widget)
            widget.setEnabled(False)
            self._section_by_widget[widget] = section
            self._logical_index_by_widget[widget] = logical_index
            self._widget_by_logical_index[logical_index] = widget
            self._content_layout.addWidget(section)

        self._apply_editor_min_heights()

        self._scroll = QScrollArea()
        self._scroll.setObjectName("mdaSectionsScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(self._content)
        QTabWidget.addTab(self, self._scroll, "")
        if tab_bar := self.tabBar():
            tab_bar.hide()
        self.setDocumentMode(True)

    def showEvent(self, a0: QShowEvent | None) -> None:
        # size hints are only fully reliable once the widget is realized, so
        # re-derive the editor heights now and again one event-loop cycle later,
        # once the layout has settled.
        super().showEvent(a0)
        self._apply_editor_min_heights()
        self._min_heights_timer.start()

    @property
    def sections(self) -> tuple[CollapsibleAcquisitionSection, ...]:
        """Return all sections in visual order."""
        sections: list[CollapsibleAcquisitionSection] = []
        for index in range(self._content_layout.count()):
            item = self._content_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, CollapsibleAcquisitionSection):
                sections.append(widget)
        return tuple(sections)

    def section(self, key: str | QWidget | int) -> CollapsibleAcquisitionSection:
        """Return the section associated with an axis key, widget, or index."""
        widget = self._resolve_widget(key)
        if widget is None:
            raise ValueError(f"Unknown MDA axis: {key!r}")
        return self._section_by_widget[widget]

    def indexOf(self, widget: QWidget | None) -> int:
        """Return the original logical tab index for an axis widget."""
        if widget in self._logical_index_by_widget:
            return self._logical_index_by_widget[widget]
        return int(QTabWidget.indexOf(self, widget))

    def isChecked(
        self,
        key: str | int | QWidget,
        position: QTabBar.ButtonPosition = QTabBar.ButtonPosition.LeftSide,
    ) -> bool | None:
        """Return whether an MDA axis participates in the sequence."""
        widget = self._resolve_widget(key)
        if widget is None:
            return None
        return self._section_by_widget[widget].checked

    def setChecked(
        self,
        key: str | int | QWidget,
        checked: bool,
        position: QTabBar.ButtonPosition = QTabBar.ButtonPosition.LeftSide,
    ) -> None:
        """Set whether an MDA axis participates in the sequence."""
        widget = self._resolve_widget(key)
        if widget is None:
            raise ValueError(f"Unknown MDA axis: {key!r}")
        self._section_by_widget[widget].set_checked(checked)

    def setValue(self, value: useq.MDASequence) -> None:
        """Restore the sequence through the original widgets and refresh summaries."""
        super().setValue(value)
        self.refresh_summaries()

    def add_autofocus(self, autofocus: AutofocusAxis) -> None:
        """Append the Autofocus section after the axes, its checkbox switching it.

        As with the tab, the section does not enable `autofocus`: it disables its
        own controls when switched off, and its enabled state says whether it can
        be used at all.
        """
        self.autofocus_section = section = CollapsibleAcquisitionSection(
            "Autofocus",
            checked=autofocus.isChecked(),
            metrics=self._metrics,
            icon=MDA_ICONS.get("autofocus"),
            parent=self._content,
        )
        section.setObjectName("mdaAutofocusSection")
        section.set_content_widget(_wrap_in_card(autofocus))
        self._content_layout.addWidget(section)
        self._autofocus = autofocus

        section.checkedChanged.connect(autofocus.setChecked)
        section.checkedChanged.connect(self._update_autofocus_summary)
        autofocus.setSwitch(section.set_checked)
        autofocus.availabilityChanged.connect(self._update_autofocus_availability)
        autofocus.valueChanged.connect(self._update_autofocus_summary)
        self._update_autofocus_availability()

    def _update_autofocus_availability(self) -> None:
        self.autofocus_section.set_checkbox_enabled(self._autofocus.isEnabled())
        self.autofocus_section.checkbox.setToolTip(self._autofocus.switchToolTip())
        self._update_autofocus_summary()

    def add_supporting_sections(self, *, save_info: SaveGroupBox) -> None:
        """Append the Saving section after the axes and Autofocus."""
        self.saving_section = CollapsibleAcquisitionSection(
            "Saving",
            checked=save_info.isChecked(),
            metrics=self._metrics,
            icon=MDA_ICONS.get("saving"),
            parent=self._content,
        )
        self.saving_section.setObjectName("mdaSavingSection")
        self.saving_section.set_content_widget(_wrap_in_card(save_info, margin=7))
        self.saving_section.checkedChanged.connect(save_info.setChecked)
        save_info.toggled.connect(self.saving_section.set_checked)
        save_info.valueChanged.connect(self._update_save_summary)
        self._content_layout.addWidget(self.saving_section)

        self._content_layout.addStretch(1)

        self._save_info = save_info
        self._supporting_sections_added = True
        self._update_save_summary()
        self.apply_save_body_style()

    def refresh_summaries(self) -> None:
        """Refresh every summary from the original source widgets."""
        for widget in self._section_by_widget:
            self._update_axis_summary(widget)
        if self._supporting_sections_added:
            self._update_save_summary()
        if hasattr(self, "autofocus_section"):
            self._update_autofocus_summary()

    def set_section_metrics(self, metrics: SectionMetrics) -> None:
        """Adopt new pixel sizes for every section and the content spacing."""
        self._metrics = metrics
        self._content_layout.setSpacing(metrics.content_spacing)
        margin_h = metrics.footer_margin_h
        self._content_layout.setContentsMargins(margin_h, _MARGIN, margin_h, _MARGIN)
        for section in self.sections:
            section.apply_metrics(metrics)
        # heights scale with the font, so re-derive the editor minimums too.
        self._apply_editor_min_heights()

    def _apply_editor_min_heights(self) -> None:
        """Keep tables usable while the outer scroll area handles overflow."""
        for widget in (self.channels, self.stage_positions, self.time_plan):
            # if the user dragged the table's resize grip, that height wins
            if widget._resize_grip.isUserResized():
                continue
            table = widget.table()
            row_h = table.verticalHeader().defaultSectionSize()
            h_header = table.horizontalHeader()
            header_h = max(h_header.height(), h_header.sizeHint().height())
            table.setMinimumHeight(
                header_h + row_h * self._MIN_TABLE_ROWS + 2 * table.frameWidth()
            )

    def _enable_tabs(self, enable: bool) -> None:
        """Enable or disable MDA editing while retaining disclosure access."""
        self._editor_enabled = enable
        for section in self.sections:
            section.set_checkbox_enabled(enable)
        super()._enable_tabs(enable)

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
        return key if key in self._section_by_widget else None

    def _on_section_checked(self, widget: QWidget, checked: bool) -> None:
        widget.setEnabled(self._editor_enabled and checked)
        self._update_axis_summary(widget)
        self.tabChecked.emit(self._logical_index_by_widget[widget], checked)

    def _update_axis_summary(self, widget: QWidget) -> None:
        section = self._section_by_widget[widget]
        if not section.checked:
            section.set_summary("Off", status=False)
            return
        detail = self._axis_detail(widget)
        section.set_summary(f"On · {detail}" if detail else "On", status=True)

    def _axis_detail(self, widget: QWidget) -> str:
        if widget is self.channels:
            channels = self.channels.value()
            if not channels:
                return "No channels"
            labels = [
                (
                    f"{channel.config} ({channel.exposure:g} ms)"
                    if channel.exposure is not None
                    else str(channel.config)
                )
                for channel in channels
            ]
            head = " · ".join(labels[:2])
            return f"{head} · +{len(labels) - 2}" if len(labels) > 2 else head

        if widget is self.stage_positions:
            count = len(self.stage_positions.value())
            return f"{count} position{'s' if count != 1 else ''}"

        if widget is self.grid_plan:
            grid_plan = self.grid_plan.value()
            rows = getattr(grid_plan, "rows", None)
            columns = getattr(grid_plan, "columns", None)
            if rows is not None and columns is not None:
                return f"{rows} x {columns}"
            return type(grid_plan).__name__.removeprefix("Grid")

        if widget is self.z_plan:
            z_plan = self.z_plan.value()
            step = getattr(z_plan, "step", None)
            if step is not None:
                return f"{type(z_plan).__name__.removeprefix('Z')} · {step:g} µm step"
            return type(z_plan).__name__.removeprefix("Z")

        if widget is self.time_plan:
            time_plan = self.time_plan.value()
            interval = getattr(time_plan, "interval", None)
            try:
                loops = getattr(time_plan, "loops", None)
            except ZeroDivisionError:
                # interval is 0 (e.g. user is mid-edit); nothing sensible to show
                loops = None
            if loops is not None and interval is not None:
                point_label = "point" if loops == 1 else "points"
                return f"{loops} {point_label} · {interval.total_seconds():g} s"
            return type(time_plan).__name__.removeprefix("T")

        return ""

    def _update_autofocus_summary(self) -> None:
        autofocus = self._autofocus
        if (kind := autofocus.kind()) is None:
            self.autofocus_section.set_summary("Off", status=False)
            return
        name = "Hardware" if kind == "hardware" else autofocus.softwareMethod()
        axes = ", ".join(autofocus.value()) or "No axis"
        self.autofocus_section.set_summary(f"On · {name} · {axes}", status=True)

    def _update_save_summary(self) -> None:
        save = self._save_info
        if not save.isChecked():
            summary = "Memory only"
        else:
            name = save.save_name.text() or "Choose destination"
            directory = save.save_dir.text()
            summary = f"{name} · {Path(directory).name}" if directory else str(name)
        self.saving_section.set_summary(summary)


class MDAWidgetCollapsible(MDAWidget):
    """`MDAWidget` that presents the acquisition axes as collapsible sections.

    Behaves exactly like [`MDAWidget`][pymmcore_widgets.MDAWidget] — same
    ``value``/``setValue``, ``prepare_mda``/``run_mda``, ``save``/``load``, core
    awareness, Settings card and execution footer — but lays the axes, Autofocus
    and Saving out as a scrollable stack of collapsible sections instead of tabs.
    """

    def _create_tab_widget(self) -> CoreMDATabs:
        return CollapsibleCoreMDATabs(None, self._mmc)

    @property
    def tabs(self) -> CollapsibleCoreMDATabs:
        """Return the collapsible axis container."""
        tabs = self.tab_wdg
        if not isinstance(tabs, CollapsibleCoreMDATabs):  # pragma: no cover
            raise RuntimeError("MDAWidgetCollapsible has the wrong axis container")
        return tabs

    def set_section_metrics(self, metrics: SectionMetrics) -> None:
        """Adopt new pixel sizes for every section, the settings card and footer."""
        self.tabs.set_section_metrics(metrics)
        self._apply_layout_metrics()

    def _apply_layout_metrics(self) -> None:
        """Inset the cards and footer from the sections' current metrics."""
        metrics = self.tabs._metrics
        # Line the cards up with the sections above them, and keep them clear of
        # both the sections and the footer.
        margin_h = metrics.footer_margin_h
        self._settings_row.setContentsMargins(margin_h, _MARGIN, margin_h, _MARGIN)
        self._footer_layout.setContentsMargins(
            metrics.footer_margin_h,
            metrics.footer_margin_top,
            metrics.footer_margin_h,
            metrics.footer_margin_bottom,
        )
        cast("QVBoxLayout", self.layout()).setSpacing(0)


__all__ = [
    "CollapsibleAcquisitionSection",
    "CollapsibleCoreMDATabs",
    "MDAWidgetCollapsible",
    "SectionMetrics",
]
