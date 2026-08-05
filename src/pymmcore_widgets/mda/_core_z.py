from __future__ import annotations

import re
from contextlib import suppress
from typing import TYPE_CHECKING, Literal

from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import Qt, Slot

from pymmcore_widgets.useq_widgets._z import (
    ROW_FIRST_BOUND,
    ROW_SECOND_BOUND,
    Mode,
    ZPlanWidget,
)

from ._xy_bounds import MarkVisit

if TYPE_CHECKING:
    from qtpy.QtWidgets import QWidget


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
        self.bottom_btn = MarkVisit(
            "mdi:arrow-collapse-down", mark_text="Mark Bottom", icon_size=16
        )
        self.top_btn = MarkVisit(
            "mdi:arrow-collapse-up", mark_text="Mark Top", icon_size=16
        )

        super().__init__(parent)
        self._mmc = mmcore or CMMCorePlus.instance()

        self.bottom_btn.mark.clicked.connect(self._mark_bottom)
        self.top_btn.mark.clicked.connect(self._mark_top)
        self.bottom_btn.visit.clicked.connect(self._visit_bottom)
        self.top_btn.visit.clicked.connect(self._visit_top)

        mark_width = max(
            self.bottom_btn.mark.sizeHint().width(),
            self.top_btn.mark.sizeHint().width(),
        )
        visit_width = max(
            self.bottom_btn.visit.sizeHint().width(),
            self.top_btn.visit.sizeHint().width(),
        )
        for buttons in (self.bottom_btn, self.top_btn):
            buttons.mark.setFixedWidth(mark_width)
            buttons.visit.setFixedWidth(visit_width)

        self._grid_layout.addWidget(
            self.top_btn, ROW_FIRST_BOUND, 2, Qt.AlignmentFlag.AlignLeft
        )
        self._grid_layout.addWidget(
            self.bottom_btn, ROW_SECOND_BOUND, 2, Qt.AlignmentFlag.AlignLeft
        )

        bound_height = max(
            self.top.sizeHint().height(),
            self.top_btn.sizeHint().height(),
            self.bottom_btn.sizeHint().height(),
        )
        self._grid_layout.setRowMinimumHeight(ROW_FIRST_BOUND, bound_height)
        self._grid_layout.setRowMinimumHeight(ROW_SECOND_BOUND, bound_height)

        # Reserve enough height for the Mark/Visit controls even when the
        # initial mode keeps them hidden.
        self.top_btn.show()
        self.bottom_btn.show()
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
        super().setMode(mode)
        self.bottom_btn.setVisible(self._mode == Mode.TOP_BOTTOM)
        self.top_btn.setVisible(self._mode == Mode.TOP_BOTTOM)

    def _mark_bottom(self) -> None:
        self.bottom.setValue(self._mmc.getZPosition())

    def _mark_top(self) -> None:
        self.top.setValue(self._mmc.getZPosition())

    def _visit_bottom(self) -> None:
        self._mmc.setZPosition(self.bottom.value())

    def _visit_top(self) -> None:
        self._mmc.setZPosition(self.top.value())

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
