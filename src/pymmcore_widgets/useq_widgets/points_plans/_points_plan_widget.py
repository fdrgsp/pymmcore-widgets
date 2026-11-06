from __future__ import annotations

import useq
from qtpy.QtCore import Signal
from qtpy.QtWidgets import QHBoxLayout, QWidget

from pymmcore_widgets.useq_widgets.points_plans import RelativePointPlanSelector

from ._well_graphics_view import WellView


class PointsPlanWidget(QWidget):
    """Widget to select the FOVVs per well of the plate.

    This widget allows the user to select the number of FOVs per well, (or to generally
    show a multi-point plan, such as a grid or random points plan, even if not within
    the context of a well plate.)

    The value() method returns the selected plan, one of:
        - [useq.RandomPoints][]
        - [useq.GridFromShape][] (Cover Area, Coverage tab)
        - [useq.GridRowsColumns][] (Cover Area, Grid tab)
        - [useq.RelativePosition][]

    Parameters
    ----------
    plan : useq.RelativeMultiPointPlan | None
        The useq MultiPoint plan to display and edit.
    parent : QWidget | None
        The parent widget.
    """

    valueChanged = Signal(object)

    def __init__(
        self,
        plan: useq.RelativeMultiPointPlan | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent=parent)

        self._selector = RelativePointPlanSelector()
        # aliases
        self.single_pos_wdg = self._selector.single_pos_wdg
        self.random_points_wdg = self._selector.random_points_wdg
        self.cover_area_wdg = self._selector.cover_area_wdg
        self.grid_wdg = self._selector.grid_wdg

        # well geometry (mm), shared with the Cover Area widget (in µm)
        self._well_size_mm: tuple[float | None, float | None] = (None, None)
        self._well_shape: useq.Shape = useq.Shape.ELLIPSE
        # number of selected wells, to show the total number of FOVs
        self._num_wells: int | None = None

        # graphics scene to draw the well and the fovs
        self._well_view = WellView()

        # main
        layout = QHBoxLayout(self)
        layout.addWidget(self._selector, 1)
        layout.addWidget(self._well_view, 2)

        # connect
        self._selector.valueChanged.connect(self._on_selector_value_changed)
        self._well_view.maxPointsDetected.connect(self._on_view_max_points_detected)
        self._well_view.positionClicked.connect(self._on_view_position_clicked)

        if plan is not None:
            self.setValue(plan)

    def value(self) -> useq.RelativeMultiPointPlan:
        """Return the selected plan."""
        return self._selector.value()

    def setValue(self, plan: useq.RelativeMultiPointPlan) -> None:
        """Set the current plan."""
        self._selector.setValue(plan)

    def setWellSize(
        self, width: float | None = None, height: float | None = None
    ) -> None:
        """Set the well size width and/or height in mm."""
        self._well_size_mm = (width, height)
        self._well_view.setWellSize(width, height)
        self._sync_well_geometry()

    def setWellShape(self, shape: useq.Shape | str) -> None:
        """Set the shape of the well.

        Can be a `useq.Shape` enum or the strings "circle", "ellipse",
        "square", or "rectangle".
        """
        if isinstance(shape, str):
            if shape.lower() == "circle":
                shape = useq.Shape.ELLIPSE
            elif shape.lower() == "square":
                shape = useq.Shape.RECTANGLE
        shape = useq.Shape(shape)
        self._well_shape = shape
        self._well_view.setWellCircular(shape == useq.Shape.ELLIPSE)
        self._sync_well_geometry()

    def setSelectedWellCount(self, n: int | None) -> None:
        """Set the number of selected wells (used to show the total FOV count)."""
        self._num_wells = n
        self._update_info(self.value())

    def setPlateRotation(self, rotation: float | None) -> None:
        """Set the plate rotation (degrees, anti-clockwise).

        Cover Area plans rotate the covered area with the plate, while the FOVs stay
        aligned with the stage axes.
        """
        self.cover_area_wdg.setRotation(rotation)

    def _sync_well_geometry(self) -> None:
        width, height = self._well_size_mm
        # convert mm -> µm once, here
        self.cover_area_wdg.setWellGeometry(
            self._well_shape,
            width * 1000 if width else None,
            height * 1000 if height else None,
        )

    def _on_selector_value_changed(self, value: useq.RelativeMultiPointPlan) -> None:
        self._well_view.setPointsPlan(value)
        self._update_info(value)
        self.valueChanged.emit(value)

    def _update_info(self, plan: useq.RelativeMultiPointPlan) -> None:
        """Show the number of FOVs per well (and in total) on the well view."""
        try:
            count = plan.num_positions()
        except ValueError:  # e.g. a coverage plan without FOV size
            self._well_view.setInfoText("")
            return
        text = f"{count} FOV{'s' * (count != 1)} per well"
        if self._num_wells is not None:
            total = count * self._num_wells
            wells = f"{self._num_wells} well{'s' * (self._num_wells != 1)}"
            text += f"\n{total} total ({wells})"
        self._well_view.setInfoText(text)

    def _on_view_max_points_detected(self, value: int) -> None:
        self.random_points_wdg.num_points.setValue(value)

    def _on_view_position_clicked(self, position: useq.RelativePosition) -> None:
        if self._selector.active_plan_type is useq.RandomPoints:
            pos_no_name = position.model_copy(update={"name": ""})
            self.random_points_wdg.start_at = pos_no_name
