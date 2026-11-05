from __future__ import annotations

from threading import Event
from unittest.mock import Mock

import numpy as np
import pytest
import useq
from qtpy.QtWidgets import QWidget

from pymmcore_widgets.hcs._plate_calibration import validated_calibration_plan
from pymmcore_widgets.hcs._plate_calibration_widget import PlateCalibrationWidget
from pymmcore_widgets.hcs._plate_survey import (
    MAX_TILES,
    SurveyGeometry,
    SurveyTile,
    acquire_survey,
    survey_mosaic,
)
from pymmcore_widgets.hcs._well_detection import (
    DetectedWell,
    DetectionResult,
    assign_well_indices,
    detect_wells,
)


class _Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tiles = []
        self.wells = ()
        self.labels = []
        self.detection_visible = True

    def add_tile(self, tile, geometry):
        self.tiles.append(tile)

    def set_detections(self, wells, labels):
        self.wells, self.labels = wells, labels

    def set_detection_visible(self, visible):
        self.detection_visible = visible

    def clear(self):
        self.tiles.clear()
        self.wells = ()
        self.labels = []


@pytest.fixture(autouse=True)
def _mock_survey_canvas(monkeypatch):
    # Test acquisition/state transitions without invoking a GPU driver.
    monkeypatch.setattr(
        "pymmcore_widgets.hcs._automated_plate_calibration_widget.SurveyCanvas", _Canvas
    )


def _plate():
    return useq.WellPlate(rows=2, columns=2, well_spacing=1.5, well_size=1)


@pytest.mark.parametrize("rotation", [0, 17, -53])
def test_shared_fit_and_atomic_centers(qtbot, global_mmcore, rotation):
    plate = _plate()
    angle = np.deg2rad(rotation)
    # Independent physical stage coordinates in useq's row/rotation convention.
    origin = np.array([1234.0, 5678.0])
    cols = np.array([np.cos(angle), np.sin(angle)]) * 1500
    rows = np.array([np.sin(angle), -np.cos(angle)]) * 1500
    centers = {
        (0, 0): tuple(origin),
        (0, 1): tuple(origin + cols),
        (1, 0): tuple(origin + rows),
    }
    plan, error = validated_calibration_plan(plate, centers, 1)
    assert error < 1e-7
    assert plan.rotation == pytest.approx(rotation)
    wdg = PlateCalibrationWidget(mmcore=global_mmcore)
    qtbot.addWidget(wdg)
    wdg.setValue(plate)
    with qtbot.waitSignal(wdg.calibrationChanged) as sig:
        wdg.setCalibratedWellCenters(centers, max_error_um=1)
    assert sig.args == [True]
    assert wdg.value() == plan
    for idx, center in centers.items():
        child = wdg._calibration_widgets[idx]
        assert child.wellCenter() == center
        assert next(iter(child._table.positions()))[1:] == pytest.approx(
            center, abs=0.005
        )
    bad = {**centers, (1, 1): (9000, 9000)}
    with pytest.raises(ValueError):
        wdg.setCalibratedWellCenters(bad)
    assert wdg.value() == plan
    wdg.setValue("24-well")
    assert not wdg._calibrated_wells
    assert wdg._rotation is None


@pytest.mark.parametrize(
    "centers",
    [
        {(0, 0): (0, 0), (0, 1): (1500, 0)},
        {(0, 0): (0, 0), (0, 1): (1500, 0), (1, 1): (3000, 0)},
        {(0, 0): (0, 0), (0, 1): (1500, 0), (1, 0): (0, 1500)},
        {(0, 0): (0, 0), (0, 1): (1800, 0), (1, 0): (0, -1800)},
        {(0, 0): (0, 0), (0, 1): (1500, 0), (2, 0): (0, -3000)},
        {(0, 0): (0, 0), (0, 1): (float("nan"), 0), (1, 0): (0, -1500)},
    ],
)
def test_invalid_calibration(centers):
    with pytest.raises(ValueError):
        validated_calibration_plan(_plate(), centers, 50)


@pytest.mark.parametrize("matrix", [(15, 0, 0, 15), (13, -7, -7, -13)])
def test_tiled_rim_detection(matrix):
    pytest.importorskip("skimage")
    geometry = SurveyGeometry((-700, -2200, 2200, 700), (128, 128), matrix)
    reference = np.array([(0, 0), (1500, 0), (0, -1500)])
    tiles = []
    rr, cc = np.mgrid[:128, :128]
    uv = np.stack([cc - 64, rr - 64], axis=-1)
    for xy in geometry.positions():
        stage = uv @ geometry.matrix().T + xy
        image = np.full((128, 128), 200.0)
        for center in reference:
            distance = np.linalg.norm(stage - center, axis=-1)
            image -= 150 * np.exp(-(((distance - 500) / 30) ** 2))
        image += np.random.default_rng(0).normal(0, 0.3, image.shape)
        tiles.append(SurveyTile(image.astype(np.float32), xy))
    overview, valid, _, _ = survey_mosaic(tuple(tiles), geometry, 1000)
    assert valid[2:-2, 2:-2].all()
    assert overview.max() > overview.min()
    result = detect_wells(tuple(tiles), geometry, _plate(), Event())
    assert len(result.wells) == 3
    for center in reference:
        assert min(np.linalg.norm(np.array(w.xy) - center) for w in result.wells) < 20
    labels = assign_well_indices(result.wells, _plate(), (0, 0), 0)
    assert set(labels) == {(0, 0), (0, 1), (1, 0)}
    plan, error = validated_calibration_plan(
        _plate(), {i: w.xy for i, w in labels.items()}, 30
    )
    assert plan.a1_center_xy == pytest.approx((0, 0), abs=20)
    assert error < 30


def test_survey_cancellation_and_cleanup():
    core = Mock()
    core.mda.is_running.return_value = False
    core.isSequenceRunning.return_value = False
    core.getBytesPerPixel.return_value = 2
    core.getShutterDevice.return_value = "Shutter"
    core.getShutterOpen.return_value = True
    core.getXYPosition.return_value = (50, 50)
    geometry = SurveyGeometry((0, 0, 100, 100), (16, 16), (10, 0, 0, 10))
    cancelled = Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        acquire_survey(core, geometry, cancelled, Mock())
    core.setXYPosition.assert_not_called()
    core.setShutterOpen.assert_called_once_with("Shutter", True)
    core.reset_mock()
    core.snap.side_effect = RuntimeError("Camera failed")
    with pytest.raises(RuntimeError, match="Camera failed"):
        acquire_survey(core, geometry, Event(), Mock())
    core.setShutterOpen.assert_called_once_with("Shutter", True)


def test_reject_conflicting_acquisition():
    core = Mock()
    core.mda.is_running.return_value = True
    with pytest.raises(ValueError, match="Stop live"):
        acquire_survey(
            core,
            SurveyGeometry((0, 0, 100, 100), (16, 16), (10, 0, 0, 10)),
            Event(),
            Mock(),
        )
    core.setXYPosition.assert_not_called()


def test_last_frame_settings_change_is_rejected():
    core = Mock()
    core.mda.is_running.return_value = False
    core.isSequenceRunning.return_value = False
    core.getFocusDevice.return_value = ""
    core.getXYPosition.return_value = (50, 50)
    core.getBytesPerPixel.return_value = 2
    core.getExposure.side_effect = [10, 10, 20]
    core.snap.return_value = np.zeros((16, 16), dtype=np.uint16)
    with pytest.raises(ValueError, match="changed during exposure"):
        acquire_survey(
            core,
            SurveyGeometry((0, 0, 100, 100), (16, 16), (10, 0, 0, 10)),
            Event(),
            Mock(),
        )


def test_manual_available_without_detector(qtbot, global_mmcore, monkeypatch):
    from pymmcore_widgets.hcs import HCSWizard
    from pymmcore_widgets.hcs import _automated_plate_calibration_widget as module

    monkeypatch.setattr(module, "find_spec", lambda name: None)
    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    assert not wizard.calibration_page.automated._start.isEnabled()
    assert "--extra hcs-auto" in wizard.calibration_page.automated._status.text()
    plan = useq.WellPlatePlan(plate="96-well", a1_center_xy=(1, 2), rotation=0)
    wizard.setValue(plan)
    value = wizard.value()
    assert value is not None
    assert value.a1_center_xy == plan.a1_center_xy
    assert value.rotation == plan.rotation
    assert wizard.calibration_page.isComplete()


@pytest.mark.parametrize("matrix", [(0, 0, 0, 0), (1, 1, 1, 1)])
def test_invalid_affine(matrix):
    with pytest.raises(ValueError, match="affine"):
        SurveyGeometry((0, 0, 100, 100), (16, 16), matrix).positions()


def _prepare_area(auto, selected=((0, 0), (0, 1), (1, 0))):
    auto._plate_view.setSelectedIndices(selected)
    auto._corners._table._set_row(0, -700, -2200)
    auto._corners._table._set_row(1, 2200, 700)


def test_detection_matches_selection_and_apply(qtbot, global_mmcore):
    from pymmcore_widgets.hcs import HCSWizard

    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    page = wizard.calibration_page
    page.widget.setValue(_plate())
    auto = page.automated
    assert [auto._tab_wdg.tabText(i) for i in range(3)] == [
        "Select Wells",
        "Calibrate Plate",
        "Test Calibration",
    ]
    _prepare_area(auto)
    wells = tuple(DetectedWell(xy, 500, 2, 1) for xy in [(0, 0), (1500, 0), (0, -1500)])
    result = DetectionResult(np.zeros((100, 100)), (-700, -2200), 30, wells)
    auto._run_generation = auto._generation
    auto._cancel.clear()
    auto._on_result(result)
    assert not page.isComplete()
    assert auto._viewer.labels == ["A1", "A2", "B1"]
    assert auto._apply.isEnabled()
    auto._apply.click()
    assert page.isComplete()
    assert page.widget.value().a1_center_xy == (0, 0)
    assert auto._tab_wdg.isTabEnabled(2)
    assert len(page.widget._calibrated_wells) == 3
    # A detection count mismatch must retain the previously applied calibration.
    extra = DetectedWell((1500, -1500), 500, 2, 1)
    auto._on_result(
        DetectionResult(result.image, result.origin, result.pixel_um, (*wells, extra))
    )
    assert not auto._apply.isEnabled()
    assert "4 wells" in auto._status.text()
    assert "3 wells are selected" in auto._status.text()
    assert page.isComplete()
    page.widget.setValue("24-well")
    auto._on_result(result)
    assert auto._result is None
    assert not page.isComplete()


def test_worker_cancel_defers_wizard_close(qtbot, global_mmcore, monkeypatch):
    from pymmcore_widgets.hcs import HCSWizard
    from pymmcore_widgets.hcs import _automated_plate_calibration_widget as module

    entered, release = Event(), Event()

    def survey(core, geometry, cancel, progress, on_tile=None, max_bytes=None):
        entered.set()
        assert release.wait(5)
        assert cancel.is_set()
        raise InterruptedError("Survey cancelled")

    monkeypatch.setattr(module, "acquire_survey", survey)
    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    wizard.calibration_page.widget.setValue("96-well")
    auto = wizard.calibration_page.automated
    _prepare_area(auto)
    geometry = SurveyGeometry((0, 0, 100, 100), (16, 16), (10, 0, 0, 10))
    monkeypatch.setattr(auto, "_read_geometry", lambda: geometry)
    try:
        auto._acquire()
        qtbot.waitUntil(entered.is_set)
        assert auto.isBusy()
        assert not wizard.calibration_page.isComplete()
        with qtbot.assertNotEmitted(wizard.rejected):
            wizard.reject()
        assert wizard._pending_done == 0
    finally:
        release.set()
    with qtbot.waitSignal(wizard.rejected):
        qtbot.waitUntil(lambda: not auto.isBusy())
    assert "cancelled" in auto._status.text()


def test_corner_editor_and_pixel_size(qtbot, global_mmcore, monkeypatch):
    from pymmcore_widgets.hcs import HCSWizard

    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    wizard.calibration_page.widget.setValue("96-well")
    auto = wizard.calibration_page.automated
    corners = auto._corners
    assert corners._table.rowCount() == 2
    assert corners._calibration_mode_wdg.count() == 1
    assert corners._calibration_mode_wdg.currentText() == "2 Opposing Corners"
    assert corners.wellCenter() is None
    assert not auto._start.isEnabled()
    global_mmcore.setXYPosition(0, 0)
    global_mmcore.waitForSystem()
    corners._set_button.click()
    assert len(corners.corners()) == 1
    global_mmcore.setXYPosition(3000, -3000)
    global_mmcore.waitForSystem()
    corners._set_button.click()
    assert corners.corners() == ((0, 0), (3000, -3000))
    assert corners.wellCenter() is not None  # same completion tick as Manual
    assert not auto._start.isEnabled()  # corners alone do not identify the scan wells
    assert not auto._tab_wdg.isTabEnabled(1)
    auto._plate_view.setSelectedIndices([(0, 0), (0, 1), (1, 0)])
    assert auto._start.isEnabled()
    assert auto._tab_wdg.isTabEnabled(1)
    # Identity/unset affine is irrelevant; scan spacing uses configured pixel size.
    geometry = auto._read_geometry()
    px = global_mmcore.getPixelSizeUm()
    b = geometry.binning
    assert geometry.pixel_to_stage == (px * b, 0, 0, px * b)
    h, w = global_mmcore.getImageHeight(), global_mmcore.getImageWidth()
    assert geometry.shape == (h // b, w // b)
    global_mmcore.setPixelSizeUm(global_mmcore.getCurrentPixelSizeConfig(), 0)
    auto._fov_changed()
    assert not auto._start.isEnabled()
    assert "pixel size" in auto._status.text()
    with pytest.raises(ValueError, match="pixel size"):
        auto._read_geometry()
    corners._clear_all_button.click()
    assert corners.wellCenter() is None


def test_stream_scan_detect_and_rerun(qtbot, global_mmcore, monkeypatch):
    from pymmcore_widgets.hcs import HCSWizard
    from pymmcore_widgets.hcs import _automated_plate_calibration_widget as module

    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    page = wizard.calibration_page
    page.widget.setValue(_plate())
    auto = page.automated
    _prepare_area(auto)
    geometry = SurveyGeometry((-700, -2200, 2200, 700), (16, 16), (200, 0, 0, 200))
    tiles = tuple(SurveyTile(np.ones((16, 16)), xy) for xy in [(0, 0), (1500, -1500)])
    wells = tuple(DetectedWell(xy, 500, 2, 1) for xy in [(0, 0), (1500, 0), (0, -1500)])
    result = DetectionResult(np.zeros((100, 100)), (-700, -2200), 30, wells)
    scan_calls, detection_calls = [], []

    def scan(core, geo, cancel, progress, on_tile, max_bytes):
        scan_calls.append(geo)
        for i, tile in enumerate(tiles):
            on_tile(tile)
            progress(i + 1, len(tiles))
        return tiles

    def detect(acquired, geo, plate, cancel, settings):
        detection_calls.append(settings)
        assert acquired == tiles
        return result

    monkeypatch.setattr(module, "acquire_survey", scan)
    monkeypatch.setattr(module, "detect_wells", detect)
    monkeypatch.setattr(auto, "_read_geometry", lambda: geometry)
    auto._acquire()
    qtbot.waitUntil(lambda: not auto.isBusy())
    assert auto._tab_wdg.currentIndex() == 1  # Start Scan switches to the canvas
    assert not detection_calls
    assert auto._result is None
    assert "Scan complete" in auto._status.text()
    auto._detect.click()
    qtbot.waitUntil(lambda: not auto.isBusy() and auto._result is not None)
    viewer = auto._viewer
    assert len(viewer.tiles) == 2
    assert len(detection_calls) == 1  # detection runs only after the explicit click
    assert viewer.labels == ["A1", "A2", "B1"]
    assert auto._detect.isEnabled()
    auto._show_detection.setChecked(False)
    assert not viewer.detection_visible
    assert len(viewer.tiles) == 2
    auto._show_detection.setChecked(True)
    assert viewer.detection_visible
    auto._apply.click()
    assert auto._tab_wdg.isTabEnabled(2)
    assert page.isComplete()
    # The test tab shares the applied plan and the existing manual stage action.
    auto._plate_test.positionDoubleClicked.emit(useq.Position(x=123, y=456))
    global_mmcore.waitForSystem()
    assert tuple(global_mmcore.getXYPosition()) == (123, 456)
    auto._detect.click()
    qtbot.waitUntil(lambda: not auto.isBusy() and len(detection_calls) == 2)
    assert len(scan_calls) == 1  # repeat detection never reacquires or moves XY
    assert len(viewer.tiles) == 2
    assert tuple(global_mmcore.getXYPosition()) == (123, 456)
    assert page.isComplete()

    class EditedSettings(module.WellDetectionSettingsDialog):
        def exec(self):
            self._sigma.setValue(2.5)
            self.accept()
            return self.DialogCode.Accepted

    monkeypatch.setattr(module, "WellDetectionSettingsDialog", EditedSettings)
    auto._settings_button.click()
    assert auto._result is None
    assert len(viewer.tiles) == 2
    assert not viewer.wells
    auto._detect.click()
    qtbot.waitUntil(lambda: not auto.isBusy() and len(detection_calls) == 3)
    assert detection_calls[-1].edge_sigma == 2.5
    assert len(scan_calls) == 1


def test_acquisition_streams_owned_tiles(global_mmcore):
    px = global_mmcore.getPixelSizeUm()
    geometry = SurveyGeometry(
        (0, 0, 100, 100),
        (global_mmcore.getImageHeight(), global_mmcore.getImageWidth()),
        (px, 0, 0, px),
    )
    streamed = []
    result = acquire_survey(global_mmcore, geometry, Event(), Mock(), streamed.append)
    assert len(streamed) == len(result) == 1
    assert streamed[0] is result[0]
    saved = result[0].image.copy()
    global_mmcore.snap()
    np.testing.assert_array_equal(saved, result[0].image)


@pytest.mark.parametrize("rotation", [17, -31])
def test_selected_b2_area_calculates_rotation(
    qtbot, global_mmcore, monkeypatch, rotation
):
    from pymmcore_widgets.hcs import HCSWizard

    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    wizard.calibration_page.widget.setValue("96-well")
    auto = wizard.calibration_page.automated
    a = np.deg2rad(rotation)
    origin = np.array([20_000, 40_000])
    col = 9000 * np.array([np.cos(a), np.sin(a)])
    row = 9000 * np.array([np.sin(a), -np.cos(a)])
    indices = [(2, 2), (1, 1), (2, 1), (1, 2)]  # C3, B2, C2, B3
    wells = tuple(
        DetectedWell(tuple(origin + r * row + c * col), 3200, 2, 1) for r, c in indices
    )
    _prepare_area(auto, tuple(indices))
    auto._run_generation = auto._generation
    auto._cancel.clear()
    auto._on_result(DetectionResult(np.zeros((10, 10)), (0, 0), 1, wells))
    assert auto._viewer.labels == ["C3", "B2", "C2", "B3"]
    assert auto._apply.isEnabled()
    auto._apply.click()
    plan = wizard.calibration_page.widget.value()
    assert plan.a1_center_xy == pytest.approx(origin)
    assert plan.rotation == pytest.approx(rotation)
    assert auto._tab_wdg.isTabEnabled(2)


def test_canvas_initialization_is_not_reentrant(qtbot, global_mmcore, monkeypatch):
    from qtpy.QtGui import QShowEvent

    from pymmcore_widgets.hcs import HCSWizard
    from pymmcore_widgets.hcs import _automated_plate_calibration_widget as module

    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    auto = wizard.calibration_page.automated

    class ReentrantCanvas(_Canvas):
        def __init__(self, parent=None):
            super().__init__(parent)
            # A native VisPy canvas can process Qt show events during construction.
            auto.showEvent(QShowEvent())

    monkeypatch.setattr(module, "SurveyCanvas", ReentrantCanvas)
    viewer = auto._ensure_viewer()
    assert auto._canvas_layout.count() == 1
    assert viewer is auto._ensure_viewer()


def test_selection_gates_scan_and_retains_images(qtbot, global_mmcore, monkeypatch):
    from pymmcore_widgets.hcs import HCSWizard
    from pymmcore_widgets.hcs import _automated_plate_calibration_widget as module

    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    auto = wizard.calibration_page.automated
    wizard.calibration_page.widget.setValue("96-well")
    acquire = Mock()
    monkeypatch.setattr(module, "acquire_survey", acquire)
    assert auto._tab_wdg.currentIndex() == 0
    assert not auto._tab_wdg.isTabEnabled(1)
    # A well selection without corners is insufficient.
    auto._plate_view.setSelectedIndices([(1, 1), (1, 2), (2, 1)])
    assert not auto._start.isEnabled()
    auto._acquire()
    acquire.assert_not_called()
    auto._corners._table._set_row(0, 0, 0)
    auto._corners._table._set_row(1, 3000, -3000)
    assert auto._start.isEnabled()
    assert auto._tab_wdg.isTabEnabled(1)
    auto._tab_wdg.setCurrentIndex(1)
    geometry = auto._read_geometry()
    tiles = (SurveyTile(np.ones(geometry.shape), (1500, -1500)),)
    auto._geometry, auto._tiles = geometry, tiles
    # A diagonal (not a single row/column) remains underdetermined.
    auto._plate_view.setSelectedIndices([(0, 0), (1, 1), (2, 2)])
    assert not auto._start.isEnabled()
    assert not auto._tab_wdg.isTabEnabled(1)
    assert auto._tab_wdg.currentIndex() == 0
    assert auto._tiles is tiles  # selection changes need no reacquisition
    auto._plate_view.setSelectedIndices([(1, 1), (1, 2), (2, 1)])
    assert auto._detect.isEnabled()
    auto._corners._clear_all_button.click()
    assert not auto._tiles
    assert not auto._start.isEnabled()
    assert not auto._tab_wdg.isTabEnabled(1)


def test_detection_must_match_selected_pattern():
    from pymmcore_widgets.hcs._well_detection import match_selected_wells

    plate = useq.WellPlate.from_str("96-well")
    wells = tuple(
        DetectedWell(xy, 3200, 1, 1) for xy in [(0, 0), (9000, 0), (0, -9000)]
    )
    with pytest.raises(ValueError, match="arrangement does not match"):
        match_selected_wells(wells, plate, ((1, 1), (1, 2), (2, 2)))


@pytest.mark.parametrize("rotation", [-17, 12])
@pytest.mark.parametrize(
    "mode", ["row-strip", "column-strip", "plate-row", "plate-column"]
)
def test_row_column_calibration(qtbot, global_mmcore, mode, rotation):
    from pymmcore_widgets.hcs import HCSWizard
    from pymmcore_widgets.hcs._well_detection import match_selected_wells

    if mode == "row-strip":
        plate = useq.WellPlate(rows=1, columns=5, well_size=1, well_spacing=(1.5, 0))
        indices = ((0, 1), (0, 2), (0, 3))
    elif mode == "column-strip":
        plate = useq.WellPlate(rows=5, columns=1, well_size=1, well_spacing=(0, 1.5))
        indices = ((1, 0), (2, 0), (3, 0))
    elif mode == "plate-row":
        plate = useq.WellPlate(rows=4, columns=5, well_size=1, well_spacing=(1.5, 2))
        indices = ((2, 1), (2, 2), (2, 3))
    else:
        plate = useq.WellPlate(rows=5, columns=4, well_size=1, well_spacing=(2, 1.5))
        indices = ((1, 2), (2, 2), (3, 2))
    origin = np.array([1234.0, 5678.0])
    angle = np.deg2rad(rotation)
    col = plate.well_spacing[0] * 1000 * np.array([np.cos(angle), np.sin(angle)])
    row = plate.well_spacing[1] * 1000 * np.array([np.sin(angle), -np.cos(angle)])
    wells = tuple(
        DetectedWell(tuple(origin + r * row + c * col), 500, 1, 1)
        for r, c in reversed(indices)
    )
    assigned = match_selected_wells(wells, plate, indices)
    centers = {idx: well.xy for idx, well in assigned.items()}
    plan, error = validated_calibration_plan(plate, centers, 1)
    assert plan.a1_center_xy == pytest.approx(origin, abs=1e-4)
    assert plan.rotation == pytest.approx(rotation)
    assert error < 1e-4
    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    wizard.calibration_page.widget.setValue(plate)
    auto = wizard.calibration_page.automated
    assert "in an L" not in auto._status.text()
    _prepare_area(auto, indices)
    assert auto._start.isEnabled()
    assert auto._tab_wdg.isTabEnabled(1)
    auto._run_generation = auto._generation
    auto._cancel.clear()
    auto._on_result(DetectionResult(np.zeros((10, 10)), (0, 0), 1, wells))
    assert auto._apply.isEnabled()
    auto._apply.click()
    assert wizard.calibration_page.widget.value() == plan
    assert auto._tab_wdg.isTabEnabled(2)
    # Manual uses exactly the same row/column calculation after automated hydration.
    fitted = wizard.calibration_page.widget._origin_spacing_rotation()
    assert fitted[0] == pytest.approx(origin, abs=1e-4)
    assert fitted[2] == pytest.approx(rotation)
    assert all(item.rect().width() > 0 for item in auto._plate_test._well_edge_spots)


@pytest.mark.parametrize("single_row", [True, False])
def test_strip_rim_detection(single_row):
    pytest.importorskip("skimage")
    plate = useq.WellPlate(
        rows=1 if single_row else 5,
        columns=5 if single_row else 1,
        well_size=1,
        well_spacing=(1.5, 0) if single_row else (0, 1.5),
    )
    selected = tuple((0, i) if single_row else (i, 0) for i in (1, 2, 3))
    angle = np.deg2rad(11)
    col = 1500 * np.array([np.cos(angle), np.sin(angle)])
    row = 1500 * np.array([np.sin(angle), -np.cos(angle)])
    reference = np.array(
        [np.array([1000, -2000]) + r * row + c * col for r, c in selected]
    )
    lo, hi = reference.min(axis=0) - 750, reference.max(axis=0) + 750
    geometry = SurveyGeometry((*lo, *hi), (128, 128), (15, 0, 0, 15))
    rr, cc = np.mgrid[:128, :128]
    uv = np.stack([cc - 64, rr - 64], axis=-1)
    tiles = []
    for xy in geometry.positions():
        stage = uv @ geometry.matrix().T + xy
        image = np.full((128, 128), 200.0)
        for center in reference:
            distance = np.linalg.norm(stage - center, axis=-1)
            image -= 150 * np.exp(-(((distance - 500) / 30) ** 2))
        image += np.random.default_rng(0).normal(0, 0.3, image.shape)
        tiles.append(SurveyTile(image.astype(np.float32), xy))
    result = detect_wells(tuple(tiles), geometry, plate, Event())
    assert len(result.wells) == 3
    from pymmcore_widgets.hcs._well_detection import match_selected_wells

    assigned = match_selected_wells(result.wells, plate, selected)
    plan, error = validated_calibration_plan(
        plate, {i: w.xy for i, w in assigned.items()}, 50
    )
    assert plan.a1_center_xy == pytest.approx((1000, -2000), abs=30)
    assert plan.rotation == pytest.approx(11, abs=0.5)
    assert error < 50


def _shape_tiles(plate, selected, rotation, origin=(1000.0, -2000.0)):
    from pymmcore_widgets.hcs._well_detection import _shape_distance

    a = np.deg2rad(rotation)
    sx, sy = np.array(plate.well_spacing) * 1000
    col = sx * np.array([np.cos(a), np.sin(a)])
    row = sy * np.array([np.sin(a), -np.cos(a)])
    reference = np.array([np.array(origin) + r * row + c * col for r, c in selected])
    half = np.array(plate.well_size) * 500
    lo, hi = reference.min(axis=0) - half.max() * 1.5, reference.max(axis=0)
    geometry = SurveyGeometry(
        (*lo, *(hi + half.max() * 1.5)), (128, 128), (15, 0, 0, 15)
    )
    rr, cc = np.mgrid[:128, :128]
    uv = np.stack([cc - 64, rr - 64], axis=-1).reshape(-1, 2)
    tiles = []
    for xy in geometry.positions():
        stage = uv @ geometry.matrix().T + xy
        image = np.full(len(stage), 200.0)
        for center in reference:
            params = np.array([*center, *half, a])
            distance = _shape_distance(stage, params, not plate.circular_wells)
            image -= 150 * np.exp(-((distance / 30) ** 2))
        image = image.reshape(128, 128)
        image += np.random.default_rng(0).normal(0, 0.3, image.shape)
        tiles.append(SurveyTile(image.astype(np.float32), xy))
    return tuple(tiles), geometry


@pytest.mark.parametrize(
    "plate, selected, rotation",
    [
        # elliptical wells
        (
            useq.WellPlate(
                rows=2, columns=2, well_spacing=(2, 1.5), well_size=(1.6, 1)
            ),
            ((0, 0), (0, 1), (1, 0), (1, 1)),
            7,
        ),
        # square wells
        (
            useq.WellPlate(
                rows=2, columns=2, well_spacing=1.5, well_size=1, circular_wells=False
            ),
            ((0, 0), (0, 1), (1, 0), (1, 1)),
            -6,
        ),
        # rectangular wells in a strip
        (
            useq.WellPlate(
                rows=1,
                columns=3,
                well_spacing=(2, 0),
                well_size=(1.6, 1),
                circular_wells=False,
            ),
            ((0, 0), (0, 1), (0, 2)),
            4,
        ),
        # two-well plate (2 x 1)
        (
            useq.WellPlate(rows=2, columns=1, well_spacing=(0, 1.5), well_size=1),
            ((0, 0), (1, 0)),
            9,
        ),
        # single circular and elliptical wells
        (useq.WellPlate(rows=1, columns=1, well_spacing=0, well_size=1), ((0, 0),), 0),
        (
            useq.WellPlate(rows=1, columns=1, well_spacing=0, well_size=(1.6, 1)),
            ((0, 0),),
            0,
        ),
    ],
)
def test_shape_and_small_plate_detection(plate, selected, rotation):
    pytest.importorskip("skimage")
    from pymmcore_widgets.hcs._well_detection import match_selected_wells

    tiles, geometry = _shape_tiles(plate, selected, rotation)
    result = detect_wells(tiles, geometry, plate, Event())
    assert len(result.wells) == len(selected)
    assert all(w.coverage == pytest.approx(1) for w in result.wells)
    assigned = match_selected_wells(result.wells, plate, selected)
    plan, error = validated_calibration_plan(
        plate, {i: w.xy for i, w in assigned.items()}, 10
    )
    assert plan.a1_center_xy == pytest.approx((1000, -2000), abs=5)
    assert plan.rotation == pytest.approx(rotation, abs=0.2)
    assert error < 5


def test_single_rectangular_well_is_not_automated():
    from pymmcore_widgets.hcs._plate_calibration import supports_automated_calibration

    coverslip = useq.WellPlate(
        rows=1, columns=1, well_size=(20, 10), well_spacing=0, circular_wells=False
    )
    assert not supports_automated_calibration(coverslip)
    with pytest.raises(ValueError, match="single rectangular well"):
        detect_wells(
            (SurveyTile(np.ones((4, 4)), (0, 0)),),
            SurveyGeometry((0, 0, 10, 10), (4, 4), (1, 0, 0, 1)),
            coverslip,
            Event(),
        )


def test_two_well_plate_automated(qtbot, global_mmcore):
    from pymmcore_widgets.hcs import HCSWizard

    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    page = wizard.calibration_page
    plate = useq.WellPlate(rows=2, columns=1, well_spacing=(0, 1.5), well_size=1)
    page.widget.setValue(plate)
    auto = page.automated
    assert page.tabs.isTabVisible(1)
    assert "both wells" in auto._status.text()
    _prepare_area(auto, ((0, 0), (1, 0)))
    assert auto._start.isEnabled()
    wells = (DetectedWell((0, 0), 500, 1, 1), DetectedWell((0, -1500), 500, 1, 1))
    auto._run_generation = auto._generation
    auto._cancel.clear()
    auto._on_result(DetectionResult(np.zeros((10, 10)), (0, 0), 1, wells))
    assert "Plate calibrated" in auto._status.text()
    auto._apply.click()
    assert page.isComplete()
    assert page.widget.value().a1_center_xy == pytest.approx((0, 0))


def test_mismatch_reports_selected_and_detected_grid():
    from pymmcore_widgets.hcs._well_detection import match_selected_wells

    plate = useq.WellPlate.from_str("96-well")
    wells = tuple(
        DetectedWell((c * 9000, -r * 9000), 3200, 1, 1)
        for r in range(3)
        for c in range(3)
    )
    selected = ((1, 1), (1, 2), (2, 1), (2, 2))
    with pytest.raises(
        ValueError, match=r"9 wells \(3 x 3\).*4 wells are selected \(2 x 2\)"
    ):
        match_selected_wells(wells, plate, selected)


@pytest.mark.parametrize("rotation", [-15, 12])
def test_rotated_rectangular_wells(rotation):
    # Proposal templates are rotated too, so tilted straight rims are still found.
    pytest.importorskip("skimage")
    plate = useq.WellPlate(
        rows=2,
        columns=2,
        well_spacing=(2, 1.5),
        well_size=(1.6, 1),
        circular_wells=False,
    )
    selected = ((0, 0), (0, 1), (1, 0), (1, 1))
    tiles, geometry = _shape_tiles(plate, selected, rotation)
    result = detect_wells(tiles, geometry, plate, Event())
    assert len(result.wells) == 4
    assert all(w.rotation_deg == pytest.approx(rotation, abs=0.5) for w in result.wells)


def test_scan_size_follows_fov(qtbot, global_mmcore):
    from pymmcore_widgets.hcs import HCSWizard

    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    wizard.calibration_page.widget.setValue("96-well")
    auto = wizard.calibration_page.automated
    auto._plate_view.setSelectedIndices([(1, 1), (1, 2), (2, 1)])
    auto._corners._table._set_row(0, 0, 0)
    auto._corners._table._set_row(1, 3000, -3000)
    assert "Ready to scan" in auto._status.text()
    assert auto._start.isEnabled()
    # A smaller camera ROI shrinks the field of view: the scan no longer fits.
    global_mmcore.setROI(0, 0, 32, 32)
    assert f"limit is {MAX_TILES} tiles" in auto._status.text()
    assert not auto._start.isEnabled()
    global_mmcore.clearROI()
    # CMMCore.clearROI emits nothing; the camera ROI widget emits roiSet after it.
    cam = global_mmcore.getCameraDevice()
    global_mmcore.events.roiSet.emit(cam, *global_mmcore.getROI())
    assert "Ready to scan" in auto._status.text()
    # So does switching to a higher magnification (smaller pixel size).
    global_mmcore.setPixelSizeUm(global_mmcore.getCurrentPixelSizeConfig(), 0.01)
    assert f"limit is {MAX_TILES} tiles" in auto._status.text()
    assert not auto._start.isEnabled()


def test_survey_stores_binned_tiles(global_mmcore):
    from pymmcore_widgets.hcs._plate_survey import bin_image, storage_binning

    image = np.arange(7 * 9, dtype=np.uint16).reshape(7, 9)
    binned = bin_image(image, 2)
    assert binned.shape == (3, 4)
    assert binned.dtype == np.uint16
    # 12-well wells (22 mm) at 4x on a 1.625 um/px camera: ~6.8 um stored pixels.
    assert storage_binning(1.625, 22_000) == 6
    assert storage_binning(1.625, 1_000) == 1
    px = global_mmcore.getPixelSizeUm()
    h, w = global_mmcore.getImageHeight(), global_mmcore.getImageWidth()
    geometry = SurveyGeometry(
        (0, 0, 100, 100), (h // 4, w // 4), (px * 4, 0, 0, px * 4), binning=4
    )
    tiles = acquire_survey(global_mmcore, geometry, Event(), Mock())
    assert tiles[0].image.shape == (h // 4, w // 4)
    with pytest.raises(ValueError, match="memory limit"):
        acquire_survey(global_mmcore, geometry, Event(), Mock(), max_bytes=10)


def test_memory_limit_gates_scan(qtbot, global_mmcore):
    from pymmcore_widgets.hcs import HCSWizard

    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    wizard.calibration_page.widget.setValue("96-well")
    auto = wizard.calibration_page.automated
    auto._plate_view.setSelectedIndices([(1, 1), (1, 2), (2, 1)])
    auto._corners._table._set_row(0, 0, 0)
    auto._corners._table._set_row(1, 3000, -3000)
    assert auto._start.isEnabled()
    auto._memory_limit.setValue(auto._memory_limit.minimum())
    auto._max_bytes = lambda: 1.0  # below any real scan
    auto._memory_limit_changed()
    assert "above the memory limit" in auto._status.text()
    assert not auto._start.isEnabled()


def test_instructions_are_info_tooltips(qtbot, global_mmcore):
    from pymmcore_widgets.hcs import HCSWizard

    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    page = wizard.calibration_page
    page.widget.setValue("96-well")
    manual = page.widget._current_calibration_widget()
    assert "well's center" in manual._info_button.toolTip()
    corners = page.automated._corners
    assert "opposite corners" in corners._info_button.toolTip()
    assert "Select Wells" in corners._info_button.toolTip()
    corners._info_button.click()  # clicking also shows the tooltip
