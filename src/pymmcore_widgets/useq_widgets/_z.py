from __future__ import annotations

import enum
from typing import Final, Literal

import useq
from qtpy.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from qtpy.QtGui import QColor, QPainter, QPaintEvent, QPen
from qtpy.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
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

from pymmcore_widgets._util import SeparatorWidget, disable_wheel_scroll


class Mode(enum.Enum):
    """Recognized ZPlanWidget modes."""

    TOP_BOTTOM = "top_bottom"
    RANGE_AROUND = "range_around"
    ABOVE_BELOW = "above_below"


ROW_STEPS = 0
ROW_DIRECTION = 1
ROW_FIRST_BOUND = 3
ROW_SECOND_BOUND = 4

UM = "µm"  # MICRO SIGN


def _format_z(value: float) -> str:
    """Format a Z value, without a minus sign on a value that rounds to zero."""
    return f"{round(value, 3) + 0.0:.3f}"


class ZStackViz(QWidget):
    """A compact, palette-aware graphic of a Z stack."""

    _WIDTH = 96
    # the drawn stack reaches this far either side of the axis
    _STACK_HALF_WIDTH = 30

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._top = 0.0
        self._bottom = 0.0
        self._center_fraction = 0.5
        self._slices = 1
        self.setFixedWidth(self._WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(self._WIDTH, 230)

    def setPlan(
        self, *, top: float, bottom: float, center_fraction: float, slices: int
    ) -> None:
        """Show a stack from `bottom` to `top`, its reference at `center_fraction`.

        `center_fraction` is from the top (0) to the bottom (1) of the stack.
        """
        self._top = top
        self._bottom = bottom
        self._center_fraction = max(0.0, min(1.0, center_fraction))
        self._slices = max(1, slices)
        self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        """Paint the axis, the stack planes, and the reference plane."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.contentsRect().adjusted(8, 0, -8, 0)
        label_height = painter.fontMetrics().height()
        axis_top = rect.top() + label_height + 4
        axis_bottom = rect.bottom() - label_height - 4
        if axis_bottom <= axis_top:
            return

        palette = self.palette()
        accent = palette.color(palette.ColorRole.Highlight)
        accent_fill = QColor(accent)
        accent_fill.setAlpha(28)
        accent_line = QColor(accent)
        accent_line.setAlpha(115)

        axis_x = rect.center().x()
        track_height = axis_bottom - axis_top

        painter.setPen(QPen(palette.color(palette.ColorRole.Mid), 2))
        painter.drawLine(QPointF(axis_x, axis_top), QPointF(axis_x, axis_bottom))
        for y in (axis_top, axis_bottom):
            painter.drawLine(QPointF(axis_x - 5, y), QPointF(axis_x + 5, y))

        stack = QRectF(
            axis_x - self._STACK_HALF_WIDTH,
            axis_top + 4,
            2 * self._STACK_HALF_WIDTH,
            max(1, track_height - 8),
        )
        painter.setPen(QPen(accent_line, 1))
        painter.setBrush(accent_fill)
        painter.drawRoundedRect(stack, 3, 3)

        # one line per plane, at most 50 of them
        lines = min(self._slices, 50)
        for i in range(lines):
            y = stack.top() + (i / (lines - 1) if lines > 1 else 0.5) * stack.height()
            painter.drawLine(
                QPointF(stack.left() + 3, y), QPointF(stack.right() - 3, y)
            )

        marker_y = axis_top + self._center_fraction * track_height
        pen = QPen(accent.darker(125), 4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(axis_x - 12, marker_y), QPointF(axis_x + 12, marker_y))

        painter.setPen(palette.color(palette.ColorRole.Text))
        align = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        width = rect.width()
        top_label = QRectF(rect.left(), rect.top(), width, label_height)
        painter.drawText(top_label, align, _format_z(self._top))
        bottom_label = QRectF(rect.left(), axis_bottom + 3, width, label_height)
        painter.drawText(bottom_label, align, _format_z(self._bottom))


class ZPlanWidget(QWidget):
    """Widget to edit a [useq.ZPlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#z-plans)."""

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

        # to store a "suggested" step size
        self._suggested: float | None = None

        self._mode: Mode = Mode.TOP_BOTTOM

        # #################### Mode Buttons ####################

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

        self._mode_buttons: dict[Mode, QRadioButton] = {
            Mode.RANGE_AROUND: self._btn_range,
            Mode.ABOVE_BELOW: self._button_above_below,
            Mode.TOP_BOTTOM: self._btn_top_bot,
        }
        self._mode_tooltips = {m: b.toolTip() for m, b in self._mode_buttons.items()}

        self._mode_btn_group = QButtonGroup(self)
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(10)
        mode_layout.addWidget(QLabel("Mode:"))
        for button in self._mode_buttons.values():
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            self._mode_btn_group.addButton(button)
            mode_layout.addWidget(button)
            mode_layout.addSpacing(15)
        mode_layout.addStretch()
        self._mode_btn_group.buttonToggled.connect(self._on_mode_toggled)

        # #################### Value Widgets ####################

        self.top = self._new_double_spinbox(-10_000, 10_000, 0.1)
        self.bottom = self._new_double_spinbox(-10_000, 10_000, 0.1)

        self.step = self._new_double_spinbox(0, 1000, 0.125)
        self.step.setSpecialValueText("N/A")
        self.step.setValue(1)

        self.steps = QSpinBox()
        self.steps.setRange(0, 1000)
        self.steps.setSpecialValueText("N/A")
        disable_wheel_scroll(self.steps)

        self.range = self._new_double_spinbox(0, 10_000, 0.5)
        self.above = self._new_double_spinbox(0, 10_000, 0.5)
        self.above.setPrefix("+")
        self.below = self._new_double_spinbox(0, 10_000, 0.5)
        self.below.setPrefix("-")

        self._direction = QComboBox()
        self._direction.addItem("Bottom → Top", True)
        self._direction.addItem("Top → Bottom", False)
        disable_wheel_scroll(self._direction)

        # #################### Other Widgets ####################

        self._use_suggested_btn = QPushButton()
        self._use_suggested_btn.setIcon(QIconifyIcon("mdi:arrow-left-thick"))
        self._use_suggested_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._use_suggested_btn.clicked.connect(self.useSuggestedStep)
        self._use_suggested_btn.hide()

        self._range_readout = QLabel()
        self._viz = ZStackViz()

        # #################### connections ####################

        self.top.valueChanged.connect(self._on_change)
        self.bottom.valueChanged.connect(self._on_change)
        self.step.valueChanged.connect(self._on_change)
        self.range.valueChanged.connect(self._on_change)
        self.above.valueChanged.connect(self._on_change)
        self.below.valueChanged.connect(self._on_change)
        self._direction.currentIndexChanged.connect(self._on_change)

        self.range.valueChanged.connect(self._on_range_changed)
        self.steps.valueChanged.connect(self._on_steps_changed)

        # #################### Grid ####################

        # a label column, a field column, and a column for what goes with them
        self._form_labels: list[QLabel] = []
        self._grid_layout = grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        self._step_label = self._form_label("Step size")
        grid.addWidget(self._step_label, ROW_STEPS, 0)
        grid.addWidget(self.step, ROW_STEPS, 1)
        step_extras = QHBoxLayout()
        step_extras.setSpacing(10)
        step_extras.addWidget(self._use_suggested_btn)
        step_extras.addWidget(QLabel("Slices"))
        step_extras.addWidget(self.steps)
        step_extras.addStretch()
        grid.addLayout(step_extras, ROW_STEPS, 2)

        grid.addWidget(self._form_label("Direction"), ROW_DIRECTION, 0)
        grid.addWidget(self._direction, ROW_DIRECTION, 1)
        grid.addWidget(self._range_readout, ROW_DIRECTION, 2)

        grid.addWidget(SeparatorWidget(), 2, 0, 1, 3)

        # one or two bound rows, depending on the mode (see `setMode`)
        self._range_widgets = self._add_bound_row(
            ROW_FIRST_BOUND, "Range", self.range, row_span=2
        )
        self._top_widgets = self._add_bound_row(ROW_FIRST_BOUND, "Top", self.top)
        self._bottom_widgets = self._add_bound_row(
            ROW_SECOND_BOUND, "Bottom", self.bottom
        )
        self._above_widgets = self._add_bound_row(ROW_FIRST_BOUND, "Above", self.above)
        self._below_widgets = self._add_bound_row(ROW_SECOND_BOUND, "Below", self.below)
        # Keep both bound rows in the layout when their controls are hidden, so
        # that the rows below do not move when the mode changes.
        for row in (ROW_FIRST_BOUND, ROW_SECOND_BOUND):
            placeholder = QWidget()
            placeholder.setFixedSize(1, self.top.sizeHint().height())
            grid.addWidget(placeholder, row, 0)

        label_width = self._step_label.sizeHint().width()
        for label in self._form_labels:
            label.setFixedWidth(label_width)
        # the fields take any spare width, so they fill the box
        grid.setColumnStretch(1, 1)
        # The last column keeps room for what may appear in it (the suggested
        # step, and a range up to 999.999 µm), so the box does not resize.
        suggested_width = (
            step_extras.sizeHint().width()
            + self._use_suggested_btn.sizeHint().width()
            + step_extras.spacing()
        )
        readout_metrics = self._range_readout.fontMetrics()
        readout_width = readout_metrics.horizontalAdvance(f"Range: 999.999 {UM}")
        grid.setColumnMinimumWidth(2, max(suggested_width, readout_width))
        # surplus height goes below the rows, not between them
        grid.setRowStretch(ROW_SECOND_BOUND + 1, 1)

        self._controls_widget = QWidget()
        self._controls_widget.setLayout(grid)
        # all the rows are still shown: the tallest the controls can be
        self._controls_widget.setMinimumHeight(
            self._controls_widget.sizeHint().height()
        )
        self._controls_box = QGroupBox()
        QVBoxLayout(self._controls_box).addWidget(self._controls_widget)

        # a style or font change (e.g. a zoom) sizes the graphic again, once done
        self._resync_timer = QTimer(self)
        self._resync_timer.setSingleShot(True)
        self._resync_timer.setInterval(0)
        self._resync_timer.timeout.connect(self._sync_visual_height)

        # #################### Layout ####################

        # Mode: (x) Symmetric  ( ) Asymmetric  ( ) Top & Bottom
        # ------------------------------------------------------
        #  graphic |  [ Step size / Direction / bound fields ]

        body_layout = QHBoxLayout()
        body_layout.setSpacing(8)
        body_layout.addWidget(self._viz, 0, Qt.AlignmentFlag.AlignTop)
        body_layout.addWidget(SeparatorWidget(orientation=Qt.Orientation.Vertical))
        body_layout.addWidget(self._controls_box, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(mode_layout)
        layout.addWidget(SeparatorWidget())
        layout.addLayout(body_layout)

        # #################### Defaults ####################

        self.setMode(Mode.TOP_BOTTOM)

    def _new_double_spinbox(
        self, minimum: float, maximum: float, step: float
    ) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setRange(minimum, maximum)
        spinbox.setSingleStep(step)
        spinbox.setDecimals(3)
        spinbox.setSuffix(f" {UM}")
        disable_wheel_scroll(spinbox)
        return spinbox

    def _form_label(self, text: str) -> QLabel:
        label = QLabel(text)
        self._form_labels.append(label)
        return label

    def _add_bound_row(
        self, row: int, text: str, field: QDoubleSpinBox, row_span: int = 1
    ) -> list[QWidget]:
        label = self._form_label(text)
        self._grid_layout.addWidget(label, row, 0, row_span, 1)
        self._grid_layout.addWidget(field, row, 1, row_span, 1)
        return [label, field]

    # ------------------------- Public API -------------------------

    def setMode(
        self,
        mode: Mode | Literal["top_bottom", "range_around", "above_below"],
    ) -> None:
        """Set the current mode.

        One of "top_bottom", "range_around", or "above_below".
        """
        self._mode = mode = Mode(mode)
        with signals_blocked(self._mode_btn_group):
            self._mode_buttons[mode].setChecked(True)

        widgets = {
            Mode.RANGE_AROUND: self._range_widgets,
            Mode.TOP_BOTTOM: self._top_widgets + self._bottom_widgets,
            Mode.ABOVE_BELOW: self._above_widgets + self._below_widgets,
        }
        for widget_mode, mode_widgets in widgets.items():
            for widget in mode_widgets:
                widget.setVisible(widget_mode is mode)

        self._sync_visual_height()
        self._on_change()

    def setModeBlocked(self, mode: Mode, reason: str) -> None:
        """Keep `mode` from being selected for `reason`, or allow it with `""`.

        For a setting elsewhere that rules the mode out: the mode's button is
        disabled, with `reason` as its tooltip. It does not change the current mode.
        """
        button = self._mode_buttons[mode]
        button.setEnabled(not reason)
        button.setToolTip(reason or self._mode_tooltips[mode])

    def isModeBlocked(self, mode: Mode) -> bool:
        """Whether `mode` cannot be selected now (see `setModeBlocked`)."""
        return not self._mode_buttons[mode].isEnabled()

    def mode(self) -> Mode:
        """Return the current mode.

        One of "top_bottom", "range_around", or "above_below".
        """
        return self._mode

    def setSuggestedStep(self, value: float | None) -> None:
        """Set the suggested z step size and update the button text."""
        self._suggested = value
        if value:
            self._use_suggested_btn.setText(f"{value} {UM}")
            self._use_suggested_btn.show()
        else:
            self._use_suggested_btn.setText("")
            self._use_suggested_btn.hide()

    def suggestedStep(self) -> float | None:
        """Return suggested z step size."""
        return float(self._suggested) if self._suggested else None

    def useSuggestedStep(self) -> None:
        """Apply the suggested z step size to the step field."""
        if self._suggested:
            self.step.setValue(float(self._suggested))

    def value(self) -> useq.ZAboveBelow | useq.ZRangeAround | useq.ZTopBottom | None:
        """Return the current value of the widget as a [useq.ZPlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#z-plans).

        Returns
        -------
        useq.ZAboveBelow | useq.ZRangeAround | useq.ZTopBottom | None
        The current [useq.ZPlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#z-plans)
        value of the widget.
        """
        if self.step.value() == 0:
            return None

        common = {"step": self.step.value(), "go_up": self.isGoUp()}
        if self._mode is Mode.TOP_BOTTOM:
            return useq.ZTopBottom(
                top=round(self.top.value(), 4),
                bottom=round(self.bottom.value(), 4),
                **common,
            )
        elif self._mode is Mode.RANGE_AROUND:
            return useq.ZRangeAround(range=round(self.range.value(), 4), **common)
        elif self._mode is Mode.ABOVE_BELOW:
            return useq.ZAboveBelow(
                above=round(self.above.value(), 4),
                below=round(self.below.value(), 4),
                **common,
            )

    def setValue(
        self, value: useq.ZAboveBelow | useq.ZRangeAround | useq.ZTopBottom
    ) -> None:
        """Set the current value of the widget from a [useq.ZPlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#z-plans).

        Parameters
        ----------
        value : useq.ZAboveBelow | useq.ZRangeAround | useq.ZTopBottom
            The
            [useq.ZPlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#z-plans)
            to set.
        """
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
        """Return True if the acquisition direction is up (bottom to top)."""
        return bool(self._direction.currentData())

    def setGoUp(self, up: bool) -> None:
        """Set the acquisition direction."""
        self._direction.setCurrentIndex(0 if up else 1)

    def currentZRange(self) -> float:
        """Return the current Z range in microns."""
        if self._mode is Mode.TOP_BOTTOM:
            return abs(self.top.value() - self.bottom.value())  # type: ignore
        elif self._mode is Mode.RANGE_AROUND:
            return self.range.value()  # type: ignore
        else:  # _Mode.ABOVE_BELOW
            return self.above.value() + self.below.value()  # type: ignore

    Mode: Final[type[Mode]] = Mode

    def changeEvent(self, a0: QEvent | None) -> None:
        super().changeEvent(a0)
        if a0 is not None and a0.type() in (
            QEvent.Type.StyleChange,
            QEvent.Type.FontChange,
        ):
            self._resync_timer.start()

    # ------------------------- Private API -------------------------

    def _on_mode_toggled(self, button: QRadioButton, checked: bool) -> None:
        if checked:
            self.setMode(next(m for m, b in self._mode_buttons.items() if b is button))

    def _sync_visual_height(self) -> None:
        """Make the graphic as tall as the box beside it, whatever the mode."""
        self._grid_layout.activate()
        height = max(
            self._controls_widget.minimumHeight(),
            self._controls_widget.sizeHint().height(),
        )
        self._controls_widget.setMinimumHeight(height)
        self._viz.setFixedHeight(self._controls_box.sizeHint().height())

    def _on_change(self, update_steps: bool = True) -> None:
        """Called when any of the widgets change."""
        val = self.value()

        z_range = self.currentZRange()
        self._range_readout.setText(f"Range: {z_range:.3f} {UM}")
        # update steps readout
        if update_steps:
            with signals_blocked(self.steps):
                self.steps.setValue(0 if val is None else val.num_positions())
        self._update_viz(z_range)

        self.valueChanged.emit(val)

    def _update_viz(self, z_range: float) -> None:
        if self._mode is Mode.TOP_BOTTOM:
            top, bottom = self.top.value(), self.bottom.value()
        elif self._mode is Mode.RANGE_AROUND:
            top, bottom = z_range / 2, -z_range / 2
        else:
            top, bottom = self.above.value(), -self.below.value()
        # the reference (0) is in the middle of an absolute plan
        center = top / z_range if z_range and self._mode is Mode.ABOVE_BELOW else 0.5
        self._viz.setPlan(
            top=top, bottom=bottom, center_fraction=center, slices=self.steps.value()
        )

    def _on_steps_changed(self, steps: int) -> None:
        if steps:
            with signals_blocked(self.step):
                self.step.setValue(self.currentZRange() / steps)
        self._on_change(update_steps=False)

    def _on_range_changed(self, z_range: float) -> None:
        half_range = f"{z_range / 2:.4f}".rstrip("0").rstrip(".")
        self.range.setSuffix(f" {UM} (±{half_range})")
