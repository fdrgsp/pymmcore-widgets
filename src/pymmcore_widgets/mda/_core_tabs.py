"""Core-connected containers for the MDA axis editors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymmcore_plus import CMMCorePlus

from pymmcore_widgets.useq_widgets._mda_sequence import MDATabs
from pymmcore_widgets.useq_widgets._time import TimePlanWidget

from ._core_channels import CoreConnectedChannelTable
from ._core_grid import CoreConnectedGridPlanWidget
from ._core_positions import CoreConnectedPositionTable
from ._core_z import CoreConnectedZPlanWidget

if TYPE_CHECKING:
    from qtpy.QtWidgets import QWidget

    from ._save_widget import SaveGroupBox


class _CoreConnectedPositionTable(CoreConnectedPositionTable):
    def __init__(
        self,
        rows: int = 0,
        mmcore: CMMCorePlus | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(rows, mmcore, parent)

        # MDAWidget handles these events with autofocus and Z-plan awareness.
        ev = self._mmc.events
        ev.systemConfigurationLoaded.disconnect(self._on_sys_config_loaded)
        ev.propertyChanged.disconnect(self._on_property_changed)


class CoreMDATabs(MDATabs):
    """Core-aware MDA axis tabs, the presentation ``MDAWidget`` uses.

    The ``MDATabs`` tabs with core-connected editors, plus one further tab after
    the axes -- Saving, checkable the same as an axis (see
    ``add_supporting_sections``).
    """

    channels: CoreConnectedChannelTable

    def __init__(
        self, parent: QWidget | None = None, core: CMMCorePlus | None = None
    ) -> None:
        self._mmc = core or CMMCorePlus.instance()
        self._supporting_sections_added = False
        super().__init__(parent)
        self.z_plan.setStackActive(bool(self.isChecked(self.z_plan)))

    def create_subwidgets(self) -> None:
        self.time_plan = TimePlanWidget(0)
        self.stage_positions = _CoreConnectedPositionTable(0, self._mmc)
        self.z_plan = CoreConnectedZPlanWidget(self._mmc)
        self.grid_plan = CoreConnectedGridPlanWidget(self._mmc)
        self.channels = CoreConnectedChannelTable(0, self._mmc)

    def _on_tab_checked(self, idx: int, checked: bool) -> None:
        # The base class shows/hides Do Stack and Acquire Every purely on their
        # own axis's state. Here both are further gated behind the channel
        # table's "Advanced" toggle, so route each axis's state through the
        # table (which recomputes visibility from both inputs).
        super()._on_tab_checked(idx, checked)
        if idx == self.indexOf(self.z_plan):
            self.z_plan.setStackActive(checked)
            self.channels.setZStackActive(checked)
        elif idx == self.indexOf(self.time_plan):
            self.channels.setTimePlanActive(checked)

    def _enable_tabs(self, enable: bool) -> None:
        """Enable or disable the tab checkboxes and their contents.

        Keep tab navigation available during an acquisition.
        """
        # disable tab checkboxes
        for cbox in self._cboxes:
            cbox.setEnabled(enable)
        # disable tabs contents (the content of an unchecked tab stays disabled).
        # Toggle the editors, not the tab pages: a page may wrap its editor (e.g. a
        # scroll area or a card), and a disabled page would keep the editor disabled
        # after its tab is checked again.
        for wdg in (
            self.time_plan,
            self.stage_positions,
            self.z_plan,
            self.grid_plan,
            self.channels,
        ):
            wdg.setEnabled(enable and self.isChecked(wdg) is not False)
        if self._supporting_sections_added:
            self._save_info.setEnabled(enable)

    def add_supporting_sections(self, *, save_info: SaveGroupBox) -> None:
        """Append the Saving tab after the five axes.

        Sync the tab checkbox with SaveGroupBox and hide its duplicated header.
        """
        if self._supporting_sections_added:
            return

        self._save_info = save_info
        self._add_tab(
            save_info, "Saving", "saving", checked=save_info.isChecked(), card=True
        )
        self.tabChecked.connect(self._on_saving_tab_checked)
        save_info.toggled.connect(self._on_save_info_toggled)

        self._supporting_sections_added = True
        self.apply_save_body_style()

    def _on_saving_tab_checked(self, index: int, checked: bool) -> None:
        """Keep the Saving tab's checkbox and `SaveGroupBox`'s own state in sync."""
        if index != self.indexOf(self._save_info):
            return
        self._save_info.setChecked(checked)

    def _on_save_info_toggled(self, checked: bool) -> None:
        """Sync the tab checkbox; setChecked emits only when its state changes."""
        self.setChecked(self._save_info, checked)

    def apply_save_body_style(self) -> None:
        """Hide the native header; the containing tab or section supplies a toggle."""
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


__all__ = ["CoreMDATabs"]
