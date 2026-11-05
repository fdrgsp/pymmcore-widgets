from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from vispy import scene

from pymmcore_widgets._util import GREEN
from pymmcore_widgets.control._stage_explorer._stage_viewer import StageViewer

if TYPE_CHECKING:
    from qtpy.QtWidgets import QWidget

    from ._plate_survey import SurveyGeometry, SurveyTile
    from ._well_detection import DetectedWell


class SurveyCanvas(StageViewer):
    """Stage Explorer canvas with a separate, toggleable well-detection layer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._detections = scene.Node(parent=self.view.scene)
        self._detections.order = 1_000_000
        self._outlines = scene.visuals.Line(
            parent=self._detections, color=GREEN, method="gl", width=2
        )
        self._centers = scene.visuals.Markers(parent=self._detections)
        self._labels = scene.visuals.Text(
            parent=self._detections,
            color=GREEN,
            font_size=10,
            anchor_x="left",
            anchor_y="bottom",
        )
        for visual in (self._outlines, self._centers, self._labels):
            visual.set_gl_state("translucent", depth_test=False)

    def add_tile(self, tile: SurveyTile, geometry: SurveyGeometry) -> None:
        """Display a raw frame at its stage location as soon as it is acquired."""
        matrix = np.eye(4)
        matrix[:2, :2] = geometry.matrix()
        matrix[:2, 3] = np.asarray(tile.xy) - geometry.matrix() @ (
            np.array(geometry.shape[::-1]) / 2
        )
        self.add_image(tile.image, matrix)
        self.zoom_to_fit()

    def set_detections(
        self, wells: tuple[DetectedWell, ...], labels: list[str]
    ) -> None:
        """Replace only detection visuals, keeping acquired image textures intact."""
        if not wells:
            self._outlines.set_data(pos=np.empty((0, 2), dtype=np.float32))
            self._centers.set_data(pos=np.empty((0, 2), dtype=np.float32))
            self._labels.text = ""
            return
        outlines = [well.outline() for well in wells]
        points = np.concatenate(outlines)
        # Connect consecutive points within each outline, never across outlines.
        ends = np.cumsum([len(outline) for outline in outlines]) - 1
        starts = np.setdiff1d(np.arange(len(points) - 1), ends)
        connections = np.column_stack([starts, starts + 1])
        self._outlines.set_data(pos=points.astype(np.float32), connect=connections)
        centers = np.array([well.xy for well in wells], dtype=np.float32)
        self._centers.set_data(pos=centers, face_color=GREEN, edge_width=0, size=6)
        self._labels.text = labels
        self._labels.pos = centers

    def set_detection_visible(self, visible: bool) -> None:
        """Show or hide the overlay without altering image visibility."""
        self._detections.visible = visible
        self.canvas.update()

    def clear(self) -> None:
        """Clear both image tiles and detection visuals."""
        super().clear()
        self.set_detections((), [])
