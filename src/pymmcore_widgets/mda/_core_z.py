from __future__ import annotations

import re
from contextlib import suppress
from math import isclose
from typing import Literal

from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QSize, Qt, Slot
from qtpy.QtWidgets import QGridLayout, QHBoxLayout, QSizePolicy, QToolButton, QWidget
from superqt.iconify import QIconifyIcon

from pymmcore_widgets.useq_widgets._z import (
    ROW_FIRST_BOUND,
    ROW_SECOND_BOUND,
    Mode,
    ZPlanWidget,
)

from ._xy_bounds import ICONS_GO

ICON_SIZE = 16
ICONS_MARK_V2 = {
    "top": "mingcute:arrow-to-up-fill",
    "bottom": "mingcute:arrow-to-down-fill",
}


class MarkVisit(QWidget):
    def __init__(
        self,
        mark_top: bool = True,
        icon_size: int = ICON_SIZE,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        mode = "top" if mark_top else "bottom"

        self.mark = QToolButton()
        self.mark.setIcon(QIconifyIcon(ICONS_MARK_V2[mode]))
        self.mark.setIconSize(QSize(icon_size, icon_size))
        self.mark.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mark.setToolTip(f"Mark {mode}.")
        self.mark.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect)

        self.visit = QToolButton()
        self.visit.setIcon(QIconifyIcon(ICONS_GO[mode]))
        self.visit.setIconSize(QSize(icon_size, icon_size))
        self.visit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.visit.setToolTip(f"Move to {mode}.")
        self.visit.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect)

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
        self.center_btn = QToolButton()
        self.center_btn.setIcon(QIconifyIcon("mingcute:dividing-line-fill"))
        self.center_btn.setToolTip("Move to center of range.")
        self.reference_btn = QToolButton()
        self.reference_btn.setIcon(QIconifyIcon("mdi:restore"))
        self.reference_btn.setToolTip("Return to reference Z (starting position).")
        for button in (self.center_btn, self.reference_btn):
            button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect)
        self._reference_z: float | None = None
        self._last_visit_z: float | None = None

        self._move_buttons = QWidget()
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

        self.bottom_btn.mark.clicked.connect(self._mark_bottom)
        self.top_btn.mark.clicked.connect(self._mark_top)
        self.bottom_btn.visit.clicked.connect(self._visit_bottom)
        self.top_btn.visit.clicked.connect(self._visit_top)
        self.center_btn.clicked.connect(self._visit_center)
        self.reference_btn.clicked.connect(self._visit_reference)

        self._grid_layout.addWidget(
            self._move_buttons, ROW_FIRST_BOUND, 2, 2, 1, Qt.AlignmentFlag.AlignLeft
        )
        # Keep the symmetric range description to the right of its move controls.
        self._grid_layout.addWidget(self._range_div2_lbl, ROW_FIRST_BOUND, 3, 2, 1)

        bound_height = max(
            self.top.sizeHint().height(),
            self.top_btn.sizeHint().height(),
            self.bottom_btn.sizeHint().height(),
        )
        self._grid_layout.setRowMinimumHeight(ROW_FIRST_BOUND, bound_height)
        self._grid_layout.setRowMinimumHeight(ROW_SECOND_BOUND, bound_height)

        self._sync_visual_height()
        self.setMode(self._mode)

        self._mmc.events.systemConfigurationLoaded.connect(self._update_suggested_step)
        self._mmc.events.propertyChanged.connect(self._on_property_changed)
        self.destroyed.connect(self._disconnect)
        self._update_suggested_step()

    def setMode(
        self,
        mode: Mode | Literal["top_bottom", "range_around", "above_below"],
    ) -> None:
        mode = Mode(mode)
        if mode != self._mode:
            self._reference_z = self._last_visit_z = None
        super().setMode(mode)
        absolute = self._mode is Mode.TOP_BOTTOM
        for buttons in (self.top_btn, self.bottom_btn):
            buttons.mark.setVisible(absolute)
        for widget in (
            self.top_btn,
            self.bottom_btn,
            self.center_btn,
            self.reference_btn,
        ):
            self._move_layout.removeWidget(widget)
        if self._mode is not Mode.RANGE_AROUND:
            self._move_layout.addWidget(self.top_btn, 0, 0)
            self._move_layout.addWidget(self.bottom_btn, 1, 0)
            self._move_layout.addWidget(self.center_btn, 0, 1, 2, 1)
        else:
            self._move_layout.addWidget(self.top_btn, 0, 0)
            self._move_layout.addWidget(self.bottom_btn, 0, 1)
            self._move_layout.addWidget(self.center_btn, 0, 2)
        self.reference_btn.setVisible(self._mode is Mode.ABOVE_BELOW)
        if self._mode is Mode.ABOVE_BELOW:
            self._move_layout.addWidget(self.reference_btn, 0, 2, 2, 1)
        alignment = Qt.AlignmentFlag.AlignLeft
        if self._mode is Mode.RANGE_AROUND:
            alignment |= Qt.AlignmentFlag.AlignVCenter
        self._grid_layout.setAlignment(self._move_buttons, alignment)
        self._sync_visual_height()

    def _mark_bottom(self) -> None:
        self.bottom.setValue(self._mmc.getZPosition())

    def _mark_top(self) -> None:
        self.top.setValue(self._mmc.getZPosition())

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
            # A move outside these controls establishes a new relative reference.
            if self._last_visit_z is None or not isclose(
                current, self._last_visit_z, rel_tol=0, abs_tol=0.001
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
