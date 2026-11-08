from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from weakref import WeakKeyDictionary

import cmap
import numpy as np
import vispy
import vispy.scene
import vispy.visuals
from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import QLabel, QMenu, QVBoxLayout, QWidget
from vispy import scene
from vispy.scene.visuals import Image

from pymmcore_widgets._util import own_vispy_init_context

from ._overview import OverviewPyramid, downsampled, scaled

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from qtpy.QtGui import QMouseEvent
    from vispy.app.canvas import MouseEvent
    from vispy.scene.widgets import ViewBox

    class VisualNode(vispy.scene.Node, vispy.visuals.Visual): ...


# Coarsest downsampling factor; beyond this a tile is a few pixels on screen.
MAX_DOWNSAMPLE = 64
DOWNSAMPLE_DELAY_MS = 150
# Draw the multiscale overview instead of the tiles only when more than this
# many tiles are in view. Below it, per-tile drawing is cheap (vispy's cost is
# per visual) and shows each tile exactly.
OVERVIEW_MIN_TILES = 32
# ... and only when that saves draws: at most this fraction of the tiles in view
# as overview chunks. Sparse, scattered tiles mostly get a chunk each, so their
# overview would cost memory without being faster.
OVERVIEW_MAX_CHUNK_RATIO = 0.5
# Coalesce re-evaluating overview vs. tiles while tiles stream in.
OVERVIEW_REFRESH_MS = 100


Factor = tuple[int, int]  # (rows, columns)


def _image_transform(transform: np.ndarray) -> np.ndarray:
    """Validate an image transform and normalize it to VisPy's row-vector form."""
    transform = np.asarray(transform)
    if transform.shape != (4, 4):
        raise ValueError("Transform must be a 4x4 matrix.")
    return transform.T if np.allclose(transform[-1], (0, 0, 0, 1)) else transform


class _TileSource:
    """An image's full-resolution data, placement, and displayed factor."""

    __slots__ = ("data", "factor", "matrix")

    def __init__(self, data: np.ndarray, matrix: np.ndarray, factor: Factor) -> None:
        self.data = data
        self.matrix = matrix  # vispy (row-vector) pixel -> world transform
        self.factor = factor


class StageViewer(QWidget):
    """A widget to add images with a transform to a vispy canves."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Stage Explorer")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._clims: tuple[float, float] | None = None
        self._cmap: cmap.Colormap = cmap.Colormap("gray")

        # Keep newer images on top without scanning the scene for its lowest order.
        self._next_order: int = 0
        # Image nodes are frozen; weak mappings hold caches without retaining nodes.
        _WorldRect = tuple[float, float, float, float]
        self._world_rects: WeakKeyDictionary[Image, _WorldRect] = WeakKeyDictionary()
        # Retain full-resolution sources while displaying downsampled textures.
        self._sources: WeakKeyDictionary[Image, _TileSource] = WeakKeyDictionary()
        self._downsample: bool = True
        # Wait until zooming settles before uploading textures at a new resolution.
        self._downsample_timer = QTimer(self)
        self._downsample_timer.setSingleShot(True)
        self._downsample_timer.setInterval(DOWNSAMPLE_DELAY_MS)
        self._downsample_timer.timeout.connect(self.apply_downsampling)

        self.canvas = vispy.scene.SceneCanvas(show=True)
        own_vispy_init_context(self.canvas)

        self.view = cast("ViewBox", self.canvas.central_widget.add_view())
        self.view.camera = scene.PanZoomCamera(aspect=1)
        self.view.camera.flip = (False, False)
        self.view.scene.transform.changed.connect(self._on_scene_transform_changed)

        self._grid_lines = vispy.scene.GridLines(
            parent=self.view.scene,
            color="#888888",
            border_width=1,
        )
        self._grid_lines.visible = False

        # Reduced, chunked copies of the map drawn instead of the tiles when
        # many are in view (see _overview.py). Not a direct child Image of the
        # scene, so zoom_to_fit and _get_images only ever see the tiles.
        self._overview = OverviewPyramid(
            self.view.scene, cmap=self._cmap.to_vispy(), clim="auto"
        )
        # Tiles added/refreshed since the overview was last painted, in update
        # order. Painting is deferred to the refresh tick so a location
        # refreshed many times in a row (fast timelapse) is painted once, and
        # adding a tile stays as cheap as before.
        self._overview_pending: dict[Image, None] = {}
        self._overview_timer = QTimer(self)
        self._overview_timer.setSingleShot(True)
        self._overview_timer.setInterval(OVERVIEW_REFRESH_MS)
        self._overview_timer.timeout.connect(self.cull_to_view)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.canvas.native)

        self._show_hover_label = True
        self._hover_pos_label = QLabel(self)
        self._hover_pos_label.setStyleSheet("color: rgba(100, 255, 255, 100); ")
        self._hover_pos_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.canvas.events.mouse_move.connect(self._on_mouse_move)

        # context menu
        self.canvas.native.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.canvas.native.customContextMenuRequested.connect(self._show_context_menu)

    def set_clims(self, clim: tuple[float, float] | None) -> None:
        """Set the color limits of the images in the scene."""
        self._clims = clim
        value = "auto" if clim is None else clim
        for child in self._get_images():
            child.clim = value
        self._overview.set_clims(value)

    def set_grid_visible(self, visible: bool) -> None:
        self._grid_lines.visible = visible

    def add_image(self, img: np.ndarray, transform: np.ndarray | None = None) -> Image:
        """Add an image to the scene with the given transform.

        Parameters
        ----------
        img : np.ndarray
            The image to add to the scene. It should be a (Y, X) or (Y, X, 3) array.
        transform : np.ndarray | None
            The transform to apply to the image. It should be a 4x4 matrix.
            If None, the image will be added with the identity transform.
            The transformation is indented to be calculated elsewhere (in higher level
            widgets) based on, e.g., the stage position, pixel size, configuration
            affine, etc.  This is a relatively low-level, direct function.
        """
        transform = np.eye(4) if transform is None else _image_transform(transform)

        # add the image to the scene with the transform
        # texture_format="auto" uses GPUScaledTexture2D so that clim changes
        # only update a shader uniform instead of re-uploading the texture.
        shown, matrix, factor = self._display(img, transform)
        frame = Image(
            shown,
            cmap=self._cmap.to_vispy(),
            parent=self.view.scene,
            clim="auto" if self._clims is None else self._clims,
            texture_format="auto",
        )
        # keep the added image on top of the others
        self._next_order -= 1
        frame.order = self._next_order
        frame.transform = scene.MatrixTransform(matrix=matrix)
        self._sources[frame] = _TileSource(img, transform, factor)
        self._set_world_rect(frame, img.shape, transform)
        self._queue_overview(frame)
        self._apply_cull(frame)
        return frame

    def update_image(
        self, frame: Image, img: np.ndarray, transform: np.ndarray | None = None
    ) -> None:
        """Refresh an existing image's data and placement, reusing its scene node."""
        transform = (
            self._sources[frame].matrix
            if transform is None
            else _image_transform(transform)
        )
        shown, matrix, factor = self._display(img, transform)
        frame.set_data(shown)
        frame.transform = scene.MatrixTransform(matrix=matrix)
        self._sources[frame] = _TileSource(img, transform, factor)
        self._set_world_rect(frame, img.shape, transform)
        # Bring the refreshed tile back to the front, so a location re-imaged
        # after an overlapping neighbour isn't hidden underneath it. Pasting
        # over the overview likewise puts it on top there.
        self._next_order -= 1
        frame.order = self._next_order
        self._queue_overview(frame)
        self._apply_cull(frame)
        frame.update()

    def clear(self) -> None:
        """Clear the scene."""
        for child in reversed(self.view.scene.children):
            if isinstance(child, Image):
                child.parent = None
        self._next_order = 0
        self._overview.reset()
        self._overview_pending.clear()
        self._overview_timer.stop()

    def zoom_to_fit(self, *, margin: float = 0.05) -> None:
        """Recenter the view to the center of all images.

        Parameters
        ----------
        margin : float
            Extra margin to add between the images and the edge of the view.
            This is a percentage of the view size. Default is 0.05 (5%).
        """
        if not (visuals := tuple(self._get_images())):
            return
        x_bounds, y_bounds, *_ = get_vispy_scene_bounds(visuals)
        self.view.camera.set_range(x=x_bounds, y=y_bounds, margin=margin)

    def canvas_to_world(self, canvas_pos: tuple[float, float]) -> tuple[float, float]:
        """Convert canvas coordinates to world coordinates."""
        # map canvas position to world position
        world_x, world_y, *_ = self.view.scene.transform.imap(canvas_pos)
        return world_x, world_y

    def world_to_canvas(self, world_pos: tuple[float, float]) -> tuple[float, float]:
        """Convert world coordinates to canvas coordinates."""
        # map world position to canvas position
        canvas_x, canvas_y, *_ = self.view.scene.transform.map(world_pos)
        return canvas_x, canvas_y

    # --------------------PRIVATE METHODS--------------------

    def _show_context_menu(self, pos: Any) -> None:
        menu = QMenu(self)

        for axis, name in enumerate("XY"):
            action = menu.addAction(f"Flip {name}")
            action.setCheckable(True)
            action.setChecked(self.view.camera.flip[axis])
            action.toggled.connect(lambda on, a=axis: self._set_flip(a, on))

        menu.exec(self.canvas.native.mapToGlobal(pos))

    def _set_flip(self, axis: int, on: bool) -> None:
        flip = list(self.view.camera.flip)
        flip[axis] = on
        self.view.camera.flip = tuple(flip)

    # --------------------CULLING--------------------

    def _on_scene_transform_changed(self, event: Any = None) -> None:
        del event
        self.cull_to_view()
        if self._downsample:
            self._downsample_timer.start()

    # --------------------DOWNSAMPLING--------------------

    def set_downsample(self, enabled: bool) -> None:
        """Show zoomed-out images at reduced resolution (or always at full)."""
        self._downsample = enabled
        # The overview is reduced resolution too: re-pick what to draw first.
        self.cull_to_view()
        self.apply_downsampling(include_hidden=not enabled)

    def apply_downsampling(self, *, include_hidden: bool = False) -> None:
        """Re-upload each visible image at the resolution the zoom level needs.

        A zoomed-out map would otherwise keep a full-resolution texture per
        tile, even though each tile covers only a few hundred screen pixels.
        Off-screen (culled) images keep their current texture until they come
        back into view, which restarts this pass.
        """
        for frame in self._get_images():
            if not (include_hidden or frame.visible):
                continue
            if (source := self._sources.get(frame)) is None:
                continue
            if (
                self._downsample_factor(source.matrix, source.data.shape)
                == source.factor
            ):
                continue
            shown, matrix, source.factor = self._display(source.data, source.matrix)
            frame.set_data(shown)
            frame.transform = scene.MatrixTransform(matrix=matrix)
            frame.update()

    def _display(
        self, img: np.ndarray, matrix: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, Factor]:
        """The array, node transform, and factor to show `img` at this zoom."""
        factor = self._downsample_factor(matrix, img.shape)
        shown = downsampled(img, factor)
        return shown, scaled(matrix, img.shape, shown.shape), factor

    def _world_per_screen_px(self) -> float | None:
        """World length of one physical screen pixel (None before layout)."""
        rect = self._culling_rect()
        screen_px = self.view.size[0] * self.canvas.pixel_scale
        if screen_px < 2 or rect.width == 0:
            return None
        return float(abs(rect.width) / screen_px)  # camera aspect is 1

    def _downsample_factor(self, matrix: np.ndarray, shape: tuple[int, ...]) -> Factor:
        """Per-axis power of two keeping >= 1 texture pixel per screen pixel.

        Each axis uses the world length of one source pixel step along it, so
        an anisotropic or sheared transform never loses detail along its more
        magnified direction.
        """
        if not self._downsample:
            return (1, 1)
        if (world_per_screen_px := self._world_per_screen_px()) is None:
            return (1, 1)

        def _axis(step: np.ndarray, size: int) -> int:
            step_len = float(np.hypot(*step))
            if step_len == 0 or (ratio := world_per_screen_px / step_len) < 2:
                return 1
            return int(min(2 ** int(np.log2(ratio)), MAX_DOWNSAMPLE, size))

        # row-vector transform: a step along x maps to row 0, along y to row 1
        return (_axis(matrix[1, :2], shape[0]), _axis(matrix[0, :2], shape[1]))

    def cull_to_view(self) -> None:
        """Show only what intersects the current camera rect.

        With many tiles in view, draws the overview level matching the zoom
        instead of the tiles (multiscale, as in napari): one draw call per
        on-screen chunk rather than per tile. Visibility skips draw calls
        without changing the bounds used by zoom_to_fit.
        """
        self._overview_timer.stop()
        self._paint_overview()
        rect = self._culling_rect()
        frames = list(self._get_images())
        hits = [self._rect_intersects(frame, rect) for frame in frames]
        level = self._overview_level(sum(hits), rect)
        self._overview.show(level, rect)
        for frame, hit in zip(frames, hits, strict=False):
            # Assign only on change: every assignment emits vispy update
            # events, which dominated panning a large map.
            if frame.visible != (show := hit and level is None):
                frame.visible = show

    def _overview_level(self, n_tiles_in_view: int, rect: Any) -> int | None:
        """The overview level to draw, or None to draw the tiles themselves."""
        if (
            not self._downsample
            or n_tiles_in_view <= OVERVIEW_MIN_TILES
            or not self._overview.has_content
            or (world_per_px := self._world_per_screen_px()) is None
            or (level := self._overview.level_for(world_per_px)) is None
        ):
            return None
        chunks = self._overview.chunks_in(level, rect)
        return level if chunks <= OVERVIEW_MAX_CHUNK_RATIO * n_tiles_in_view else None

    def _apply_cull(self, frame: Image) -> None:
        """Set the initial visibility of a newly added/updated image."""
        frame.visible = self._overview.active_level is None and self._rect_intersects(
            frame, self._culling_rect()
        )
        # A new tile may tip the view over OVERVIEW_MIN_TILES (or a new chunk
        # needs culling): re-evaluate once the burst of updates settles.
        if not self._overview_timer.isActive():
            self._overview_timer.start()

    def _queue_overview(self, frame: Image) -> None:
        """Schedule painting a new/refreshed tile into the overview."""
        # Re-insert so the paint order follows the latest update (newest on top).
        self._overview_pending.pop(frame, None)
        self._overview_pending[frame] = None

    def _paint_overview(self) -> None:
        """Hand the pending tiles to the overview, in update order."""
        pending, self._overview_pending = self._overview_pending, {}
        for frame in pending:
            if (source := self._sources.get(frame)) is None or frame.parent is None:
                continue
            self._overview.set_tile(frame, source.data, source.matrix)

    def overview_nbytes(self) -> int:
        """Approximate memory held by the zoomed-out overview, in bytes."""
        self._paint_overview()  # count what's queued too, so the total is exact
        return self._overview.nbytes()

    def _culling_rect(self) -> Any:
        """The world-space extent actually rendered on screen.

        With aspect=1, PanZoomCamera pads camera.rect to fit the viewport. Its
        private _real_rect includes that padding; fall back if unavailable.
        """
        cam = self.view.camera
        return getattr(cam, "_real_rect", None) or cam.rect

    def _set_world_rect(
        self, frame: Image, shape: tuple[int, ...], matrix: np.ndarray
    ) -> None:
        """Cache bounds when an image moves, avoiding matrix work during pan/zoom."""
        h, w = shape[0], shape[1]
        corners = np.array(
            [[0, 0, 0, 1], [w, 0, 0, 1], [0, h, 0, 1], [w, h, 0, 1]], dtype=float
        )
        world = corners @ matrix
        world = world[:, :3] / world[:, 3, np.newaxis]
        self._world_rects[frame] = (
            float(world[:, 0].min()),
            float(world[:, 0].max()),
            float(world[:, 1].min()),
            float(world[:, 1].max()),
        )

    def _rect_intersects(self, frame: Image, rect: Any) -> bool:
        """Whether a cached world rect overlaps the camera rect."""
        cached = self._world_rects.get(frame)
        if cached is None:  # pragma: no cover - defensive
            return True
        x0, x1, y0, y1 = cached
        # Camera bounds remain normalized when flipped.
        return not (
            x1 < rect.left or x0 > rect.right or y1 < rect.bottom or y0 > rect.top
        )

    def _get_images(self) -> Iterator[Image]:
        """Yield images in the scene."""
        for child in self.view.scene.children:
            if isinstance(child, Image):
                yield child

    def _on_mouse_move(self, event: MouseEvent) -> None:
        if not self._show_hover_label:
            return  # pragma: no cover

        # map canvas position to world position
        world_x, world_y = self.canvas_to_world(event.pos)
        self._hover_pos_label.setText(f"({world_x:.2f}, {world_y:.2f})")
        self._hover_pos_label.adjustSize()

        # move hover label to the mouse position
        # ensure horizontally and vertically within the view
        lbl_width = self._hover_pos_label.width()
        x = event.pos[0] - (lbl_width // 2)
        margin = 5
        x = max(margin, min(x, self.width() - lbl_width - margin))
        y = event.pos[1] - 32
        if y < 8:
            y += 48
        self._hover_pos_label.move(x, y)
        self._hover_pos_label.setVisible(True)

    def leaveEvent(self, a0: QMouseEvent | None) -> None:
        self._hover_pos_label.setVisible(False)  # pragma: no cover


def get_vispy_scene_bounds(
    visuals: Iterable[VisualNode],
) -> tuple[list[float], list[float], list[float]]:
    """Get the bounding box for `visuals` in world coordinates."""
    # tracks: [xmin, xmax], [ymin, ymax], [zmin, zmax]
    bounds = np.array([[np.inf, -np.inf], [np.inf, -np.inf], [np.inf, -np.inf]])

    for obj in visuals:
        (x_min, x_max), (y_min, y_max) = obj.bounds(0), obj.bounds(1)
        local_bounds = np.array(
            [
                [x_min, y_min, 0, 1],
                [x_min, y_max, 0, 1],
                [x_max, y_min, 0, 1],
                [x_max, y_max, 0, 1],
            ]
        )

        # Map local bounds to world coordinates
        transform = obj.node_transform(obj.scene_node)
        world_bounds = transform.map(local_bounds)

        # Convert from homogeneous to 3D coordinates
        world_bounds = world_bounds[:, :3] / world_bounds[:, 3, np.newaxis]

        # Update world bounds
        bounds[:, 0] = np.minimum(bounds[:, 0], world_bounds.min(axis=0))
        bounds[:, 1] = np.maximum(bounds[:, 1], world_bounds.max(axis=0))

    # replace inf values with 0 ... better than -inf
    bounds = np.where(np.isinf(bounds), 0, bounds)

    return tuple(bounds.tolist())
