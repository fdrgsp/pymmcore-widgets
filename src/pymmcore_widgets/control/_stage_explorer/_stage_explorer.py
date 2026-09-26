from __future__ import annotations

import errno
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, cast

import numpy as np
import psutil
import useq
from pymmcore_plus import CMMCorePlus, Keyword
from qtpy.QtCore import (
    QObject,
    QSignalBlocker,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from qtpy.QtGui import QIcon
from qtpy.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)
from superqt import QEnumComboBox, QIconifyIcon, QLabeledRangeSlider
from useq import OrderMode

from pymmcore_widgets.control._q_stage_controller import QStageMoveAccumulator
from pymmcore_widgets.control._rois.roi_manager import GRAY, SceneROIManager

from ._stage_position_marker import StagePositionMarker
from ._stage_viewer import StageViewer, get_vispy_scene_bounds

if TYPE_CHECKING:
    from collections.abc import Hashable
    from typing import Any

    from PyQt6.QtGui import QAction, QActionGroup, QKeyEvent
    from qtpy.QtGui import QCloseEvent, QHideEvent, QShowEvent
    from vispy.app.canvas import MouseEvent
    from vispy.scene.visuals import Image
else:
    from qtpy.QtWidgets import QAction, QActionGroup

# suppress scientific notation when printing numpy arrays
np.set_printoptions(suppress=True)

logger = logging.getLogger(__name__)

STAGE_POLL_INTERVAL_MS = 100
STAGE_POS_TOLERANCE_UM_SQ = 0.01  # 0.1 µm squared

# Minimum gap between scene repaints while frames are streaming in. Frames
# arriving faster than this are still stored (newest wins) -- only the repaint
# is coalesced, since anything drawn in between would be overwritten before a
# single screen refresh could show it.
REDRAW_INTERVAL_MS = 33  # ~30 fps

# Two images are treated as "the same place on the map" -- and therefore share
# one scene node -- when their centers are closer than this fraction of the
# FOV. Only used for snaps: MDA frames are keyed by their position/grid index
# instead, which is exact. Small enough that deliberately stepping the stage
# always makes a new tile, large enough to absorb stage repeatability error
# when returning to a position.
SNAP_DEDUP_FOV_FRACTION = 0.01

# An Image node costs roughly twice its array size in RSS: the array itself
# (vispy holds a reference, it does not copy) plus the GL-side copy.
TILE_MEMORY_FACTOR = 2

# Fraction of *available* RAM reserved for the map by default. The pymmcore-gui
# acquisition scratch store uses 60%, leaving 20% for the application, Qt, and
# the operating system. These are ceilings rather than eager reservations, but
# keeping their defaults complementary prevents both consumers independently
# targeting most of the same memory.
MAP_MEMORY_DEFAULT_FRACTION = 0.2

# Floor for the default above: under everyday, moderate memory pressure
# (a browser, an IDE, ... -- not a genuine shortage, just normal load) the
# fraction alone can round to an impractically small default. This keeps
# the *default* usable; it never overrides the user's own choice, and it's
# separate from LOW_SYSTEM_MEMORY_FLOOR_MB below, which is about genuine
# live shortage, not about picking a reasonable starting value.
MAP_MEMORY_DEFAULT_FLOOR_GB = 0.5

# Independent of the map's own limit above (a static number, whether set by
# the user or defaulted from available RAM at construction): a floor on
# *live* system memory, re-checked on every new location. Something else on
# the machine can claim RAM at any time after that default was computed --
# the map's own bookkeeping alone would stay well under its limit and keep
# growing regardless, right up to a real allocation failure. Raising the
# map's limit can't fix that case (the machine, not the map, is what's out
# of room), so this blocks new locations even when the static limit above
# says there's room left.
LOW_SYSTEM_MEMORY_FLOOR_MB = 512.0
_GL_OUT_OF_MEMORY = 0x0505


def _map_memory_defaults() -> tuple[float, float, float]:
    """(min, max, default) for the map-memory-limit spinbox, in GB.

    The range is bounded by total physical RAM -- there is no point letting
    the limit exceed what the machine could ever hold. The default is
    MAP_MEMORY_DEFAULT_FRACTION of RAM available right now, floored at
    MAP_MEMORY_DEFAULT_FLOOR_GB so everyday memory pressure doesn't round it
    down to something impractically small.
    """
    vm = psutil.virtual_memory()
    total_gb = round(vm.total / 1024**3, 1)
    available_gb = vm.available / 1024**3
    default_gb = min(
        max(
            MAP_MEMORY_DEFAULT_FLOOR_GB,
            round(available_gb * MAP_MEMORY_DEFAULT_FRACTION, 1),
        ),
        total_gb,
    )
    return (0.1, total_gb, default_gb)


def _is_allocation_error(exc: BaseException) -> bool:
    """Return whether ``exc`` specifically reports exhausted memory."""
    if isinstance(exc, MemoryError):
        return True
    if isinstance(exc, OSError) and exc.errno == errno.ENOMEM:
        return True
    if isinstance(exc, RuntimeError):
        # VisPy's OpenGL debug wrapper exposes the final GL error as ``err``.
        # Some backends only preserve its name in the exception text.
        return getattr(exc, "err", None) == _GL_OUT_OF_MEMORY or (
            "out of memory" in str(exc).lower()
        )
    return False


class _LatestFrameRelay(QObject):
    """Keep only the newest MDA frame per map location before GUI dispatch.

    ``frameReady`` is emitted from the acquisition thread. A normal Qt queued
    connection would therefore put one event containing a full image array onto
    the GUI queue for every acquired frame. If acquisition outruns rendering,
    those events and arrays accumulate before Stage Explorer's existing redraw
    throttle gets a chance to see them.

    ``submit`` is connected directly and only mutates data protected by ``_lock``.
    The first pending frame emits one lightweight wake-up; subsequent frames
    replace the pending value for their location until the GUI drains the batch.
    ``_notification_pending`` stays armed for one redraw interval after a drain,
    which caps GUI wake-ups as well as GPU uploads.
    """

    framesPending = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = Lock()
        self._pending: dict[Hashable, tuple[np.ndarray, useq.MDAEvent]] = {}
        self._notification_pending = False
        self._enabled = False

    @Slot(object, object, dict)
    def submit(
        self,
        image: np.ndarray,
        event: useq.MDAEvent,
        metadata: dict[str, Any],
    ) -> None:
        """Store ``image`` and request one GUI drain when none is outstanding."""
        del metadata
        notify = False
        key = self._event_key(event)
        with self._lock:
            if not self._enabled:
                return
            # Reinsert an existing key so batch order reflects most-recent
            # arrival. This preserves the expected topmost ordering for
            # overlapping tiles when several locations are drained together.
            self._pending.pop(key, None)
            self._pending[key] = (image, event)
            if not self._notification_pending:
                self._notification_pending = True
                notify = True
        if notify:
            self.framesPending.emit()

    def set_enabled(self, enabled: bool) -> None:
        """Enable collection, discarding retained frames when disabled."""
        with self._lock:
            self._enabled = enabled
            if not enabled:
                self._pending.clear()
                self._notification_pending = False

    def take_or_disarm(self) -> tuple[tuple[np.ndarray, useq.MDAEvent], ...]:
        """Take a batch, or atomically allow the next frame to notify the GUI."""
        with self._lock:
            if self._pending:
                batch = tuple(self._pending.values())
                self._pending.clear()
                return batch
            self._notification_pending = False
            return ()

    def take_and_disarm(self) -> tuple[tuple[np.ndarray, useq.MDAEvent], ...]:
        """Take all pending frames and allow a future frame to notify again."""
        with self._lock:
            batch = tuple(self._pending.values())
            self._pending.clear()
            self._notification_pending = False
            return batch

    @staticmethod
    def _event_key(event: useq.MDAEvent) -> Hashable:
        """Return a cheap, hardware-free key suitable for worker-thread use."""
        p_idx = event.index.get("p")
        g_idx = event.index.get("g")
        if (seq := event.sequence) is not None and (
            p_idx is not None or g_idx is not None
        ):
            return (seq.uid, p_idx, g_idx)
        return ("xy", event.x_pos, event.y_pos)


class _StagePoller(QThread):
    """Background thread that polls the XY stage position."""

    positionChanged = Signal(float, float)

    def __init__(self, mmc: CMMCorePlus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mmc = mmc

    def run(self) -> None:
        """Poll the stage position.

        If the stage position has changed by more than the tolerance since the last
        poll, emit the positionChanged signal with the new stage position.
        """
        last: tuple[float, float] | None = None
        was_erroring = False
        while not self.isInterruptionRequested():
            if self._mmc.getXYStageDevice():
                try:
                    x, y = self._mmc.getXYPosition()
                except Exception:
                    # A transient hardware/communication error here must not
                    # kill this thread -- an uncaught exception ends run(), and
                    # nothing ever restarts it, permanently freezing the
                    # explorer's position display for the rest of the session
                    # even after the device recovers and manual moves keep
                    # working fine. Log once per failure streak, not every
                    # poll, so a persistent fault doesn't spam the log every
                    # STAGE_POLL_INTERVAL_MS.
                    if not was_erroring:
                        logger.exception("Failed to poll XY stage position")
                        was_erroring = True
                    self.msleep(STAGE_POLL_INTERVAL_MS)
                    continue
                was_erroring = False
                if last is not None:
                    dx, dy = x - last[0], y - last[1]
                    if dx * dx + dy * dy < STAGE_POS_TOLERANCE_UM_SQ:
                        self.msleep(STAGE_POLL_INTERVAL_MS)
                        continue
                last = (x, y)
                self.positionChanged.emit(x, y)
            self.msleep(STAGE_POLL_INTERVAL_MS)

    def stop(self) -> None:
        self.requestInterruption()
        self.wait()


# this might belong in _stage_position_marker.py
class PositionIndicator(str, Enum):
    """Way in which the stage position is indicated."""

    RECTANGLE = "FOV Rectangle"
    CENTER = "FOV Center"
    BOTH = "Both"

    def __str__(self) -> str:
        return self.value

    @property
    def show_rect(self) -> bool:
        """Whether to show the rectangle."""
        return self in (self.RECTANGLE, self.BOTH)

    @property
    def show_marker(self) -> bool:
        """Whether to show the marker."""
        return self in (self.CENTER, self.BOTH)


class StageExplorer(QWidget):
    """A stage positions explorer widget.

    This widget provides a visual representation of the stage positions. The user can
    interact with the stage positions by panning and zooming the view. The user can also
    move the stage to a specific position (and, optionally, snap an image) by
    double-clicking on the view.

    Parameters
    ----------
    parent : QWidget | None
        Optional parent widget, by default None.
    mmcore : CMMCorePlus | None
        Optional [`CMMCorePlus`][pymmcore_plus.CMMCorePlus] micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new)
        [`CMMCorePlus.instance`][pymmcore_plus.core._mmcore_plus.CMMCorePlus.instance].

    Properties
    ----------
    auto_zoom_to_fit : bool
        A boolean property that controls whether to automatically "zoom to fit"
        the view when a new image is added to the scene or when the position of the
        stage marker (if enabled) is out of view. By default, False.
        By default, False.
    snap_on_double_click : bool
        A boolean property that controls whether to snap an image when the user
        double-clicks on the view. By default, False.
    poll_stage_position : bool
        A boolean property that controls whether to poll the stage position.
        If True, the widget will poll the stage position and display a rectangle
        around the current stage position. By default, False.
    """

    sendToMDARequested = Signal(list, bool)

    def __init__(
        self, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ):
        super().__init__(parent)
        self.setWindowTitle("Stage Explorer")

        self._mmc = mmcore or CMMCorePlus.instance()
        self._mmc.events.roiSet.connect(self._on_roi_changed)

        self._stage_controller: QStageMoveAccumulator | None = None
        self._set_stage_controller()

        self._stage_viewer = StageViewer(self)
        self._stage_viewer.setCursor(Qt.CursorShape.CrossCursor)
        self.roi_manager = SceneROIManager(self._stage_viewer.canvas)

        # properties
        self._auto_zoom_to_fit: bool = False
        self._snap_on_double_click: bool = True
        self._poll_stage_position: bool = self._has_devices()
        self._our_mda_running: bool = False
        self._position_indicator: PositionIndicator = PositionIndicator.RECTANGLE

        # --- map tiles -----------------------------------------------------
        # One scene node per *location*, not per frame. MDA position indices
        # are cached to spatially matched tile keys for the active sequence;
        # snaps use the same spatial matching directly.
        self._tiles: dict[Hashable, Image] = {}
        self._tile_centers: dict[Hashable, tuple[float, float]] = {}
        self._tile_bytes: dict[Hashable, int] = {}
        self._map_memory_bytes: int = 0
        self._next_snap_id: int = 0
        self._mda_tile_keys: dict[Hashable, Hashable] = {}
        # Frames waiting to be pushed to the GPU, newest-per-location wins.
        self._pending_tiles: dict[Hashable, tuple[np.ndarray, float, float]] = {}
        # monotonic() of each location's last actual GPU upload, so throttling
        # is per-location rather than a single gate shared by the whole map --
        # a location untouched in the last REDRAW_INTERVAL_MS always applies
        # immediately (no added latency for a snap, a slow acquisition, or a
        # multi-position scan visiting a fresh location every time), and only
        # a location being re-hit faster than the redraw interval gets its
        # updates coalesced.
        self._tile_last_applied: dict[Hashable, float] = {}
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(REDRAW_INTERVAL_MS)
        self._redraw_timer.timeout.connect(self._flush_pending_tiles)
        # MDA frames first pass through a worker-thread relay so full image
        # arrays cannot accumulate as queued Qt events when acquisition is
        # faster than the GUI. This timer keeps that relay armed between
        # drains, limiting GUI wake-ups to the same cadence as redraws.
        self._frame_relay = _LatestFrameRelay(self)
        self._frame_relay.framesPending.connect(self._on_frames_pending)
        self._frame_relay_timer = QTimer(self)
        self._frame_relay_timer.setSingleShot(True)
        self._frame_relay_timer.setInterval(REDRAW_INTERVAL_MS)
        self._frame_relay_timer.timeout.connect(self._on_frame_relay_timer)
        self._max_map_memory_mb: float = _map_memory_defaults()[2] * 1000.0
        self._map_memory_exceeded: bool = False
        # Whether the poller was running when hideEvent last paused it, so
        # showEvent knows whether to restart it (see hideEvent/showEvent).
        self._was_polling_before_hide: bool = False

        # background thread for polling stage position
        self._stage_poller = _StagePoller(self._mmc)
        self._stage_poller.positionChanged.connect(self._on_stage_position_polled)

        # marker for stage position (created when a camera is available)
        self._stage_pos_marker: StagePositionMarker | None = None
        self._create_stage_pos_marker()

        # --- cached parameters for efficient affine calculations ---
        self._affine_state = AffineState(self._mmc)

        # toolbar and actions
        self._toolbar = tb = StageExplorerToolbar()
        # (also add the actions from the ROI manager)
        self._toolbar.insertActions(
            tb.delete_rois_action, self.roi_manager.mode_actions.actions()
        )
        # add stage pos label to the toolbar
        self._stage_pos_label = QLabel()
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._toolbar.addWidget(spacer)
        self._toolbar.addWidget(self._stage_pos_label)

        # connect actions to methods
        tb.clear_action.triggered.connect(self._on_clear_action)
        tb.zoom_to_fit_action.triggered.connect(self._on_zoom_to_fit_action)
        tb.auto_zoom_to_fit_action.triggered.connect(self._on_auto_zoom_to_fit_action)
        tb.snap_action.triggered.connect(self._on_snap_action)
        tb.poll_stage_action.triggered.connect(self._on_poll_stage_action)
        tb.show_grid_action.triggered.connect(self._on_show_grid_action)
        tb.delete_rois_action.triggered.connect(self.roi_manager.clear)
        tb.scan_action.triggered.connect(self._on_scan_action)
        tb.stop_scan_action.triggered.connect(self._on_stop_scan_action)
        tb.send_to_mda_action.triggered.connect(self._on_send_to_mda)
        tb.marker_mode_action_group.triggered.connect(self._update_marker_mode)
        tb.scan_menu.valueChanged.connect(self._on_scan_options_changed)
        tb.map_memory_menu.valueChanged.connect(self._on_map_memory_limit_changed)
        tb.map_memory_menu.set_value(self._max_map_memory_mb / 1000)

        self._contrast_slider = ContrastSlider(self)
        self._contrast_slider.setVisible(False)
        self._contrast_slider.valueChanged.connect(self._on_contrast_slider_changed)

        self._memory_banner = _MapMemoryBanner(self)
        self._memory_banner.clearRequested.connect(self._on_clear_action)
        self._memory_banner.setVisible(False)

        # main layout
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self._toolbar, 0)
        main_layout.addWidget(self._memory_banner, 0)
        main_layout.addWidget(self._stage_viewer, 1)
        main_layout.addWidget(self._contrast_slider, 0)

        # connections core events
        self._mmc.events.systemConfigurationLoaded.connect(self._on_sys_config_loaded)
        self._mmc.events.imageSnapped.connect(self._on_image_snapped)
        mda_events = self._mmc.mda.events
        if isinstance(mda_events, QObject):
            # The callback executes in the acquisition thread and only swaps
            # protected Python references. Its lightweight framesPending signal
            # is the sole event queued back to the GUI thread.
            cast("Any", mda_events.frameReady).connect(
                self._frame_relay.submit, Qt.ConnectionType.DirectConnection
            )
        else:
            # Psygnal callbacks are synchronous in the emitting thread, which
            # provides the same pre-GUI-queue behavior without a Qt connection
            # type argument.
            mda_events.frameReady.connect(self._frame_relay.submit)
        mda_events.sequenceFinished.connect(self._on_sequence_finished)
        self._mmc.events.pixelSizeChanged.connect(self._on_pixel_size_changed)
        self._mmc.events.pixelSizeAffineChanged.connect(
            self._on_pixel_size_affine_changed
        )

        # connections vispy events
        self._stage_viewer.canvas.events.mouse_double_click.connect(
            self._on_mouse_double_click
        )

        # initial setup
        self._on_roi_changed()
        self._toolbar.snap_action.setChecked(self._snap_on_double_click)
        self._update_actions_enabled()
        if self._poll_stage_position:
            self._toolbar.poll_stage_action.trigger()
        self._sync_stage_pos_marker()
        self.zoom_to_fit()

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        self._frame_relay.set_enabled(False)
        self._frame_relay_timer.stop()
        self._stop_poller()
        super().closeEvent(a0)

    def hideEvent(self, a0: QHideEvent | None) -> None:
        """Pause hardware polling while nothing can see this widget.

        Covers a closed dock as well as a background tab in a tabbed dock
        area -- either way, the stage poller would otherwise keep querying
        hardware (and _on_image_snapped/_on_frame_ready keep redrawing the
        scene, guarded separately below) for a view nobody is looking at.
        """
        super().hideEvent(a0)
        self._frame_relay.set_enabled(False)
        self._frame_relay_timer.stop()
        self._was_polling_before_hide = self._stage_poller.isRunning()
        self._stop_poller()

    def showEvent(self, a0: QShowEvent | None) -> None:
        """Resume polling paused by hideEvent and refresh the stale marker."""
        super().showEvent(a0)
        self._frame_relay.set_enabled(True)
        if self._was_polling_before_hide and self._mmc.getXYStageDevice():
            self._stage_poller.start()
            self._sync_stage_pos_marker()
        self._was_polling_before_hide = False

    def __del__(self) -> None:
        self._stop_poller()

    def _stop_poller(self) -> None:
        try:
            if self._stage_poller.isRunning():
                self._stage_poller.stop()
        except RuntimeError:  # pragma: no cover
            pass

    # -----------------------------PUBLIC METHODS-------------------------------------

    def toolBar(self) -> StageExplorerToolbar:
        """Return the toolbar of the widget."""
        return self._toolbar

    @property
    def auto_zoom_to_fit(self) -> bool:
        """Whether to automatically zoom to fit the full scene in the view.

        When True, the view will automatically zoom to fit the full scene
        when a new image is added or when the stage position marker moves
        out of view.
        """
        return self._auto_zoom_to_fit

    @auto_zoom_to_fit.setter
    def auto_zoom_to_fit(self, value: bool) -> None:
        self._auto_zoom_to_fit = value
        self._toolbar.auto_zoom_to_fit_action.setChecked(value)
        if value:
            self.zoom_to_fit()

    @property
    def snap_on_double_click(self) -> bool:
        """Whether to snap an image on double click.

        When True, the widget will snap an image when the user double-clicks
        on the view, after the stage has moved to the clicked position.
        """
        return self._snap_on_double_click

    @snap_on_double_click.setter
    def snap_on_double_click(self, value: bool) -> None:
        self._snap_on_double_click = value
        self._toolbar.snap_action.setChecked(value)

    @property
    def poll_stage_position(self) -> bool:
        """Whether to continually show the current stage position."""
        return self._poll_stage_position

    @poll_stage_position.setter
    def poll_stage_position(self, value: bool) -> None:
        """Set the poll stage position property."""
        self._poll_stage_position = value
        self._toolbar.poll_stage_action.setChecked(value)
        self._on_poll_stage_action(value)

    def add_image(
        self, image: np.ndarray, stage_x_um: float, stage_y_um: float
    ) -> None:
        """Add an image to the scene at a give (x, y) stage position in microns.

        Routed through the same location-keyed tile registry as snaps and MDA
        frames: a call that lands close to an existing tile (see
        `_snap_tile_key`) refreshes it in place instead of stacking another
        node on top, so external callers get the same bounded-growth
        guarantee as the built-in snap/MDA paths.
        """
        self._queue_tile(
            self._snap_tile_key(stage_x_um, stage_y_um), image, stage_x_um, stage_y_um
        )

    def zoom_to_fit(self, *, margin: float = 0.05) -> None:
        """Zoom to fit the current view to the images in the scene.

        ...also considering the stage position marker.
        """
        visuals: list = list(self._stage_viewer._get_images())  # pyright: ignore
        if self._stage_pos_marker is not None:
            visuals.append(self._stage_pos_marker)
        x_bounds, y_bounds, *_ = get_vispy_scene_bounds(visuals)
        self._stage_viewer.view.camera.set_range(x=x_bounds, y=y_bounds, margin=margin)

    # -----------------------------PRIVATE METHODS------------------------------------

    def _has_devices(self) -> bool:
        """Return True if devices (beyond the core) are loaded."""
        return len(self._mmc.getLoadedDevices()) > 1

    def _update_actions_enabled(self) -> None:
        """Enable/disable toolbar actions based on loaded devices."""
        has_devices = self._has_devices()
        tb = self._toolbar
        tb.snap_action.setEnabled(has_devices)
        tb.poll_stage_action.setEnabled(has_devices)
        tb.scan_action.setEnabled(has_devices)
        tb.delete_rois_action.setEnabled(has_devices)
        for action in self.roi_manager.mode_actions.actions():
            action.setEnabled(has_devices)

    # ACTIONS ----------------------------------------------------------------------

    @Slot()
    def _on_clear_action(self) -> None:
        """Clear the scene and hide the contrast slider."""
        self._frame_relay.take_and_disarm()
        self._frame_relay_timer.stop()
        self._stage_viewer.clear()
        self._tiles.clear()
        self._tile_centers.clear()
        self._tile_bytes.clear()
        self._map_memory_bytes = 0
        self._mda_tile_keys.clear()
        self._pending_tiles.clear()
        self._tile_last_applied.clear()
        self._redraw_timer.stop()
        self._map_memory_exceeded = False
        self._memory_banner.setVisible(False)
        self._contrast_slider.reset_data_range()
        self._contrast_slider.setVisible(False)

    @Slot()
    def _on_roi_changed(self) -> None:
        """Update the ROI manager when a new ROI is set."""
        img_w = self._mmc.getImageWidth()
        img_h = self._mmc.getImageHeight()
        if not img_w or not img_h:
            return
        px = self._mmc.getPixelSizeUm()
        self.roi_manager.update_fovs((img_w * px, img_h * px))

        # by default, vispy add the images from the bottom-left corner. We need to
        # translate by -w/2 and -h/2 so the position corresponds to the center of the
        # images. In addition, this makes sure the rotation (if any) is applied around
        # the center of the image.
        self._half_img_shift = np.eye(4)
        self._half_img_shift[0:2, 3] = (-img_w / 2, -img_h / 2)

        if self._stage_pos_marker is not None:
            self._stage_pos_marker.set_rect_size(img_w, img_h)

    @Slot(bool)
    def _on_snap_action(self, checked: bool) -> None:
        """Update the stage viewer settings based on the state of the action."""
        self.snap_on_double_click = checked

    @Slot(bool)
    def _on_zoom_to_fit_action(self, checked: bool) -> None:
        """Set the zoom to fit property based on the state of the action."""
        # self._toolbar.zoom_to_fit_action.setChecked(checked)
        self._auto_zoom_to_fit = False
        self.zoom_to_fit()

    @Slot(bool)
    def _on_auto_zoom_to_fit_action(self, checked: bool) -> None:
        """Set the auto zoom to fit property based on the state of the action."""
        self._auto_zoom_to_fit = checked
        if checked:
            self.zoom_to_fit()

    @Slot()
    def _update_marker_mode(self) -> None:
        """Update the poll mode and show/hide the required stage position marker.

        Usually, the sender will be the action_group on the PositionIndicatorMenu.
        """
        sender = self.sender()
        if self._stage_pos_marker is None:
            return
        if isinstance(sender, QActionGroup) and (action := sender.checkedAction()):
            pi = PositionIndicator(action.text())
            self._position_indicator = pi
            self._stage_pos_marker.set_rect_visible(pi.show_rect)
            self._stage_pos_marker.set_marker_visible(pi.show_marker)

    @Slot(tuple)
    def _on_contrast_slider_changed(self, value: tuple[float, float]) -> None:
        """Apply the contrast slider values to all images."""
        self._stage_viewer.set_clims(value)

    @Slot()
    def _on_scan_action(self) -> None:
        """Scan the selected ROI."""
        if not (active_rois := self.roi_manager.selected_rois()):
            return
        active_roi = active_rois[0]

        overlap, mode = self._toolbar.scan_menu.value()
        if plan := active_roi.create_grid_plan(*self._fov_w_h(), overlap, mode):
            seq = useq.MDASequence(grid_plan=plan)
            if not self._mmc.mda.is_running():
                self._our_mda_running = True
                self._mmc.run_mda(seq)

    @Slot()
    def _on_stop_scan_action(self) -> None:
        """Cancel the running scan, or stop the stage if no scan is running."""
        if self._mmc.mda.is_running():
            self._mmc.mda.cancel()
        elif xy_dev := self._mmc.getXYStageDevice():
            self._mmc.stop(xy_dev)

    @Slot()
    def _on_sequence_finished(self) -> None:
        """Reset scan state when the MDA sequence finishes."""
        self._our_mda_running = False
        # sequenceFinished may reach the GUI while the relay's redraw timer is
        # still holding the newest frame. Drain it before flushing the lower
        # level tile throttle so the final acquired image is always displayed.
        self._frame_relay_timer.stop()
        self._deliver_frame_batch(self._frame_relay.take_and_disarm())
        # The throttle may be holding the final frame of the run; without this
        # the map would be missing the last image until something else
        # happened to trigger a flush.
        self._flush_pending_tiles()
        self._redraw_timer.stop()
        self._mda_tile_keys.clear()

    @Slot(object)
    def _on_scan_options_changed(self, value: tuple[float, OrderMode]) -> None:
        """Update scan settings on the ROI manager so visuals refresh."""
        overlap, mode = value
        self.roi_manager.set_scan_options(overlap, mode)

    @Slot(float)
    def _on_map_memory_limit_changed(self, gigabytes: float) -> None:
        """Apply a new limit chosen from the toolbar's `MapMemoryMenu`."""
        self.max_map_memory_mb = gigabytes * 1000.0

    @Slot()
    def _on_send_to_mda(self) -> None:
        """Send every Explorer ROI to an MDA position table."""
        fov_w, fov_h = self._fov_w_h()
        z_pos = self._mmc.getZPosition() if self._mmc.getFocusDevice() else None
        manager = self.roi_manager
        positions = [
            roi.create_useq_position(
                fov_w,
                fov_h,
                z_pos=z_pos,
                overlap=manager.scan_overlap,
                mode=manager.scan_mode,
            )
            for roi in manager.all_rois()
        ]
        if not positions:
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Send to MDA")
        msg.setText("Replace existing stage positions or add to them?")
        replace_btn = msg.addButton("Replace", QMessageBox.ButtonRole.AcceptRole)
        add_btn = msg.addButton("Add", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is replace_btn:
            self.sendToMDARequested.emit(positions, True)
        elif clicked is add_btn:
            self.sendToMDARequested.emit(positions, False)
        elif clicked is cancel_btn or clicked is None:
            return

    def keyPressEvent(self, a0: QKeyEvent | None) -> None:
        if a0 is None:
            return
        if a0.key() == Qt.Key.Key_Escape:
            if self._our_mda_running:
                self._mmc.mda.cancel()
        super().keyPressEvent(a0)

    # CORE ------------------------------------------------------------------------

    def _fov_w_h(self) -> tuple[float, float]:
        """Return the field of view width and height."""
        px = self._mmc.getPixelSizeUm()
        fov_w = self._mmc.getImageWidth() * px
        fov_h = self._mmc.getImageHeight() * px
        return fov_w, fov_h

    @Slot()
    def _on_sys_config_loaded(self) -> None:
        """Clear the scene and reinitialize when the system configuration is loaded."""
        self._stage_viewer.clear()
        self._contrast_slider.reset_data_range()
        self._contrast_slider.setVisible(False)
        self._set_stage_controller()
        self._affine_state.refresh()

        self._create_stage_pos_marker()
        self._on_roi_changed()
        self._update_actions_enabled()

        # start/stop the poller based on whether an XY stage is available
        has_xy = bool(self._mmc.getXYStageDevice())
        if has_xy and not self._stage_poller.isRunning():
            self._toolbar.poll_stage_action.setChecked(True)
            self._on_poll_stage_action(True)
            self._sync_stage_pos_marker()
            self.zoom_to_fit()
        elif not has_xy and self._stage_poller.isRunning():
            self._toolbar.poll_stage_action.setChecked(False)
            self._on_poll_stage_action(False)

    def _create_stage_pos_marker(self) -> None:
        """(Re)create the stage position marker if a camera is available."""
        w, h = self._mmc.getImageWidth(), self._mmc.getImageHeight()
        if not w or not h:
            return
        if self._stage_pos_marker is not None:
            self._stage_pos_marker.parent = None
        self._stage_pos_marker = StagePositionMarker(
            parent=self._stage_viewer.view.scene,
            rect_width=w,
            rect_height=h,
            marker_symbol_size=min(w, h) / 10,
            show_rect=self._position_indicator.show_rect,
            show_marker_symbol=self._position_indicator.show_marker,
        )
        self._stage_pos_marker.visible = False

    def _set_stage_controller(self) -> None:
        self._stage_controller = None
        if xy_dev := self._mmc.getXYStageDevice():
            self._stage_controller = QStageMoveAccumulator.for_device(xy_dev, self._mmc)

    @Slot(float)
    def _on_pixel_size_changed(self, value: float) -> None:
        """Refresh the affine state when pixel size changes."""
        self._affine_state.refresh()

    @Slot()
    def _on_pixel_size_affine_changed(self) -> None:
        """Refresh the affine state when pixel size affine changes."""
        self._affine_state.refresh()

    @Slot(object)
    def _on_mouse_double_click(self, event: MouseEvent) -> None:
        """Move the stage to the clicked position."""
        if not self._mmc.getXYStageDevice() or self._stage_controller is None:
            return
        if self.roi_manager.mode == "create-poly":
            return

        # map the clicked canvas position to the stage position
        x, y, _, _ = self._stage_viewer.view.camera.transform.imap(event.pos)
        self._stage_controller.move_absolute((x, y))
        self._stage_controller.snap_on_finish = self._snap_on_double_click

        # update the stage position label
        self._stage_pos_label.setText(f"X: {x:.2f} µm  Y: {y:.2f} µm")
        # snap an image if the snap on double click property is set

    @Slot()
    def _on_image_snapped(self) -> None:
        """Add the snapped image to the scene."""
        # mmc.events.imageSnapped fires for *any* snap in the app, not just
        # ones from this widget -- skip the scene redraw while hidden (closed
        # dock, or a background tab) since nothing can see it anyway.
        if not self.isVisible() or self._mmc.mda.is_running():
            return
        # get the snapped image
        img = self._mmc.getImage()
        # get the current stage position -- a transient stage error here must
        # not drop the already-captured image, so log and skip placing it
        # rather than propagating (see _StagePoller.run for the same fault).
        try:
            x, y = self._mmc.getXYPosition()
        except Exception:
            logger.exception("Failed to read XY stage position for snapped image")
            return
        self._add_image_and_update_widget(img, x, y)

    @Slot(object, object)
    def _on_frame_ready(self, image: np.ndarray, event: useq.MDAEvent) -> None:
        """Process one frame already delivered on the GUI thread.

        The core's ``frameReady`` signal is connected to ``_frame_relay`` rather
        than this slot directly. Keeping this method as the unthrottled entry
        point is useful to callers that already run on the GUI thread and
        preserves the existing private API used by downstream subclasses.
        """
        self._handle_frame_ready(image, event, already_coalesced=False)

    def _handle_frame_ready(
        self,
        image: np.ndarray,
        event: useq.MDAEvent,
        *,
        already_coalesced: bool,
    ) -> None:
        """Place one MDA frame, optionally bypassing the tile redraw throttle."""
        # frameReady fires for *any* running MDA, not just one started from
        # this widget -- skip the scene redraw while hidden, same as
        # _on_image_snapped above.
        if not self.isVisible():
            return
        # Only the spatial axes take part in the key: a z-stack or a
        # multi-channel event images the same place on the map, so the last
        # one to arrive is what the map shows. That matches what was already
        # displayed before (they were drawn stacked opaquely on top of each
        # other) without keeping the hidden ones alive.
        x = event.x_pos if event.x_pos is not None else self._mmc.getXPosition()
        y = event.y_pos if event.y_pos is not None else self._mmc.getYPosition()
        self._add_image_and_update_widget(
            image,
            x,
            y,
            key=self._mda_tile_key(event, x, y),
            throttle=not already_coalesced,
        )

    @Slot()
    def _on_frames_pending(self) -> None:
        """Drain the relay immediately, then keep it armed for one redraw period."""
        batch = self._frame_relay.take_or_disarm()
        if not batch:
            return
        self._deliver_frame_batch(batch)
        self._frame_relay_timer.start()

    @Slot()
    def _on_frame_relay_timer(self) -> None:
        """Drain frames accumulated during the previous redraw interval."""
        batch = self._frame_relay.take_or_disarm()
        if not batch:
            return
        self._deliver_frame_batch(batch)
        self._frame_relay_timer.start()

    def _deliver_frame_batch(
        self, batch: tuple[tuple[np.ndarray, useq.MDAEvent], ...]
    ) -> None:
        """Apply the newest frame for every location in one GUI-thread batch."""
        for image, event in batch:
            self._handle_frame_ready(image, event, already_coalesced=True)

    # STAGE POSITION MARKER -----------------------------------------------------

    @Slot(bool)
    def _on_poll_stage_action(self, checked: bool) -> None:
        """Set the poll stage position property based on the state of the action."""
        if self._stage_pos_marker is not None:
            self._stage_pos_marker.visible = checked
        self._poll_stage_position = checked
        if checked and self._mmc.getXYStageDevice():
            self._stage_poller.start()
        else:
            self._stop_poller()

    @Slot(bool)
    def _on_show_grid_action(self, checked: bool) -> None:
        """Set the show grid property based on the state of the action."""
        self._stage_viewer.set_grid_visible(checked)

    def _update_stage_pos_marker(self, stage_x: float, stage_y: float) -> None:
        """Update the marker position and the stage-position label."""
        self._stage_pos_label.setText(f"X: {stage_x:.2f} µm  Y: {stage_y:.2f} µm")

        # fast path: copy cached rotation/scale part and just update translation
        if self._stage_pos_marker is not None:
            matrix = self._affine_state.system_affine_translated(stage_x, stage_y)
            self._stage_pos_marker.apply_transform(matrix.T)

    def _sync_stage_pos_marker(self) -> None:
        """Synchronously move the marker to the current stage position.

        The background `_StagePoller` reports position changes via a queued,
        cross-thread signal, so its first update can't be delivered until the
        Qt event loop runs again -- e.g. only *after* __init__ returns. Any
        `zoom_to_fit()` called before that would fit around the marker's
        stale/default position rather than the real one, so this is used to
        get an up-to-date position immediately beforehand.
        """
        if self._stage_pos_marker is None or not self._mmc.getXYStageDevice():
            return
        try:
            x, y = self._mmc.getXYPosition()
        except Exception:
            logger.exception("Failed to read initial XY stage position")
            return
        self._update_stage_pos_marker(x, y)

    @Slot(float, float)
    def _on_stage_position_polled(self, stage_x: float, stage_y: float) -> None:
        """Update the marker and label with the polled stage position."""
        self._update_stage_pos_marker(stage_x, stage_y)

        # zoom_to_fit only if auto _auto_zoom_to_fit property is set to True.
        if self._auto_zoom_to_fit:
            self.zoom_to_fit()

    # IMAGES -----------------------------------------------------------------------

    def _add_image_and_update_widget(
        self,
        image: np.ndarray,
        stage_x_um: float,
        stage_y_um: float,
        key: Hashable | None = None,
        *,
        throttle: bool = True,
    ) -> None:
        """Add the image to the scene and update position label and view.

        (called by _on_image_snapped and _on_frame_ready).
        """
        if key is None:
            key = self._snap_tile_key(stage_x_um, stage_y_um)
        if throttle:
            self._queue_tile(key, image, stage_x_um, stage_y_um)
        else:
            # The frame relay has already limited this path to one GUI update
            # per redraw interval. Applying it directly avoids two independent
            # 33 ms throttles combining into an unintended ~15 fps display.
            self._pending_tiles.pop(key, None)
            self._apply_tile(key, image, stage_x_um, stage_y_um)
            self._tile_last_applied[key] = time.monotonic()

        # update the stage position label if the stage position is not being polled
        if not self._poll_stage_position:
            self._stage_pos_label.setText(
                f"X: {stage_x_um:.2f} µm  Y: {stage_y_um:.2f} µm"
            )

        # reset the view if the image is not within the view
        if self._auto_zoom_to_fit and not self._is_visual_within_view(
            stage_x_um, stage_y_um
        ):
            self._stage_viewer.zoom_to_fit()

    # MAP TILES ---------------------------------------------------------------

    def _mda_tile_key(self, event: useq.MDAEvent, x: float, y: float) -> Hashable:
        """Identify the map location an MDA frame belongs to.

        A sequence's position/grid index is cached after its first spatial
        match, keeping repeated timepoints O(1). The first frame at a location
        is matched against the existing map, so a new acquisition at the same
        physical position refreshes the existing tile instead of creating one
        merely because its sequence UUID changed.
        """
        if (seq := event.sequence) is not None:
            event_key = (seq.uid, event.index.get("p"), event.index.get("g"))
            if (tile_key := self._mda_tile_keys.get(event_key)) is not None:
                return tile_key
            tile_key = self._snap_tile_key(x, y)
            self._mda_tile_keys[event_key] = tile_key
            return tile_key
        return self._snap_tile_key(x, y)

    def _snap_tile_key(self, x: float, y: float) -> Hashable:
        """Find the existing tile at (x, y), or mint a key for a new one.

        Used where no position index is available (snaps). Nearest-match
        within a tolerance rather than quantizing coordinates into buckets:
        two images a fraction of a micron apart must never land in different
        buckets just because they straddle a cell boundary.
        """
        fov_w, fov_h = self._fov_w_h()
        tol = min(fov_w, fov_h) * SNAP_DEDUP_FOV_FRACTION
        if tol > 0:
            tol_sq = tol * tol
            best: Hashable | None = None
            best_sq = tol_sq
            for key, (cx, cy) in self._tile_centers.items():
                dist_sq = (x - cx) ** 2 + (y - cy) ** 2
                if dist_sq <= best_sq:
                    best, best_sq = key, dist_sq
            if best is not None:
                return best
        self._next_snap_id += 1
        return ("snap", self._next_snap_id)

    def _queue_tile(self, key: Hashable, image: np.ndarray, x: float, y: float) -> None:
        """Apply a frame for `key`, or buffer it if `key` was just updated.

        Per-location leading-edge throttle: a location that hasn't been
        touched in the last REDRAW_INTERVAL_MS is updated right away; one
        being hit faster than that (a fast timelapse revisiting the same
        stage position) has its updates coalesced into the next tick instead.
        """
        now = time.monotonic()
        last = self._tile_last_applied.get(key)
        if last is None or (now - last) * 1000 >= REDRAW_INTERVAL_MS:
            self._pending_tiles.pop(key, None)
            self._apply_tile(key, image, x, y)
            self._tile_last_applied[key] = now
        else:
            self._pending_tiles[key] = (image, x, y)
            if not self._redraw_timer.isActive():
                self._redraw_timer.start()

    def _flush_pending_tiles(self) -> None:
        """Push every buffered frame into the scene."""
        if not self._pending_tiles:
            self._redraw_timer.stop()
            return
        pending, self._pending_tiles = self._pending_tiles, {}
        now = time.monotonic()
        for key, (image, x, y) in pending.items():
            self._apply_tile(key, image, x, y)
            self._tile_last_applied[key] = now

    def _apply_tile(self, key: Hashable, image: np.ndarray, x: float, y: float) -> None:
        """Create or refresh the scene node for one map location."""
        matrix = self._tile_transform(x, y)
        if (node := self._tiles.get(key)) is not None:
            self._stage_viewer.update_image(node, image, transform=matrix.T)
            cost = image.nbytes * TILE_MEMORY_FACTOR
            previous_cost = self._tile_bytes[key]
            self._tile_bytes[key] = cost
            self._map_memory_bytes += cost - previous_cost
        else:
            cost = image.nbytes * TILE_MEMORY_FACTOR
            system_low = self._system_memory_low(cost)
            if system_low or self._would_exceed_memory(cost):
                # Refuse the *new* location only. Nothing already on the map
                # is discarded, and existing locations keep updating, so this
                # is fully recoverable by clearing the map.
                self._set_memory_exceeded(True, system_low=system_low)
                return
            try:
                node = self._stage_viewer.add_image(image, transform=matrix.T)
            except Exception as exc:
                if not _is_allocation_error(exc):
                    raise
                # The checks above only see *system* RAM; GPU texture memory
                # is a separate pool on most non-unified-memory hardware, so
                # a genuine allocation failure here is a distinct resource
                # this widget has no other way to anticipate. Degrade the
                # same way as running low on system memory (refuse further
                # new locations, nothing already drawn is touched) rather
                # than letting it crash the whole app over one tile.
                logger.exception("Failed to add stage-map tile at (%.1f, %.1f)", x, y)
                self._set_memory_exceeded(True, system_low=True)
                return
            self._tiles[key] = node
            self._tile_bytes[key] = cost
            self._map_memory_bytes += cost
            # A location just went through -- whatever condition previously
            # blocked one (the map's own limit, or the machine running low)
            # must no longer hold, so the "paused" banner would otherwise be
            # left showing stale even as new locations keep being added.
            self._set_memory_exceeded(False)
        self._tile_centers[key] = (x, y)
        self._update_contrast_range(image)

    def _tile_transform(self, x: float, y: float) -> np.ndarray:
        stage_shift = np.eye(4)
        stage_shift[0:2, 3] = (x, y)
        matrix: np.ndarray = (
            stage_shift @ self._affine_state.system_affine @ self._half_img_shift
        )
        return matrix

    def _update_contrast_range(self, image: np.ndarray) -> None:
        if not self._contrast_slider.isVisible():
            self._contrast_slider.setVisible(True)
            self._contrast_slider.set_maximum(2 ** self._mmc.getImageBitDepth() - 1)
        self._contrast_slider.update_data_range(np.min(image), np.max(image))

    # MAP MEMORY ---------------------------------------------------------------

    @property
    def max_map_memory_mb(self) -> float:
        """Ceiling on the memory held by the map, in MB."""
        return self._max_map_memory_mb

    @max_map_memory_mb.setter
    def max_map_memory_mb(self, value: float) -> None:
        self._max_map_memory_mb = float(value)
        # Keep the toolbar's editor in sync when the limit is set
        # programmatically (e.g. a host app computing it from system RAM)
        # rather than through the menu itself.
        self._toolbar.map_memory_menu.set_value(self._max_map_memory_mb / 1000)
        if self._map_memory_exceeded and not (
            self._would_exceed_memory(0) or self._system_memory_low(0)
        ):
            self._set_memory_exceeded(False)

    def map_memory_bytes(self) -> int:
        """Approximate memory currently held by the map's images."""
        return self._map_memory_bytes

    def _would_exceed_memory(self, additional: int) -> bool:
        """Whether `additional` bytes would push the map past its own limit."""
        limit = self._max_map_memory_mb * 1e6
        return limit > 0 and (self.map_memory_bytes() + additional) > limit

    def _system_memory_low(self, additional: int) -> bool:
        """Whether `additional` bytes would leave the machine dangerously low.

        Live-checked (unlike `_would_exceed_memory`'s static per-map limit)
        so it still catches memory pressure caused by something other than
        this widget -- see LOW_SYSTEM_MEMORY_FLOOR_MB.
        """
        available = psutil.virtual_memory().available
        return (available - additional) < LOW_SYSTEM_MEMORY_FLOOR_MB * 1e6

    def _set_memory_exceeded(self, exceeded: bool, *, system_low: bool = False) -> None:
        if exceeded == self._map_memory_exceeded:
            return
        self._map_memory_exceeded = exceeded
        if exceeded:
            self._memory_banner.set_state(
                len(self._tiles),
                self.map_memory_bytes() / 1e9,
                system_low=system_low,
            )
        self._memory_banner.setVisible(exceeded)

    def _is_visual_within_view(self, x: float, y: float) -> bool:
        """Return True if the visual is within the view, otherwise False."""
        view_rect = self._stage_viewer.view.camera.rect
        fov_w, fov_h = self._fov_w_h()
        half_width = fov_w / 2
        half_height = fov_h / 2
        # NOTE: x, y is the center of the image
        vertices = [
            (x - half_width, y - half_height),  # bottom-left
            (x + half_width, y - half_height),  # bottom-right
            (x - half_width, y + half_height),  # top-left
            (x + half_width, y + half_height),  # top-right
        ]
        return all(view_rect.contains(*vertex) for vertex in vertices)


_CLIM_SLIDER_STYLE = (
    """
QSlider::groove:horizontal {
    height: 15px;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(128, 128, 128, 0.25),
        stop:1 rgba(128, 128, 128, 0.1)
    );
    border-radius: 3px;
}

QSlider::handle:horizontal {
    width: 38px;
    background: #999999;
    border-radius: 3px;
}

QSlider::sub-page:horizontal {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(100, 100, 100, 0.25),
        stop:1 rgba(100, 100, 100, 0.1)
    );
}

QLabel { font-size: 12px; }

QRangeSlider { qproperty-barColor: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(100, 80, 120, 0.2),
        stop:1 rgba(100, 80, 120, 0.4)
    )}
"""
    + "SliderLabel { font-size: 10px; color: white;}"
)


class _MapMemoryBanner(QWidget):
    """Shown when the map's memory budget is hit; offers Clear to recover.

    Nothing already drawn is ever discarded by the budget itself (see
    `StageExplorer._apply_tile`) -- this is the recovery path for a user who
    wants to keep going anyway, by explicitly discarding the map so far.
    """

    clearRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), pal.color(pal.ColorRole.Highlight))
        self.setPalette(pal)

        self._label = QLabel()
        self._label.setWordWrap(True)
        clear_btn = QPushButton("Clear Map")
        clear_btn.clicked.connect(self.clearRequested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(self._label, 1)
        layout.addWidget(clear_btn, 0)

    def set_state(
        self, n_tiles: int, gigabytes: float, *, system_low: bool = False
    ) -> None:
        if system_low:
            # Raising the map's own limit wouldn't help here -- the machine
            # itself, not the map's bookkeeping, is what's out of room.
            self._label.setText(
                f"Stage map paused at {n_tiles} tiles (~{gigabytes:.1f} GB) -- "
                "this machine is low on free memory, so no further locations "
                "will be added regardless of the map's own limit. Already "
                "drawn tiles keep updating; free up memory or clear the map."
            )
        else:
            self._label.setText(
                f"Stage map paused at {n_tiles} tiles (~{gigabytes:.1f} GB) -- "
                "further locations won't be added until it's cleared. Already "
                "drawn tiles keep updating."
            )


class ContrastSlider(QWidget):
    """A contrast range slider with an auto-contrast toggle button."""

    valueChanged = Signal(tuple)
    """Emitted as (min, max) floats whenever the effective clim changes."""
    autoToggled = Signal(bool)
    """Emitted when the auto-contrast button is toggled."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._auto: bool = True
        self._data_min: float = float("inf")
        self._data_max: float = float("-inf")

        self._slider = QLabeledRangeSlider(Qt.Orientation.Horizontal, self)
        # Keep the contrast control visually identical to ndv's Qt LUT slider.
        self._slider.setStyleSheet(_CLIM_SLIDER_STYLE)
        self._slider.setRange(0, 2**16 - 1)
        self._slider.setHandleLabelPosition(
            QLabeledRangeSlider.LabelPosition.LabelsOnHandle
        )
        self._slider.setEdgeLabelMode(QLabeledRangeSlider.EdgeLabelMode.NoLabel)
        self._slider.valueChanged.connect(self._on_slider_changed)

        self._auto_btn = QPushButton("Auto", self)
        self._auto_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._auto_btn.setCheckable(True)
        self._auto_btn.setChecked(True)
        self._auto_btn.toggled.connect(self._on_auto_toggled)

        layout = QHBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._auto_btn, 0)

    @property
    def auto(self) -> bool:
        """Whether auto-contrast is enabled."""
        return self._auto

    def set_maximum(self, value: int) -> None:
        """Set the slider domain maximum."""
        self._slider.setMaximum(max(1, value))

    def update_data_range(self, img_min: float, img_max: float) -> None:
        """Expand the running data range and, if auto, update the handles."""
        self._data_min = min(self._data_min, img_min)
        self._data_max = max(self._data_max, img_max)
        if self._auto:
            self._set_handles(self._data_min, self._data_max)
            self.valueChanged.emit((self._data_min, self._data_max))

    def reset_data_range(self) -> None:
        """Reset the running data range (e.g. after clearing the scene)."""
        self._data_min = float("inf")
        self._data_max = float("-inf")

    def _set_handles(self, lo: float, hi: float) -> None:
        with QSignalBlocker(self._slider):
            self._slider.setValue((int(lo), int(hi)))

    def _on_slider_changed(self, value: tuple[int, int]) -> None:
        if self._auto_btn.isChecked():
            self._auto_btn.setChecked(False)
        self.valueChanged.emit((float(value[0]), float(value[1])))

    def _on_auto_toggled(self, checked: bool) -> None:
        self._auto = checked
        if checked and self._data_min < self._data_max:
            self._set_handles(self._data_min, self._data_max)
            self.valueChanged.emit((self._data_min, self._data_max))
        self.autoToggled.emit(checked)


class PositionIndicatorMenu(QMenu):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.action_group = group = QActionGroup(self)
        group.setExclusive(True)
        for icon, mode in (
            (
                QIconifyIcon("ic:outline-check-box-outline-blank"),
                PositionIndicator.RECTANGLE,
            ),
            (QIconifyIcon("ic:baseline-plus"), PositionIndicator.CENTER),
            (QIconifyIcon("ic:outline-add-box"), PositionIndicator.BOTH),
        ):
            action = cast("QAction", group.addAction(icon, mode.value))
            action.setCheckable(True)
            action.setIconVisibleInMenu(True)
            if mode is PositionIndicator.RECTANGLE:
                action.setChecked(True)
        self.addActions(group.actions())


class StageExplorerToolbar(QToolBar):
    """A custom toolbar for the StageExplorer widget.

    This toolbar contains actions to control the stage explorer, such as zooming to fit,
    snapping images, and showing the current stage position.
    """

    if TYPE_CHECKING:

        def addAction(self, icon: QIcon, text: str) -> QAction: ...

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setIconSize(QSize(22, 22))
        self.setMovable(False)
        self.setContentsMargins(0, 0, 0, 0)

        self.clear_action = self.addAction(
            QIconifyIcon("mdi:close", color=GRAY),
            "Clear View",
        )
        self.zoom_to_fit_action = self.addAction(
            QIconifyIcon("mdi:fullscreen", color=GRAY),
            "Zoom to Fit",
        )
        self.auto_zoom_to_fit_action = self.addAction(
            QIcon(str(Path(__file__).parent / "auto_zoom_to_fit_icon.svg")),
            "Auto Zoom to Fit",
        )
        self.auto_zoom_to_fit_action.setCheckable(True)
        self.snap_action = self.addAction(
            QIconifyIcon("mdi:camera-outline", color=GRAY),
            "Snap on Double Click",
        )
        self.snap_action.setCheckable(True)
        self.poll_stage_action = self.addAction(
            QIconifyIcon("mdi:map-marker-outline", color=GRAY),
            "Show FOV Position",
        )
        self.poll_stage_action.setCheckable(True)
        poll_btn = cast("QToolButton", self.widgetForAction(self.poll_stage_action))

        # menu that can be shown on right-click
        menu = PositionIndicatorMenu(self)
        self.marker_mode_action_group = menu.action_group
        poll_btn.setMenu(menu)
        poll_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

        self.show_grid_action = self.addAction(
            QIconifyIcon("mdi:grid", color=GRAY),
            "Show Grid",
        )
        self.show_grid_action.setCheckable(True)

        self.map_memory_action = self.addAction(
            QIconifyIcon("mdi:memory", color=GRAY),
            "Map Memory Limit",
        )
        memory_btn = cast("QToolButton", self.widgetForAction(self.map_memory_action))
        self.map_memory_menu = MapMemoryMenu(self)
        memory_btn.setMenu(self.map_memory_menu)
        # InstantPopup, unlike poll_stage_action/scan_action above: those
        # have a primary action distinct from their menu (toggle polling,
        # start a scan), which is what the split MenuButtonPopup look
        # communicates -- click the icon for that action, click the arrow for
        # options. This button's only job is showing the limit editor, so
        # the whole button should do that, not imply a separate primary
        # action that doesn't exist.
        memory_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        self.addSeparator()
        self.delete_rois_action = self.addAction(
            QIconifyIcon("mdi:vector-square-remove", color=GRAY),
            "Delete All ROIs",
        )
        self.addSeparator()
        self.scan_action = self.addAction(
            QIconifyIcon("ph:path-duotone", color=GRAY),
            "Scan Selected ROI",
        )
        scan_btn = cast("QToolButton", self.widgetForAction(self.scan_action))
        self.scan_menu = ScanMenu(self)
        scan_btn.setMenu(self.scan_menu)
        scan_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.send_to_mda_action = self.addAction(
            QIconifyIcon("mdi:send", color=GRAY),
            "Send to MDA",
        )
        self.stop_scan_action = self.addAction(
            QIconifyIcon("bi:sign-stop", color=GRAY),
            "Stop Scan",
        )


class ScanMenu(QMenu):
    """Menu widget that exposes scan grid options (overlap + scan order)."""

    valueChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        opts_widget = QWidget(self)
        form = QFormLayout(opts_widget)
        form.setContentsMargins(8, 8, 8, 8)

        self._overlap_spin = QDoubleSpinBox(opts_widget)
        self._overlap_spin.setRange(-100, 100)
        self._overlap_spin.setSuffix(" %")
        form.addRow("Overlap", self._overlap_spin)

        self._mode_cbox = QEnumComboBox(self, OrderMode)
        self._mode_cbox.setCurrentEnum(OrderMode.spiral)
        form.addRow("Order", self._mode_cbox)

        action = QWidgetAction(self)
        action.setDefaultWidget(opts_widget)
        self.addAction(action)

        self._overlap_spin.valueChanged.connect(self._emit)
        self._mode_cbox.currentTextChanged.connect(self._emit)

    def value(self) -> tuple[float, useq.OrderMode]:
        """Return (overlap, order_mode)."""
        return self._overlap_spin.value(), self._mode_cbox.currentEnum()

    @Slot()
    def _emit(self) -> None:
        self.valueChanged.emit(self.value())


class MapMemoryMenu(QMenu):
    """Menu widget that exposes the map's memory budget (see `max_map_memory_mb`).

    Both the spinbox's range and its initial value scale with this machine's
    real RAM (see `_map_memory_defaults`) rather than a flat, one-size-fits-
    none number.
    """

    valueChanged = Signal(float)  # GB

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        opts_widget = QWidget(self)
        form = QFormLayout(opts_widget)
        form.setContentsMargins(8, 8, 8, 8)

        minimum, maximum, default = _map_memory_defaults()
        self._limit_spin = QDoubleSpinBox(opts_widget)
        self._limit_spin.setRange(minimum, maximum)
        self._limit_spin.setDecimals(1)
        self._limit_spin.setSingleStep(0.5)
        self._limit_spin.setSuffix(" GB")
        self._limit_spin.setValue(default)
        form.addRow("Map memory limit:", self._limit_spin)

        action = QWidgetAction(self)
        action.setDefaultWidget(opts_widget)
        self.addAction(action)

        self._limit_spin.valueChanged.connect(self._emit)

    def value(self) -> float:
        """Return the current limit, in GB."""
        return float(self._limit_spin.value())

    def set_value(self, gigabytes: float) -> None:
        """Set the displayed limit, in GB, without emitting `valueChanged`."""
        with QSignalBlocker(self._limit_spin):
            self._limit_spin.setValue(gigabytes)

    def set_range(self, minimum: float, maximum: float) -> None:
        """Set the spinbox's allowed range, in GB."""
        self._limit_spin.setRange(minimum, maximum)

    @Slot(float)
    def _emit(self, value: float) -> None:
        self.valueChanged.emit(value)


@dataclass(slots=True)
class AffineState:
    """Cached state for the affine transformation of the stage viewer.

    Call refresh() to recompute the state based on the current camera settings.
    """

    mmc: CMMCorePlus
    pixel_size_um: float = field(init=False)
    pixel_size_affine: tuple[float, ...] = field(init=False)
    system_affine: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.refresh()

    # Public

    def refresh(self) -> None:
        """Recompute everything that depends on camera settings."""
        self.pixel_size_um = self.mmc.getPixelSizeUm()
        self.pixel_size_affine = self.mmc.getPixelSizeAffine()
        self.system_affine = self._compute_system_affine()

    def system_affine_translated(self, x: float, y: float) -> np.ndarray:
        """Return the system affine matrix translated to the given (x, y) position."""
        # fast path: copy cached rotation/scale part and just update translation
        matrix = self.system_affine.copy()
        matrix[0:2, 3] = (x, y)
        return matrix

    # Private helpers

    def _compute_system_affine(self) -> np.ndarray:
        flip_x = flip_y = False
        if cam := self.mmc.getCameraDevice():
            flip_x = self.mmc.getProperty(cam, Keyword.Transpose_MirrorX) == "1"
            flip_y = self.mmc.getProperty(cam, Keyword.Transpose_MirrorY) == "1"

        if self._pixel_config_is_identity():
            return self._linear_matrix(flip_x, flip_y)
        return self._pixel_config_matrix(flip_x, flip_y)

    def _linear_matrix(
        self, rotation: float = 0, flip_x: bool = False, flip_y: bool = False
    ) -> np.ndarray:
        """Build linear transformation matrix for rotation and scaling.

        The matrix is still 4x4, but has no translation component.
        """
        # rotation matrix
        R = np.eye(4)
        rotation_rad = np.deg2rad(rotation)
        cos_ = np.cos(rotation_rad)
        sin_ = np.sin(rotation_rad)
        R[:2, :2] = np.array([[cos_, -sin_], [sin_, cos_]])
        # scaling matrix
        # a pixel size of 0 (e.g. no calibration for the current objective/
        # resolution preset) would otherwise produce a singular matrix, which
        # crashes when applied as a vispy transform. Fall back to 1.0 (no
        # scaling) in that case.
        pixel_size_um = self.pixel_size_um or 1.0
        S = np.diag([pixel_size_um, pixel_size_um, 1, 1])
        # flip the image if required
        if flip_x:
            S[0, 0] *= -1
        if flip_y:
            S[1, 1] *= -1
        return cast("np.ndarray", R @ S)

    def _pixel_config_is_identity(self) -> bool:
        return np.allclose(self.pixel_size_affine, (1.0, 0.0, 0.0, 0.0, 1.0, 0.0))

    def _pixel_config_matrix(
        self, flip_x: bool = False, flip_y: bool = False
    ) -> np.ndarray:
        """Return the current pixel configuration affine, if set.

        If the pixel configuration is not set (i.e. is the identity matrix),
        it will return None.
        """
        tform = np.eye(4)
        tform[:2, :3] = np.array(self.pixel_size_affine).reshape(2, 3)
        # flip the image if required
        # TODO: Should this ALWAYS be done?
        if flip_x:
            tform[0, 0] *= -1
        if flip_y:
            tform[1, 1] *= -1
        return tform
