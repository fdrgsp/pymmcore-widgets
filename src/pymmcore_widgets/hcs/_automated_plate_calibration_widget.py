from __future__ import annotations

from importlib.util import find_spec
from threading import Event
from typing import TYPE_CHECKING

import numpy as np
from pymmcore_plus import Keyword
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon
from superqt.utils import create_worker, signals_blocked

from pymmcore_widgets._icons import StandardIcon
from pymmcore_widgets._util import SeparatorWidget, memory_limit_defaults
from pymmcore_widgets.control._stage_explorer._stage_explorer import AffineState
from pymmcore_widgets.useq_widgets._well_plate_widget import WellPlateView

from ._plate_calibration import (
    fit_plate_calibration,
    min_calibration_wells,
    supports_automated_calibration,
    valid_calibration_selection,
    validated_calibration_plan,
)
from ._plate_calibration_widget import CalibrationInfo
from ._plate_survey import (
    MAX_TILES,
    SurveyGeometry,
    SurveyTile,
    acquire_survey,
    storage_binning,
    survey_mosaic,
)
from ._survey_canvas import MOSAIC_MAX_PX, SurveyCanvas
from ._well_calibration_widget import (
    CANCEL_ICON,
    COMBO_ROLE,
    FOUR,
    ICON_GREEN,
    ICON_RED,
    RUN_ICON,
    TWO,
    Mode,
    WellCalibrationWidget,
)
from ._well_detection import (
    DetectedWell,
    DetectionResult,
    DetectionSettings,
    detect_wells,
    match_selected_wells,
)
from ._well_detection_settings import WellDetectionSettingsDialog

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    import useq
    from pymmcore_plus import CMMCorePlus
    from qtpy.QtGui import QShowEvent


# Camera properties that change the field of view or its orientation.
_CAMERA_GEOMETRY = {
    "Binning",
    Keyword.Transpose_MirrorX.value,
    Keyword.Transpose_MirrorY.value,
}


def _scan_and_stitch(
    core: CMMCorePlus,
    geometry: SurveyGeometry,
    cancel: Event,
    progress: Callable[[int, int], None],
    on_tile: Callable[[SurveyTile], None],
    max_bytes: float,
) -> tuple[tuple[SurveyTile, ...], Any]:
    """Acquire the scan, then stitch a display mosaic off the GUI thread."""
    tiles = acquire_survey(core, geometry, cancel, progress, on_tile, max_bytes)
    if not tiles:
        return tiles, None
    return tiles, survey_mosaic(tiles, geometry, 0, max_side=MOSAIC_MAX_PX)


INF = float("inf")
# Above this deviation from the plate grid, the calibration is applied with a warning.
WARN_ERROR_UM = 100.0


# Ways to define the scan rectangle: it is the bounding box of the points.
SCAN_AREA_MODES = (
    Mode("2 Opposing Corners", 2, TWO),
    Mode("4 Edge Points", 4, FOUR),
)


class _ScanAreaWidget(WellCalibrationWidget):
    """The manual point editor, recording the corners or edges of the scan area."""

    cornersChanged = Signal()

    def __init__(self, mmcore: CMMCorePlus, parent: QWidget) -> None:
        super().__init__(parent, mmcore)
        self.well_label.setText("Scan Area")
        self.setInstructions(
            "Record the corners or edges of an area containing the selected wells.",
            "First, select the wells on the plate diagram in the "
            "<b>Select Wells</b> tab. "
            "Then define a scan area that contains all selected wells, including "
            "their entire edges, and record each point with <b>Set</b>:<br>"
            "- <b>2 Opposing Corners</b>: move the stage to two opposite corners "
            "of the area.<br>"
            "- <b>4 Edge Points</b>: move the stage to one point on each side of "
            "the area (left, right, top and bottom), e.g. just outside the "
            "outermost well edges. Useful when the corners are hard to find, "
            "since they lie between wells.<br>"
            "The scan covers the rectangle enclosing all recorded points. "
            "It is preferable to use a low magnification objective (e.g., 4x, 10x) "
            "and transmitted light to focus on the well edges.",
        )
        self.setCircularWell(False)
        with signals_blocked(self._calibration_mode_wdg):
            self._calibration_mode_wdg.clear()
            for i, mode in enumerate(SCAN_AREA_MODES):
                self._calibration_mode_wdg.addItem(mode.icon, mode.text)
                self._calibration_mode_wdg.setItemData(i, mode, COMBO_ROLE)
        self._on_mode_changed(SCAN_AREA_MODES[0])
        self._table.itemChanged.connect(self._on_points_changed)

    def _on_points_changed(self, _item: object) -> None:
        # PySide6 rejects forwarding itemChanged's argument to a no-argument signal
        self.cornersChanged.emit()

    def mode(self) -> Mode:
        """Return how the scan area is being recorded."""
        return self._calibration_mode_wdg.currentMode()

    def corners(self) -> tuple[tuple[float, float], ...]:
        """Return recorded points in table order."""
        return tuple((x, y) for _, x, y in self._table.positions())

    def bounds(self) -> tuple[float, float, float, float] | None:
        """Return the (x0, y0, x1, y1) rectangle enclosing every recorded point."""
        points = self.corners()
        if len(points) != self.mode().points:
            return None
        (x0, y0), (x1, y1) = np.min(points, axis=0), np.max(points, axis=0)
        if x1 <= x0 or y1 <= y0:
            return None
        return float(x0), float(y0), float(x1), float(y1)

    def _validate_calibration(self) -> None:
        # This sets the same green completion tick as manual well calibration.
        bounds = self.bounds()
        self.setWellCenter(bounds[:2] if bounds else None)


class AutomatedPlateCalibrationWidget(QWidget):
    """Scan an area, view images live, then detect and test plate calibration."""

    centersAccepted = Signal(object, float)
    testPositionRequested = Signal(object)
    busyChanged = Signal(bool)
    _progress = Signal(int, int, int)
    _tileReady = Signal(int, object)

    def __init__(self, mmcore: CMMCorePlus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mmc = mmcore
        self._plate: useq.WellPlate | None = None
        self._cancel = Event()
        self._worker: Any = None
        self._generation = 0
        self._run_generation = -1
        self._tiles: tuple[SurveyTile, ...] = ()
        self._geometry: SurveyGeometry | None = None
        self._result: DetectionResult | None = None
        self._assigned: dict[tuple[int, int], DetectedWell] = {}
        self._detection_settings = DetectionSettings()
        self.destroyed.connect(self._cancel.set)

        self._corners = _ScanAreaWidget(mmcore, self)
        self._corners.layout().setContentsMargins(0, 0, 0, 0)
        # Same RAM-based range and default as the Stage Explorer map memory limit.
        minimum, maximum, default = memory_limit_defaults()
        self._memory_limit = QDoubleSpinBox()
        self._memory_limit.setRange(minimum, maximum)
        self._memory_limit.setDecimals(1)
        self._memory_limit.setSingleStep(0.5)
        self._memory_limit.setSuffix(" GB")
        self._memory_limit.setValue(default)
        self._memory_limit.setToolTip("Maximum memory used by the scan images.")
        memory = QHBoxLayout()
        memory.addWidget(QLabel("Memory limit:"))
        memory.addWidget(self._memory_limit, 1)
        self._start = QPushButton("Start Scan")
        self._start.setIcon(QIconifyIcon(RUN_ICON, color=ICON_GREEN))
        self._start.setEnabled(False)
        self._stop = QPushButton("Cancel")
        self._stop.setIcon(QIconifyIcon(CANCEL_ICON, color=ICON_RED))
        self._stop.hide()
        # the side panel with the point table (same width as Manual's)
        self._panel = left = QGroupBox()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.addWidget(self._corners)
        left_layout.addStretch()
        left_layout.addWidget(SeparatorWidget())
        left_layout.addLayout(memory)
        left_layout.addWidget(self._start)
        left_layout.addWidget(self._stop)

        self._canvas_host = QWidget()
        self._canvas_layout = QVBoxLayout(self._canvas_host)
        self._canvas_layout.setContentsMargins(0, 0, 0, 0)
        self._canvas_host.setMinimumSize(320, 240)
        # Create the OpenGL canvas now, before the window is shown: adding the first
        # QOpenGLWidget to a visible window makes Qt recreate the native window,
        # which flickers and can move it (as the other vispy widgets do).
        self._viewer = SurveyCanvas(self._canvas_host)
        self._canvas_layout.addWidget(self._viewer)
        self._detect = QPushButton("Detect Wells")
        self._settings_button = QPushButton()
        self._settings_button.setIcon(QIconifyIcon(StandardIcon.SETTINGS))
        self._settings_button.setToolTip("Well detection settings")
        self._settings_button.setAccessibleName("Well Detection Settings")
        self._show_detection = QCheckBox("Show detection")
        self._show_detection.setChecked(True)
        self._apply = QPushButton("Apply Calibration")
        controls = QHBoxLayout()
        controls.addWidget(self._detect)
        controls.addWidget(self._settings_button)
        controls.addWidget(self._show_detection)
        controls.addStretch()
        controls.addWidget(self._apply)
        scan = QWidget()
        scan_layout = QVBoxLayout(scan)
        scan_layout.setContentsMargins(0, 0, 0, 0)
        scan_layout.addWidget(self._canvas_host, 1)
        scan_layout.addLayout(controls)
        self._plate_test = WellPlateView()
        self._plate_test.setDragMode(WellPlateView.DragMode.NoDrag)
        self._plate_test.setSelectionMode(WellPlateView.SelectionMode.NoSelection)
        self._plate_test.setDrawWellEdgeSpots(True)
        self._plate_test.setDrawLabels(False)
        test = QWidget()
        test_layout = QVBoxLayout(test)
        test_layout.setContentsMargins(0, 0, 0, 0)
        test_layout.addWidget(QLabel("Double-click a point to move the stage."))
        test_layout.addWidget(self._plate_test, 1)
        self._plate_view = WellPlateView()
        self._plate_view.setSelectionMode(WellPlateView.SelectionMode.MultiSelection)
        self._plate_view.setSelectedColor(Qt.GlobalColor.yellow)
        self._plate_view.setClearButtonVisible(True)
        selection = QWidget()
        selection_layout = QVBoxLayout(selection)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        selection_layout.addWidget(self._plate_view, 1)
        self._tab_wdg = QTabWidget()
        self._tab_wdg.addTab(selection, "Select Wells")
        self._tab_wdg.addTab(scan, "Calibrate Plate")
        self._tab_wdg.addTab(test, "Test Calibration")
        self._tab_wdg.setTabEnabled(1, False)
        self._tab_wdg.setTabEnabled(2, False)
        top = QHBoxLayout()
        top.addWidget(left, 0)
        top.addWidget(self._tab_wdg, 1)
        self._progress_bar = QProgressBar()
        self._progress_bar.hide()
        self._status = CalibrationInfo()
        self._status.setText(self._selection_hint())
        main = QVBoxLayout(self)
        main.addLayout(top, 1)
        main.addWidget(self._progress_bar)
        main.addWidget(self._status)

        self._start.clicked.connect(self._acquire)
        self._stop.clicked.connect(self.cancel)
        self._detect.clicked.connect(self._detect_wells)
        self._settings_button.clicked.connect(self._edit_settings)
        self._show_detection.toggled.connect(self._toggle_detection)
        self._apply.clicked.connect(self._apply_centers)
        self._corners.cornersChanged.connect(self._corners_changed)
        self._memory_limit.valueChanged.connect(self._memory_limit_changed)
        self._plate_view.selectionChanged.connect(self._selection_changed)
        self._tab_wdg.currentChanged.connect(self._on_tab_changed)
        self._progress.connect(self._on_progress)
        self._tileReady.connect(self._on_tile)
        self._plate_test.positionDoubleClicked.connect(self.testPositionRequested.emit)
        # The scan size depends on the field of view: objective, ROI, and binning.
        self._mmc.events.pixelSizeChanged.connect(self._fov_changed)
        self._mmc.events.systemConfigurationLoaded.connect(self._fov_changed)
        self._mmc.events.roiSet.connect(self._fov_changed)
        self._mmc.events.pixelSizeAffineChanged.connect(self._fov_changed)
        self._mmc.events.propertyChanged.connect(self._on_property_changed)
        self._update_controls()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._update_controls()

    def setPlate(self, plate: useq.WellPlate) -> None:
        """Discard scan drafts when the selected plate model changes."""
        if plate != self._plate:
            self._plate = plate
            self._detection_settings = DetectionSettings()
            with signals_blocked(self._plate_view):
                self._plate_view.drawPlate(plate)
                self._plate_view.clearSelection()
            self._status.setText(self._selection_hint())
            with signals_blocked(self._corners._table):
                self._corners._table.clear_all()
            self._corners._validate_calibration()
            self._invalidate()
            self.setCalibration(None)
        self._update_controls()

    def setCalibration(self, plan: useq.WellPlatePlan | None) -> None:
        """Synchronize the automated Test Calibration tab with the applied plan."""
        if plan is None:
            self._tab_wdg.setCurrentIndex(0)
            self._plate_test.clear()
        else:
            self._plate_test.drawPlate(plan)
        self._tab_wdg.setTabEnabled(2, plan is not None)

    def isBusy(self) -> bool:
        """Whether a scan or detection worker is still active."""
        return self._worker is not None

    def cancel(self) -> None:
        """Cancel between hardware operations or detection steps."""
        self._cancel.set()

    def _invalidate(self, *, clear_images: bool = True) -> None:
        self.cancel()
        self._generation += 1
        self._result = None
        self._assigned.clear()
        if clear_images:
            self._tiles = ()
            self._geometry = None
            self._viewer.clear()
        else:
            self._viewer.set_detections((), [])
        self._update_controls()

    def _corners_changed(self) -> None:
        self._invalidate()
        self._show_ready_message()

    def _selection_changed(self) -> None:
        self._invalidate(clear_images=False)
        self._show_ready_message()

    def _valid_selection(self) -> bool:
        min_wells = min_calibration_wells(self._plate) if self._plate else 3
        return valid_calibration_selection(
            self._plate_view.selectedIndices(), min_wells
        )

    def _show_ready_message(self) -> None:
        """Show the next setup step, or the scan size at the current FOV."""
        warning = False
        if not self._valid_selection():
            text = self._selection_hint()
        elif self._corners.wellCenter() is None:
            text = f"Record the {self._corners.mode().text.lower()} of the scan area."
        else:
            try:
                text, fits = self._scan_size()
                warning = not fits
            except ValueError as error:
                text, warning = str(error), True
        self._status.setText(text, warning=warning)
        self._update_controls()  # missing detector/pixel size take precedence

    def _max_bytes(self) -> float:
        return float(self._memory_limit.value() * 1024**3)

    def _scan_size(self) -> tuple[str, bool]:
        """Describe the scan at the current objective, ROI, and binning."""
        geometry = self._read_geometry()
        nx, ny = geometry.grid()
        h, w = geometry.shape
        px_x, px_y = np.linalg.norm(geometry.matrix(), axis=0)
        nbytes = nx * ny * h * w * self._mmc.getBytesPerPixel()
        size = (
            f"{nx} x {ny} = {nx * ny} tiles at a field of view of "
            f"{w * px_x / 1000:.2f} x {h * px_y / 1000:.2f} mm "
            f"({nbytes / 1024**3:.2f} GB)"
        )
        if nx * ny > MAX_TILES:
            return (
                f"The scan needs {size}; the limit is {MAX_TILES} tiles. "
                "Use a lower magnification or select fewer wells."
            ), False
        if nbytes > self._max_bytes():
            return (
                f"The scan needs {size}, above the memory limit. "
                "Raise the limit or select fewer wells."
            ), False
        return f"Ready to scan {size}.", True

    def _memory_limit_changed(self) -> None:
        # Keep scan and detection results on screen; only refresh the setup step.
        if self._tiles or self._result is not None:
            self._update_controls()
        else:
            self._show_ready_message()

    def _scan_fits(self) -> bool:
        try:
            return self._scan_size()[1]
        except ValueError:
            return False

    def _selection_hint(self) -> str:
        rims = " whose edges are fully inside the scan area."
        if self._plate is not None:
            if (n := self._plate.rows * self._plate.columns) < 3:
                return f"Select {'the well' if n == 1 else 'both wells'}" + rims
            if self._plate.rows == 1:
                return "Select at least 3 wells in this row" + rims
            if self._plate.columns == 1:
                return "Select at least 3 wells in this column" + rims
        return (
            "Select a 2 x 2 block (recommended), or 3+ wells in one row/column," + rims
        )

    def _scan_ready(self) -> bool:
        return self._corners.wellCenter() is not None and self._valid_selection()

    def _on_tab_changed(self, index: int) -> None:
        self._update_controls()

    def _fov_changed(self) -> None:
        self._invalidate()
        self._show_ready_message()

    def _on_property_changed(self, device: str, prop: str, value: object) -> None:
        if device == self._mmc.getCameraDevice() and prop in _CAMERA_GEOMETRY:
            self._fov_changed()

    def _read_geometry(self) -> SurveyGeometry:
        bounds = self._corners.bounds()
        if bounds is None:
            raise ValueError("Record the scan area first.")
        if not self._mmc.getCameraDevice() or not self._mmc.getXYStageDevice():
            raise ValueError("A camera and motorized XY stage are required.")
        px = self._mmc.getPixelSizeUm()
        if not np.isfinite(px) or px <= 0:
            raise ValueError("Set the pixel size before using automated calibration.")
        # Store tiles at a well-relative resolution: enough for detection, and
        # small enough that 4x/10x scans of large wells fit in memory.
        well_um = max(self._plate.well_size) * 1000 if self._plate else 0
        b = storage_binning(px, well_um)
        h, w = self._mmc.getImageHeight(), self._mmc.getImageWidth()
        # Same camera-to-stage mapping as the Stage Explorer: the pixel-size
        # affine when configured (rotation/flip), plus camera mirror settings.
        linear = AffineState(self._mmc).system_affine[:2, :2] * b
        geometry = SurveyGeometry(
            bounds,
            (h // b, w // b),
            tuple(float(v) for v in linear.ravel()),  # type: ignore[arg-type]
            binning=b,
        )
        geometry.grid()  # validate the corners
        return geometry

    def _update_controls(self) -> None:
        supported = bool(self._plate and supports_automated_calibration(self._plate))
        installed = find_spec("skimage") is not None
        px = self._mmc.getPixelSizeUm()
        calibrated = bool(np.isfinite(px) and px > 0)
        idle = not self.isBusy()
        self._corners.setEnabled(idle and self._tab_wdg.currentIndex() != 2)
        self._start.setEnabled(
            idle
            and supported
            and installed
            and calibrated
            and self._scan_ready()
            and self._scan_fits()
        )
        self._stop.setVisible(not idle)
        self._detect.setEnabled(
            idle
            and bool(self._tiles)
            and installed
            and supported
            and self._scan_ready()
        )
        self._settings_button.setEnabled(idle and supported)
        self._apply.setEnabled(idle and self._labels_ready())
        self._plate_test.setEnabled(idle)
        self._plate_view.setEnabled(idle)
        ready = self._scan_ready() and calibrated and supported
        if not ready and self._tab_wdg.currentIndex() == 1:
            self._tab_wdg.setCurrentIndex(0)
        self._tab_wdg.setTabEnabled(1, ready)
        if not installed:
            self._status.setText(
                "Install detection with: uv sync --extra hcs-auto", warning=True
            )
        elif not calibrated:
            self._status.setText(
                "Set the pixel size to enable automated calibration.", warning=True
            )
        elif not supported:
            self._status.setText(
                "Use Manual for a plate with a single rectangular well.",
                warning=True,
            )

    def _start_worker(
        self, function: Callable, args: tuple, returned: Callable
    ) -> None:
        self._cancel.clear()
        self._run_generation = self._generation
        self._worker = create_worker(
            function,
            *args,
            _start_thread=False,
            _connect={
                "returned": returned,
                "errored": self._on_error,
                "finished": self._on_finished,
            },
        )
        self._update_controls()
        self.busyChanged.emit(True)
        self._worker.start()

    def _acquire(self) -> None:
        if self.isBusy() or self._plate is None:
            return
        if self._mmc.mda.is_running():
            self._status.setText(
                "Stop the running acquisition before surveying.", warning=True
            )
            return
        if not self._scan_ready():
            self._show_ready_message()
            return
        if self._mmc.isSequenceRunning():
            self._mmc.stopSequenceAcquisition()  # live mode: stop it and scan
        try:
            geometry = self._read_geometry()
            self._invalidate()
            self._geometry = geometry
            self._tab_wdg.setCurrentIndex(1)
            generation = self._generation

            def progress(value: int, total: int) -> None:
                self._progress.emit(generation, value, total)

            def on_tile(tile: SurveyTile) -> None:
                self._tileReady.emit(generation, tile)

            self._progress_bar.setRange(0, len(geometry.positions()))
            self._progress_bar.setValue(0)
            self._progress_bar.show()
            self._status.setText("Scanning… Images appear as they are acquired.")
            self._start_worker(
                _scan_and_stitch,
                (
                    self._mmc,
                    geometry,
                    self._cancel,
                    progress,
                    on_tile,
                    self._max_bytes(),
                ),
                self._on_scan_complete,
            )
        except (ValueError, RuntimeError) as error:
            self._status.setText(str(error), warning=True)

    def _on_tile(self, generation: int, tile: SurveyTile) -> None:
        if generation == self._generation and self._geometry is not None:
            self._viewer.add_tile(tile, self._geometry)

    def _on_progress(self, generation: int, value: int, total: int) -> None:
        if generation == self._generation:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(value)

    def _on_scan_complete(self, result: tuple[tuple[SurveyTile, ...], Any]) -> None:
        if self._run_generation == self._generation and not self._cancel.is_set():
            self._tiles, mosaic = result
            if mosaic is not None:
                image, _, origin, scale = mosaic
                self._viewer.show_mosaic(image, origin, scale)
            self._status.setText(
                "Scan complete. Set detection parameters with the gear, "
                "then select Detect Wells."
            )

    def _detect_wells(self) -> None:
        if (
            self.isBusy()
            or not self._tiles
            or self._geometry is None
            or self._plate is None
        ):
            return
        self._invalidate(clear_images=False)
        self._progress_bar.setRange(0, 0)
        self._progress_bar.show()
        self._status.setText("Detecting well edges…")
        self._start_worker(
            detect_wells,
            (
                self._tiles,
                self._geometry,
                self._plate,
                self._cancel,
                self._detection_settings,
            ),
            self._on_result,
        )

    def _on_error(self, error: BaseException) -> None:
        if self._run_generation == self._generation:
            self._status.setText(str(error), warning=True)

    def _on_finished(self) -> None:
        self._worker = None
        self._progress_bar.hide()
        self._update_controls()
        self.busyChanged.emit(False)

    def _on_result(self, result: DetectionResult) -> None:
        if self._cancel.is_set() or self._run_generation != self._generation:
            return
        self._result = result
        assert self._plate is not None
        try:
            self._assigned = match_selected_wells(
                result.wells, self._plate, self._plate_view.selectedIndices()
            )
            self._show_calibration(
                f"Detected {len(result.wells)} wells matching the selection. "
                "Check the labels on the overlay and apply calibration."
            )
        except ValueError as error:
            self._assigned.clear()
            self._status.setText(str(error), warning=True)
        self._draw_result()
        self._update_controls()

    def _draw_result(self) -> None:
        if self._result is None:
            return
        assert self._plate is not None
        names = {
            id(well): str(self._plate.all_well_names[idx])
            for idx, well in self._assigned.items()
        }
        labels = [
            names.get(id(well), str(i + 1)) for i, well in enumerate(self._result.wells)
        ]
        self._viewer.set_detections(self._result.wells, labels)

    def _toggle_detection(self, visible: bool) -> None:
        self._viewer.set_detection_visible(visible)

    def _edit_settings(self) -> None:
        if self.isBusy():
            return
        dialog = WellDetectionSettingsDialog(self._detection_settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            settings = dialog.value()
            if settings != self._detection_settings:
                self._detection_settings = settings
                self._invalidate(clear_images=False)
                self._status.setText(
                    "Settings updated. Select Detect Wells to rerun on the scan."
                )
        dialog.deleteLater()

    def _labels_ready(self) -> bool:
        if self._result is None or self._plate is None:
            return False
        try:
            validated_calibration_plan(self._plate, self._calibration_centers(), INF)
        except ValueError:
            return False
        return True

    def _calibration_centers(self) -> dict[tuple[int, int], tuple[float, float]]:
        return {idx: well.xy for idx, well in self._assigned.items()}

    def _apply_centers(self) -> None:
        if self._result is None or self._plate is None or self.isBusy():
            return
        try:
            centers = self._calibration_centers()
            # Apply is never blocked by the fit error; _show_calibration warns.
            self.centersAccepted.emit(centers, INF)
            self._show_calibration(
                f"Calibration applied from {len(centers)} wells. "
                "Use Test Calibration to check other wells."
            )
        except ValueError as error:
            self._status.setText(str(error), warning=True)

    def _show_calibration(self, note: str) -> None:
        """Show the fitted calibration; warn when wells deviate from the grid."""
        assert self._plate is not None
        centers = self._calibration_centers()
        fit = fit_plate_calibration(centers, self._plate)
        _, max_error = validated_calibration_plan(self._plate, centers, INF)
        warning = ""
        if max_error > WARN_ERROR_UM:
            warning = (
                f"Wells deviate up to {max_error:.0f} µm from the plate grid "
                f"(more than {WARN_ERROR_UM:.0f} µm). Check the well labels and "
                "use Test Calibration; a larger block of wells averages this out."
            )
        note = f"Maximum deviation {max_error:.1f} µm. {note}"
        self._status.setCalibration(self._plate, *fit, note=note, warning=warning)
