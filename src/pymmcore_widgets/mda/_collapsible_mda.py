"""Collapsible-sections presentation of the core-connected MDA widget.

``MDAWidgetCollapsible`` is a drop-in [`MDAWidget`][pymmcore_widgets.MDAWidget]
that presents the acquisition axes as compact, collapsible sections in a
scrollable body with a fixed execution footer, rather than as a checkable tab
widget.  It subclasses ``MDAWidget`` and reuses all of its behavior
(``value``/``setValue``, ``prepare_mda``/``run_mda``, ``save``/``load``, editor
enablement, and core awareness); only the presentation differs.

The widget is theme-agnostic: sizes come from a :class:`SectionMetrics` value
(sensible defaults, overridable with :meth:`MDAWidgetCollapsible.set_section_metrics`)
and every part carries an ``objectName`` so a downstream application can restyle
it with a stylesheet.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, cast

from qtpy.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from qtpy.QtGui import QColor, QPainter, QPaintEvent, QPalette, QPen, QShowEvent
from qtpy.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QTableWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pymmcore_widgets.control._camera_roi_widget import CameraRoiWidget
from pymmcore_widgets.useq_widgets import PYMMCW_METADATA_KEY

from ._core_mda import CoreMDATabs, MDAWidget

CAMERA_ROI_METADATA_KEY = "camera_roi"

if TYPE_CHECKING:
    import useq
    from pymmcore_plus import CMMCorePlus
    from qtpy.QtWidgets import QComboBox

    from pymmcore_widgets.useq_widgets._mda_sequence import (
        AutofocusAxis,
        KeepShutterOpen,
    )

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
    footer_margin_h: int = 8
    footer_margin_top: int = 4
    footer_margin_bottom: int = 8


class _CardFrame(QFrame):
    """A frame that paints a subtle rounded border around a section.

    The border color is derived from the palette's ``Text`` role at a low alpha
    so it reads in both light and dark themes. It is painted (rather than set
    via a stylesheet or a border palette role) so it survives a downstream
    application that clears stylesheets or overrides widget palettes.
    """

    _RADIUS = 6
    _BORDER_ALPHA = 70
    _FILL_ALPHA = 14

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = self.palette().color(QPalette.ColorRole.Text)
        border = QColor(text)
        border.setAlpha(self._BORDER_ALPHA)
        fill = QColor(text)
        fill.setAlpha(self._FILL_ALPHA)
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)


class CollapsibleAcquisitionSection(QWidget):
    """A disclosure section with an optional, independent enable checkbox."""

    checkedChanged = Signal(bool)

    def __init__(
        self,
        title: str,
        *,
        checked: bool | None = None,
        summary: str = "",
        expanded: bool = False,
        metrics: SectionMetrics | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._expanded = expanded
        self._metrics = metrics or SectionMetrics()
        self._content_widget: QWidget | None = None

        self._header = QFrame()
        self._header.setObjectName("mdaSectionHeader")
        self._header_layout = QHBoxLayout(self._header)
        self._header_layout.setContentsMargins(0, 0, 0, 0)

        self._disclosure = QToolButton()
        self._disclosure.setObjectName("mdaSectionDisclosure")
        self._disclosure.setAutoRaise(True)
        self._disclosure.setProperty("variant", "ghost")
        self._disclosure.setAccessibleName(f"Expand {title}")
        self._disclosure.clicked.connect(self.toggle)
        self._header_layout.addWidget(self._disclosure)

        self._checkbox: QCheckBox | None
        self._title_button: QToolButton | None
        if checked is None:
            self._checkbox = None
            title_button = self._title_button = QToolButton()
            title_button.setText(title)
            title_button.setAutoRaise(True)
            title_button.setProperty("variant", "ghost")
            title_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            title_button.clicked.connect(self.toggle)
            # A QToolButton grows to fill spare width and centers its text; a
            # QCheckBox left-packs its content, so checkbox sections stay left
            # while this one would drift right once the summary hides on expand.
            # Pin it to its size hint on the left to match the checkbox sections.
            self._header_layout.addWidget(
                title_button,
                0,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )
        else:
            self._title_button = None
            checkbox = self._checkbox = QCheckBox(title)
            checkbox.setChecked(checked)
            checkbox.setAccessibleName(f"Use {title} in the acquisition")
            checkbox.toggled.connect(self.checkedChanged)
            self._header_layout.addWidget(checkbox)

        self._summary_text = summary
        self._summary = QLabel(summary)
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

        # Wrap header + body in a bordered "card" so sections read as distinct
        # groups (see _CardFrame for why the border is painted rather than
        # styled via a stylesheet or palette role).
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
        self._on_disclosure_toggled(expanded)

    @property
    def title(self) -> str:
        """Return the displayed section title."""
        return self._title

    @property
    def expanded(self) -> bool:
        """Return whether the section body is visible."""
        return self._expanded

    @property
    def checked(self) -> bool | None:
        """Return the acquisition checkbox state, or ``None`` when absent."""
        return self._checkbox.isChecked() if self._checkbox is not None else None

    @property
    def checkbox(self) -> QCheckBox | None:
        """Return the acquisition checkbox, if this section has one."""
        return self._checkbox

    @property
    def summary(self) -> str:
        """Return the collapsed summary text (without any status-dot markup)."""
        return self._summary_text

    @property
    def content_widget(self) -> QWidget | None:
        """Return the original widget hosted by this section."""
        return self._content_widget

    @property
    def content_visible(self) -> bool:
        """Return whether the section body is currently visible."""
        return not self._body.isHidden()

    def set_content_widget(self, widget: QWidget) -> None:
        """Put ``widget`` directly in the section body."""
        if self._content_widget is not None:
            raise RuntimeError(f"{self._title!r} already has a content widget")
        self._content_widget = widget
        self._body_layout.addWidget(widget)
        # QTabWidget explicitly hides pages as they are removed.  Reparenting
        # alone does not clear that state, so mark the original editor visible
        # inside its new (possibly collapsed) parent.
        widget.show()

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        """Append a supporting widget to a non-axis section."""
        self._body_layout.addWidget(widget, stretch)

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
        if self._checkbox is None:
            raise TypeError(f"{self._title!r} is not a checkable section")
        self._checkbox.setChecked(checked)

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
        if self._checkbox is not None:
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
        self._disclosure.setFixedSize(m.disclosure_width, m.header_height)
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
    ``CoreMDATabs.create_subwidgets`` are moved intact from their temporary tabs
    into section bodies.
    """

    _AXES = (
        ("c", "Channels", "channels", 4, True),
        ("p", "Positions", "stage_positions", 1, False),
        ("g", "Grid / Tile Scan", "grid_plan", 2, False),
        ("z", "Z Stack", "z_plan", 3, False),
        ("t", "Time Series", "time_plan", 0, False),
    )
    # minimum number of table rows kept visible for the row-based editors
    _MIN_TABLE_ROWS = 3

    def __init__(
        self, parent: QWidget | None = None, core: CMMCorePlus | None = None
    ) -> None:
        self._sections_ready = False
        self._metrics = SectionMetrics()
        self._section_by_axis: dict[str, CollapsibleAcquisitionSection] = {}
        self._section_by_widget: dict[QWidget, CollapsibleAcquisitionSection] = {}
        self._logical_index_by_widget: dict[QWidget, int] = {}
        self._widget_by_logical_index: dict[int, QWidget] = {}
        self._supporting_sections_added = False
        self._editor_enabled = True
        super().__init__(parent, core)

        # ``_sections_ready`` is still False, so ``self.isChecked`` routes to the
        # base ``CheckableTabWidget`` implementation (see the override below).
        # A zero-arg ``super()`` here would resolve ``__class__`` in the
        # comprehension's own scope and fail, so go through ``self``.
        initial_states = {
            widget: bool(self.isChecked(widget)) for widget in self._axis_widgets()
        }

        while QTabWidget.count(self):
            QTabWidget.removeTab(self, 0)
        for checkbox in self._cboxes:
            checkbox.deleteLater()
        self._cboxes.clear()

        self._content = QWidget()
        self._content.setObjectName("mdaSectionsContent")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(self._metrics.content_spacing)

        for axis, title, attr, logical_index, expanded in self._AXES:
            widget = cast("QWidget", getattr(self, attr))
            section = CollapsibleAcquisitionSection(
                title,
                checked=initial_states[widget],
                expanded=expanded,
                metrics=self._metrics,
                parent=self._content,
            )
            section.setObjectName(f"mda{attr.title().replace('_', '')}Section")
            section.set_content_widget(widget)
            widget.setEnabled(initial_states[widget])
            self._section_by_axis[axis] = section
            self._section_by_widget[widget] = section
            self._logical_index_by_widget[widget] = logical_index
            self._widget_by_logical_index[logical_index] = widget
            self._content_layout.addWidget(section)

        self._apply_editor_min_heights()

        self._scroll = QScrollArea()
        self._scroll.setObjectName("mdaSectionsScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setWidget(self._content)
        QTabWidget.addTab(self, self._scroll, "")
        if tab_bar := self.tabBar():
            tab_bar.hide()
        self.setDocumentMode(True)

        self._sections_ready = True
        for widget, section in self._section_by_widget.items():
            section.checkedChanged.connect(
                lambda checked, axis_widget=widget: self._on_section_checked(
                    axis_widget, checked
                )
            )
            value_changed = getattr(widget, "valueChanged", None)
            if value_changed is not None:
                value_changed.connect(
                    lambda *_args, axis_widget=widget: self._update_axis_summary(
                        axis_widget
                    )
                )
        # the grid editor's natural height changes with its mode (Absolute
        # Bounds is tallest); re-pin it whenever the mode/value changes.
        self.grid_plan.valueChanged.connect(self._schedule_editor_min_heights)
        self.refresh_summaries()

    def showEvent(self, a0: QShowEvent | None) -> None:
        # size hints are only fully reliable once the widget is realized, so
        # re-derive the editor heights here (the grid editor especially reports
        # a not-yet-settled hint during construction).
        super().showEvent(a0)
        self._schedule_editor_min_heights()

    def _schedule_editor_min_heights(self, *_: object) -> None:
        # run now and again one event-loop cycle later, so a just-switched grid
        # mode has settled its layout before we measure the content height.
        self._apply_editor_min_heights()
        QTimer.singleShot(0, self._apply_editor_min_heights)

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
        if self._sections_ready and widget in self._logical_index_by_widget:
            return self._logical_index_by_widget[widget]
        return int(QTabWidget.indexOf(self, widget))

    def removeTab(self, index: int) -> None:
        """Remove a logical axis from the collapsible presentation.

        ``MDATabs`` consumers use ``removeTab(indexOf(axis_widget))`` to omit an
        axis entirely, notably the per-position sub-sequence editor.  The
        collapsible presentation has only one physical ``QTabWidget`` page, so
        route logical axis indices to their section instead.
        """
        if not self._sections_ready:
            super().removeTab(index)
            return
        if (widget := self._widget_by_logical_index.get(index)) is not None:
            section = self._section_by_widget[widget]
            section.set_checked(False)
            section.hide()
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
        return bool(self._section_by_widget[widget].checked)

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
        self._section_by_widget[widget].set_checked(checked)

    def setValue(self, value: useq.MDASequence) -> None:
        """Restore the sequence through the original widgets and refresh summaries."""
        super().setValue(value)
        self.refresh_summaries()

    def add_supporting_sections(
        self,
        *,
        axis_order: QComboBox,
        keep_shutter_open: KeepShutterOpen,
        autofocus_axis: AutofocusAxis,
        camera_roi: CameraRoiWidget,
        save_info: SaveGroupBox,
    ) -> None:
        """Append ROI, Saving, and global Settings after the five axes."""
        if self._supporting_sections_added:
            return

        self.roi_section = CollapsibleAcquisitionSection(
            "Camera ROI",
            checked=False,
            expanded=False,
            metrics=self._metrics,
            parent=self._content,
        )
        self.roi_section.setObjectName("mdaRoiSection")
        self.roi_section.set_content_widget(camera_roi)
        self.roi_section.checkedChanged.connect(self._on_roi_section_checked)
        camera_roi.layout().setContentsMargins(5, 5, 5, 5)
        camera_roi.roiChanged.connect(self._on_roi_value_changed)
        camera_roi.setEnabled(False)
        self._camera_roi = camera_roi
        self._content_layout.addWidget(self.roi_section)

        self.saving_section = CollapsibleAcquisitionSection(
            "Saving",
            checked=save_info.isChecked(),
            expanded=False,
            metrics=self._metrics,
            parent=self._content,
        )
        self.saving_section.setObjectName("mdaSavingSection")
        self.saving_section.set_content_widget(save_info)
        self.saving_section.checkedChanged.connect(save_info.setChecked)
        save_info.toggled.connect(self.saving_section.set_checked)
        save_info.valueChanged.connect(self._update_save_summary)
        self._content_layout.addWidget(self.saving_section)

        self.settings_section = CollapsibleAcquisitionSection(
            "Settings",
            expanded=False,
            metrics=self._metrics,
            parent=self._content,
        )
        self._axis_order = axis_order
        self._keep_shutter_open = keep_shutter_open
        self._autofocus_axis = autofocus_axis
        axis_order.currentTextChanged.connect(self._update_settings_summary)
        keep_shutter_open.valueChanged.connect(self._update_settings_summary)
        autofocus_axis.valueChanged.connect(self._update_settings_summary)
        # Toggling an axis repopulates the order combo with signals blocked (so
        # currentTextChanged does not fire); tabChecked does fire, and this
        # connection runs after the upstream handler that rebuilds the combo, so
        # the order string is already up to date when we read it.
        self.tabChecked.connect(self._update_settings_summary)
        axis_row = QWidget()
        axis_layout = QHBoxLayout(axis_row)
        axis_layout.setContentsMargins(0, 0, 0, 0)
        axis_layout.addWidget(QLabel("Axis order:"))
        axis_layout.addWidget(axis_order)
        axis_layout.addStretch()
        self.settings_section.add_widget(axis_row)
        self.settings_section.add_widget(keep_shutter_open)
        self.settings_section.add_widget(autofocus_axis)
        self._content_layout.addWidget(self.settings_section)
        self._content_layout.addStretch()

        self._save_info = save_info
        self._supporting_sections_added = True
        self._update_roi_summary()
        self._update_save_summary()
        self._update_settings_summary()
        self.apply_save_body_style()

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

    def refresh_summaries(self) -> None:
        """Refresh every summary from the original source widgets."""
        for widget in self._section_by_widget:
            self._update_axis_summary(widget)
        if self._supporting_sections_added:
            self._update_roi_summary()
            self._update_save_summary()
            self._update_settings_summary()

    def _on_roi_section_checked(self, checked: bool) -> None:
        self._camera_roi.setEnabled(self._editor_enabled and checked)
        self._update_roi_summary()

    def _on_roi_value_changed(
        self, x: int, y: int, width: int, height: int, _mode: str
    ) -> None:
        self._update_roi_summary(x, y, width, height)

    def _update_roi_summary(
        self,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        if not self.roi_section.checked:
            self.roi_section.set_summary("Off · Full chip", status=False)
            return
        if None in (x, y, width, height):
            value = self._camera_roi.roiValue()
            x = value["x"]
            y = value["y"]
            width = value["width"]
            height = value["height"]
        self.roi_section.set_summary(
            f"On · {width} x {height} at ({x}, {y})", status=True
        )

    def _update_settings_summary(self, *_: object) -> None:
        # Show the acquisition axis order (e.g. "cz"), and only append the
        # shutter/autofocus axes when they are actually in use -- naming the
        # axes (e.g. "Keep Shutter Open: z", "AF: p") rather than just the
        # feature.
        parts: list[str] = []
        if order := self._axis_order.currentText():
            parts.append(order)
        if shutter := self._keep_shutter_open.value():
            parts.append(f"Keep Shutter Open: {', '.join(shutter)}")
        if af := self._autofocus_axis.value():
            parts.append(f"AF: {', '.join(af)}")
        self.settings_section.set_summary(" · ".join(parts))

    def set_section_metrics(self, metrics: SectionMetrics) -> None:
        """Adopt new pixel sizes for every section and the content spacing."""
        self._metrics = metrics
        self._content_layout.setSpacing(metrics.content_spacing)
        for section in self.sections:
            section.apply_metrics(metrics)
        # heights scale with the font, so re-derive the editor minimums too.
        self._apply_editor_min_heights()

    def _apply_editor_min_heights(self) -> None:
        """Keep editors tall enough to remain usable when several are open.

        The sections live in a scroll area, so an editor with an Expanding
        height would otherwise be squeezed when its neighbors are also expanded:
        the channel/position/time tables collapse to a single row, and the
        grid editor (itself a scroll area) collapses to just its mode selector.
        Give each a sensible minimum; the outer scroll area then scrolls instead
        of collapsing the editors.
        """
        for widget in self._section_by_widget:
            table_getter = getattr(widget, "table", None)
            if callable(table_getter) and isinstance(
                table := table_getter(), QTableWidget
            ):
                # if the user dragged the table's resize grip, that height wins
                grip = getattr(widget, "_resize_grip", None)
                if grip is not None and grip.isUserResized():
                    continue
                row_h = table.verticalHeader().defaultSectionSize()
                h_header = table.horizontalHeader()
                header_h = max(h_header.height(), h_header.sizeHint().height())
                table.setMinimumHeight(
                    header_h + row_h * self._MIN_TABLE_ROWS + 2 * table.frameWidth()
                )
            elif isinstance(widget, QScrollArea) and (inner := widget.widget()):
                # The grid editor is itself a scroll area whose mode pages have
                # an Expanding size policy, so any extra height becomes a gap in
                # the middle, while too little clips the fields. Pin it to its
                # content's natural height for the current mode (it changes with
                # the mode -- Absolute Bounds is tallest). Activate the layout
                # first, and note this is re-run deferred on show / mode change
                # so the hint is measured once settled.
                if (inner_layout := inner.layout()) is not None:
                    inner_layout.activate()
                widget.setFixedHeight(
                    inner.sizeHint().height() + 2 * widget.frameWidth()
                )

    def set_editor_enabled(self, enabled: bool) -> None:
        """Enable or disable MDA editing while retaining disclosure access."""
        self._editor_enabled = enabled
        for widget, section in self._section_by_widget.items():
            section.set_checkbox_enabled(enabled)
            widget.setEnabled(enabled and bool(section.checked))
        if self._supporting_sections_added:
            self.roi_section.set_checkbox_enabled(enabled)
            self._camera_roi.setEnabled(enabled and bool(self.roi_section.checked))
            self.settings_section._body.setEnabled(enabled)
            self.saving_section.set_checkbox_enabled(enabled)
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
            return _compact_items(labels, "channels")

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
            loops = getattr(time_plan, "loops", None)
            interval = getattr(time_plan, "interval", None)
            if loops is not None and interval is not None:
                seconds = (
                    interval.total_seconds()
                    if hasattr(interval, "total_seconds")
                    else interval
                )
                point_label = "point" if loops == 1 else "points"
                return f"{loops} {point_label} · {seconds:g} s"
            return type(time_plan).__name__.removeprefix("T")

        return ""

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
    ``value``/``setValue``, ``prepare_mda``/``run_mda``, ``save``/``load``, and
    core awareness — but lays the axes out as a scrollable stack of collapsible
    sections with a fixed execution footer instead of a checkable tab widget.
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
        self._footer_layout: QVBoxLayout | None = None
        self._install_layout()

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
        """Adopt new pixel sizes for every section and the footer."""
        self.tabs.set_section_metrics(metrics)
        if self._footer_layout is not None:
            self._footer_layout.setContentsMargins(
                metrics.footer_margin_h,
                metrics.footer_margin_top,
                metrics.footer_margin_h,
                metrics.footer_margin_bottom,
            )

    def value(self) -> useq.MDASequence:
        """Return the sequence with the planned camera ROI in widget metadata."""
        value = super().value()
        meta: dict = value.metadata.setdefault(PYMMCW_METADATA_KEY, {})
        meta[CAMERA_ROI_METADATA_KEY] = {
            "enabled": self.tabs.roi_section.checked,
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
        self.tabs.roi_section.set_checked(enabled)
        self.tabs.refresh_summaries()

    def prepare_mda(self) -> bool | str | Path | None:
        """Validate the MDA and apply its camera ROI once before acquisition."""
        output = super().prepare_mda()
        if isinstance(output, bool):
            return output
        self._apply_camera_roi()
        return output

    def _apply_camera_roi(self) -> None:
        if not self.tabs.roi_section.checked:
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
        self._save_button.setEnabled(enable)
        self._load_button.setEnabled(enable)

    def _install_layout(self) -> None:
        """Move the global/save/footer controls into the sectioned presentation."""
        tabs = self.tabs
        layout = self.layout()
        if layout is None:  # pragma: no cover
            raise RuntimeError("MDAWidget has no layout")
        _clear_layout(layout)

        tabs.add_supporting_sections(
            axis_order=self.axis_order,
            keep_shutter_open=self.keep_shutter_open,
            autofocus_axis=self.af_axis,
            camera_roi=self.camera_roi,
            save_info=self.save_info,
        )

        metrics = tabs._metrics
        footer = QFrame()
        footer.setObjectName("mdaExecutionFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(
            metrics.footer_margin_h,
            metrics.footer_margin_top,
            metrics.footer_margin_h,
            metrics.footer_margin_bottom,
        )
        self._footer_layout = footer_layout
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


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is not None and (child_layout := item.layout()) is not None:
            _clear_layout(child_layout)


def _compact_items(items: list[str], noun: str) -> str:
    if not items:
        return f"No {noun}"
    if len(items) <= 2:
        return " · ".join(items)
    return f"{' · '.join(items[:2])} · +{len(items) - 2}"


__all__ = [
    "CollapsibleAcquisitionSection",
    "CollapsibleCoreMDATabs",
    "MDAWidgetCollapsible",
    "SectionMetrics",
]
