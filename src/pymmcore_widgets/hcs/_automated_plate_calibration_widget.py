from __future__ import annotations

from importlib.util import find_spec
from threading import Event
from typing import TYPE_CHECKING

import numpy as np
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
)
from ._survey_canvas import SurveyCanvas
from ._well_calibration_widget import COMBO_ROLE, TWO, Mode, WellCalibrationWidget
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


class _ScanAreaWidget(WellCalibrationWidget):
    """The manual point editor, fixed to two opposing survey corners."""

    cornersChanged = Signal()

    def __init__(self, mmcore: CMMCorePlus, parent: QWidget) -> None:
        super().__init__(parent, mmcore)
        self.well_label.setText("Scan Area")
        self.setInstructions(
            "Record two opposite corners of an area containing the selected wells.",
            "First, select the wells on the plate diagram in the "
            "<b>Select Wells</b> tab. "
            "Then move the stage to two <b>opposite corners</b> of a scan area that "
            "contains all selected wells, including their complete rims, "
            "and record each corner with <b>Set</b>. "
            "It is preferable to use a low magnification objective (e.g., 4x, 10x) "
            "and transmitted light to focus on the well rims.",
        )
        self.setCircularWell(False)
        mode = Mode("2 Opposing Corners", 2, TWO)
        with signals_blocked(self._calibration_mode_wdg):
            self._calibration_mode_wdg.clear()
            self._calibration_mode_wdg.addItem(mode.icon, mode.text)
            self._calibration_mode_wdg.setItemData(0, mode, COMBO_ROLE)
        self._on_mode_changed(mode)
        self._table.itemChanged.connect(self.cornersChanged.emit)

    def corners(self) -> tuple[tuple[float, float], ...]:
        """Return recorded corners in table order."""
        return tuple((x, y) for _, x, y in self._table.positions())

    def _validate_calibration(self) -> None:
        points = self.corners()
        ready = (
            len(points) == 2
            and points[0][0] != points[1][0]
            and points[0][1] != points[1][1]
        )
        # This sets the same green completion tick as manual well calibration.
        self.setWellCenter(points[0] if ready else None)


class AutomatedPlateCalibrationWidget(QWidget):
    """Scan two corners, view images live, then detect and test plate calibration."""

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
        self._viewer: SurveyCanvas | None = None
        self._creating_viewer = False
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
        self._start.setEnabled(False)
        self._stop = QPushButton("Cancel")
        self._stop.hide()
        left = QGroupBox()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.addWidget(self._corners)
        left_layout.addWidget(SeparatorWidget())
        left_layout.addLayout(memory)
        left_layout.addWidget(self._start)
        left_layout.addWidget(self._stop)
        left_layout.addStretch()

        self._canvas_host = QWidget()
        self._canvas_layout = QVBoxLayout(self._canvas_host)
        self._canvas_layout.setContentsMargins(0, 0, 0, 0)
        self._canvas_host.setMinimumSize(320, 240)
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
        self._mmc.events.propertyChanged.connect(self._on_property_changed)
        self._update_controls()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._tab_wdg.currentIndex() == 1 and not self._creating_viewer:
            self._ensure_viewer()
        self._update_controls()

    def _ensure_viewer(self) -> SurveyCanvas:
        if self._viewer is None:
            self._creating_viewer = True
            try:
                self._viewer = SurveyCanvas(self._canvas_host)
            finally:
                self._creating_viewer = False
            self._canvas_layout.addWidget(self._viewer)
            self._viewer.set_detection_visible(self._show_detection.isChecked())
        return self._viewer

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
        if self._viewer is not None:
            if clear_images:
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
            text = "Set two opposite corners of the scan area."
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
        px = geometry.pixel_to_stage[0]
        nbytes = nx * ny * h * w * self._mmc.getBytesPerPixel()
        size = (
            f"{nx} x {ny} = {nx * ny} tiles at a field of view of "
            f"{w * px / 1000:.2f} x {h * px / 1000:.2f} mm "
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
        rims = " whose complete rims are included in the scan area."
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
        if index == 1 and not self._creating_viewer:
            self._ensure_viewer()
        self._update_controls()

    def _fov_changed(self) -> None:
        self._invalidate()
        self._show_ready_message()

    def _on_property_changed(self, device: str, prop: str, value: object) -> None:
        if prop == "Binning" and device == self._mmc.getCameraDevice():
            self._fov_changed()

    def _read_geometry(self) -> SurveyGeometry:
        points = self._corners.corners()
        if len(points) != 2:
            raise ValueError("Set two opposing corners first.")
        if not self._mmc.getCameraDevice() or not self._mmc.getXYStageDevice():
            raise ValueError("A camera and motorized XY stage are required.")
        px = self._mmc.getPixelSizeUm()
        if not np.isfinite(px) or px <= 0:
            raise ValueError("Set the pixel size before using automated calibration.")
        (x0, y0), (x1, y1) = points
        # Store tiles at a well-relative resolution: enough for detection, and
        # small enough that 4x/10x scans of large wells fit in memory.
        well_um = max(self._plate.well_size) * 1000 if self._plate else 0
        b = storage_binning(px, well_um)
        h, w = self._mmc.getImageHeight(), self._mmc.getImageWidth()
        geometry = SurveyGeometry(
            (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)),
            (h // b, w // b),
            (px * b, 0, 0, px * b),
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
        if not self._scan_ready():
            self._show_ready_message()
            return
        try:
            geometry = self._read_geometry()
            self._invalidate()
            self._geometry = geometry
            self._tab_wdg.setCurrentIndex(1)
            self._ensure_viewer()
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
                acquire_survey,
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
            self._ensure_viewer().add_tile(tile, self._geometry)

    def _on_progress(self, generation: int, value: int, total: int) -> None:
        if generation == self._generation:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(value)

    def _on_scan_complete(self, tiles: tuple[SurveyTile, ...]) -> None:
        if self._run_generation == self._generation and not self._cancel.is_set():
            self._tiles = tiles
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
        self._status.setText("Detecting well rims…")
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
            _, max_error = validated_calibration_plan(
                self._plate, self._calibration_centers(), 100
            )
            self._show_calibration(
                f"Detected {len(result.wells)} wells matching the selection "
                f"(maximum error {max_error:.1f} µm). "
                "Check the overlay and apply calibration."
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
        self._ensure_viewer().set_detections(self._result.wells, labels)

    def _toggle_detection(self, visible: bool) -> None:
        if self._viewer is not None:
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
            validated_calibration_plan(self._plate, self._calibration_centers(), 100)
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
            _, max_error = validated_calibration_plan(self._plate, centers, 100)
            self.centersAccepted.emit(centers, 100)
            self._show_calibration(
                f"Calibration applied from {len(centers)} wells "
                f"(maximum error {max_error:.1f} µm). "
                "Use Test Calibration to check other wells."
            )
        except ValueError as error:
            self._status.setText(str(error), warning=True)

    def _show_calibration(self, note: str) -> None:
        assert self._plate is not None
        fit = fit_plate_calibration(self._calibration_centers(), self._plate)
        self._status.setCalibration(self._plate, *fit, note=note)
