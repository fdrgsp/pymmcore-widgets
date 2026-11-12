from __future__ import annotations

import logging
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

import cmap
import numpy as np
from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import QLabel, QMenu, QVBoxLayout, QWidget
from vispy import scene
from vispy.scene.visuals import Image

from pymmcore_widgets._util import own_vispy_init_context

from ._image_processing import ImageProcessor, PreparedImages
from ._overview import (
    BASE_FACTOR,
    GUI_BATCH_ITEMS,
    GUI_BATCH_SECONDS,
    MAX_OVERVIEW_BYTES,
    Bounds,
    OverviewPyramid,
    _moved,
    intersects,
    pixel_steps,
    pow2_at_most,
    scaled,
    world_bounds,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    import vispy.visuals
    from qtpy.QtGui import QMouseEvent
    from vispy.app.canvas import MouseEvent
    from vispy.scene.widgets import ViewBox

    class VisualNode(scene.Node, vispy.visuals.Visual): ...


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
# Hidden images kept for tiles coming (back) into view: enough to switch from
# the overview back to the tiles without creating any.
MAX_SPARE_IMAGES = OVERVIEW_MIN_TILES
# Tiles downsampled per worker job: shown together, in one repaint, rather than
# each repainting every tile in view. Bounded so a zoom change waits less.
SAMPLE_BATCH = 8


logger = logging.getLogger(__name__)
Factor = tuple[int, int]  # (rows, columns)


def _image_transform(transform: np.ndarray) -> np.ndarray:
    """Validate an image transform and normalize it to VisPy's row-vector form."""
    transform = np.asarray(transform)
    if transform.shape != (4, 4):
        raise ValueError("Transform must be a 4x4 matrix.")
    return transform.T if np.allclose(transform[-1], (0, 0, 0, 1)) else transform


class Tile:
    """An image on the map: full-resolution data, placement and stacking rank.

    vispy spends time on every visual in the scene at each camera move, drawn or
    not, so tiles are not visuals themselves: one in view is drawn by an `Image`
    lent from the viewer's pool. Out of view, `image` may still hold the last
    one it was drawn with, to be reused as is while no other tile claims it.
    """

    __slots__ = (
        "bounds",
        "data",
        "factor",
        "image",
        "index",
        "matrix",
        "prepared",
        "rank",
    )

    data: np.ndarray
    matrix: np.ndarray  # vispy (row-vector) pixel -> world transform
    bounds: Bounds

    def __init__(self, index: int) -> None:
        self.index = index  # row in the viewer's bounds array
        self.rank = 0  # drawn above tiles of lower rank
        self.prepared: tuple[Factor, np.ndarray] | None = None
        self.image: Image | None = None
        # Downsampling of the data `image` holds; None if it holds stale data.
        self.factor: Factor | None = None


class StageViewer(QWidget):
    """A widget to add images with a transform to a vispy canvas."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Stage Explorer")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._clims: tuple[float, float] | None = None
        self._cmap = cmap.Colormap("gray").to_vispy()

        self._tiles: list[Tile] = []
        # World (x0, x1, y0, y1) of each tile by `Tile.index`, to cull at once.
        self._bounds = np.empty((0, 4))
        self._next_rank = 0
        self._drawn: set[Tile] = set()
        # Hidden pool images, least recently used first, and their last tile.
        self._spare: dict[Image, Tile] = {}
        self._downsample: bool = True
        # Wait until zooming settles before uploading textures at a new resolution.
        self._downsample_timer = QTimer(self)
        self._downsample_timer.setSingleShot(True)
        self._downsample_timer.setInterval(DOWNSAMPLE_DELAY_MS)
        self._downsample_timer.timeout.connect(self.apply_downsampling)

        self.canvas = scene.SceneCanvas(show=True)
        own_vispy_init_context(self.canvas)

        self.view = cast("ViewBox", self.canvas.central_widget.add_view())
        self.view.camera = scene.PanZoomCamera(aspect=1)
        self.view.camera.flip = (False, False)
        self.view.scene.transform.changed.connect(self._on_scene_transform_changed)

        self._grid_lines = scene.GridLines(
            parent=self.view.scene, color="#888888", border_width=1
        )
        self._grid_lines.visible = False

        # Below the grid lines and overlays (order 0); tiles drawn in rank order.
        self._tile_layer = scene.Node(parent=self.view.scene)
        self._tile_layer.order = -1
        # Reduced, chunked copies of the map drawn instead of the tiles when
        # many are in view (see _overview.py).
        self._overview = OverviewPyramid(self.view.scene, cmap=self._cmap, clim="auto")
        # Tiles added/refreshed since the overview was last painted, in update
        # order. Painting is deferred to the refresh tick so a location
        # refreshed many times in a row (fast timelapse) is painted once.
        self._overview_pending: dict[Tile, None] = {}
        self._overview_timer = QTimer(self)
        self._overview_timer.setSingleShot(True)
        self._overview_timer.setInterval(OVERVIEW_REFRESH_MS)
        self._overview_timer.timeout.connect(self._process_images)
        self._processor = ImageProcessor()
        self.destroyed.connect(self._processor.close)
        self._epoch = 0
        self._paused = False
        self._job: (
            tuple[int, Tile | None, int, list[tuple[Tile, int, Factor]]] | None
        ) = None
        self._sample_pending: dict[Tile, Factor] = {}
        self._refresh_pending: set[Tile] = set()
        self._overview_limit = MAX_OVERVIEW_BYTES
        self._base_res_hint: float | None = None
        self._requested_level: int | None = None
        self._processing_timer = QTimer(self)
        self._processing_timer.setInterval(5)
        self._processing_timer.timeout.connect(self._process_images)
        self._display_timer = QTimer(self)
        self._display_timer.setSingleShot(True)
        self._display_timer.setInterval(1)
        self._display_timer.timeout.connect(self.cull_to_view)

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

        self.canvas.native.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.canvas.native.customContextMenuRequested.connect(self._show_context_menu)

    def set_clims(self, clim: tuple[float, float] | None) -> None:
        """Set the color limits of the images in the scene."""
        self._clims = clim
        value = "auto" if clim is None else clim
        for image in self._images():
            image.clim = value
        self._overview.set_clims(value)

    def set_grid_visible(self, visible: bool) -> None:
        self._grid_lines.visible = visible

    def add_image(self, img: np.ndarray, transform: np.ndarray | None = None) -> Tile:
        """Add an image to the scene with the given transform.

        Parameters
        ----------
        img : np.ndarray
            The image to add to the scene. It should be a (Y, X) or (Y, X, 3) array.
        transform : np.ndarray | None
            The transform to apply to the image. It should be a 4x4 matrix.
            If None, the image will be added with the identity transform.
            The transformation is intended to be calculated elsewhere (in higher
            level widgets) based on, e.g., the stage position, pixel size,
            configuration affine, etc.  This is a relatively low-level, direct
            function.
        """
        tile = Tile(len(self._tiles))
        self._tiles.append(tile)
        matrix = np.eye(4) if transform is None else _image_transform(transform)
        self._place(tile, img, matrix)
        return tile

    def update_image(
        self, tile: Tile, img: np.ndarray, transform: np.ndarray | None = None
    ) -> None:
        """Refresh a tile's data and, if given, its placement."""
        matrix = tile.matrix if transform is None else _image_transform(transform)
        self._place(tile, img, matrix)

    def clear(self) -> None:
        """Clear the scene."""
        self._epoch += 1
        self._processor.reset()
        self._sample_pending.clear()
        self._refresh_pending.clear()
        self._base_res_hint = None
        self._requested_level = None
        self._display_timer.stop()
        self._downsample_timer.stop()
        for image in self._images():
            image.parent = None
        self._tiles.clear()
        self._drawn.clear()
        self._spare.clear()
        self._bounds = np.empty((0, 4))
        self._next_rank = 0
        self._overview.reset()
        self._overview_pending.clear()
        self._overview_timer.stop()

    def zoom_to_fit(
        self, *, margin: float = 0.05, include: Iterable[VisualNode] = ()
    ) -> None:
        """Fit the view to all images, and to the `include` visuals.

        Parameters
        ----------
        margin : float
            Extra margin to add between the images and the edge of the view.
            This is a percentage of the view size. Default is 0.05 (5%).
        include : Iterable[VisualNode]
            Other visuals to fit in the view too (e.g. markers).
        """
        bounds = np.vstack(
            [self._bounds[: len(self._tiles)], *map(visual_bounds, include)]
        )
        if not len(bounds):
            return
        x0, _, y0, _ = bounds.min(axis=0)
        _, x1, _, y1 = bounds.max(axis=0)
        self.view.camera.set_range(x=(x0, x1), y=(y0, y1), margin=margin)

    def canvas_to_world(self, canvas_pos: tuple[float, float]) -> tuple[float, float]:
        """Convert canvas coordinates to world coordinates."""
        world_x, world_y, *_ = self.view.scene.transform.imap(canvas_pos)
        return world_x, world_y

    def world_to_canvas(self, world_pos: tuple[float, float]) -> tuple[float, float]:
        """Convert world coordinates to canvas coordinates."""
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

    def _on_scene_transform_changed(self, _event: Any = None) -> None:
        self.cull_to_view()
        if self._downsample:
            self._downsample_timer.start()

    # --------------------TILES--------------------

    def _place(self, tile: Tile, data: np.ndarray, matrix: np.ndarray) -> None:
        """Set a tile's data and placement, and bring it to the front.

        A re-imaged location must not stay hidden under an overlapping neighbour.
        """
        # Own the source buffers: acquisition callers may reuse their arrays
        # immediately after returning. Workers and rendering only read these.
        data = np.array(data, copy=True)
        matrix = np.array(matrix, copy=True)
        data.flags.writeable = matrix.flags.writeable = False
        bounds = world_bounds(data.shape, matrix)
        rows, cols = pixel_steps(matrix)
        base = BASE_FACTOR * min(rows, cols)
        if self._base_res_hint is None or base > 2 * self._base_res_hint:
            self._base_res_hint = base
            self._epoch += 1
        elif hasattr(tile, "matrix") and (
            tile.data.shape != data.shape
            or not np.allclose(tile.matrix[:2, :2], matrix[:2, :2])
            or _moved(tile.bounds, bounds, self._base_res_hint)
        ):
            self._epoch += 1
        tile.data, tile.matrix = data, matrix
        tile.prepared = None
        tile.bounds = bounds
        if tile.index >= len(self._bounds):
            grown = np.empty((max(64, 2 * len(self._bounds)), 4))
            grown[: len(self._bounds)] = self._bounds
            self._bounds = grown
        self._bounds[tile.index] = tile.bounds
        self._next_rank += 1
        tile.rank = self._next_rank
        tile.factor = None
        self._sample_pending.pop(tile, None)
        # Re-insert so the overview paint order follows the updates (newest on top).
        self._overview_pending.pop(tile, None)
        self._overview_pending[tile] = None

        if tile in self._drawn:
            self._show(tile)
        elif self._overview.active_level is None and intersects(
            tile.bounds, self._culling_rect()
        ):
            self._bind(tile)
        # A new tile may tip the view over OVERVIEW_MIN_TILES (or a new chunk
        # needs culling): re-evaluate once the burst of updates settles.
        if not self._overview_timer.isActive():
            self._overview_timer.start()

    def _bind(self, tile: Tile) -> None:
        """Draw `tile` with its last image if still spare, else the oldest spare."""
        if tile.image is not None:
            del self._spare[tile.image]
        elif self._spare:
            tile.image = self._take_spare()
        else:
            tile.image = Image(
                cmap=self._cmap,
                clim="auto" if self._clims is None else self._clims,
                parent=self._tile_layer,
                # clim changes then only update a shader uniform, no re-upload
                texture_format="auto",
            )
            # painter's order: tiles of higher rank are drawn later, on top
            tile.image.set_gl_state("translucent", depth_test=False)
            tile.image.transform = scene.MatrixTransform()
        tile.image.visible = True
        self._drawn.add(tile)
        self._show(tile)

    def _release(self, tile: Tile) -> None:
        """Stop drawing `tile`, keeping its image as the newest spare."""
        image = cast("Image", tile.image)
        image.visible = False
        self._drawn.discard(tile)
        self._spare[image] = tile

    def _take_spare(self) -> Image:
        """Remove the least recently used spare image from its last tile."""
        image = next(iter(self._spare))
        previous = self._spare.pop(image)
        previous.image = previous.factor = None
        return image

    def _show(self, tile: Tile) -> None:
        """Upload a drawn tile at the resolution the zoom needs, in rank order."""
        image = cast("Image", tile.image)
        factor = self._downsample_factor(tile)
        if factor != tile.factor:
            if factor == (1, 1):
                shown = tile.data
            elif tile.prepared is not None and tile.prepared[0] == factor:
                shown = tile.prepared[1]
            else:
                self._sample_pending[tile] = factor
                self._processing_timer.start()
                # Never display pixels left over from another tile in the pool.
                image.visible = tile.factor is not None
                shown = None
            if shown is not None:
                tile.factor = factor
                image.set_data(shown)
                image.transform.matrix = scaled(
                    tile.matrix, tile.data.shape, shown.shape
                )
                image.visible = True
        if image.order != tile.rank:
            image.order = tile.rank
            # vispy caches the draw order and only refreshes it when nodes are
            # added or removed, not when their order changes
            self.canvas._draw_order.clear()

    def _images(self) -> Iterator[Image]:
        """The pool's images: drawn, then spare."""
        for tile in self._drawn:
            yield cast("Image", tile.image)
        yield from self._spare

    def cull_to_view(self) -> None:
        """Draw only what intersects the current camera rect.

        With many tiles in view, draws the overview level matching the zoom
        instead of the tiles (multiscale, as in napari): one draw call per
        on-screen chunk rather than per tile.
        """
        rect = self._culling_rect()
        b = self._bounds[: len(self._tiles)]
        hits = np.flatnonzero(
            (b[:, 1] >= rect.left)
            & (b[:, 0] <= rect.right)
            & (b[:, 3] >= rect.bottom)
            & (b[:, 2] <= rect.top)
        )
        level = self._overview_level(len(hits), rect)
        self._requested_level = level
        if level is not None and not self._overview._levels[level].built:
            self._processing_timer.start()
            # Keep drawing the current tiles until the prepared overview arrives.
            level = None
        more = self._overview.show(level, rect)

        wanted = set() if level is not None else {self._tiles[i] for i in hits.tolist()}
        for tile in self._drawn - wanted:
            self._release(tile)
        started = perf_counter()
        for _ in range(min(GUI_BATCH_ITEMS, len(self._refresh_pending))):
            tile = self._refresh_pending.pop()
            if tile in self._drawn:
                self._show(tile)
            if perf_counter() - started >= GUI_BATCH_SECONDS:
                break
        more |= bool(self._refresh_pending)
        for count, tile in enumerate(wanted - self._drawn):
            if (
                count >= GUI_BATCH_ITEMS
                or perf_counter() - started >= GUI_BATCH_SECONDS
            ):
                more = True
                break
            self._bind(tile)
        if more:
            self._display_timer.start()
        while len(self._spare) > MAX_SPARE_IMAGES:
            self._take_spare().parent = None

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

    def hideEvent(self, event: Any) -> None:
        self._paused = True
        self._processing_timer.stop()
        self._overview_timer.stop()
        self._display_timer.stop()
        self._downsample_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event: Any) -> None:
        self._paused = False
        if self._processor.closed:
            # Qt permits a closed widget to be shown again. Retain the map,
            # rebuilding its CPU cache with a fresh worker on demand.
            self._processor = ImageProcessor()
            self.destroyed.connect(self._processor.close)
            self._overview.reset()
            self._overview_pending = dict.fromkeys(
                sorted(self._tiles, key=lambda t: t.rank)
            )
        self._processing_timer.start()
        self._display_timer.start()
        super().showEvent(event)

    def _process_images(self) -> None:
        """Deliver one result on the GUI thread, then schedule one CPU job."""
        processor = self._processor
        if processor.closed or self._paused:
            self._processing_timer.stop()
            return
        self._processing_timer.start()
        if (future := processor.future) is not None:
            if not future.done():
                return
            processor.future = None
            job, self._job = self._job, None
            processor.accepted = False
            try:
                result = future.result()
            except Exception:
                logger.exception("Stage map image processing failed")
            else:
                if job is not None:
                    epoch, tile, rank, samples = job
                    if epoch != self._epoch or (tile is not None and tile.rank != rank):
                        result.overview = None
                    self._accept_prepared(samples, result)
                    processor.accepted = result.overview is not None
        # Prioritize visible tiles over offscreen overview work.
        samples = []
        while self._sample_pending and len(samples) < SAMPLE_BATCH:
            tile = next(iter(self._sample_pending))
            factor = self._sample_pending.pop(tile)
            if tile in self._drawn and factor == self._downsample_factor(tile):
                samples.append((tile, tile.rank, factor))
        if samples:
            self._job = (self._epoch, None, 0, samples)
            processor.submit(
                samples=[(tile.data, f) for tile, _, f in samples],
                max_bytes=self._overview_limit,
            )
            return
        level = self._requested_level
        if level is not None and level < len(self._overview._levels):
            if not self._overview._levels[level].built:
                self._job = (self._epoch, None, 0, [])
                processor.submit(level=level, max_bytes=self._overview_limit)
                return
        if self._overview_pending:
            tile = next(iter(self._overview_pending))
            del self._overview_pending[tile]
            self._job = (self._epoch, tile, tile.rank, [])
            processor.submit(
                tile.index, tile.data, tile.matrix, max_bytes=self._overview_limit
            )
        else:
            self._processing_timer.stop()

    def _accept_prepared(
        self, samples: list[tuple[Tile, int, Factor]], result: PreparedImages
    ) -> None:
        if result.overview is not None:
            self._overview.accept(result.overview)
        for (tile, rank, factor), shown in zip(samples, result.shown, strict=False):
            # a tile placed again since holds other data
            if tile.rank == rank:
                tile.prepared = (factor, shown)
                if tile in self._drawn:
                    self._show(tile)
        self.cull_to_view()

    def overview_nbytes(self) -> int:
        """Conservative memory estimate, including work not yet prepared.

        Accounting never flushes pending work. Reserve thumbnails and a single
        in-flight frame/result in addition to delivered overview buffers.
        """
        reserve = 0
        for tile in self._overview_pending:
            rows, cols = pixel_steps(tile.matrix)
            res = self._base_res_hint or BASE_FACTOR * min(rows, cols)
            h, w = tile.data.shape[:2]
            fy = pow2_at_most(res / rows, h) if rows else 1
            fx = pow2_at_most(res / cols, w) if cols else 1
            reserve += (
                (h // fy)
                * (w // fx)
                * tile.data.dtype.itemsize
                * int(np.prod(tile.data.shape[2:]))
            )
        if self._processor.future is not None and self._job is not None:
            job_tile = self._job[1]
            if job_tile is not None:
                reserve += job_tile.data.nbytes
        samples = sum(
            tile.prepared[1].nbytes for tile in self._tiles if tile.prepared is not None
        )
        return self._overview.nbytes() + reserve + samples

    def shutdown(self) -> None:
        """Stop timers, discard results, and join the processing worker."""
        self._epoch += 1
        self._processing_timer.stop()
        self._overview_timer.stop()
        self._display_timer.stop()
        self._downsample_timer.stop()
        self._overview_pending.clear()
        self._sample_pending.clear()
        self._refresh_pending.clear()
        self._processor.close()
        self._job = None

    def closeEvent(self, event: Any) -> None:
        self.shutdown()
        super().closeEvent(event)

    def _culling_rect(self) -> Any:
        """The world-space extent actually rendered on screen.

        With aspect=1, PanZoomCamera pads camera.rect to fit the viewport. Its
        private _real_rect includes that padding; fall back if unavailable.
        """
        cam = self.view.camera
        return getattr(cam, "_real_rect", None) or cam.rect

    # --------------------DOWNSAMPLING--------------------

    def set_downsample(self, enabled: bool) -> None:
        """Show zoomed-out images at reduced resolution (or always at full)."""
        self._downsample = enabled
        # The overview is reduced resolution too: re-pick what to draw first.
        self.cull_to_view()
        self.apply_downsampling()

    def apply_downsampling(self) -> None:
        """Re-upload each drawn tile at the resolution the zoom level needs.

        A zoomed-out map would otherwise keep a full-resolution texture per
        tile, even though each tile covers only a few hundred screen pixels.
        """
        self._refresh_pending.update(self._drawn)
        self.cull_to_view()

    def _world_per_screen_px(self) -> float | None:
        """World length of one physical screen pixel (None before layout)."""
        rect = self._culling_rect()
        screen_px = self.view.size[0] * self.canvas.pixel_scale
        if screen_px < 2 or rect.width == 0:
            return None
        return float(abs(rect.width) / screen_px)  # camera aspect is 1

    def _downsample_factor(self, tile: Tile) -> Factor:
        """Per-axis power of two keeping >= 1 texture pixel per screen pixel.

        Each axis uses the world length of one source pixel step along it, so
        an anisotropic or sheared transform never loses detail along its more
        magnified direction.
        """
        if not self._downsample or (px := self._world_per_screen_px()) is None:
            return (1, 1)

        def axis(step: float, size: int) -> int:
            return min(pow2_at_most(px / step, size), MAX_DOWNSAMPLE) if step else 1

        (row, col), (h, w) = pixel_steps(tile.matrix), tile.data.shape[:2]
        return axis(row, h), axis(col, w)

    # --------------------HOVER LABEL--------------------

    def _on_mouse_move(self, event: MouseEvent) -> None:
        if not self._show_hover_label:
            return  # pragma: no cover

        world_x, world_y = self.canvas_to_world(event.pos)
        self._hover_pos_label.setText(f"({world_x:.2f}, {world_y:.2f})")
        self._hover_pos_label.adjustSize()

        # center the label above the cursor, kept within the view
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


def visual_bounds(visual: VisualNode) -> Bounds:
    """World (x0, x1, y0, y1) of a visual."""
    (x0, x1), (y0, y1) = visual.bounds(0), visual.bounds(1)
    corners = np.array([[x0, y0, 0, 1], [x0, y1, 0, 1], [x1, y0, 0, 1], [x1, y1, 0, 1]])
    world = visual.node_transform(visual.scene_node).map(corners)
    world = world[:, :2] / world[:, 3:]
    (wx0, wy0), (wx1, wy1) = world.min(axis=0), world.max(axis=0)
    return float(wx0), float(wx1), float(wy0), float(wy1)
