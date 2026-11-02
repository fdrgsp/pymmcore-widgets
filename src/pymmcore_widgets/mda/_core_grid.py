from __future__ import annotations

from typing import TYPE_CHECKING

from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import Slot

from pymmcore_widgets.useq_widgets._grid import GridPlanWidget, Mode

from ._xy_bounds import CoreXYBoundsControl

if TYPE_CHECKING:
    from qtpy.QtWidgets import QWidget


class CoreConnectedGridPlanWidget(GridPlanWidget):
    """[GridPlanWidget](../GridPlanWidget#) connected to a Micro-Manager core instance.

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
        self._mmc = mmcore or CMMCorePlus.instance()
        self._core_xy_bounds = CoreXYBoundsControl(
            core=self._mmc,
            compact_layout=True,
        )

        super().__init__(parent)

        # replace self._mode_to_widget[Mode.BOUNDS] with self._core_xy_bounds
        self._mode_to_widget[Mode.BOUNDS] = self._core_xy_bounds

        # remove self.bounds_wdg from GridPlanWidget
        self._stack.removeWidget(self.bounds_wdg)
        self.bounds_wdg.hide()
        # add CoreXYBoundsControl widget to GridPlanWidget
        self._stack.addWidget(self._core_xy_bounds)
        # the base class connected the bounds_wdg replaced above
        self._core_xy_bounds._bounds_wdg.valueChanged.connect(self._on_change)
        field_width = self.row_col_wdg.rows.minimumWidth()
        for field in (
            self._core_xy_bounds.left,
            self._core_xy_bounds.top,
            self._core_xy_bounds.right,
            self._core_xy_bounds.bottom,
        ):
            field.setMinimumWidth(field_width)
        # Edges/Corners goes in the left column, above the summary, which
        # keeps the bounds page to its four rows like the other modes
        self._core_xy_bounds._bounds_by_label.hide()
        self._add_mode_option(Mode.BOUNDS, "Bounds by", self._core_xy_bounds._bounds_by)
        # and its fields line up with the other modes' fields
        self._extra_aligned_labels.extend(
            (label, (edge_text, corner_text))
            for label, edge_text, corner_text in (
                self._core_xy_bounds._compact_field_labels
            )
        )
        self._align_label_columns()
        self._stack.setStableWidgets(
            self.row_col_wdg,
            self.width_height_wdg,
            self._core_xy_bounds,
        )

        self._mmc.events.systemConfigurationLoaded.connect(self._update_fov_size)
        self._mmc.events.pixelSizeChanged.connect(self._update_fov_size)
        self._mmc.events.roiSet.connect(self._update_fov_size)
        self._update_fov_size()

    @Slot()
    def _update_fov_size(self) -> None:
        """Update the FOV size in the grid plan widget."""
        if px := self._mmc.getPixelSizeUm():
            self.setFovWidth(self._mmc.getImageWidth() * px)
            self.setFovHeight(self._mmc.getImageHeight() * px)
