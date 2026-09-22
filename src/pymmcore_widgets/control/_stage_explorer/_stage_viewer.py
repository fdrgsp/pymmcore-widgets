from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from weakref import WeakKeyDictionary

import cmap
import numpy as np
import vispy
import vispy.scene
import vispy.visuals
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QLabel, QMenu, QVBoxLayout, QWidget
from vispy import scene
from vispy.scene.visuals import Image

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from PyQt6.QtGui import QMouseEvent
    from vispy.app.canvas import MouseEvent
    from vispy.scene.widgets import ViewBox

    class VisualNode(vispy.scene.Node, vispy.visuals.Visual): ...


class StageViewer(QWidget):
    """A widget to add images with a transform to a vispy canves."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Stage Explorer")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._clims: tuple[float, float] | None = None
        self._cmap: cmap.Colormap = cmap.Colormap("gray")

        # Monotonic draw-order counter. Each new image must sit on top of the
        # previous ones; deriving that from min(child.order) is an O(N) scan of
        # the whole scene on every single added frame, so keep a running value.
        self._next_order: int = 0
        # World-space bounding box per image, used by culling below.
        # A WeakKeyDictionary rather than an attribute on the node: Image
        # is a vispy Frozen class and rejects new attributes after
        # construction, and this way an entry disappears on its own once
        # the node it describes is garbage collected.
        _WorldRect = tuple[float, float, float, float]
        self._world_rects: WeakKeyDictionary[Image, _WorldRect] = WeakKeyDictionary()
        # Whether off-screen images are hidden. vispy issues a draw call for
        # every Image node regardless of whether it intersects the viewport, so
        # a large map costs the same zoomed in as zoomed out (measured: 400
        # tiles = 33ms either way, vs 3.5ms with only the 9 visible ones).
        self._cull_offscreen: bool = True

        self.canvas = vispy.scene.SceneCanvas(show=True)

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
        # normalize the transform
        if transform is None:
            transform = np.eye(4)
        else:
            transform = np.asarray(transform)
            if transform.shape != (4, 4):
                raise ValueError("Transform must be a 4x4 matrix.")
            # vispy uses a column-major order for the transform matrix
            # so we need to transpose it to get the correct order
            if np.allclose(transform[-1], (0, 0, 0, 1)):
                transform = transform.T

        # add the image to the scene with the transform
        # texture_format="auto" uses GPUScaledTexture2D so that clim changes
        # only update a shader uniform instead of re-uploading the texture.
        frame = Image(
            img,
            cmap=self._cmap.to_vispy(),
            parent=self.view.scene,
            clim="auto" if self._clims is None else self._clims,
            texture_format="auto",
        )
        # keep the added image on top of the others
        self._next_order -= 1
        frame.order = self._next_order
        frame.transform = scene.MatrixTransform(matrix=transform)
        self._set_world_rect(frame, img.shape, transform)
        self._apply_cull(frame)
        return frame

    def update_image(
        self, frame: Image, img: np.ndarray, transform: np.ndarray | None = None
    ) -> None:
        """Replace the contents (and placement) of an image already in the scene.

        This is the counterpart to `add_image` for a location that is imaged
        more than once -- a timelapse at a fixed stage position, or a snap
        repeated at the same spot. Re-using the node uploads into the existing
        texture instead of building a whole new scene-graph node, which keeps
        both the node count and the memory footprint bounded by the number of
        *distinct* locations on the map rather than by the number of frames
        acquired.
        """
        frame.set_data(img)
        if transform is not None:
            transform = np.asarray(transform)
            if transform.shape != (4, 4):
                raise ValueError("Transform must be a 4x4 matrix.")
            if np.allclose(transform[-1], (0, 0, 0, 1)):
                transform = transform.T
            frame.transform = scene.MatrixTransform(matrix=transform)
            self._set_world_rect(frame, img.shape, transform)
        else:
            self._set_world_rect(frame, img.shape, np.array(frame.transform.matrix))
        # Bring the refreshed tile back to the front, so a location re-imaged
        # after an overlapping neighbour isn't hidden underneath it.
        self._next_order -= 1
        frame.order = self._next_order
        self._apply_cull(frame)
        frame.update()

    def clear(self) -> None:
        """Clear the scene."""
        for child in reversed(self.view.scene.children):
            if isinstance(child, Image):
                child.parent = None
        self._next_order = 0

    def zoom_to_fit(self, *, margin: float = 0.05) -> None:
        """Recenter the view to the center of all images.

        Parameters
        ----------
        margin : float
            Extra margin to add between the images and the edge of the view.
            This is a percentage of the view size. Default is 0.05 (5%).
        """
        if not (visuals := self._get_images()):
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

        flip_x, flip_y, *_ = self.view.camera.flip

        flip_x_action = menu.addAction("Flip X")
        flip_x_action.setCheckable(True)
        flip_x_action.setChecked(flip_x)
        flip_x_action.toggled.connect(lambda checked: self._set_flip(x=checked))

        flip_y_action = menu.addAction("Flip Y")
        flip_y_action.setCheckable(True)
        flip_y_action.setChecked(flip_y)
        flip_y_action.toggled.connect(lambda checked: self._set_flip(y=checked))

        menu.exec(self.canvas.native.mapToGlobal(pos))

    def _set_flip(self, x: bool | None = None, y: bool | None = None) -> None:
        cur_x, cur_y, cur_z = self.view.camera.flip
        self.view.camera.flip = (
            x if x is not None else cur_x,
            y if y is not None else cur_y,
            cur_z,
        )

    # --------------------CULLING--------------------

    def set_cull_offscreen(self, enabled: bool) -> None:
        """Hide (or stop hiding) images that don't intersect the viewport."""
        self._cull_offscreen = enabled
        self.cull_to_view()

    def _on_scene_transform_changed(self, event: Any = None) -> None:
        del event
        self.cull_to_view()

    def cull_to_view(self) -> None:
        """Show only the images intersecting the current camera rect.

        vispy performs no viewport culling of its own, so every Image node in
        the scene is drawn on every frame even when it is far outside the
        view. Toggling `visible` is enough to skip the draw call, and it does
        not affect `bounds()`, so `zoom_to_fit` still sees the whole map.
        """
        if not self._cull_offscreen:
            for child in self._get_images():
                child.visible = True
            return
        rect = self._culling_rect()
        for child in self._get_images():
            child.visible = self._rect_intersects(child, rect)

    def _apply_cull(self, frame: Image) -> None:
        """Set the initial visibility of a newly added/updated image."""
        if self._cull_offscreen:
            frame.visible = self._rect_intersects(frame, self._culling_rect())

    def _culling_rect(self) -> Any:
        """The world-space extent actually rendered on screen.

        `camera.rect` is only the range the camera was explicitly asked to
        show. This widget's camera enforces ``aspect=1`` (square pixels), so
        whenever the viewbox itself isn't square -- which is virtually always
        true for a real dock or window -- PanZoomCamera pads that range on
        one axis to preserve 1:1 pixel aspect (letterboxing/pillarboxing).
        What's actually drawn on screen is `camera._real_rect`, computed by
        `PanZoomCamera._update_transform` and confirmed (empirically) to be
        updated synchronously whenever `rect` changes, i.e. always fresh here
        -- using the unpadded `camera.rect` instead would hide tiles that are
        still genuinely on screen. `_real_rect` isn't public API, so this
        falls back to the unpadded rect (the old, more-aggressive behavior)
        if a future vispy version removes or renames it, rather than raising.
        """
        cam = self.view.camera
        return getattr(cam, "_real_rect", None) or cam.rect

    def _set_world_rect(
        self, frame: Image, shape: tuple[int, ...], matrix: np.ndarray
    ) -> None:
        """Cache an image's world-space bounding box.

        Culling runs on every pan/zoom, so recomputing each node's bounds
        from its transform every time would put an O(N) matrix loop in the
        middle of interaction. The box only changes when the image is added
        or moved, so it is computed there and cached instead. (Cached in a
        WeakKeyDictionary rather than as a node attribute -- vispy's Image is
        a Frozen class and rejects new attributes post-construction.)
        """
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
        # camera rect is normalized (left < right, bottom < top) even when
        # the camera is flipped, which this widget's camera is on both axes.
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
        local_bounds = np.array([[x_min, y_min, 0, 1], [x_max, y_max, 0, 1]])

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
