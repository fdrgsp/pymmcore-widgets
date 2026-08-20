from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pymmcore_plus import CMMCorePlus, DeviceType
from qtpy.QtCore import QEvent, QObject, QSize, Qt, QTimerEvent, Signal, Slot
from qtpy.QtGui import QContextMenuEvent, QWheelEvent
from qtpy.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon

from ._q_stage_controller import QStageMoveAccumulator

if TYPE_CHECKING:
    from typing import Any

MOVE_BUTTONS: dict[str, tuple[int, int, int, int]] = {
    # btn glyph (r, c, xmag, ymag)
    "mdi:arrow-top-left-thick": (0, 0, -1, 1),
    "mdi:arrow-up-thick": (0, 1, 0, 1),
    "mdi:arrow-top-right-thick": (0, 2, 1, 1),
    "mdi:arrow-left-thick": (1, 0, -1, 0),
    "mdi:arrow-right-thick": (1, 2, 1, 0),
    "mdi:arrow-bottom-left-thick": (2, 0, -1, -1),
    "mdi:arrow-down-thick": (2, 1, 0, -1),
    "mdi:arrow-bottom-right-thick": (2, 2, 1, -1),
}


class MoveStageButton(QPushButton):
    def __init__(self, glyph: str, xmag: int, ymag: int, parent: QWidget | None = None):
        super().__init__(parent=parent)
        self.xmag = xmag
        self.ymag = ymag
        self.setAutoRepeat(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 38)
        self.setIcon(QIconifyIcon(glyph, color="green"))
        self.setIconSize(QSize(28, 28))


class MoveStageSpinBox(QDoubleSpinBox):
    """Common behavior for SpinBoxes that move stages."""

    def __init__(
        self,
        label: str,
        minimum: float = -10000000,
        maximum: float = 10000000,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.setToolTip(f"Set {label} in µm")
        self.setSuffix(" µm")
        self.setMinimum(minimum)
        self.setMaximum(maximum)
        self.setDecimals(1)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, 0)
        self.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # enable custom context menu handling for right-click events
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)


class HaltButton(QPushButton):
    def __init__(
        self, device: str, core: CMMCorePlus, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent=parent)
        self._device = device
        self._core = core
        self.setIcon(QIconifyIcon("bi:sign-stop", color="red"))
        self.setIconSize(QSize(28, 28))
        self.setToolTip("Halt stage movement")
        self.setFixedSize(38, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._on_clicked)

    def setDevice(self, device: str) -> None:
        """Change the device that this button halts."""
        self._device = device

    @Slot()
    def _on_clicked(self) -> None:
        if self._device:
            self._core.stop(self._device)


class StageMovementButtons(QWidget):
    """Grid of buttons to move a stage in 2D.

    NW  N  NE
     W     E
    SW  S  SE
    """

    moveRequested = Signal(float, float)

    def __init__(self, show_x: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._x_visible = show_x

        btn_grid = QGridLayout(self)
        btn_grid.setContentsMargins(0, 0, 0, 0)
        btn_grid.setSpacing(2)

        for glyph, (row, col, xmag, ymag) in MOVE_BUTTONS.items():
            if xmag != 0 and not show_x:
                continue
            btn = MoveStageButton(glyph, xmag, ymag)
            btn.clicked.connect(self._on_move_btn_clicked)
            btn_grid.addWidget(btn, row, col)

        # step size spinbox, exposed for the parent StageWidget to place
        self.step_size = MoveStageSpinBox(label="step size", minimum=0)
        self.step_size.setValue(10)
        self.step_size.valueChanged.connect(self._update_tooltips)

        self._update_tooltips()

    @Slot()
    def _on_move_btn_clicked(self) -> None:
        btn = cast("MoveStageButton", self.sender())
        self.moveRequested.emit(self._scale(btn.xmag), self._scale(btn.ymag))

    @Slot()
    def _update_tooltips(self) -> None:
        """Update tooltips for the move buttons."""
        for btn in self.findChildren(MoveStageButton):
            if xmag := btn.xmag:
                btn.setToolTip(f"move by {self._scale(xmag)} µm")
            elif ymag := btn.ymag:
                btn.setToolTip(f"move by {self._scale(ymag)} µm")

    def _scale(self, mag: int) -> float:
        """Convert step mag of (1, 2, 3) to absolute XY units.

        Can be used to step 1x field of view, etc...
        """
        return float(mag * self.step_size.value())


class StageWidget(QWidget):
    """A Widget to control a XY and/or a Z stage.

    Parameters
    ----------
    device: str:
        Stage device.
    absolute_positioning: bool | None
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

    BTN_SIZE = 30

    def __init__(
        self,
        device: str,
        *,
        absolute_positioning: bool = False,
        parent: QWidget | None = None,
        mmcore: CMMCorePlus | None = None,
    ):
        super().__init__(parent=parent)

        self._mmc = mmcore or CMMCorePlus.instance()
        self._device = device
        self._poll_timer_id: int | None = None

        self._dtype = self._mmc.getDeviceType(self._device)
        if self._dtype not in {DeviceType.Stage, DeviceType.XYStage}:
            raise ValueError("This widget only supports Stage and XYStage devices.")

        self._is_2axis = self._dtype is DeviceType.XYStage
        self._Ylabel = "Y" if self._is_2axis else self._device

        # Initialize stage controller
        self._stage_controller = QStageMoveAccumulator.for_device(
            self._device, self._mmc
        )

        # WIDGETS ------------------------------------------------

        self._move_btns = StageMovementButtons(self._is_2axis)
        self._step = self._move_btns.step_size

        self._step_row = QGridLayout()
        self._step_row.setSpacing(2)
        self._step_row.addWidget(QLabel("Step: "), 0, 0, Qt.AlignmentFlag.AlignRight)
        self._step_row.addWidget(self._step, 0, 1)
        self._step_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._pos = QGridLayout()
        self._pos.setSpacing(2)
        self._pos_boxes: list[MoveStageSpinBox] = []
        self._pos_menu = QMenu(self)
        self._pos_toggle_action = self._pos_menu.addAction("Enable Editing")
        self._pos_toggle_action.setCheckable(True)
        self._pos_toggle_action.setChecked(absolute_positioning)
        self._pos_toggle_action.triggered.connect(self.enable_absolute_positioning)

        pos_row = 0
        if self._is_2axis:
            self._pos.addWidget(QLabel("X: "), pos_row, 0, Qt.AlignmentFlag.AlignRight)
            self._x_pos = MoveStageSpinBox(label="X")
            self._pos_boxes.append(self._x_pos)
            self._pos.addWidget(self._x_pos, pos_row, 1)
            self._x_pos.editingFinished.connect(self._move_absolute)
            pos_row += 1

        self._pos.addWidget(
            QLabel(f"{self._Ylabel}: "), pos_row, 0, Qt.AlignmentFlag.AlignRight
        )
        self._y_pos = MoveStageSpinBox(label="Y")
        self._pos_boxes.append(self._y_pos)
        self._y_pos.editingFinished.connect(self._move_absolute)
        self._pos.addWidget(self._y_pos, pos_row, 1)

        self._pos.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._halt = HaltButton(device, self._mmc, self)
        self._poll_cb = QCheckBox("Poll")
        self.snap_checkbox = QCheckBox(text="Snap")
        self._invert_x = QCheckBox(text="Invert X")
        self._invert_y = QCheckBox(text=f"Invert {self._Ylabel}")

        # LAYOUT ------------------------------------------------

        # checkboxes below the move buttons
        chxbox_grid = QGridLayout()
        chxbox_grid.setSpacing(12)
        chxbox_grid.setContentsMargins(0, 0, 0, 0)
        chxbox_grid.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chxbox_grid.addWidget(self.snap_checkbox, 0, 0)
        chxbox_grid.addWidget(self._poll_cb, 0, 1)
        chxbox_grid.addWidget(self._invert_x, 1, 0)
        chxbox_grid.addWidget(self._invert_y, 1, 1)

        # halt button sits in the empty center cell of the move-buttons grid
        move_btns_layout = cast("QGridLayout", self._move_btns.layout())
        move_btns_layout.addWidget(self._halt, 1, 1, Qt.AlignmentFlag.AlignCenter)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.addLayout(self._step_row)
        main_layout.addWidget(self._move_btns, alignment=Qt.AlignmentFlag.AlignCenter)
        main_layout.addLayout(self._pos)
        main_layout.addLayout(chxbox_grid)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if not self._is_2axis:
            self._invert_x.hide()

        # catch events (context-menu, wheel-scroll) on every descendant widget,
        # so e.g. mouse-wheel scrolling works no matter where the cursor is
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)

        # SIGNALS -----------------------------------------------

        self._move_btns.moveRequested.connect(self._on_move_requested)
        self._poll_cb.toggled.connect(self._toggle_poll_timer)
        self._mmc.events.systemConfigurationLoaded.connect(self._on_system_cfg)
        self._stage_controller.moveFinished.connect(self._update_position_from_core)

        # INITIALIZATION ----------------------------------------

        self._update_position_from_core()
        self.enable_absolute_positioning(absolute_positioning)

    def step(self) -> float:
        """Return the current step size."""
        return self._step.value()  # type: ignore

    def setStep(self, step: float) -> None:
        """Set the step size."""
        self._step.setValue(step)

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

    def _enable_wdg(self, enabled: bool) -> None:
        self._step.setEnabled(enabled)
        self._move_btns.setEnabled(enabled)
        for box in self._pos_boxes:
            box.setEnabled(enabled)
        self.snap_checkbox.setEnabled(enabled)
        self._poll_cb.setEnabled(enabled)

    @Slot()
    def _on_system_cfg(self) -> None:
        if self._device in self._mmc.getLoadedDevicesOfType(self._dtype):
            self._enable_wdg(True)
            self._update_position_from_core()
        else:
            self._enable_wdg(False)

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
            self._update_position_from_core()
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
        # scrolling over the step spinbox should change the step value itself,
        # not move the stage
        return obj is self._step or (
            isinstance(obj, QWidget) and self._step.isAncestorOf(obj)
        )

    def _move_by_wheel(self, event: QWheelEvent) -> bool:
        """Move a single-axis (Z) stage via mouse-wheel scrolling."""
        if self._is_2axis or not self._move_btns.isEnabled():
            return False
        delta = event.angleDelta().y()
        if delta == 0:
            return False
        direction = 1 if delta > 0 else -1
        self._on_move_requested(0, direction * self.step())
        return True

    @Slot()
    def _update_position_from_core(self) -> None:
        if self._device not in self._mmc.getLoadedDevicesOfType(self._dtype):
            return
        if self._is_2axis:
            x, y = self._mmc.getXYPosition(self._device)
            self._x_pos.setValue(x)
            self._y_pos.setValue(y)
        else:
            y = self._mmc.getPosition(self._device)
            self._y_pos.setValue(y)

    @Slot(float, float)
    def _on_move_requested(self, xmag: float, ymag: float) -> None:
        if self._invert_x.isChecked():
            xmag *= -1
        if self._invert_y.isChecked():
            ymag *= -1

        val = (xmag, ymag) if self._is_2axis else ymag
        self._do_move(val, relative=True)

    def _move_absolute(self) -> None:
        y = self._y_pos.value()
        val = (self._x_pos.value(), y) if self._is_2axis else y
        self._do_move(val, relative=False)

    def _do_move(self, val: Any, relative: bool) -> None:
        if relative:
            self._stage_controller.move_relative(val)
        else:
            self._stage_controller.move_absolute(val)
        self._stage_controller.snap_on_finish = self.snap_checkbox.isChecked()

    def _disconnect(self) -> None:
        self._mmc.events.systemConfigurationLoaded.disconnect(self._on_system_cfg)
        if self._is_2axis:
            event = self._mmc.events.XYStagePositionChanged
        else:
            event = self._mmc.events.stagePositionChanged
        event.disconnect(self._update_position_from_core)
