from __future__ import annotations

import re
from contextlib import suppress
from math import isclose
from typing import TYPE_CHECKING, Literal

from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QSize, Qt, QTimer, Slot
from qtpy.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QMenu,
    QSizePolicy,
    QToolButton,
    QWidget,
)
from superqt.iconify import QIconifyIcon
from superqt.utils import create_worker, ensure_main_thread

from pymmcore_widgets._util import RightClickMenu
from pymmcore_widgets.useq_widgets._z import (
    ROW_FIRST_BOUND,
    ROW_SECOND_BOUND,
    Mode,
    ZPlanWidget,
    format_z,
)

from ._xy_bounds import (
    _EDITABLE_TOOLTIP,
    _READ_ONLY_TOOLTIP,
    ICONS_GO,
    _moving_tooltip,
)

if TYPE_CHECKING:
    from qtpy.QtGui import QHideEvent, QShowEvent

ICON_SIZE = 16
# Core-driven moves arrive via stagePositionChanged; polling only has to catch
# moves the core doesn't report (focus knob, joystick).
POLL_INTERVAL_MS = 250
ICONS_MARK_V2 = {
    "top": "mingcute:arrow-to-up-fill",
    "bottom": "mingcute:arrow-to-down-fill",
}


def _read_z_position(core: CMMCorePlus) -> tuple[str, float | None]:
    """Read hardware in a worker, including the device to detect focus changes."""
    device = core.getFocusDevice()
    return device, core.getPosition(device) if device else None


def _tool_button(
    glyph: str, tooltip: str, parent: QWidget | None = None
) -> QToolButton:
    button = QToolButton(parent)
    button.setIcon(QIconifyIcon(glyph))
    button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setToolTip(tooltip)
    button.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect)
    return button


class MarkVisit(QWidget):
    def __init__(self, mark_top: bool = True):
        super().__init__()

        mode = "top" if mark_top else "bottom"

        self.mark = _tool_button(ICONS_MARK_V2[mode], f"Mark {mode}.")
        self.visit = _tool_button(ICONS_GO[mode], f"Move to {mode}.")

        layout = QHBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.mark)
        layout.addWidget(self.visit)


class CoreConnectedZPlanWidget(ZPlanWidget):
    """[ZPlanWidget](../ZPlanWidget#) connected to a Micro-Manager core instance.

    Parameters
    ----------
    mmcore : CMMCorePlus | None
        Optional [`CMMCorePlus`][pymmcore_plus.CMMCorePlus] micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new)
        [`CMMCorePlus.instance`][pymmcore_plus.core._mmcore_plus.CMMCorePlus.instance].
    parent : QWidget | None
        Optional parent widget, by default None.
    """

    def __init__(
        self, mmcore: CMMCorePlus | None = None, parent: QWidget | None = None
    ) -> None:
        self.bottom_btn = MarkVisit(mark_top=False)
        self.top_btn = MarkVisit(mark_top=True)
        self._move_buttons = QWidget()
        self.center_btn = _tool_button(
            "mingcute:dividing-line-fill",
            "Move to center of range.",
            self._move_buttons,
        )
        self.reference_btn = _tool_button(
            "mdi:restore",
            "Return to reference Z (starting position).",
            self._move_buttons,
        )
        self._reference_z: float | None = None
        self._last_visit_z: float | None = None
        self._live_z: float | None = None
        self._live_device: str | None = None
        self._stack_active = True
        self._mda_running = False
        self._polling = False
        self._poll_pending = False
        self._poll_generation = 0
        # generation of the (single) read in flight, checked when it reports back
        self._read_generation = 0
        self.poll_z = QCheckBox("Poll")
        self.poll_z.setToolTip("Poll the Z-stage position and show it with a marker.")
        # Lay it out by its real size: some styles (e.g. macOS) report a
        # smaller layout rect, and its column then grows by the difference,
        # leaving a gap between the Z graphic above and the divider beside it.
        self.poll_z.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect)
        self.set_reference_btn = _tool_button(
            "mdi:crosshairs-gps", "Set the reference Z to the current position."
        )
        # as tall as Poll, at any zoom, so hiding it doesn't resize the widget
        self.set_reference_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Ignored
        )

        self._move_layout = QGridLayout(self._move_buttons)
        self._move_layout.setContentsMargins(0, 0, 0, 0)
        self._move_layout.setHorizontalSpacing(5)
        self._move_layout.setVerticalSpacing(4)
        for buttons in (self.bottom_btn, self.top_btn):
            buttons.mark.setFixedSize(30, 26)
        for button in (
            self.bottom_btn.visit,
            self.top_btn.visit,
            self.center_btn,
            self.reference_btn,
        ):
            button.setFixedWidth(30)
            button.setMinimumHeight(26)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        super().__init__(parent)
        self._mmc = mmcore or CMMCorePlus.instance()
        self._mda_running = self._mmc.mda.is_running()
        # single-shot, restarted when each read finishes: a constant gap between
        # reads, however long the hardware takes to answer
        self._poll_timer = QTimer(self)
        self._poll_timer.setSingleShot(True)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_position)
        self.poll_z.toggled.connect(self._update_polling)
        self.valueChanged.connect(self._update_live_marker)

        poll_row = QWidget()
        poll_layout = QHBoxLayout(poll_row)
        poll_layout.setContentsMargins(0, 0, 0, 0)
        poll_layout.setSpacing(2)
        poll_layout.addWidget(self.poll_z)
        poll_layout.addWidget(self.set_reference_btn)
        self._viz_layout.addWidget(poll_row, 0, Qt.AlignmentFlag.AlignHCenter)

        self.bottom_btn.mark.clicked.connect(self._mark_bottom)
        self.top_btn.mark.clicked.connect(self._mark_top)
        self.bottom_btn.visit.clicked.connect(self._visit_bottom)
        self.top_btn.visit.clicked.connect(self._visit_top)
        self.center_btn.clicked.connect(self._visit_center)
        self.reference_btn.clicked.connect(self._visit_reference)
        self.set_reference_btn.clicked.connect(self._set_reference)

        self._grid_layout.addWidget(
            self._move_buttons, ROW_FIRST_BOUND, 2, 2, 1, Qt.AlignmentFlag.AlignLeft
        )

        self._edit_menu = QMenu(self)
        self._edit_action = self._edit_menu.addAction("Enable Editing")
        self._edit_action.setCheckable(True)
        self._edit_action.toggled.connect(self.enable_editing)
        RightClickMenu(self._edit_menu, (self.top, self.bottom), self.isEnabled)
        self.enable_editing(False)

        self._move_tooltips = {
            button: button.toolTip()
            for button in (
                self.top_btn.visit,
                self.bottom_btn.visit,
                self.center_btn,
                self.reference_btn,
            )
        }
        self._move_menu = QMenu(self)
        self._move_action = self._move_menu.addAction("Enable Moving")
        self._move_action.setCheckable(True)
        self._move_action.toggled.connect(self.enable_moving)
        RightClickMenu(self._move_menu, self._move_tooltips, self.isEnabled)
        self.enable_moving(False)

        self._sync_visual_height()
        self.setMode(self._mode)

        self._mmc.events.systemConfigurationLoaded.connect(self._update_suggested_step)
        self._mmc.events.systemConfigurationLoaded.connect(self._reset_live_position)
        self._mmc.events.propertyChanged.connect(self._on_property_changed)
        self._mmc.events.stagePositionChanged.connect(self._on_stage_position_changed)
        self._mmc.mda.events.sequenceStarted.connect(self._on_mda_started)
        self._mmc.mda.events.sequenceFinished.connect(self._on_mda_finished)
        self.destroyed.connect(self._disconnect)
        self._update_suggested_step()

    def setMode(
        self,
        mode: Mode | Literal["top_bottom", "range_around", "above_below"],
    ) -> None:
        mode = Mode(mode)
        if mode != self._mode:
            self._reference_z = self._last_visit_z = None
        # Arrange the buttons first, then show them: super() lays out the widget
        # for the new mode, and an earlier layout would see half of the change.
        for widget in (
            self.top_btn,
            self.bottom_btn,
            self.center_btn,
            self.reference_btn,
        ):
            self._move_layout.removeWidget(widget)
        self._move_layout.addWidget(self.top_btn, 0, 0)
        if mode is not Mode.RANGE_AROUND:
            self._move_layout.addWidget(self.bottom_btn, 1, 0)
            self._move_layout.addWidget(self.center_btn, 0, 1, 2, 1)
        else:
            self._move_layout.addWidget(self.bottom_btn, 0, 1)
            self._move_layout.addWidget(self.center_btn, 0, 2)
        if mode is Mode.ABOVE_BELOW:
            self._move_layout.addWidget(self.reference_btn, 0, 2, 2, 1)
        absolute = mode is Mode.TOP_BOTTOM
        for buttons in (self.top_btn, self.bottom_btn):
            buttons.mark.setVisible(absolute)
        self.reference_btn.setVisible(mode is Mode.ABOVE_BELOW)
        self.set_reference_btn.setVisible(not absolute)
        alignment = Qt.AlignmentFlag.AlignLeft
        if mode is Mode.RANGE_AROUND:
            alignment |= Qt.AlignmentFlag.AlignVCenter
        self._grid_layout.setAlignment(self._move_buttons, alignment)
        super().setMode(mode)
        self._update_live_marker()

    def enable_editing(self, enabled: bool) -> None:
        """Toggle whether the user can type in both the top and bottom fields.

        They are disabled by default, as the Mark buttons set them.
        """
        self._edit_action.setChecked(enabled)
        for field in (self.top, self.bottom):
            field.setEnabled(enabled)
            field.setToolTip(_EDITABLE_TOOLTIP if enabled else _READ_ONLY_TOOLTIP)

    def enable_moving(self, enabled: bool) -> None:
        """Toggle whether the Move buttons can move the Z stage.

        They are disabled by default, so that a click does not move the stage.
        """
        self._move_action.setChecked(enabled)
        for button, tooltip in self._move_tooltips.items():
            button.setEnabled(enabled)
            button.setToolTip(_moving_tooltip(tooltip, enabled))

    def _sync_visual_height(self) -> None:
        # measured each time, as the buttons' size follows the app's zoom
        bound_height = max(
            self.top_btn.sizeHint().height(), self.bottom_btn.sizeHint().height()
        )
        for row in (ROW_FIRST_BOUND, ROW_SECOND_BOUND):
            self._grid_layout.setRowMinimumHeight(row, bound_height)
        super()._sync_visual_height()

    def setStackActive(self, active: bool) -> None:
        """Allow polling only when the containing Z-stack axis is checked."""
        self._stack_active = active
        self._update_polling()

    def showEvent(self, event: QShowEvent | None) -> None:
        """Resume requested polling when the view becomes visible."""
        super().showEvent(event)
        self._update_polling()

    def hideEvent(self, event: QHideEvent | None) -> None:
        """Pause polling and discard pending results while hidden."""
        super().hideEvent(event)
        self._update_polling()

    # Core and MDA events may be emitted from other threads with the psygnal backend.
    @Slot()
    @ensure_main_thread
    def _on_mda_started(self) -> None:
        # Polling competes with the acquisition for the stage's serial port.
        self._mda_running = True
        self._update_polling()

    @Slot()
    @ensure_main_thread
    def _on_mda_finished(self) -> None:
        self._mda_running = False
        self._update_polling()

    @Slot()
    def _update_polling(self) -> None:
        active = (
            self.poll_z.isChecked()
            and self.isVisible()
            and self._stack_active
            and not self._mda_running
        )
        if active == self._polling:
            return
        self._polling = active
        if active:
            self._poll_position()
        else:
            self._poll_timer.stop()
            self._poll_generation += 1
            self._live_z = None
            self._viz.setStagePosition(None)

    @Slot()
    def _poll_position(self) -> None:
        if self._poll_pending or not self._polling:
            return
        self._poll_pending = True
        self._read_generation = self._poll_generation
        create_worker(
            _read_z_position,
            self._mmc,
            _start_thread=True,
            _connect={
                "returned": self._on_position_polled,
                "errored": self._on_poll_error,
                "finished": self._on_poll_finished,
            },
        )

    @Slot(object)
    def _on_position_polled(self, result: tuple[str, float | None]) -> None:
        if self._read_generation == self._poll_generation:
            self._set_live_position(*result)

    @Slot(object)
    def _on_poll_error(self, error: Exception) -> None:
        if self._read_generation != self._poll_generation:
            return
        self._live_z = None
        self._viz.setStagePosition(None)
        self._viz.setToolTip(f"Live Z unavailable: {error}")

    @Slot()
    def _on_poll_finished(self) -> None:
        self._poll_pending = False
        if self._polling:
            self._poll_timer.start()

    @Slot(str, float)
    @ensure_main_thread
    def _on_stage_position_changed(self, device: str, position: float) -> None:
        if not self._polling or device != self._mmc.getFocusDevice():
            return
        # Newer than any read still in flight, so make that read stale.
        self._poll_generation += 1
        self._set_live_position(device, position)

    def _set_live_position(self, device: str, position: float | None) -> None:
        if self._live_device is not None and device != self._live_device:
            self._reference_z = self._last_visit_z = None
        self._live_device = device
        self._live_z = position
        self._update_live_marker()

    @Slot()
    def _update_live_marker(self) -> None:
        live = self._live_z
        if live is None or self._mode is Mode.TOP_BOTTOM:
            self._viz.setStagePosition(live)
            return
        if self._reference_z is None:
            self._reference_z = live
        offset = live - self._reference_z
        # setStagePosition sets the absolute tooltip; relative modes extend it.
        self._viz.setStagePosition(offset)
        self._viz.setToolTip(
            f"Live Z: {format_z(live)} µm "
            f"({format_z(offset, '+.3f')} µm from reference)"
        )

    @Slot()
    @ensure_main_thread
    def _reset_live_position(self) -> None:
        self._poll_generation += 1
        self._reference_z = self._last_visit_z = None
        self._live_device = None
        self._live_z = None
        self._viz.setStagePosition(None)

    def _mark_bottom(self) -> None:
        self.bottom.setValue(self._mmc.getZPosition())

    def _mark_top(self) -> None:
        self.top.setValue(self._mmc.getZPosition())

    def _set_reference(self) -> None:
        self._reference_z = self._last_visit_z = self._mmc.getZPosition()
        self._update_live_marker()

    def _visit_bottom(self) -> None:
        self._visit_bound("bottom")

    def _visit_top(self) -> None:
        self._visit_bound("top")

    def _visit_center(self) -> None:
        self._visit_bound("center")

    def _visit_reference(self) -> None:
        self._visit_bound("reference")

    def _visit_bound(
        self, bound: Literal["top", "bottom", "center", "reference"]
    ) -> None:
        if self._mode is Mode.TOP_BOTTOM:
            top, bottom = self.top.value(), self.bottom.value()
            reference = (top + bottom) / 2
        else:
            current = self._mmc.getZPosition()
            # Keep the live preview anchored. Outside polling, an external move
            # establishes a new relative reference.
            if self._reference_z is None or (
                not self.poll_z.isChecked()
                and (
                    self._last_visit_z is None
                    or not isclose(
                        current, self._last_visit_z, rel_tol=0, abs_tol=0.001
                    )
                )
            ):
                self._reference_z = current
            assert self._reference_z is not None
            reference = self._reference_z
            if self._mode is Mode.RANGE_AROUND:
                above = below = self.range.value() / 2
            else:
                above, below = self.above.value(), self.below.value()
            top, bottom = self._reference_z + above, self._reference_z - below

        target = {
            "top": top,
            "bottom": bottom,
            "center": (top + bottom) / 2,
            "reference": reference,
        }[bound]
        self._mmc.setZPosition(target)
        self._last_visit_z = target

    @Slot()
    def _update_suggested_step(self) -> None:
        self.setSuggestedStep(_suggested_step_from_objective(self._mmc))

    @Slot(str, str, object)
    def _on_property_changed(self, device: str, prop: str, _value: object) -> None:
        if prop == "Label" and device in self._mmc.guessObjectiveDevices():
            self._update_suggested_step()

    def _disconnect(self) -> None:
        with suppress(Exception):
            self._mmc.events.systemConfigurationLoaded.disconnect(
                self._update_suggested_step
            )
            self._mmc.events.propertyChanged.disconnect(self._on_property_changed)
            self._mmc.events.systemConfigurationLoaded.disconnect(
                self._reset_live_position
            )
            self._mmc.events.stagePositionChanged.disconnect(
                self._on_stage_position_changed
            )
            self._mmc.mda.events.sequenceStarted.disconnect(self._on_mda_started)
            self._mmc.mda.events.sequenceFinished.disconnect(self._on_mda_finished)


_NA_PATTERN = re.compile(
    r"(?:\bNA\s*[:=_-]?\s*(?P<after>(?:\d+(?:\.\d*)?|\.\d+))"
    r"|(?P<before>(?:\d+(?:\.\d*)?|\.\d+))\s*\bNA\b)",
    re.IGNORECASE,
)


def _suggested_step_from_name(name: str) -> float | None:
    """Return the numeric value explicitly associated with ``NA`` in a name."""
    match = _NA_PATTERN.search(name)
    if match is None:
        return None
    value = float(match.group("after") or match.group("before"))
    return value if 0 < value <= 2 else None


def _suggested_step_from_objective(core: CMMCorePlus) -> float | None:
    """Return the suggestion encoded in the current objective's state label."""
    with suppress(RuntimeError):
        for device in core.guessObjectiveDevices():
            if value := _suggested_step_from_name(core.getStateLabel(device)):
                return value
    return None
