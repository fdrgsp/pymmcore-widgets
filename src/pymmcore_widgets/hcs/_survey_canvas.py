from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from vispy import scene

from pymmcore_widgets._util import GREEN
from pymmcore_widgets.control._stage_explorer._stage_viewer import StageViewer

from ._plate_survey import bin_image

# The canvas is a preview: cap displayed tiles so large scans fit in GPU memory
# (81 full 2048 px frames are ~1.3 GB of float textures).
DISPLAY_MAX_PX = 512
# A finished scan is shown as one stitched image of at most this many pixels per
# side: one texture and draw call instead of hundreds keeps pan/zoom smooth.
MOSAIC_MAX_PX = 4096

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
        self._overlay = (self._outlines, self._centers, self._labels)
        for visual in self._overlay:
            visual.set_gl_state("translucent", depth_test=False)
            # Nothing to draw yet: drawing empty buffers crashes some GL drivers.
            visual.visible = False

    def add_tile(self, tile: SurveyTile, geometry: SurveyGeometry) -> None:
        """Display a frame at its stage location as soon as it is acquired."""
        factor = max(1, int(np.ceil(max(tile.image.shape) / DISPLAY_MAX_PX)))
        image = bin_image(tile.image, factor)  # center-preserving crop and average
        linear = geometry.matrix() * factor
        matrix = np.eye(4)
        matrix[:2, :2] = linear
        matrix[:2, 3] = np.asarray(tile.xy) - linear @ (np.array(image.shape[::-1]) / 2)
        self.add_image(image, matrix)
        self.zoom_to_fit()

    def show_mosaic(
        self, image: np.ndarray, origin: tuple[float, float], scale: float
    ) -> None:
        """Replace the per-tile previews with one stitched image of the scan."""
        super().clear()
        matrix = np.diag([scale, scale, 1.0, 1.0])
        # mosaic pixel (c, r) is centered at origin + (c, r) * scale
        matrix[:2, 3] = np.asarray(origin) - scale / 2
        self.add_image(image, matrix)
        self.zoom_to_fit()

    def set_detections(
        self, wells: tuple[DetectedWell, ...], labels: list[str]
    ) -> None:
        """Replace only detection visuals, keeping acquired image textures intact."""
        if not wells:
            # Hide rather than upload empty arrays: glDrawElements on a zero-length
            # index buffer is a null read on some drivers (e.g. NVIDIA on Windows).
            for visual in self._overlay:
                visual.visible = False
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
        for visual in self._overlay:
            visual.visible = True

    def set_detection_visible(self, visible: bool) -> None:
        """Show or hide the overlay without altering image visibility."""
        self._detections.visible = visible
        self.canvas.update()

    def clear(self) -> None:
        """Clear both image tiles and detection visuals."""
        super().clear()
        self.set_detections((), [])
