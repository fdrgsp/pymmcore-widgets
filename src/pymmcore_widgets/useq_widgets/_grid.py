from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

import useq
from qtpy.QtCore import QEvent, QPointF, QSize, Qt, Signal
from qtpy.QtGui import (
    QBrush,
    QColor,
    QFontMetrics,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QResizeEvent,
    QTransform,
)
from qtpy.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from superqt import QEnumComboBox
from superqt.iconify import QIconifyIcon
from superqt.utils import signals_blocked

from pymmcore_widgets._util import (
    QWIDGETSIZE_MAX,
    SeparatorWidget,
    disable_wheel_scroll_recursive,
)

if TYPE_CHECKING:
    from typing import Literal, TypeAlias

    from shapely import Polygon

    GridPlan: TypeAlias = (
        useq.GridFromEdges
        | useq.GridRowsColumns
        | useq.GridWidthHeight
        | useq.GridFromPolygon
    )

    class ValueWidget(Protocol, QWidget):  # pyright: ignore
        def setValue(self, plan: Any) -> None: ...


class RelativeTo(Enum):
    center = "center"
    top_left = "top_left"


class OrderMode(Enum):
    """Different ways of ordering the grid positions."""

    row_wise_snake = "row_wise_snake"
    column_wise_snake = "column_wise_snake"
    spiral = "spiral"
    row_wise = "row_wise"
    column_wise = "column_wise"


class Mode(Enum):
    NUMBER = "number"
    AREA = "area"
    BOUNDS = "bounds"
    POLYGON = "polygon"

    def __str__(self) -> str:
        return self.value

    def to_useq_cls(self) -> type[GridPlan]:
        return _MODE_TO_USEQ[self]

    @classmethod
    def for_grid_plan(cls, plan: GridPlan) -> Mode:
        if isinstance(plan, useq.GridRowsColumns):
            return cls.NUMBER
        elif isinstance(plan, useq.GridFromEdges):
            return cls.BOUNDS
        elif isinstance(plan, useq.GridWidthHeight):
            return cls.AREA
        elif isinstance(plan, useq.GridFromPolygon):
            return cls.POLYGON
        raise TypeError(f"Unknown grid plan type: {type(plan)}")  # pragma: no cover


_MODE_TO_USEQ: dict[Mode, type[GridPlan]] = {
    Mode.NUMBER: useq.GridRowsColumns,
    Mode.BOUNDS: useq.GridFromEdges,
    Mode.AREA: useq.GridWidthHeight,
    Mode.POLYGON: useq.GridFromPolygon,
}


# (icon, label) of each mode's radio button
_MODE_ITEMS: dict[Mode, tuple[str, str]] = {
    Mode.NUMBER: ("mdi:grid", "Rows && Columns"),
    Mode.AREA: ("mdi:arrow-expand-all", "Width && Height"),
    Mode.BOUNDS: ("mdi:vector-square", "Absolute Bounds"),
    Mode.POLYGON: ("mdi:vector-polygon", "Polygon"),
}
_TIMES = "\u00d7"  # the multiplication sign, between rows/columns and sizes
# above this many positions the summary only states the count, rather than
# iterating every position to measure the grid
_MAX_SUMMARY_POSITIONS = 10_000
# Every mode's rows are this tall -- the height of the bounds' Mark and Move
# buttons -- with _ROW_SPACING between them, so the fields sit at the same y in
# every mode and nothing jumps on a mode switch.
_ROW_HEIGHT = 26
_ROW_SPACING = 4


class GridPlanWidget(QWidget):
    """Widget to edit a [`useq-schema` GridPlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#grid-plans).

    The mode is picked from a row of radio buttons on top, as in the Z stack
    editor. Below it, the settings every mode shares (overlap, acquisition order
    and a summary of the resulting grid) sit in a column on the left, and the
    current mode's own settings on the right.

    This widget does not scroll on its own; a caller that needs it to (e.g. a
    tab page with limited height) should wrap it in its own ``QScrollArea``.
    """

    valueChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # Expanding, so it fills whatever it's placed in (a card, a scroll
        # area's viewport, ...) instead of leaving a gap of bare container.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._mode: Mode = Mode.AREA  # will change to NUMBER below in init
        self._fov_width: float | None = None
        self._fov_height: float | None = None

        # WIDGETS -----------------------------------------------

        self._mode_radios: dict[Mode, QRadioButton] = {}
        self._mode_btn_group = QButtonGroup(self)
        for mode, (_, text) in _MODE_ITEMS.items():
            radio = self._mode_radios[mode] = QRadioButton(text)
            radio.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            radio.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            self._mode_btn_group.addButton(radio)
        # Polygon is only offered once a GridFromPolygon plan is set (see
        # setMode): it cannot be defined from this widget.
        self._mode_radios[Mode.POLYGON].hide()
        self._update_mode_icons()

        self.overlap = QDoubleSpinBox()
        self.overlap.setRange(-1000, 1000)
        self.overlap.setValue(0)
        self.overlap.setSuffix(" %")
        self.order = _OrderButtons()

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        # the summary's length varies; it wraps instead of widening the column
        self._summary.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )

        self.row_col_wdg = _RowsColsWidget()
        self.width_height_wdg = _WidthHeightWidget()
        self.bounds_wdg = _BoundsWidget()
        self.polygon_wdg = _PolygonWidget()
        # ease of lookup
        self._mode_to_widget: dict[Mode, ValueWidget] = {
            Mode.NUMBER: self.row_col_wdg,
            Mode.AREA: self.width_height_wdg,
            Mode.BOUNDS: self.bounds_wdg,
            Mode.POLYGON: self.polygon_wdg,
        }
        # aliases
        self.relative_to = self.row_col_wdg.relative_to

        # LAYOUT -----------------------------------------------

        # top: the mode, as in ZPlanWidget
        mode_layout = QHBoxLayout()
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.addWidget(QLabel("Mode:"))
        mode_layout.setSpacing(10)
        for mode in (Mode.NUMBER, Mode.AREA, Mode.BOUNDS):
            if mode is not Mode.NUMBER:
                mode_layout.addSpacing(25)
            mode_layout.addWidget(self._mode_radios[mode])
        # Polygon carries its spacing in a container shown and hidden with it,
        # so while hidden it leaves no gap at the end of the row
        self._polygon_slot = QWidget()
        slot_layout = QHBoxLayout(self._polygon_slot)
        slot_layout.setContentsMargins(25, 0, 0, 0)
        slot_layout.addWidget(self._mode_radios[Mode.POLYGON])
        self._polygon_slot.hide()
        mode_layout.addWidget(self._polygon_slot)
        mode_layout.addStretch()

        # left: what every mode shares. Its rows are mode rows too (_ROW_HEIGHT
        # tall, _ROW_SPACING apart), so each lines up with a row on the right.
        self._common = QWidget()
        self._common_form = QGridLayout()
        self._common_form.setContentsMargins(0, 0, 0, 0)
        self._common_form.setHorizontalSpacing(10)
        self._common_form.setVerticalSpacing(_ROW_SPACING)
        for row, (text, field) in enumerate(
            (("Overlap", self.overlap), ("Order", self.order))
        ):
            label = QLabel(text)
            label.setMinimumHeight(_ROW_HEIGHT)
            self._common_form.addWidget(label, row, 0)
            self._common_form.addWidget(field, row, 1)
        # rows that belong to one mode only (see _add_mode_option)
        self._mode_options: list[tuple[Mode, QWidget, QWidget]] = []
        # labels outside the mode forms that share their label column, each with
        # every text it can show (see _align_label_columns)
        self._extra_aligned_labels: list[tuple[QLabel, tuple[str, ...]]] = []
        # The summary sits at the bottom, level with the last mode row (e.g.
        # the bounds' Right), whichever mode is shown.
        self._summary.setMinimumHeight(_ROW_HEIGHT)
        common = QVBoxLayout(self._common)
        common.setContentsMargins(0, 0, 0, 0)
        common.setSpacing(_ROW_SPACING)
        common.addLayout(self._common_form)
        common.addStretch()
        common.addWidget(self._summary)
        # its contents are the same in every mode, so it keeps one width and
        # the mode settings beside it never shift on a mode switch
        self._common.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )

        divider = SeparatorWidget(orientation=Qt.Orientation.Vertical)

        # right: the current mode's own settings
        self._stack = _ResizableStackedWidget(self)
        self._stack.addWidget(self.row_col_wdg)
        self._stack.addWidget(self.width_height_wdg)
        self._stack.addWidget(self.bounds_wdg)
        self._stack.addWidget(self.polygon_wdg)
        self._stack.setStableWidgets(
            self.row_col_wdg,
            self.width_height_wdg,
            self.bounds_wdg,
        )

        body_layout = QHBoxLayout()
        body_layout.setSpacing(16)
        body_layout.addWidget(self._common)
        body_layout.addWidget(divider)
        body_layout.addWidget(self._stack, 1)

        main_layout = QVBoxLayout(self)
        main_layout.addLayout(mode_layout)
        main_layout.addWidget(SeparatorWidget())
        main_layout.addLayout(body_layout)

        # this widget commonly sits inside an outer collapsible section or a
        # caller-provided QScrollArea (see the standard MDAWidget's Grid tab),
        # so disable mouse-wheel scrolling on its combo/spin boxes to avoid
        # accidentally changing a value while scrolling past it
        disable_wheel_scroll_recursive(self)

        # CONNECTIONS ------------------------------------------

        self._mode_btn_group.buttonToggled.connect(self._on_mode_toggled)
        self.row_col_wdg.relative_to.currentIndexChanged.connect(
            lambda index: self._sync_relative_to(
                index,
                self.row_col_wdg,
                self.width_height_wdg,
            )
        )
        self.width_height_wdg.relative_to.currentIndexChanged.connect(
            lambda index: self._sync_relative_to(
                index,
                self.width_height_wdg,
                self.row_col_wdg,
            )
        )
        self.row_col_wdg.valueChanged.connect(self._on_change)
        self.width_height_wdg.valueChanged.connect(self._on_change)
        self.bounds_wdg.valueChanged.connect(self._on_change)
        self.overlap.valueChanged.connect(self._on_change)
        self.order.valueChanged.connect(self._on_change)

        self._align_label_columns()
        self._update_summary_palette()
        self.setMode(Mode.NUMBER)

    def changeEvent(self, event: QEvent | None) -> None:
        super().changeEvent(event)
        if event is None:
            return  # pragma: no cover
        # the label column width is measured from the font, so re-align whenever
        # the font or style changes (e.g. an application zoom/theme change).
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._align_label_columns()
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self._update_summary_palette()
            self._update_mode_icons()

    def _align_label_columns(self) -> None:
        """Give every mode's form common label and field widths.

        Each mode's settings use their own ``QFormLayout``, so without this the
        fields would start at a different x in each mode, jumping on every mode
        switch. Sizing every label to the widest one lines them up. Labels in
        ``_extra_aligned_labels`` join them, sized to their widest text.
        """
        labels: list[QLabel] = []
        fields: list[QWidget] = []
        for wdg in (self.row_col_wdg, self.width_height_wdg, self.bounds_wdg):
            form = wdg.layout()
            if not isinstance(form, QFormLayout):
                continue  # pragma: no cover
            for row in range(form.rowCount()):
                item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                label = item.widget() if item is not None else None
                if isinstance(label, QLabel) and label.text():
                    labels.append(label)
                    field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                    if field_item is not None and (field := field_item.widget()):
                        fields.append(field)
        if not labels:
            return  # pragma: no cover
        # Release the widths pinned by a previous alignment first: a QLabel's
        # sizeHint() never reports less than its minimum width, so measuring
        # them while still fixed could only ever grow the column -- after
        # zooming out, the labels kept the previous (larger) zoom's width.
        texts: list[tuple[QLabel, tuple[str, ...]]] = [
            (label, (label.text(),)) for label in labels
        ]
        texts += self._extra_aligned_labels
        labels += [label for label, _ in self._extra_aligned_labels]
        for wdg in (*labels, *fields):
            wdg.setMinimumWidth(0)
            wdg.setMaximumWidth(QWIDGETSIZE_MAX)
        # Measure with QFontMetrics rather than sizeHint(): it is correct even
        # before the labels are shown/polished (sizeHint() can still be 0 then).
        width = max(
            QFontMetrics(label.font()).horizontalAdvance(text)
            for label, label_texts in texts
            for text in label_texts
        )
        # QLabel.sizeHint() can exceed that font-metrics estimate by a pixel or
        # two for some strings (its internal text layout rounds differently),
        # so fold in any already-known sizeHint too. sizeHint() can legitimately
        # be 0 before the label is shown/polished, but that never raises our max.
        width = max(width, max(label.sizeHint().width() for label in labels))
        for label in labels:
            label.setFixedWidth(width)

        field_width = max(field.sizeHint().width() for field in fields)
        for field in fields:
            field.setFixedWidth(field_width)

    # ------------------------- Public API -------------------------

    def mode(self) -> Mode:
        """Return the current mode, one of "number", "area", or "bounds"."""
        return self._mode

    def setMode(
        self, mode: Mode | Literal["number", "area", "bounds", "polygon"]
    ) -> None:
        """Set the current mode, one of "number", "area", "bounds", or "polygon".

        Parameters
        ----------
        mode : Mode | Literal["number", "area", "bounds", "polygon"]
            The mode to set.
        """
        mode = Mode(mode)
        if mode is Mode.POLYGON and not self.polygon_wdg._polygon:
            raise ValueError(
                "Polygon mode may not be selected programmatically without a "
                " pre-assigned GridFromPolygon Plan."
            )

        if mode is Mode.POLYGON:
            self._mode_radios[Mode.POLYGON].show()
            self._polygon_slot.show()
        with signals_blocked(self._mode_btn_group):
            self._mode_radios[mode].setChecked(True)

        for option_mode, *widgets in self._mode_options:
            for wdg in widgets:
                wdg.setVisible(option_mode is mode)

        previous, self._mode = getattr(self, "_mode", None), mode
        if previous != self._mode:
            current_wdg = self._mode_to_widget[self._mode]
            self._stack.setCurrentWidget(current_wdg)
            self._on_change()

    def value(self) -> GridPlan:
        """Return the current value of the widget as a [`useq-schema` GridPlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#grid-plans).

        Returns
        -------
        useq.GridFromEdges | useq.GridRowsColumns | useq.GridWidthHeight
            The current [GridPlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#grid-plans)
            value of the widget.
        """
        kwargs = {
            **self._stack.currentWidget().value(),
            "overlap": (self.overlap.value(), self.overlap.value()),
            "mode": self.order.currentEnum().value,
            "fov_width": self._fov_width,
            "fov_height": self._fov_height,
        }
        if self._mode not in {Mode.NUMBER, Mode.AREA}:
            kwargs.pop("relative_to", None)

        return self._mode.to_useq_cls()(**kwargs)

    def setValue(self, value: GridPlan) -> None:
        """Set the current value of the widget from a [`useq-schema` GridPlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#grid-plans).

        Parameters
        ----------
        value : useq.GridFromEdges | useq.GridRowsColumns | useq.GridWidthHeight | useq.GridFromPolygon
            The [`useq-schema` GridPlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#grid-plans)
            to set.
        """  # noqa: E501
        mode = Mode.for_grid_plan(value)

        with signals_blocked(self):
            mode_wdg = self._mode_to_widget[mode]
            mode_wdg.setValue(value)
            self._stack.setCurrentWidget(mode_wdg)
            if value.fov_height:
                self._fov_height = value.fov_height
            if value.fov_width:
                self._fov_width = value.fov_width
            with signals_blocked(self.overlap), signals_blocked(self.order):
                if value.overlap:
                    self.overlap.setValue(value.overlap[0])
                self.order.setCurrentEnum(OrderMode(value.mode.value))
                self.setMode(mode)

        self._on_change()

    def setFovWidth(self, value: float) -> None:
        """Set the current field of view width."""
        self._fov_width = value
        self._on_change()

    def setFovHeight(self, value: float) -> None:
        """Set the current field of view height."""
        self._fov_height = value
        self._on_change()

    def fovWidth(self) -> float | None:
        """Return the current field of view width."""
        return self._fov_width

    def fovHeight(self) -> float | None:
        """Return the current field of view height."""
        return self._fov_height

    # ------------------------- Private API -------------------------

    def _add_mode_option(self, mode: Mode, label: str, widget: QWidget) -> None:
        """Add a row of `mode`'s own settings to the left column, above the summary.

        The row shows only in `mode`, but keeps its space in the others, so the
        summary under it does not move on a mode switch.
        """
        label_wdg = QLabel(label)
        label_wdg.setMinimumHeight(_ROW_HEIGHT)
        row = self._common_form.rowCount()
        self._common_form.addWidget(label_wdg, row, 0)
        self._common_form.addWidget(widget, row, 1)
        for wdg in (label_wdg, widget):
            policy = wdg.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            wdg.setSizePolicy(policy)
            wdg.setVisible(mode is self._mode)
        self._mode_options.append((mode, label_wdg, widget))

    def _icon_color(self) -> str:
        """The palette's text color, so the mode icons follow the theme."""
        color = self.palette().color(
            QPalette.ColorGroup.Active, QPalette.ColorRole.Text
        )
        return str(color.name())

    def _update_mode_icons(self) -> None:
        """Draw the mode icons in the (possibly new) palette's text color."""
        color = self._icon_color()
        for mode, radio in self._mode_radios.items():
            radio.setIcon(QIconifyIcon(_MODE_ITEMS[mode][0], color=color))

    def _on_mode_toggled(self, button: QAbstractButton, checked: bool) -> None:
        if checked:
            for mode, radio in self._mode_radios.items():
                if radio is button:
                    self.setMode(mode)

    def _sync_relative_to(
        self,
        index: int,
        source: _RowsColsWidget | _WidthHeightWidget,
        target: _RowsColsWidget | _WidthHeightWidget,
    ) -> None:
        with signals_blocked(target.relative_to):
            target.relative_to.setCurrentIndex(index)
        source.valueChanged.emit()

    def _update_summary_palette(self) -> None:
        """Draw the summary in a muted version of the text color."""
        palette = self._summary.palette()
        color = QColor(
            self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
        )
        color.setAlpha(165)
        palette.setColor(QPalette.ColorRole.WindowText, color)
        self._summary.setPalette(palette)

    def _on_change(self) -> None:
        if (val := self.value()) is None:
            return  # pragma: no cover
        # here we are calling setValue on the polygon widget only because
        # we want to be able to change parameters such as the overlap or acquisition
        # order from the gui.
        if isinstance(val, useq.GridFromPolygon):
            self.polygon_wdg.setValue(val)
        self._summary.setText(_grid_summary(val))
        self.valueChanged.emit(val)


def _grid_summary(plan: GridPlan) -> str:
    """Describe the grid `plan` makes: tiles, rows x columns, and its size in mm.

    Whatever cannot be known is left out: the tiles of an area or a set of bounds
    depend on the field of view, and so does the size of any grid. A polygon only
    gets its tile count.
    """
    try:
        count = plan.num_positions()
    except ValueError:
        return "Set the pixel size to compute the tiles."
    parts = [f"{count} tile{'s' if count != 1 else ''}"]
    if count > _MAX_SUMMARY_POSITIONS or isinstance(plan, useq.GridFromPolygon):
        return parts[0]
    positions = list(plan)
    rows = {p.grid_row for p in positions}
    cols = {p.grid_col for p in positions}
    if None not in rows and None not in cols:
        parts.append(f"{len(rows)} {_TIMES} {len(cols)}")
    if plan.fov_width and plan.fov_height:
        xs = [p.x for p in positions if p.x is not None]
        ys = [p.y for p in positions if p.y is not None]
        if xs and ys:
            width = (max(xs) - min(xs) + plan.fov_width) / 1000
            height = (max(ys) - min(ys) + plan.fov_height) / 1000
            parts.append(f"{width:.2f} {_TIMES} {height:.2f} mm")
    return " · ".join(parts)


def _mode_form(parent: QWidget) -> QFormLayout:
    """A mode's settings form: left-aligned labels, fields top-left."""
    layout = QFormLayout(parent)
    layout.setContentsMargins(0, 0, 0, 0)
    # as in the left column and the bounds grid, so fields line up across modes
    layout.setHorizontalSpacing(10)
    layout.setVerticalSpacing(_ROW_SPACING)
    layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return layout


def _add_mode_row(form: QFormLayout, text: str, field: QWidget) -> None:
    """Add a _ROW_HEIGHT row to a mode's form, its field centered in it.

    The height is pinned once, here, rather than in _align_label_columns, which
    re-runs on every font change and would undo an app-wide zoom's rescaling of
    it (as it rescales the bounds' Mark and Move buttons).
    """
    label = QLabel(text)
    label.setMinimumHeight(_ROW_HEIGHT)
    form.addRow(label, field)
    # QFormLayout top-aligns a field in a taller row; center it, like the
    # bounds fields beside their buttons
    form.setAlignment(field, Qt.AlignmentFlag.AlignVCenter)


class _RowsColsWidget(QWidget):
    valueChanged = Signal()

    def __init__(self) -> None:
        super().__init__()

        self.rows = QSpinBox()
        self.rows.setRange(1, 1000)
        self.rows.setValue(1)
        self.rows.setSuffix(" fields")
        self.columns = QSpinBox()
        self.columns.setRange(1, 1000)
        self.columns.setValue(1)
        self.columns.setSuffix(" fields")
        self.relative_to = QEnumComboBox(self, RelativeTo)

        layout = _mode_form(self)
        _add_mode_row(layout, "Rows", self.rows)
        _add_mode_row(layout, "Columns", self.columns)
        _add_mode_row(layout, "Start at", self.relative_to)

        self.rows.valueChanged.connect(self.valueChanged)
        self.columns.valueChanged.connect(self.valueChanged)

    def value(self) -> dict[str, int | str]:
        return {
            "rows": self.rows.value(),
            "columns": self.columns.value(),
            "relative_to": self.relative_to.currentEnum().value,
        }

    def setValue(self, plan: useq.GridRowsColumns) -> None:
        self.rows.setValue(plan.rows)
        self.columns.setValue(plan.columns)
        self.relative_to.setCurrentText(plan.relative_to.value)


class _WidthHeightWidget(QWidget):
    valueChanged = Signal()

    def __init__(self) -> None:
        super().__init__()

        self.area_width = QDoubleSpinBox()
        self.area_width.setRange(0.01, 100)
        self.area_width.setDecimals(2)
        # here for area_width and area_height we are using mm instead of µm because
        # (as in GridWidthHeight) because it is probably easier for a user to define
        # the area in mm
        self.area_width.setSuffix(" mm")
        self.area_width.setSingleStep(0.1)
        self.area_height = QDoubleSpinBox()
        self.area_height.setRange(0.01, 100)
        self.area_height.setDecimals(2)
        self.area_height.setSuffix(" mm")
        self.area_height.setSingleStep(0.1)
        self.relative_to = QEnumComboBox(self, RelativeTo)

        layout = _mode_form(self)
        _add_mode_row(layout, "Width", self.area_width)
        _add_mode_row(layout, "Height", self.area_height)
        _add_mode_row(layout, "Start at", self.relative_to)

        self.area_width.valueChanged.connect(self.valueChanged)
        self.area_height.valueChanged.connect(self.valueChanged)

    def value(self) -> dict[str, float | str]:
        # converting width and height to microns because GridWidthHeight expects µm
        return {
            "width": self.area_width.value() * 1000,
            "height": self.area_height.value() * 1000,
            "relative_to": self.relative_to.currentEnum().value,
        }

    def setValue(self, plan: useq.GridWidthHeight) -> None:
        # GridWidthHeight width and height are expressed in µm but this widget
        # uses mm, so we convert width and height to mm here
        self.area_width.setValue(plan.width / 1000)
        self.area_height.setValue(plan.height / 1000)
        self.relative_to.setCurrentText(plan.relative_to.value)


class _BoundsWidget(QWidget):
    valueChanged = Signal()

    def __init__(self) -> None:
        super().__init__()

        self.left = QDoubleSpinBox()
        self.left.setRange(-10000000, 10000000)
        self.left.setDecimals(3)
        self.left.setSuffix(" µm")
        self.top = QDoubleSpinBox()
        self.top.setRange(-10000000, 10000000)
        self.top.setDecimals(3)
        self.top.setSuffix(" µm")
        self.right = QDoubleSpinBox()
        self.right.setRange(-10000000, 10000000)
        self.right.setDecimals(3)
        self.right.setSuffix(" µm")
        self.bottom = QDoubleSpinBox()
        self.bottom.setRange(-10000000, 10000000)
        self.bottom.setDecimals(3)
        self.bottom.setSuffix(" µm")

        form = _mode_form(self)
        _add_mode_row(form, "Top", self.top)
        _add_mode_row(form, "Bottom", self.bottom)
        _add_mode_row(form, "Left", self.left)
        _add_mode_row(form, "Right", self.right)

        self.top.valueChanged.connect(self.valueChanged)
        self.bottom.valueChanged.connect(self.valueChanged)
        self.left.valueChanged.connect(self.valueChanged)
        self.right.valueChanged.connect(self.valueChanged)

    def value(self) -> dict[str, float]:
        return {
            "left": self.left.value(),
            "top": self.top.value(),
            "right": self.right.value(),
            "bottom": self.bottom.value(),
        }

    def setValue(self, plan: useq.GridFromEdges) -> None:
        self.left.setValue(plan.left)
        self.top.setValue(plan.top)
        self.right.setValue(plan.right)
        self.bottom.setValue(plan.bottom)


class _PolygonWidget(QWidget):
    """QWidget that draws a useq.GridFromPolygon."""

    POLY_PEN = QPen(Qt.GlobalColor.darkMagenta)
    POLY_BRUSH = QBrush(Qt.BrushStyle.NoBrush)
    BB_PEN = QPen(Qt.GlobalColor.darkGray, 0, Qt.PenStyle.DotLine)
    VERTEX_PEN = QPen(Qt.GlobalColor.magenta, 0)
    VERTEX_BRUSH = QBrush(Qt.GlobalColor.magenta)
    CENTER_PEN = QPen(Qt.GlobalColor.darkGreen, 0)
    CENTER_BRUSH = QBrush(Qt.GlobalColor.darkGreen)
    FOV_PEN = QPen(Qt.GlobalColor.darkGray)
    FOV_BRUSH = QBrush(Qt.BrushStyle.NoBrush)
    # maximum allowed FOV rectangle size in pixels; if an FOV would be larger
    # than this when rendered, the view will be zoomed out to keep it at or
    # below this size.
    MAX_FOV_PIXELS = 50

    def __init__(self) -> None:
        super().__init__()

        self._polygon: useq.GridFromPolygon | None = None

        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.view.setTransform(QTransform.fromScale(1, -1))  # y-up

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    # ----------------------------PUBLIC METHODS----------------------------

    def value(self) -> dict[str, Any]:
        if not self._polygon:  # pragma: no cover
            return {}
        return {
            "vertices": self._polygon.vertices,
            "convex_hull": self._polygon.convex_hull,
            "offset": self._polygon.offset,
        }

    def setValue(self, plan: useq.GridFromPolygon) -> None:
        """Set and render the polygon/grid plan."""
        self._polygon = plan
        self._redraw()

    # ----------------------------PRIVATE METHODS----------------------------

    def _redraw(self) -> None:
        self.scene.clear()
        if (plan := self._polygon) is None:
            return

        fw, fh = plan.fov_width or 0, plan.fov_height or 0
        pen_size = int(fw * 0.04) if fw > 0 else 1
        vertex_radius = center_radius = pen_size

        verts: list[tuple[float, float]] = list(plan.vertices or [])

        # draw polygon outline
        poly = plan.poly
        poly_item = self._make_polygon_item(poly)
        self.POLY_PEN.setWidth(pen_size)
        poly_item.setPen(self.POLY_PEN)
        poly_item.setBrush(self.POLY_BRUSH)
        self.scene.addItem(poly_item)

        # draw vertices
        for x, y in verts:
            self._add_dot(x, y, vertex_radius, self.VERTEX_PEN, self.VERTEX_BRUSH)

        # draw dashed bounding box
        min_x, min_y, max_x, max_y = poly.bounds
        bb = QGraphicsRectItem(min_x, min_y, max_x - min_x, max_y - min_y)
        self.BB_PEN.setWidth(pen_size)
        bb.setPen(self.BB_PEN)
        self.scene.addItem(bb)

        # draw grid centers and FOV rectangles
        centers = self._compute_centers(plan)

        # connect centers
        if len(centers) >= 2:
            path = QPainterPath(QPointF(*centers[0]))
            for x, y in centers[1:]:
                path.lineTo(x, y)
            path_item = QGraphicsPathItem(path)
            path_pen = QPen(self.CENTER_PEN)
            path_pen.setWidth(pen_size)
            path_pen.setStyle(Qt.PenStyle.DotLine)
            path_item.setPen(path_pen)
            path_item.setZValue(0.5)
            self.scene.addItem(path_item)

        if fw > 0 and fh > 0:
            hw, hh = fw / 2.0, fh / 2.0
            for cx, cy in centers:
                rect = QGraphicsRectItem(cx - hw, cy - hh, fw, fh)
                self.FOV_PEN.setWidth(pen_size)
                rect.setPen(self.FOV_PEN)
                rect.setBrush(self.FOV_BRUSH)
                self.scene.addItem(rect)

        for cx, cy in centers:
            self._add_dot(cx, cy, center_radius, self.CENTER_PEN, self.CENTER_BRUSH)

        self._fit_view_to_items()

    def _make_polygon_item(self, shapely_poly: Polygon) -> QGraphicsPathItem:
        """Create a QGraphicsPathItem for a shapely Polygon with holes."""
        path = QPainterPath()
        # exterior
        ext = [QPointF(x, y) for (x, y) in shapely_poly.exterior.coords]
        if ext:
            path.addPolygon(QPolygonF(ext))
        # holes
        for interior in shapely_poly.interiors:
            pts = [QPointF(x, y) for (x, y) in interior.coords]
            if pts:
                path.addPolygon(QPolygonF(pts))
        item = QGraphicsPathItem(path)
        return item

    def _add_dot(self, x: float, y: float, r: float, pen: QPen, brush: QBrush) -> None:
        d = 2 * r
        if ell := self.scene.addEllipse(x - r, y - r, d, d, pen, brush):
            ell.setZValue(1.0)

    def _fit_view_to_items(self, pad: int = 10) -> None:
        rect = self.scene.itemsBoundingRect()
        if rect.isNull():
            return
        # add padding
        padded = rect.adjusted(-pad, -pad, pad, pad)
        self.scene.setSceneRect(padded)
        # keep transform (y-up) while fitting
        self.view.resetTransform()
        self.view.fitInView(padded, Qt.AspectRatioMode.KeepAspectRatio)
        # after fitting, ensure that individual FOV rectangles are not rendered
        # larger than MAX_FOV_PIXELS. If they are, scale the view down.
        current_scale = self.view.transform().m11()
        if (
            self._polygon is not None
            and (fw := self._polygon.fov_width)
            and fw > 0
            and (current_scale * fw) > self.MAX_FOV_PIXELS
        ):
            max_allowed = self.MAX_FOV_PIXELS / fw
            factor = max_allowed / current_scale
            self.view.scale(factor, factor)
        self.view.setTransform(QTransform.fromScale(1, -1) * self.view.transform())

    def _compute_centers(self, plan: useq.GridFromPolygon) -> list[tuple[float, float]]:
        """Compute grid center points within the polygon."""
        centers: list[tuple[float, float]] = []
        for item in plan:
            x, y = item.x, item.y
            if x is None or y is None:
                continue
            centers.append((float(x), float(y)))
        return centers

    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        super().resizeEvent(a0)
        self._fit_view_to_items()


class _ResizableStackedWidget(QStackedWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)
        self._stable_widgets: tuple[QWidget, ...] = ()
        self.currentChanged.connect(self.onCurrentChanged)

    def addWidget(self, w: QWidget | None) -> int:
        if w is not None:
            w.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )
        return super().addWidget(w)  # type: ignore [no-any-return]

    def setStableWidgets(self, *widgets: QWidget) -> None:
        """Reserve the largest size among ``widgets`` when one is current."""
        self._stable_widgets = widgets
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        return self._combined_hint("sizeHint")

    def minimumSizeHint(self) -> QSize:
        return self._combined_hint("minimumSizeHint")

    def _combined_hint(self, method: str) -> QSize:
        current = self.currentWidget()
        if current in self._stable_widgets:
            widgets = self._stable_widgets
        else:
            widgets = (current,) if current is not None else ()

        hint = QSize()
        for widget in widgets:
            hint = hint.expandedTo(getattr(widget, method)())
        return hint

    def onCurrentChanged(self, idx: int) -> None:
        self.updateGeometry()


class _OrderButtons(QWidget):
    """One checkable button per acquisition order, each drawing its own path."""

    valueChanged = Signal()

    _ORDERS: ClassVar[dict[OrderMode, str]] = {
        OrderMode.row_wise_snake: "Row-wise snake",
        OrderMode.column_wise_snake: "Column-wise snake",
        OrderMode.row_wise: "Row-wise",
        OrderMode.column_wise: "Column-wise",
        OrderMode.spiral: "Spiral",
    }
    _ICON_SIZE = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._group = QButtonGroup(self)
        self._buttons: dict[OrderMode, QToolButton] = {}
        for order, tooltip in self._ORDERS.items():
            btn = QToolButton()
            btn.setCheckable(True)
            btn.setIconSize(QSize(self._ICON_SIZE, self._ICON_SIZE))
            # a mode row's height, so the Order row lines up with the rows beside it
            btn.setFixedHeight(_ROW_HEIGHT)
            btn.setToolTip(tooltip)
            btn.setAccessibleName(tooltip)
            self._group.addButton(btn)
            self._buttons[order] = btn
            layout.addWidget(btn)
        layout.addStretch()
        self._buttons[OrderMode.row_wise_snake].setChecked(True)
        self._group.buttonToggled.connect(self._on_button_toggled)
        self._update_icons()

    def currentEnum(self) -> OrderMode:
        """Return the selected acquisition order."""
        for order, btn in self._buttons.items():
            if btn.isChecked():
                return order
        return OrderMode.row_wise_snake  # pragma: no cover

    def setCurrentEnum(self, order: OrderMode) -> None:
        """Select an acquisition order."""
        self._buttons[OrderMode(order)].setChecked(True)

    def changeEvent(self, event: QEvent | None) -> None:
        super().changeEvent(event)
        if event is not None and event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
        ):
            self._update_icons()

    def _on_button_toggled(self, _: QAbstractButton, checked: bool) -> None:
        if checked:
            self.valueChanged.emit()

    def _update_icons(self) -> None:
        """Draw the selected path in the text color and the others dimmed.

        The difference is part of the icons (their checked state) rather than
        left to the style alone, whose checked tool button can be hard to tell
        apart from an unchecked one (e.g. on macOS in a light theme). The text
        color, unlike the highlight, contrasts with the background in any theme.
        """
        palette = self.palette()
        checked = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
        unchecked = QColor(checked)
        unchecked.setAlpha(110)
        ratio = self.devicePixelRatioF()
        for order, btn in self._buttons.items():
            icon = QIcon()
            for color, state in (
                (unchecked, QIcon.State.Off),
                (checked, QIcon.State.On),
            ):
                pixmap = _order_pixmap(order, color, self._ICON_SIZE, ratio)
                icon.addPixmap(pixmap, QIcon.Mode.Normal, state)
            btn.setIcon(icon)


def _order_pixmap(order: OrderMode, color: QColor, size: int, ratio: float) -> QPixmap:
    """Draw the path `order` takes through a 3 x 3 grid, starting at the dot."""
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    pad = 3
    step = (size - 2 * pad) / 2
    pts = [
        [QPointF(pad + c * step, pad + r * step) for c in range(3)] for r in range(3)
    ]
    cols = [[pts[r][c] for r in range(3)] for c in range(3)]
    runs: list[list[QPointF]]
    if order is OrderMode.row_wise_snake:
        runs = [
            [p for r, row in enumerate(pts) for p in (row if r % 2 == 0 else row[::-1])]
        ]
    elif order is OrderMode.column_wise_snake:
        runs = [
            [
                p
                for c, col in enumerate(cols)
                for p in (col if c % 2 == 0 else col[::-1])
            ]
        ]
    elif order is OrderMode.row_wise:
        runs = pts
    elif order is OrderMode.column_wise:
        runs = cols
    else:  # spiral, outward from the center
        runs = [[pts[1][1], pts[1][2], pts[2][2], pts[2][1], pts[2][0],
                 pts[1][0], pts[0][0], pts[0][1], pts[0][2]]]  # fmt: skip

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for i, run in enumerate(runs):
        if i:  # a raster's jump back to the start of the next line
            painter.setPen(QPen(color, 1, Qt.PenStyle.DotLine))
            painter.drawLine(runs[i - 1][-1], run[0])
        painter.setPen(QPen(color, 1.6))
        path = QPainterPath(run[0])
        for point in run[1:]:
            path.lineTo(point)
        painter.drawPath(path)
    painter.setBrush(color)
    painter.drawEllipse(runs[0][0], 1.8, 1.8)
    painter.end()
    return pixmap


def _alternate(line: list[QPointF], index: int) -> list[QPointF]:
    """Every other line of a snake runs backwards."""
    return line if index % 2 == 0 else line[::-1]
