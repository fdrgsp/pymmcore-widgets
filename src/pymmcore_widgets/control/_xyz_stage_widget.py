from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import Qt, QTimerEvent, Slot
from qtpy.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pymmcore_widgets._util import RightClickMenu

from ._q_stage_controller import QStageMoveAccumulator
from ._stage_widget import (
    HaltButton,
    MoveStageButton,
    MoveStageSpinBox,
    StageMovementButtons,
    _WheelPollStageWidget,
)

if TYPE_CHECKING:
    from qtpy.QtGui import QWheelEvent


class XYZStageWidget(_WheelPollStageWidget):
    """A widget to control the Core's default XY and Z (focus) stage devices.

    Unlike [`StageWidget`][pymmcore_widgets.StageWidget], this widget does not
    target a fixed device. It always follows whichever devices are currently
    set as the Core's default XY stage (`CMMCorePlus.getXYStageDevice`) and
    focus device (`CMMCorePlus.getFocusDevice`), updating automatically if
    those defaults change. If a default device is not set, the corresponding
    movement controls are replaced with a placeholder message and the related
    fields are disabled.

    Parameters
    ----------
    absolute_positioning: bool
        If True, the position displays can be edited to set absolute positions.
        If False, the position displays cannot be edited.
    parent : QWidget | None
        Optional parent widget.
    mmcore : CMMCorePlus | None
        Optional [`pymmcore_plus.CMMCorePlus`][] micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new)
        [`CMMCorePlus.instance`][pymmcore_plus.core._mmcore_plus.CMMCorePlus.instance].
    """

    def __init__(
        self,
        *,
        absolute_positioning: bool = False,
        parent: QWidget | None = None,
        mmcore: CMMCorePlus | None = None,
    ) -> None:
        super().__init__(parent=parent)

        self._mmc = mmcore or CMMCorePlus.instance()
        self._poll_timer_id: int | None = None

        # sentinel `None` (rather than "") ensures the first `_bind_*_device`
        # call always runs its body, even if the core has no default device
        self._xy_device: str | None = None
        self._z_device: str | None = None
        self._xy_controller: QStageMoveAccumulator | None = None
        self._z_controller: QStageMoveAccumulator | None = None

        # WIDGETS ------------------------------------------------

        self._xy_move_btns = StageMovementButtons(show_x=True)
        # Apply the independent X/Y steps in _on_xy_move_requested.
        self._xy_move_btns.step_size.setValue(1.0)
        self._xy_halt = HaltButton("", self._mmc, self)
        xy_grid = cast("QGridLayout", self._xy_move_btns.layout())
        xy_grid.addWidget(self._xy_halt, 1, 1, Qt.AlignmentFlag.AlignCenter)

        self._x_step = MoveStageSpinBox(label="X step", minimum=0)
        self._x_step.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self._x_step.setValue(10)
        self._y_step = MoveStageSpinBox(label="Y step", minimum=0)
        self._y_step.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self._y_step.setValue(10)
        self._x_step.valueChanged.connect(self._update_xy_tooltips)
        self._y_step.valueChanged.connect(self._update_xy_tooltips)

        self._z_move_btns = StageMovementButtons(show_x=False)
        self._z_step = self._z_move_btns.step_size
        self._z_step.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self._z_halt = HaltButton("", self._mmc, self)
        z_grid = cast("QGridLayout", self._z_move_btns.layout())
        z_grid.addWidget(self._z_halt, 1, 1, Qt.AlignmentFlag.AlignCenter)

        self._xy_no_device_lbl = QLabel("No core XY Stage")
        self._z_no_device_lbl = QLabel("No core Focus Device")
        for lbl in (self._xy_no_device_lbl, self._z_no_device_lbl):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setFixedWidth(110)

        self._xy_stack = QStackedWidget()
        self._xy_stack.addWidget(self._xy_move_btns)
        self._xy_stack.addWidget(self._xy_no_device_lbl)

        self._z_stack = QStackedWidget()
        self._z_stack.addWidget(self._z_move_btns)
        self._z_stack.addWidget(self._z_no_device_lbl)

        # position spinboxes, with a shared "Enable Editing" context menu
        self._pos_menu = QMenu(self)
        self._pos_toggle_action = self._pos_menu.addAction("Enable Editing")
        self._pos_toggle_action.setCheckable(True)
        self._pos_toggle_action.setChecked(absolute_positioning)
        self._pos_toggle_action.triggered.connect(self.enable_absolute_positioning)

        self._x_pos = MoveStageSpinBox(label="X")
        self._y_pos = MoveStageSpinBox(label="Y")
        self._z_pos = MoveStageSpinBox(label="Z")
        self._pos_boxes = [self._x_pos, self._y_pos, self._z_pos]
        RightClickMenu(self._pos_menu, self._pos_boxes, self.isEnabled)
        self._x_pos.editingFinished.connect(self._move_xy_absolute)
        self._y_pos.editingFinished.connect(self._move_xy_absolute)
        self._z_pos.editingFinished.connect(self._move_z_absolute)

        self._poll_cb = QCheckBox("Poll")
        self.snap_checkbox = QCheckBox(text="Snap")
        # short labels: these sit after a shared "Invert:" label, see LAYOUT below
        self._invert_x = QCheckBox(text="X")
        self._invert_y = QCheckBox(text="Y")
        self._invert_z = QCheckBox(text="Z")

        # LAYOUT ------------------------------------------------

        # Keep the arrow grids at their natural size.
        grid_fixed = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop

        invert_row = QHBoxLayout()
        invert_row.setSpacing(10)
        invert_row.addWidget(self.snap_checkbox)
        invert_row.addWidget(self._poll_cb)
        invert_row.addStretch()
        invert_row.addWidget(QLabel("Invert: "))
        invert_row.addWidget(self._invert_x)
        invert_row.addWidget(self._invert_y)
        invert_row.addWidget(self._invert_z)

        # position and step fields, one row per axis, aligned with X/Y/Z
        pos_col = QGridLayout()
        pos_col.setSpacing(2)
        for row, (axis, step, pos) in enumerate(
            (
                ("X", self._x_step, self._x_pos),
                ("Y", self._y_step, self._y_pos),
                ("Z", self._z_step, self._z_pos),
            )
        ):
            right = Qt.AlignmentFlag.AlignRight
            pos_col.addWidget(QLabel(f"Step {axis}: "), row, 0, right)
            pos_col.addWidget(step, row, 1)
            pos_col.addWidget(QLabel(f"{axis}: "), row, 2, right)
            pos_col.addWidget(pos, row, 3)
            pos_col.setRowStretch(row, 1)

        # Let the position fields stretch to the arrows' height.
        self._top_row = QHBoxLayout()
        self._top_row.setSpacing(6)
        self._top_row.addWidget(self._xy_stack, 0, grid_fixed)
        self._top_row.addWidget(self._z_stack, 0, grid_fixed)
        self._top_row.addLayout(pos_col)
        self._top_row.addStretch()

        move_box = QGroupBox()
        move_box_layout = QVBoxLayout(move_box)
        move_box_layout.setContentsMargins(4, 4, 4, 4)
        move_box_layout.addLayout(self._top_row)
        move_box_layout.addLayout(invert_row)

        move_box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.addWidget(move_box)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        # catch wheel-scroll events on every descendant widget,
        # so e.g. mouse-wheel scrolling works no matter where the cursor is
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)

        # SIGNALS -----------------------------------------------

        self._xy_move_btns.moveRequested.connect(self._on_xy_move_requested)
        self._z_move_btns.moveRequested.connect(self._on_z_move_requested)
        self._poll_cb.toggled.connect(self._toggle_poll_timer)
        self._mmc.events.systemConfigurationLoaded.connect(self._on_devices_changed)
        self._mmc.events.propertyChanged.connect(self._on_property_changed)
        self.destroyed.connect(self._disconnect)

        # INITIALIZATION ------------------------------------------

        self._on_devices_changed()
        self.enable_absolute_positioning(absolute_positioning)
        self._update_xy_tooltips()

    def x_step(self) -> float:
        """Return the current X step size."""
        return self._x_step.value()  # type: ignore

    def set_x_step(self, step: float) -> None:
        """Set the X step size."""
        self._x_step.setValue(step)

    def y_step(self) -> float:
        """Return the current Y step size."""
        return self._y_step.value()  # type: ignore

    def set_y_step(self, step: float) -> None:
        """Set the Y step size."""
        self._y_step.setValue(step)

    def z_step(self) -> float:
        """Return the current Z step size."""
        return self._z_step.value()  # type: ignore

    def set_z_step(self, step: float) -> None:
        """Set the Z step size."""
        self._z_step.setValue(step)

    @Slot(bool)
    def enable_absolute_positioning(self, enabled: bool) -> None:
        """Toggles whether the position spinboxes can be edited by the user.

        Parameters
        ----------
        enabled: bool:
            If True, the position spinboxes will be enabled for user editing.
            If False, the position spinboxes will be disabled for user editing.
        """
        self._pos_toggle_action.setChecked(enabled)
        self._update_pos_boxes()
        for box in self._pos_boxes:
            box.set_editing_tooltip(enabled)

    def _update_pos_boxes(self) -> None:
        """Enable the position boxes that have a device, if editing is enabled."""
        editing = self._pos_toggle_action.isChecked()
        self._x_pos.setEnabled(editing and bool(self._xy_device))
        self._y_pos.setEnabled(editing and bool(self._xy_device))
        self._z_pos.setEnabled(editing and bool(self._z_device))

    @Slot(str, str, object)
    def _on_property_changed(self, device: str, prop: str, value: str) -> None:
        if device == "Core" and prop in ("XYStage", "Focus"):
            self._on_devices_changed()

    @Slot()
    def _on_devices_changed(self) -> None:
        self._bind_xy_device(self._mmc.getXYStageDevice())
        self._bind_z_device(self._mmc.getFocusDevice())

    def _bind_xy_device(self, device: str) -> None:
        if device == self._xy_device:
            return
        if self._xy_controller is not None:
            self._xy_controller.moveFinished.disconnect(self._update_xy_position)

        self._xy_device = device
        self._xy_halt.setDevice(device)
        xy_page = self._xy_move_btns if device else self._xy_no_device_lbl
        self._show_page(self._xy_stack, xy_page, bool(device))
        self._x_step.setEnabled(bool(device))
        self._y_step.setEnabled(bool(device))
        self._update_pos_boxes()
        self._invert_x.setEnabled(bool(device))
        self._invert_y.setEnabled(bool(device))

        if device:
            self._xy_controller = QStageMoveAccumulator.for_device(device, self._mmc)
            self._xy_controller.moveFinished.connect(self._update_xy_position)
            self._update_xy_position()
        else:
            self._xy_controller = None

    def _show_page(self, stack: QStackedWidget, page: QWidget, device: bool) -> None:
        stack.setCurrentWidget(page)
        # Size to the current page rather than the stack's largest page.
        stack.setFixedSize(page.sizeHint())
        # Align arrow rows with the fields; center the shorter placeholder.
        v_align = Qt.AlignmentFlag.AlignTop if device else Qt.AlignmentFlag.AlignVCenter
        self._top_row.setAlignment(stack, Qt.AlignmentFlag.AlignLeft | v_align)

    def _bind_z_device(self, device: str) -> None:
        if device == self._z_device:
            return
        if self._z_controller is not None:
            self._z_controller.moveFinished.disconnect(self._update_z_position)

        self._z_device = device
        self._z_halt.setDevice(device)
        z_page = self._z_move_btns if device else self._z_no_device_lbl
        self._show_page(self._z_stack, z_page, bool(device))
        self._z_step.setEnabled(bool(device))
        self._update_pos_boxes()
        self._invert_z.setEnabled(bool(device))

        if device:
            self._z_controller = QStageMoveAccumulator.for_device(device, self._mmc)
            self._z_controller.moveFinished.connect(self._update_z_position)
            self._update_z_position()
        else:
            self._z_controller = None

    @Slot()
    def _update_xy_position(self) -> None:
        if not self._xy_device:
            return
        x, y = self._mmc.getXYPosition(self._xy_device)
        self._x_pos.setValue(x)
        self._y_pos.setValue(y)

    @Slot()
    def _update_z_position(self) -> None:
        if not self._z_device:
            return
        self._z_pos.setValue(self._mmc.getPosition(self._z_device))

    @Slot(float, float)
    def _on_xy_move_requested(self, xmag: float, ymag: float) -> None:
        if self._xy_controller is None:
            return
        xmag *= self._x_step.value()
        ymag *= self._y_step.value()
        if self._invert_x.isChecked():
            xmag *= -1
        if self._invert_y.isChecked():
            ymag *= -1
        self._xy_controller.move_relative((xmag, ymag))
        self._xy_controller.snap_on_finish = self.snap_checkbox.isChecked()

    @Slot()
    def _update_xy_tooltips(self) -> None:
        """Update the XY grid buttons' tooltips for the independent X/Y steps."""
        for btn in self._xy_move_btns.findChildren(MoveStageButton):
            if xmag := btn.xmag:
                btn.setToolTip(f"move by {xmag * self._x_step.value()} µm")
            elif ymag := btn.ymag:
                btn.setToolTip(f"move by {ymag * self._y_step.value()} µm")

    @Slot(float, float)
    def _on_z_move_requested(self, xmag: float, ymag: float) -> None:
        if self._z_controller is None:
            return
        if self._invert_z.isChecked():
            ymag *= -1
        self._z_controller.move_relative(ymag)
        self._z_controller.snap_on_finish = self.snap_checkbox.isChecked()

    def _move_xy_absolute(self) -> None:
        if self._xy_controller is None:
            return
        self._xy_controller.move_absolute((self._x_pos.value(), self._y_pos.value()))
        self._xy_controller.snap_on_finish = self.snap_checkbox.isChecked()

    def _move_z_absolute(self) -> None:
        if self._z_controller is None:
            return
        self._z_controller.move_absolute(self._z_pos.value())
        self._z_controller.snap_on_finish = self.snap_checkbox.isChecked()

    def timerEvent(self, event: QTimerEvent | None) -> None:
        if event and event.timerId() == self._poll_timer_id:
            self._update_xy_position()
            self._update_z_position()
        super().timerEvent(event)

    def _step_boxes(self) -> tuple[QWidget, ...]:
        return (self._x_step, self._y_step, self._z_step)

    def _move_by_wheel(self, event: QWheelEvent) -> bool:
        """Move the Z stage via mouse-wheel scrolling."""
        if self._z_controller is None:
            return False
        delta = event.angleDelta().y()
        if delta == 0:
            return False
        direction = 1 if delta > 0 else -1
        self._on_z_move_requested(0, direction * self.z_step())
        return True

    def _disconnect(self) -> None:
        self._mmc.events.systemConfigurationLoaded.disconnect(self._on_devices_changed)
        self._mmc.events.propertyChanged.disconnect(self._on_property_changed)
        if self._xy_controller is not None:
            self._xy_controller.moveFinished.disconnect(self._update_xy_position)
        if self._z_controller is not None:
            self._z_controller.moveFinished.disconnect(self._update_z_position)
