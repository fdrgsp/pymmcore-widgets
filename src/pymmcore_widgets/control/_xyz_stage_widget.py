from __future__ import annotations

from typing import cast

from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QEvent, QObject, Qt, QTimerEvent, Slot
from qtpy.QtGui import QContextMenuEvent, QWheelEvent
from qtpy.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ._q_stage_controller import QStageMoveAccumulator
from ._stage_widget import (
    HaltButton,
    MoveStageButton,
    MoveStageSpinBox,
    StageMovementButtons,
)


class XYZStageWidget(QWidget):
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
        # X and Y have independent step values (self._x_step / self._y_step
        # below), so the grid's own built-in step scaling is left at 1.0 (a
        # no-op multiplier) and never shown; see _on_xy_move_requested.
        self._xy_move_btns.step_size.setValue(1.0)
        self._xy_halt = HaltButton("", self._mmc, self)
        xy_grid = cast("QGridLayout", self._xy_move_btns.layout())
        xy_grid.addWidget(self._xy_halt, 1, 1, Qt.AlignmentFlag.AlignCenter)

        self._x_step = MoveStageSpinBox(label="X step", minimum=0)
        self._x_step.setValue(10)
        self._y_step = MoveStageSpinBox(label="Y step", minimum=0)
        self._y_step.setValue(10)
        self._x_step.valueChanged.connect(self._update_xy_tooltips)
        self._y_step.valueChanged.connect(self._update_xy_tooltips)

        self._z_move_btns = StageMovementButtons(show_x=False)
        self._z_step = self._z_move_btns.step_size
        self._z_halt = HaltButton("", self._mmc, self)
        z_grid = cast("QGridLayout", self._z_move_btns.layout())
        z_grid.addWidget(self._z_halt, 1, 1, Qt.AlignmentFlag.AlignCenter)

        self._xy_no_device_lbl = QLabel("No core XY Stage")
        self._z_no_device_lbl = QLabel("No core Focus Device")
        for lbl in (self._xy_no_device_lbl, self._z_no_device_lbl):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            # wide enough that the wrapped text is fully readable (the button
            # grids alone -- especially the single-column Z one -- are too
            # narrow for "No core Focus Device" etc. not to be clipped)
            lbl.setFixedWidth(110)

        self._xy_stack = QStackedWidget()
        self._xy_stack.addWidget(self._xy_move_btns)
        self._xy_stack.addWidget(self._xy_no_device_lbl)

        self._z_stack = QStackedWidget()
        self._z_stack.addWidget(self._z_move_btns)
        self._z_stack.addWidget(self._z_no_device_lbl)

        # position spinboxes, with a shared "Enable Editing" context menu
        self._pos_boxes: list[MoveStageSpinBox] = []
        self._pos_menu = QMenu(self)
        self._pos_toggle_action = self._pos_menu.addAction("Enable Editing")
        self._pos_toggle_action.setCheckable(True)
        self._pos_toggle_action.setChecked(absolute_positioning)
        self._pos_toggle_action.triggered.connect(self.enable_absolute_positioning)

        self._x_pos = MoveStageSpinBox(label="X")
        self._y_pos = MoveStageSpinBox(label="Y")
        self._z_pos = MoveStageSpinBox(label="Z")
        self._pos_boxes.extend([self._x_pos, self._y_pos, self._z_pos])
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

        # constrain the movement grids to their natural (sizeHint) size in both
        # directions, matching StageWidget's own `addWidget(..., AlignCenter)`
        # pattern, so they never stretch/spread their rows or columns apart
        grid_fixed = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop

        # invert checkboxes sit directly under the arrows, left-aligned to
        # them; Snap/Poll ride along on the same row, after X/Y/Z
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
        pos_col.addWidget(QLabel("Step X: "), 0, 0, Qt.AlignmentFlag.AlignRight)
        pos_col.addWidget(self._x_step, 0, 1)
        pos_col.addWidget(QLabel("X: "), 0, 2, Qt.AlignmentFlag.AlignRight)
        pos_col.addWidget(self._x_pos, 0, 3)
        pos_col.addWidget(QLabel("Step Y: "), 1, 0, Qt.AlignmentFlag.AlignRight)
        pos_col.addWidget(self._y_step, 1, 1)
        pos_col.addWidget(QLabel("Y: "), 1, 2, Qt.AlignmentFlag.AlignRight)
        pos_col.addWidget(self._y_pos, 1, 3)
        pos_col.addWidget(QLabel("Step Z: "), 2, 0, Qt.AlignmentFlag.AlignRight)
        pos_col.addWidget(self._z_step, 2, 1)
        pos_col.addWidget(QLabel("Z: "), 2, 2, Qt.AlignmentFlag.AlignRight)
        pos_col.addWidget(self._z_pos, 2, 3)
        pos_col.setRowStretch(0, 1)
        pos_col.setRowStretch(1, 1)
        pos_col.setRowStretch(2, 1)

        # top_row: arrows are pinned to their natural size (grid_fixed);
        # pos_col is deliberately left unaligned so it stretches to match
        # *just* the arrows' height (not the invert row below), letting its
        # row stretch spread its fields evenly across that same vertical span
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

        # a plain nested QLayout only grows if its *contents* ask for more
        # space, but a QGroupBox is a real QWidget whose own (Preferred,
        # Preferred) size policy actively requests extra space from its
        # parent layout. Pin it to its natural sizeHint in both directions so
        # it never stretches/spreads its contents apart on window resize.
        move_box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.addWidget(move_box)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        # catch events (context-menu, wheel-scroll) on every descendant widget,
        # so e.g. mouse-wheel scrolling works no matter where the cursor is
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)

        # SIGNALS -----------------------------------------------

        self._xy_move_btns.moveRequested.connect(self._on_xy_move_requested)
        self._z_move_btns.moveRequested.connect(self._on_z_move_requested)
        self._poll_cb.toggled.connect(self._toggle_poll_timer)
        self._mmc.events.systemConfigurationLoaded.connect(self._on_devices_changed)
        self._mmc.events.propertyChanged.connect(self._on_property_changed)

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
        for box in self._pos_boxes:
            # use read-only (rather than disabled) so the boxes keep receiving
            # mouse events and the "Enable Editing" context menu stays reachable
            box.setReadOnly(not enabled)

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
        self._xy_stack.setCurrentWidget(xy_page)
        # a QStackedWidget normally sizes itself to fit its *largest* page; size
        # it to just the current page instead, so the compact button grid isn't
        # stretched to accommodate the (much wider) "no device" placeholder text
        self._xy_stack.setFixedSize(xy_page.sizeHint())
        # the grid must stay top-aligned so its rows match X/Y/Z in pos_col,
        # but the (shorter) placeholder should be centered in that same span
        v_align = Qt.AlignmentFlag.AlignTop if device else Qt.AlignmentFlag.AlignVCenter
        self._top_row.setAlignment(self._xy_stack, Qt.AlignmentFlag.AlignLeft | v_align)
        self._x_step.setEnabled(bool(device))
        self._y_step.setEnabled(bool(device))
        self._x_pos.setEnabled(bool(device))
        self._y_pos.setEnabled(bool(device))
        self._invert_x.setEnabled(bool(device))
        self._invert_y.setEnabled(bool(device))

        if device:
            self._xy_controller = QStageMoveAccumulator.for_device(device, self._mmc)
            self._xy_controller.moveFinished.connect(self._update_xy_position)
            self._update_xy_position()
        else:
            self._xy_controller = None

    def _bind_z_device(self, device: str) -> None:
        if device == self._z_device:
            return
        if self._z_controller is not None:
            self._z_controller.moveFinished.disconnect(self._update_z_position)

        self._z_device = device
        self._z_halt.setDevice(device)
        z_page = self._z_move_btns if device else self._z_no_device_lbl
        self._z_stack.setCurrentWidget(z_page)
        # see matching comments in _bind_xy_device
        self._z_stack.setFixedSize(z_page.sizeHint())
        v_align = Qt.AlignmentFlag.AlignTop if device else Qt.AlignmentFlag.AlignVCenter
        self._top_row.setAlignment(self._z_stack, Qt.AlignmentFlag.AlignLeft | v_align)
        self._z_step.setEnabled(bool(device))
        self._z_pos.setEnabled(bool(device))
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
        # xmag/ymag are the raw (-1, 0, 1) direction multipliers here, since
        # the grid's own step scaling is fixed at 1.0 -- apply the
        # independent X/Y step values ourselves
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

    @Slot(bool)
    def _toggle_poll_timer(self, on: bool) -> None:
        if on:
            if self._poll_timer_id is None:
                self._poll_timer_id = self.startTimer(500)
        else:
            if self._poll_timer_id is not None:
                self.killTimer(self._poll_timer_id)
                self._poll_timer_id = None

    def timerEvent(self, event: QTimerEvent | None) -> None:
        if event and event.timerId() == self._poll_timer_id:
            self._update_xy_position()
            self._update_z_position()
        super().timerEvent(event)

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        # NB QAbstractSpinBox has its own Context Menu handler, which conflicts
        # with the one we want to generate. So we intercept the event here >:)
        # See https://stackoverflow.com/a/71126504
        if obj in self._pos_boxes and isinstance(event, QContextMenuEvent):
            self._pos_menu.exec(event.globalPos())
            return True
        if (
            isinstance(event, QWheelEvent)
            and not self._is_step_box(obj)
            and self._move_by_wheel(event)
        ):
            return True
        return super().eventFilter(obj, event)  # type: ignore [no-any-return]

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        if event is None or not self._move_by_wheel(event):
            super().wheelEvent(event)

    def _is_step_box(self, obj: QObject | None) -> bool:
        # scrolling over a step spinbox should change that step value itself,
        # not move the stage
        step_boxes = (self._x_step, self._y_step, self._z_step)
        return obj in step_boxes or (
            isinstance(obj, QWidget)
            and any(box.isAncestorOf(obj) for box in step_boxes)
        )

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
