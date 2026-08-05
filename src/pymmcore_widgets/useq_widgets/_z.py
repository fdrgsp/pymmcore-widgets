from __future__ import annotations

import enum
from typing import Final, Literal

import useq
from qtpy.QtCore import QPointF, QRectF, QSize, Qt, Signal
from qtpy.QtGui import QColor, QPainter, QPen
from qtpy.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon
from superqt.utils import signals_blocked

from pymmcore_widgets._util import SeparatorWidget


class Mode(enum.Enum):
    """Recognized ZPlanWidget modes."""

    TOP_BOTTOM = "top_bottom"
    RANGE_AROUND = "range_around"
    ABOVE_BELOW = "above_below"


ROW_STEPS = 0
ROW_DIRECTION = 1
ROW_FIRST_BOUND = 3
ROW_SECOND_BOUND = 4

UM = "\u00b5m"  # MICRO SIGN


class ZStackViz(QWidget):
    """A compact, palette-aware visualization of a Z stack."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._top = 0.0
        self._bottom = 0.0
        self._center_fraction = 0.5
        self._slices = 1
        self.setFixedWidth(130)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        """Return a compact preferred size."""
        return QSize(130, 230)

    def setPlan(
        self,
        *,
        top: float,
        bottom: float,
        center_fraction: float,
        slices: int,
    ) -> None:
        """Update the values displayed by the visualization."""
        self._top = top
        self._bottom = bottom
        self._center_fraction = max(0.0, min(1.0, center_fraction))
        self._slices = max(1, slices)
        self.update()

    def paintEvent(self, event: object) -> None:
        """Paint the axis, stack planes, and reference plane."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.contentsRect().adjusted(8, 0, -8, 0)
        metrics = painter.fontMetrics()
        label_height = metrics.height()
        axis_top = rect.top() + label_height + 4
        axis_bottom = rect.bottom() - label_height - 4
        if axis_bottom <= axis_top:
            return

        palette = self.palette()
        axis_color = palette.color(palette.ColorRole.Mid)
        text_color = palette.color(palette.ColorRole.Text)
        accent = palette.color(palette.ColorRole.Highlight)
        accent_fill = QColor(accent)
        accent_fill.setAlpha(28)
        accent_line = QColor(accent)
        accent_line.setAlpha(115)

        axis_x = rect.center().x()
        bar_left = axis_x - 30
        bar_right = axis_x + 30
        track_height = axis_bottom - axis_top

        painter.setPen(QPen(axis_color, 2))
        painter.drawLine(QPointF(axis_x, axis_top), QPointF(axis_x, axis_bottom))
        painter.drawLine(QPointF(axis_x - 5, axis_top), QPointF(axis_x + 5, axis_top))
        painter.drawLine(
            QPointF(axis_x - 5, axis_bottom), QPointF(axis_x + 5, axis_bottom)
        )

        stack_rect = QRectF(
            bar_left,
            axis_top + 4,
            bar_right - bar_left,
            max(1, track_height - 8),
        )
        painter.setPen(QPen(accent_line, 1))
        painter.setBrush(accent_fill)
        painter.drawRoundedRect(stack_rect, 3, 3)

        line_count = min(self._slices, 50)
        fractions: tuple[float, ...]
        if line_count == 1:
            fractions = (0.5,)
        else:
            fractions = tuple(i / (line_count - 1) for i in range(line_count))
        for fraction in fractions:
            y = stack_rect.top() + fraction * stack_rect.height()
            painter.drawLine(
                QPointF(stack_rect.left() + 3, y),
                QPointF(stack_rect.right() - 3, y),
            )

        marker_y = axis_top + self._center_fraction * track_height
        marker = QColor(accent).darker(125)
        painter.setPen(QPen(marker, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(axis_x - 12, marker_y), QPointF(axis_x + 12, marker_y))

        painter.setPen(text_color)
        painter.drawText(
            QRectF(rect.left(), rect.top(), rect.width(), label_height),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            f"{self._top:.3f}",
        )
        painter.drawText(
            QRectF(
                rect.left(),
                axis_bottom + 3,
                rect.width(),
                label_height,
            ),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            f"{self._bottom:.3f}",
        )


class ZPlanWidget(QWidget):
    """Widget to edit a useq Z plan."""

    valueChanged = Signal(object)

    # public widgets
    top: QDoubleSpinBox
    bottom: QDoubleSpinBox
    step: QDoubleSpinBox
    steps: QSpinBox
    range: QDoubleSpinBox
    above: QDoubleSpinBox
    below: QDoubleSpinBox

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._suggested: float | None = None
        self._mode = Mode.TOP_BOTTOM

        # #################### Mode selector ####################

        self._btn_range = QRadioButton("Symmetric")
        self._btn_range.setIcon(QIconifyIcon("mdi:arrow-split-horizontal"))
        self._btn_range.setToolTip("Range symmetric around reference.")
        self._button_above_below = QRadioButton("Asymmetric")
        self._button_above_below.setIcon(QIconifyIcon("mdi:arrow-expand-up"))
        self._button_above_below.setToolTip(
            "Range asymmetrically above/below reference."
        )
        self._btn_top_bot = QRadioButton("Top && Bottom")
        self._btn_top_bot.setIcon(QIconifyIcon("mdi:arrow-expand-vertical"))
        self._btn_top_bot.setToolTip("Mark top and bottom.")
        self._mode_btn_group = QButtonGroup(self)
        self._mode_btn_group.addButton(self._btn_range)
        self._mode_btn_group.addButton(self._button_above_below)
        self._mode_btn_group.addButton(self._btn_top_bot)
        self._mode_btn_group.buttonToggled.connect(self._on_mode_toggled)
        for button in self._mode_btn_group.buttons():
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setSizePolicy(
                QSizePolicy.Policy.Maximum,
                QSizePolicy.Policy.Fixed,
            )

        mode_layout = QHBoxLayout()
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.addWidget(QLabel("Mode:"))
        mode_layout.addSpacing(24)
        mode_layout.addWidget(self._btn_range)
        mode_layout.addSpacing(32)
        mode_layout.addWidget(self._button_above_below)
        mode_layout.addSpacing(32)
        mode_layout.addWidget(self._btn_top_bot)
        mode_layout.addStretch()

        # #################### Value widgets ####################

        self.top = self._new_double_spinbox(-10_000, 10_000, 0.1)
        self.bottom = self._new_double_spinbox(-10_000, 10_000, 0.1)

        self.step = self._new_double_spinbox(0, 1000, 0.125)
        self.step.setSpecialValueText("N/A")
        self.step.setValue(1)

        self.steps = QSpinBox()
        self.steps.setRange(0, 1000)
        self.steps.setSpecialValueText("N/A")
        self.steps.setValue(0)

        self.range = self._new_double_spinbox(0, 10_000, 0.5)
        self._range_div2_lbl = QLabel()

        self.above = self._new_double_spinbox(0, 10_000, 0.5)
        self.above.setPrefix("+")
        self.below = self._new_double_spinbox(0, 10_000, 0.5)
        self.below.setPrefix("-")

        self._direction = QComboBox()
        self._direction.addItem("Bottom \u2192 Top", True)
        self._direction.addItem("Top \u2192 Bottom", False)

        self._use_suggested_btn = QPushButton()
        self._use_suggested_btn.setIcon(QIconifyIcon("mdi:arrow-left-thick"))
        self._use_suggested_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._use_suggested_btn.clicked.connect(self.useSuggestedStep)
        self._use_suggested_btn.hide()

        self._summary_label = QLabel()
        self._summary_label.setWordWrap(False)
        self._viz = ZStackViz()

        # #################### Connections ####################

        self.top.valueChanged.connect(self._on_change)
        self.bottom.valueChanged.connect(self._on_change)
        self.step.valueChanged.connect(self._on_change)
        self.range.valueChanged.connect(self._on_change)
        self.above.valueChanged.connect(self._on_change)
        self.below.valueChanged.connect(self._on_change)
        self._direction.currentIndexChanged.connect(self._on_change)
        self.range.valueChanged.connect(self._on_range_changed)
        self.steps.valueChanged.connect(self._on_steps_changed)

        # #################### Aligned grid ####################

        self._form_labels: list[QLabel] = []
        self._grid_layout = grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        self._range_widgets = self._add_bound_row(
            ROW_FIRST_BOUND,
            "Range",
            self.range,
            self._range_div2_lbl,
            row_span=2,
        )
        self._top_widgets = self._add_bound_row(ROW_FIRST_BOUND, "Top", self.top)
        self._bottom_widgets = self._add_bound_row(
            ROW_SECOND_BOUND, "Bottom", self.bottom
        )
        self._below_widgets = self._add_bound_row(ROW_FIRST_BOUND, "Below", self.below)
        self._above_widgets = self._add_bound_row(ROW_SECOND_BOUND, "Above", self.above)

        # Keep both bound rows participating in the layout when their controls
        # are hidden.  Otherwise QGridLayout removes the inter-row spacing and
        # the lower controls jump when switching modes.
        bound_height = self.top.sizeHint().height()
        self._bound_row_placeholders: list[QWidget] = []
        for row in (ROW_FIRST_BOUND, ROW_SECOND_BOUND):
            placeholder = QWidget()
            placeholder.setFixedSize(1, bound_height)
            self._bound_row_placeholders.append(placeholder)
            grid.addWidget(placeholder, row, 5)

        self._bounds_separator = SeparatorWidget()
        grid.addWidget(self._bounds_separator, 2, 0, 1, 6)

        self._step_label = self._form_label("Step size")
        grid.addWidget(self._step_label, ROW_STEPS, 0)
        grid.addWidget(self.step, ROW_STEPS, 1)
        step_extras = QHBoxLayout()
        step_extras.setContentsMargins(0, 0, 0, 0)
        step_extras.setSpacing(10)
        step_extras.addWidget(self._use_suggested_btn)
        step_extras.addWidget(QLabel("Slices"))
        step_extras.addWidget(self.steps)
        step_extras.addStretch()
        grid.addLayout(step_extras, ROW_STEPS, 2, 1, 4)

        self._direction_label = self._form_label("Direction")
        grid.addWidget(self._direction_label, ROW_DIRECTION, 0)
        grid.addWidget(self._direction, ROW_DIRECTION, 1)
        grid.addWidget(self._summary_label, ROW_DIRECTION, 2, 1, 4)

        label_width = self._step_label.sizeHint().width()
        for label in self._form_labels:
            label.setFixedWidth(label_width)
        grid.setColumnMinimumWidth(0, label_width)
        grid.setColumnMinimumWidth(1, 175)
        grid.setColumnMinimumWidth(2, 150)
        grid.setColumnStretch(3, 1)
        grid.setRowMinimumHeight(ROW_FIRST_BOUND, self.bottom.sizeHint().height())
        grid.setRowMinimumHeight(ROW_SECOND_BOUND, self.top.sizeHint().height())
        # When mode-specific controls are hidden, keep the height reserved for
        # a stable widget but send any surplus to an empty row at the bottom.
        # Otherwise QGridLayout distributes it between the visible rows and
        # makes their spacing vary from one Z mode to another.
        grid.setRowStretch(ROW_SECOND_BOUND + 1, 1)

        self._controls_widget = QWidget()
        self._controls_widget.setLayout(grid)

        # All mode-specific controls are still visible here, so this captures the
        # tallest possible base layout before setMode() begins hiding rows.
        self._reserved_controls_height = self._controls_widget.sizeHint().height()
        self._controls_widget.setMinimumHeight(self._reserved_controls_height)

        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        body_layout.addWidget(self._viz, 0, Qt.AlignmentFlag.AlignTop)
        body_layout.addWidget(self._controls_widget, 1, Qt.AlignmentFlag.AlignTop)

        layout = QVBoxLayout(self)
        layout.addLayout(mode_layout)
        layout.addWidget(SeparatorWidget())
        layout.addLayout(body_layout)

        # #################### Defaults ####################

        self.setMode(Mode.RANGE_AROUND)

    def _new_double_spinbox(
        self, minimum: float, maximum: float, step: float
    ) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setRange(minimum, maximum)
        spinbox.setSingleStep(step)
        spinbox.setDecimals(3)
        spinbox.setSuffix(f" {UM}")
        return spinbox

    def _form_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._form_labels.append(label)
        return label

    def _add_bound_row(
        self,
        row: int,
        text: str,
        field: QDoubleSpinBox,
        trailing: QWidget | None = None,
        row_span: int = 1,
    ) -> list[QWidget]:
        label = self._form_label(text)
        alignment = Qt.AlignmentFlag.AlignVCenter
        self._grid_layout.addWidget(label, row, 0, row_span, 1, alignment)
        self._grid_layout.addWidget(field, row, 1, row_span, 1, alignment)
        widgets: list[QWidget] = [label, field]
        if trailing is not None:
            self._grid_layout.addWidget(trailing, row, 2, row_span, 2, alignment)
            widgets.append(trailing)
        return widgets

    # ------------------------- Public API -------------------------

    def setMode(
        self,
        mode: Mode | Literal["top_bottom", "range_around", "above_below"],
    ) -> None:
        """Set the current Z-plan definition mode."""
        if isinstance(mode, str):
            mode = Mode(mode)
        self._mode = mode

        mode_buttons = {
            Mode.RANGE_AROUND: self._btn_range,
            Mode.ABOVE_BELOW: self._button_above_below,
            Mode.TOP_BOTTOM: self._btn_top_bot,
        }
        with signals_blocked(self._mode_btn_group):
            mode_buttons[mode].setChecked(True)

        groups = {
            Mode.RANGE_AROUND: self._range_widgets,
            Mode.TOP_BOTTOM: self._bottom_widgets + self._top_widgets,
            Mode.ABOVE_BELOW: self._below_widgets + self._above_widgets,
        }
        for group_mode, widgets in groups.items():
            for widget in widgets:
                widget.setVisible(group_mode is mode)

        self._sync_visual_height()
        self._on_change()

    def mode(self) -> Mode:
        """Return the current Z-plan definition mode."""
        return self._mode

    def setSuggestedStep(self, value: float | None) -> None:
        """Set the suggested Z step size and update its optional action."""
        self._suggested = value
        if value is not None and value > 0:
            self._use_suggested_btn.setText(f"{value:g} {UM}")
            self._use_suggested_btn.show()
        else:
            self._use_suggested_btn.setText("")
            self._use_suggested_btn.hide()

    def suggestedStep(self) -> float | None:
        """Return the suggested Z step size."""
        return float(self._suggested) if self._suggested is not None else None

    def useSuggestedStep(self) -> None:
        """Apply the suggested Z step size to the step field."""
        if self._suggested is not None and self._suggested > 0:
            self.step.setValue(float(self._suggested))

    def value(self) -> useq.ZAboveBelow | useq.ZRangeAround | useq.ZTopBottom | None:
        """Return the current useq Z plan, or ``None`` when disabled."""
        if self.step.value() == 0:
            return None

        common = {"step": self.step.value(), "go_up": self.isGoUp()}
        if self._mode is Mode.TOP_BOTTOM:
            return useq.ZTopBottom(
                top=round(self.top.value(), 4),
                bottom=round(self.bottom.value(), 4),
                **common,
            )
        if self._mode is Mode.RANGE_AROUND:
            return useq.ZRangeAround(range=round(self.range.value(), 4), **common)
        return useq.ZAboveBelow(
            above=round(self.above.value(), 4),
            below=round(self.below.value(), 4),
            **common,
        )

    def setValue(
        self, value: useq.ZAboveBelow | useq.ZRangeAround | useq.ZTopBottom
    ) -> None:
        """Set the widget from a useq Z plan."""
        if isinstance(value, useq.ZTopBottom):
            self.top.setValue(value.top)
            self.bottom.setValue(value.bottom)
            self.setMode(Mode.TOP_BOTTOM)
        elif isinstance(value, useq.ZRangeAround):
            self.range.setValue(value.range)
            self.setMode(Mode.RANGE_AROUND)
        elif isinstance(value, useq.ZAboveBelow):
            self.above.setValue(value.above)
            self.below.setValue(value.below)
            self.setMode(Mode.ABOVE_BELOW)
        else:
            raise TypeError(f"Invalid value type: {type(value)}")

        self.step.setValue(value.step)
        self.setGoUp(value.go_up)

    def isGoUp(self) -> bool:
        """Return whether acquisition proceeds from bottom to top."""
        return bool(self._direction.currentData())

    def setGoUp(self, up: bool) -> None:
        """Set the acquisition direction."""
        self._direction.setCurrentIndex(0 if up else 1)

    def currentZRange(self) -> float:
        """Return the current Z range in microns."""
        if self._mode is Mode.TOP_BOTTOM:
            return float(abs(self.top.value() - self.bottom.value()))
        if self._mode is Mode.RANGE_AROUND:
            return float(self.range.value())
        return float(self.above.value() + self.below.value())

    Mode: Final[type[Mode]] = Mode

    # ------------------------- Private API -------------------------

    def _on_mode_toggled(self, button: QRadioButton, checked: bool) -> None:
        if not checked:
            return
        modes = {
            self._btn_range: Mode.RANGE_AROUND,
            self._button_above_below: Mode.ABOVE_BELOW,
            self._btn_top_bot: Mode.TOP_BOTTOM,
        }
        self.setMode(modes[button])

    def _sync_visual_height(self) -> None:
        self._grid_layout.activate()
        height = max(
            self._reserved_controls_height,
            self._controls_widget.sizeHint().height(),
        )
        self._reserved_controls_height = height
        self._controls_widget.setMinimumHeight(height)
        self._viz.setFixedHeight(height)

    def _on_change(self, update_steps: bool = True) -> None:
        value = self.value()
        if update_steps:
            with signals_blocked(self.steps):
                self.steps.setValue(0 if value is None else value.num_positions())

        z_range = self.currentZRange()
        slices = self.steps.value()
        self._summary_label.setText(f"Range: {z_range:.3f} {UM}")
        self._update_visualization(z_range, max(1, slices))
        self.valueChanged.emit(value)

    def _update_visualization(self, z_range: float, slices: int) -> None:
        if self._mode is Mode.TOP_BOTTOM:
            top = self.top.value()
            bottom = self.bottom.value()
            center_fraction = 0.5
        elif self._mode is Mode.RANGE_AROUND:
            top = z_range / 2
            bottom = -z_range / 2
            center_fraction = 0.5
        else:
            top = self.above.value()
            bottom = -self.below.value()
            center_fraction = top / z_range if z_range else 0.5
        self._viz.setPlan(
            top=top,
            bottom=bottom,
            center_fraction=center_fraction,
            slices=slices,
        )

    def _on_steps_changed(self, steps: int) -> None:
        if steps:
            with signals_blocked(self.step):
                self.step.setValue(self.currentZRange() / steps)
        self._on_change(update_steps=False)

    def _on_range_changed(self, z_range: float) -> None:
        self._range_div2_lbl.setText(f"\u00b1 {z_range / 2:.2f} {UM} from center")
