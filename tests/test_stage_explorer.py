from __future__ import annotations

from threading import Thread
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import numpy as np
import useq
from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QMessageBox, QToolButton
from vispy.app.canvas import MouseEvent
from vispy.scene.visuals import Image

from pymmcore_widgets.control._rois.roi_model import RectangleROI
from pymmcore_widgets.control._stage_explorer import (
    _stage_explorer as stage_explorer_mod,
)
from pymmcore_widgets.control._stage_explorer._stage_explorer import (
    ContrastSlider,
    ScanMenu,
    StageExplorer,
)
from pymmcore_widgets.control._stage_explorer._stage_viewer import StageViewer

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytestqt.qtbot import QtBot

IMG = np.random.randint(0, 255, (100, 50), dtype=np.uint8)


def _build_transform_matrix(x: float, y: float) -> np.ndarray:
    T = np.eye(4)
    T[0, 3] += x
    T[1, 3] += y
    return T


def test_stage_viewer_add_image(qtbot: QtBot) -> None:
    stage_viewer = StageViewer()
    qtbot.addWidget(stage_viewer)
    T = _build_transform_matrix(100, 150)
    stage_viewer.add_image(IMG, T.T)
    images = [i for i in stage_viewer.view.scene.children if isinstance(i, Image)]
    assert len(images) == 1
    added_img = next(iter(images))
    assert tuple(added_img.transform.matrix[3, :2]) == (100, 150)


def test_stage_viewer_clims_cmaps(qtbot: QtBot) -> None:
    stage_viewer = StageViewer()
    qtbot.addWidget(stage_viewer)
    T = _build_transform_matrix(100, 150)
    stage_viewer.add_image(IMG, T.T)

    # just some smoke tests
    stage_viewer.set_clims((0, 1))


def test_stage_viewer_clear_scene(qtbot: QtBot) -> None:
    stage_viewer = StageViewer()
    qtbot.addWidget(stage_viewer)
    T = _build_transform_matrix(200, 50)
    stage_viewer.add_image(IMG, T.T)
    assert [i for i in stage_viewer.view.scene.children if isinstance(i, Image)]
    stage_viewer.clear()
    assert not [i for i in stage_viewer.view.scene.children if isinstance(i, Image)]


def test_stage_viewer_reset_view(qtbot: QtBot) -> None:
    stage_viewer = StageViewer()
    qtbot.addWidget(stage_viewer)
    T = _build_transform_matrix(500, 100)
    stage_viewer.add_image(IMG, T.T)
    stage_viewer.zoom_to_fit()
    cx, cy = stage_viewer.view.camera.rect.center
    assert round(cx) == 525  # image width is 50, center should be Tx + width/2
    assert round(cy) == 150  # image height is 100, center should be Ty + height/2


def test_stage_explorer_initialization(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    assert explorer.windowTitle() == "Stage Explorer"
    assert explorer.snap_on_double_click is True


def test_stage_explorer_snap_on_double_click(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.snap_on_double_click = True
    assert explorer.snap_on_double_click is True
    explorer.snap_on_double_click = False
    assert explorer.snap_on_double_click is False


def test_stage_explorer_add_image(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    image = np.random.randint(0, 255, (100, 100), dtype=np.uint8)
    stage_x, stage_y = 50.0, 75.0
    explorer.add_image(image, stage_x, stage_y)
    # Verify the image was added to the stage viewer
    nimages = len(list(explorer._stage_viewer._get_images()))
    assert nimages == 1


def test_stage_explorer_actions(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.add_image(IMG, 0, 0)

    snap_action = explorer._toolbar.snap_action
    assert explorer.snap_on_double_click is True
    with qtbot.waitSignal(snap_action.triggered):
        snap_action.trigger()
    assert explorer.snap_on_double_click is False

    auto_action = explorer._toolbar.auto_zoom_to_fit_action
    auto_action.trigger()
    assert explorer.auto_zoom_to_fit
    # this turns it off
    explorer._toolbar.zoom_to_fit_action.trigger()
    assert not explorer.auto_zoom_to_fit

    assert not explorer._stage_viewer._grid_lines.visible
    grid_action = explorer._toolbar.show_grid_action
    grid_action.trigger()
    assert explorer._stage_viewer._grid_lines.visible


def test_stage_explorer_move_on_click(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)

    explorer.add_image(IMG, 0, 0)
    stage_pos = explorer._mmc.getXYPosition()

    explorer._snap_on_double_click = True
    event = MouseEvent("mouse_press", pos=(100, 100), button=1)
    with qtbot.waitSignal(explorer._mmc.events.imageSnapped):
        explorer._on_mouse_double_click(event)

    assert explorer._mmc.getXYPosition() != stage_pos


def test_stage_explorer_position_indicator(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)

    poll_action = explorer._toolbar.poll_stage_action
    assert explorer._poll_stage_position is True
    assert explorer._stage_poller.isRunning()

    # wait for the poller to emit at least once
    qtbot.waitUntil(lambda: explorer._stage_pos_marker is not None, timeout=1000)
    assert explorer._stage_pos_marker is not None
    assert explorer._stage_pos_marker.visible

    with qtbot.waitSignal(poll_action.triggered):
        poll_action.trigger()

    assert explorer._poll_stage_position is False
    assert not explorer._stage_poller.isRunning()


def test_stage_poller_survives_transient_hardware_error(qtbot: QtBot) -> None:
    """A getXYPosition failure must not permanently kill the poller thread.

    Regression test: the poller's run() loop used to call getXYPosition() with
    no exception handling, so an uncaught error (e.g. a transient stage
    communication fault) ended the QThread's run() method for good -- nothing
    ever restarted it, silently freezing the position display for the rest of
    the session even after the device recovered.
    """
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    assert explorer._stage_poller.isRunning()

    real_getXYPosition = explorer._mmc.getXYPosition
    calls = {"n": 0}

    def flaky_getXYPosition(*args: object, **kwargs: object) -> Sequence[float]:
        calls["n"] += 1
        if calls["n"] <= 3:
            raise RuntimeError('Error in device "XYStage": (Error message unavailable)')
        return real_getXYPosition(*args, **kwargs)

    with patch.object(explorer._mmc, "getXYPosition", flaky_getXYPosition):
        # The thread must still be alive and retrying after several failures.
        qtbot.waitUntil(lambda: calls["n"] > 3, timeout=2000)
        assert explorer._stage_poller.isRunning()

        # And it recovers on its own once the device starts responding again.
        qtbot.waitUntil(lambda: explorer._stage_pos_marker is not None, timeout=2000)
    assert explorer._stage_poller.isRunning()


def test_mouse_hover_shows_position(qtbot: QtBot) -> None:
    viewer = StageViewer()
    viewer.show()
    qtbot.addWidget(viewer)
    # Simulate mouse move event
    event = MouseEvent("mouse_move", pos=(100, 2))
    viewer._on_mouse_move(event)

    # Check if the hover label is visible and shows the correct position
    assert viewer._hover_pos_label.isVisible()
    assert viewer._hover_pos_label.text().startswith("(")


# ---------------------------------------------------------------------------
# ScanMenu
# ---------------------------------------------------------------------------


def test_scan_menu_default_value(qtbot: QtBot) -> None:
    menu = ScanMenu()
    qtbot.addWidget(menu)
    overlap, mode = menu.value()
    assert overlap == 0.0
    assert mode == useq.OrderMode.spiral


def test_scan_menu_value_changed_signal(qtbot: QtBot) -> None:
    menu = ScanMenu()
    qtbot.addWidget(menu)
    with qtbot.waitSignal(menu.valueChanged):
        menu._overlap_spin.setValue(10.0)
    assert menu.value()[0] == 10.0


def test_scan_menu_mode_change(qtbot: QtBot) -> None:
    menu = ScanMenu()
    qtbot.addWidget(menu)
    with qtbot.waitSignal(menu.valueChanged):
        menu._mode_cbox.setCurrentEnum(useq.OrderMode.row_wise_snake)
    assert menu.value()[1] == useq.OrderMode.row_wise_snake


# ---------------------------------------------------------------------------
# StageExplorer - scan options propagation
# ---------------------------------------------------------------------------


def test_scan_options_propagate_to_manager(qtbot: QtBot) -> None:
    """Changing scan menu options updates overlap/mode on the ROI manager."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)

    explorer._on_scan_options_changed((5.0, useq.OrderMode.spiral))

    assert explorer.roi_manager.scan_overlap == 5.0
    assert explorer.roi_manager.scan_mode == useq.OrderMode.spiral


def test_scan_options_available_via_manager(qtbot: QtBot) -> None:
    """ROI manager exposes scan settings for use when building positions."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)

    explorer._toolbar.scan_menu._overlap_spin.setValue(3.0)
    explorer._toolbar.scan_menu._mode_cbox.setCurrentEnum(useq.OrderMode.spiral)

    assert explorer.roi_manager.scan_overlap == 3.0
    assert explorer.roi_manager.scan_mode == useq.OrderMode.spiral


# ---------------------------------------------------------------------------
# roi_manager - all_rois / selected_rois
# ---------------------------------------------------------------------------


def test_all_rois(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)

    assert explorer.roi_manager.all_rois() == []

    roi_a = RectangleROI((0, 0), (10, 10), fov_size=(5.0, 5.0))
    roi_b = RectangleROI((20, 20), (30, 30), fov_size=(5.0, 5.0))
    explorer.roi_manager.add_roi(roi_a)
    explorer.roi_manager.add_roi(roi_b)
    assert explorer.roi_manager.all_rois() == [roi_a, roi_b]


def test_selected_rois(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)

    roi = RectangleROI((0, 0), (10, 10), fov_size=(5.0, 5.0))
    explorer.roi_manager.add_roi(roi)
    assert explorer.roi_manager.selected_rois() == []

    explorer.roi_manager.select_roi(roi)
    assert explorer.roi_manager.selected_rois() == [roi]


# ---------------------------------------------------------------------------
# ContrastSlider - data range tracking
# ---------------------------------------------------------------------------


def test_contrast_slider_data_range_expands(qtbot: QtBot) -> None:
    """update_data_range expands the running data range and auto-sets handles."""
    widget = ContrastSlider()
    qtbot.addWidget(widget)

    widget.update_data_range(10, 200)
    assert widget._slider.value() == (10, 200)

    widget.update_data_range(5, 180)  # expands min, not max
    assert widget._slider.value() == (5, 200)


def test_contrast_slider_data_range_no_clobber_when_auto_off(qtbot: QtBot) -> None:
    """update_data_range does not move handles when auto is off."""
    widget = ContrastSlider()
    qtbot.addWidget(widget)

    widget.update_data_range(0, 255)
    widget._slider.setValue((50, 150))  # user manually adjusts (turns auto off)

    widget.update_data_range(0, 300)  # range expands
    lo, hi = widget._slider.value()
    assert lo == 50
    assert hi == 150


def test_contrast_slider_reset_data_range(qtbot: QtBot) -> None:
    """reset_data_range clears the running range."""
    widget = ContrastSlider()
    qtbot.addWidget(widget)

    widget.update_data_range(0, 255)
    widget.reset_data_range()

    assert widget._data_min == float("inf")
    assert widget._data_max == float("-inf")


# ---------------------------------------------------------------------------
# StageExplorer - _has_devices / _update_actions_enabled
# ---------------------------------------------------------------------------


def test_stage_explorer_has_devices(qtbot: QtBot) -> None:
    """_has_devices returns True when the test config is loaded."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    assert explorer._has_devices()


def test_stage_explorer_update_actions_enabled(qtbot: QtBot) -> None:
    """Toolbar actions are all enabled when devices are loaded."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    tb = explorer._toolbar
    assert tb.snap_action.isEnabled()
    assert tb.poll_stage_action.isEnabled()
    assert tb.scan_action.isEnabled()
    assert tb.delete_rois_action.isEnabled()
    for action in explorer.roi_manager.mode_actions.actions():
        assert action.isEnabled()


def test_stage_explorer_update_actions_disabled_without_devices(
    qtbot: QtBot,
) -> None:
    """Toolbar actions are all disabled when no devices are loaded."""
    mmc = CMMCorePlus()  # empty core - only the 'Core' device
    explorer = StageExplorer(mmcore=mmc)
    qtbot.addWidget(explorer)
    tb = explorer._toolbar
    assert not tb.snap_action.isEnabled()
    assert not tb.poll_stage_action.isEnabled()
    assert not tb.scan_action.isEnabled()
    assert not tb.delete_rois_action.isEnabled()
    for action in explorer.roi_manager.mode_actions.actions():
        assert not action.isEnabled()


# ---------------------------------------------------------------------------
# StageExplorer - contrast slider
# ---------------------------------------------------------------------------


def test_stage_explorer_contrast_slider_hidden_initially(qtbot: QtBot) -> None:
    """The contrast slider starts hidden."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()
    assert not explorer._contrast_slider.isVisible()


def test_stage_explorer_contrast_slider_shown_after_image(qtbot: QtBot) -> None:
    """Adding an image makes the contrast slider visible."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()
    img = np.random.randint(0, 255, (100, 100), dtype=np.uint8)
    explorer.add_image(img, 0.0, 0.0)
    assert explorer._contrast_slider.isVisible()


def test_stage_explorer_contrast_slider_values_set_on_first_image(
    qtbot: QtBot,
) -> None:
    """Slider handle values are auto-set from the first image's data range."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    img = np.array([[10, 200]], dtype=np.uint8)
    explorer.add_image(img, 0.0, 0.0)
    lo, hi = explorer._contrast_slider._slider.value()
    assert lo == 10
    assert hi == 200
    assert explorer._contrast_slider._slider.maximum() == (
        2 ** explorer._mmc.getImageBitDepth() - 1
    )


def test_stage_explorer_contrast_slider_not_reset_by_second_image(
    qtbot: QtBot,
) -> None:
    """Slider handles are NOT reset when a second image expands the data range."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()
    img1 = np.array([[10, 200]], dtype=np.uint8)
    explorer.add_image(img1, 0.0, 0.0)
    # manually change slider values after first image
    explorer._contrast_slider._slider.setValue((50, 150))

    img2 = np.array([[0, 255]], dtype=np.uint8)  # wider range
    explorer.add_image(img2, 10.0, 0.0)

    # slider handles should still reflect the manually set values
    lo, hi = explorer._contrast_slider._slider.value()
    assert lo == 50
    assert hi == 150


def test_stage_explorer_contrast_slider_applies_clims(qtbot: QtBot) -> None:
    """Moving the contrast slider updates the clims on all images."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    img = np.random.randint(0, 255, (50, 50), dtype=np.uint8)
    explorer.add_image(img, 0.0, 0.0)

    explorer._on_contrast_slider_changed((30, 220))

    assert explorer._stage_viewer._clims == (30.0, 220.0)


# ---------------------------------------------------------------------------
# ContrastSlider - auto button toggles off on manual slider interaction
# ---------------------------------------------------------------------------


def test_contrast_slider_auto_off_on_user_interaction(qtbot: QtBot) -> None:
    """Manually moving the slider turns the auto button off."""
    widget = ContrastSlider()
    qtbot.addWidget(widget)
    widget._slider.setRange(0, 255)
    assert widget._auto_btn.isChecked()

    widget._slider.setValue((50, 200))

    assert not widget._auto_btn.isChecked()
    assert not widget.auto


def test_contrast_slider_uses_ndv_style(qtbot: QtBot) -> None:
    """The Stage Explorer contrast control uses ndv's slider rendering."""
    widget = ContrastSlider()
    qtbot.addWidget(widget)

    style = widget._slider.styleSheet()
    assert "QSlider::groove:horizontal" in style
    assert "QSlider::handle:horizontal" in style
    assert "SliderLabel { font-size: 10px; color: white;}" in style


def test_contrast_slider_matches_ndv_controls(qtbot: QtBot) -> None:
    """Only the editable handle labels and Auto button are shown, as in ndv."""
    widget = ContrastSlider()
    qtbot.addWidget(widget)

    layout = widget.layout()
    assert layout is not None
    assert layout.count() == 2
    assert layout.itemAt(0).widget() is widget._slider
    assert layout.itemAt(1).widget() is widget._auto_btn
    assert widget._slider.minimum() == 0
    assert widget._slider.maximum() == 2**16 - 1

    widget.set_maximum(4095)
    assert widget._slider.maximum() == 4095

    widget._slider.setValue((100, 1000))
    assert tuple(label.value() for label in widget._slider._handle_labels) == (
        100,
        1000,
    )


def test_contrast_slider_auto_stays_on_during_programmatic_update(
    qtbot: QtBot,
) -> None:
    """Programmatic update_data_range does NOT disable the auto button."""
    widget = ContrastSlider()
    qtbot.addWidget(widget)
    assert widget._auto_btn.isChecked()

    widget.update_data_range(10, 200)

    assert widget._auto_btn.isChecked()
    assert widget.auto


def test_contrast_slider_auto_toggle_restores_handles(qtbot: QtBot) -> None:
    """Re-enabling auto restores handles to the tracked data range."""
    widget = ContrastSlider()
    qtbot.addWidget(widget)

    widget.update_data_range(10, 200)
    widget._slider.setValue((50, 150))  # turns auto off
    assert not widget.auto

    widget._auto_btn.setChecked(True)
    assert widget.auto
    assert widget._slider.value() == (10, 200)


def test_stage_explorer_clear_action_hides_contrast_slider(qtbot: QtBot) -> None:
    """Triggering the clear action hides the contrast slider and clears images."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()
    img = np.random.randint(0, 255, (50, 50), dtype=np.uint8)
    explorer.add_image(img, 0.0, 0.0)
    assert explorer._contrast_slider.isVisible()

    explorer._toolbar.clear_action.trigger()

    assert not explorer._contrast_slider.isVisible()
    assert not list(explorer._stage_viewer._get_images())


# ---------------------------------------------------------------------------
# StageExplorer - _on_roi_changed early-return guard
# ---------------------------------------------------------------------------


def test_stage_explorer_roi_changed_skipped_when_no_image_dimensions(
    qtbot: QtBot,
) -> None:
    """_on_roi_changed returns early when width/height are 0."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    half_before = explorer._half_img_shift.copy()

    with (
        patch.object(explorer._mmc, "getImageWidth", return_value=0),
        patch.object(explorer._mmc, "getImageHeight", return_value=0),
    ):
        explorer._on_roi_changed()

    # half_img_shift should be unchanged (early return was hit)
    np.testing.assert_array_equal(explorer._half_img_shift, half_before)


# ---------------------------------------------------------------------------
# StageExplorer - _update_marker_mode guard when marker is None
# ---------------------------------------------------------------------------


def test_stage_explorer_update_marker_mode_no_marker(qtbot: QtBot) -> None:
    """_update_marker_mode returns gracefully when stage_pos_marker is None."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer._stage_pos_marker = None
    # should not raise
    explorer._update_marker_mode()


# ---------------------------------------------------------------------------
# StageExplorer - _on_sys_config_loaded
# ---------------------------------------------------------------------------


def test_stage_explorer_sys_config_loaded_restarts_poller(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """_on_sys_config_loaded restarts the poller when an XY stage is present."""
    explorer = StageExplorer(mmcore=global_mmcore)
    qtbot.addWidget(explorer)
    # stop the poller first so we can verify it restarts
    explorer._stop_poller()
    assert not explorer._stage_poller.isRunning()

    explorer._on_sys_config_loaded()

    assert explorer._stage_poller.isRunning()


def test_stage_explorer_sys_config_loaded_stops_poller_when_no_xy(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """_on_sys_config_loaded stops the poller when no XY stage is available."""
    explorer = StageExplorer(mmcore=global_mmcore)
    qtbot.addWidget(explorer)
    # wait until the poller is running
    qtbot.waitUntil(lambda: explorer._stage_poller.isRunning(), timeout=2000)

    with patch.object(explorer._mmc, "getXYStageDevice", return_value=""):
        explorer._on_sys_config_loaded()

    assert not explorer._stage_poller.isRunning()


# ---------------------------------------------------------------------------
# StageExplorer - _create_stage_pos_marker replaces existing marker
# ---------------------------------------------------------------------------


def test_stage_explorer_create_stage_pos_marker_replaces_existing(
    qtbot: QtBot,
) -> None:
    """_create_stage_pos_marker detaches and replaces a pre-existing marker."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    assert explorer._stage_pos_marker is not None
    first_marker = explorer._stage_pos_marker

    explorer._create_stage_pos_marker()

    assert explorer._stage_pos_marker is not None
    assert explorer._stage_pos_marker is not first_marker
    # old marker must be detached from the scene
    assert first_marker.parent is None


# ---------------------------------------------------------------------------
# StageExplorer - _set_stage_controller
# ---------------------------------------------------------------------------


def test_stage_explorer_stage_controller_none_without_xy_device(
    qtbot: QtBot,
) -> None:
    """_set_stage_controller sets controller to None when no XY device is loaded."""
    mmc = CMMCorePlus()  # empty - no XY stage
    explorer = StageExplorer(mmcore=mmc)
    qtbot.addWidget(explorer)
    assert explorer._stage_controller is None


def test_stage_explorer_stage_controller_set_with_xy_device(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """_set_stage_controller creates a controller when an XY device is present."""
    explorer = StageExplorer(mmcore=global_mmcore)
    qtbot.addWidget(explorer)
    assert explorer._stage_controller is not None


# ---------------------------------------------------------------------------
# StageExplorer - _on_pixel_size_changed accepts *args
# ---------------------------------------------------------------------------


def test_stage_explorer_pixel_size_handlers_refresh_affine(
    qtbot: QtBot,
) -> None:
    """Both pixel size handlers call _affine_state.refresh()."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    # neither should raise
    explorer._on_pixel_size_changed(0.065)
    explorer._on_pixel_size_affine_changed()


# ---------------------------------------------------------------------------
# StageExplorer - stop scan action
# ---------------------------------------------------------------------------


def test_stop_scan_action_cancels_when_mda_running(qtbot: QtBot) -> None:
    """Triggering stop_scan_action calls mda.cancel() when a sequence is running."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    with (
        patch.object(explorer._mmc.mda, "is_running", return_value=True),
        patch.object(explorer._mmc.mda, "cancel") as mock_cancel,
    ):
        explorer._toolbar.stop_scan_action.trigger()
    mock_cancel.assert_called_once()


def test_stop_scan_action_stops_stage_when_no_mda(qtbot: QtBot) -> None:
    """Triggering stop_scan_action stops the XY stage when no sequence is running."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    xy_dev = explorer._mmc.getXYStageDevice()
    with (
        patch.object(explorer._mmc.mda, "is_running", return_value=False),
        patch.object(explorer._mmc, "stop") as mock_stop,
    ):
        explorer._toolbar.stop_scan_action.trigger()
    mock_stop.assert_called_once_with(xy_dev)


def test_sequence_finished_resets_our_mda_running(qtbot: QtBot) -> None:
    """_on_sequence_finished resets _our_mda_running to False."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer._our_mda_running = True
    explorer._on_sequence_finished()
    assert not explorer._our_mda_running


def test_send_to_mda_emits_roi_positions(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.roi_manager.add_roi(RectangleROI((0, 0), (100, 100), fov_size=(50, 50)))

    def choose_replace(msg: QMessageBox) -> int:
        replace = next(btn for btn in msg.buttons() if btn.text() == "Replace")
        replace.click()
        return 0

    with (
        patch.object(QMessageBox, "exec", choose_replace),
        qtbot.waitSignal(explorer.sendToMDARequested) as emitted,
    ):
        explorer._toolbar.send_to_mda_action.trigger()

    positions, replace = emitted.args
    assert len(positions) == 1
    assert isinstance(positions[0], useq.AbsolutePosition)
    assert replace is True


# ---------------------------------------------------------------------------
# Map tiles: dedup, node reuse, redraw throttling
# ---------------------------------------------------------------------------

TILE_IMG = np.random.randint(0, 4096, (64, 64), dtype=np.uint16)


def test_frame_relay_coalesces_before_gui_dispatch() -> None:
    """A burst retains one image and emits one lightweight GUI notification."""
    relay = stage_explorer_mod._LatestFrameRelay()
    relay.set_enabled(True)
    notifications: list[None] = []
    relay.framesPending.connect(lambda: notifications.append(None))
    event = next(iter(useq.MDASequence(stage_positions=[(0, 0)])))

    for value in range(100):
        relay.submit(np.full((8, 8), value, dtype=np.uint16), event, {})

    assert len(notifications) == 1
    batch = relay.take_or_disarm()
    assert len(batch) == 1
    assert batch[0][0][0, 0] == 99

    # An empty timer tick disarms the relay, allowing the next burst to send
    # exactly one new wake-up.
    assert relay.take_or_disarm() == ()
    relay.submit(TILE_IMG, event, {})
    assert len(notifications) == 2


def test_frame_relay_keeps_latest_frame_for_each_location() -> None:
    """Coalescing drops intermediate timepoints, never distinct map locations."""
    relay = stage_explorer_mod._LatestFrameRelay()
    relay.set_enabled(True)
    seq = useq.MDASequence(stage_positions=[(0, 0), (500, 0), (1000, 0)])

    for value, event in enumerate(seq):
        relay.submit(np.full((8, 8), value, dtype=np.uint16), event, {})

    batch = relay.take_or_disarm()
    assert len(batch) == 3
    assert [int(image[0, 0]) for image, _event in batch] == [0, 1, 2]


def test_frame_ready_burst_is_coalesced_before_qt_event_queue(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    """Cross-thread frameReady emissions queue one wake-up, not every image."""
    explorer = StageExplorer(mmcore=global_mmcore)
    qtbot.addWidget(explorer)
    explorer.show()
    event = next(iter(useq.MDASequence(stage_positions=[(0, 0)])))

    def emit_burst() -> None:
        for value in range(100):
            image = np.full((16, 16), value, dtype=np.uint16)
            global_mmcore.mda.events.frameReady.emit(image, event, {})

    worker = Thread(target=emit_burst)
    worker.start()
    worker.join()

    # The main thread has not processed the relay's lightweight wake-up yet;
    # no image-bearing Qt events were queued for the 100 individual frames.
    assert not explorer._tiles
    qtbot.waitUntil(lambda: bool(explorer._tiles), timeout=1000)
    node = next(iter(explorer._tiles.values()))
    assert node._data[0, 0] == 99


def test_sequence_finished_drains_pre_gui_frame_relay(qtbot: QtBot) -> None:
    """The newest relayed frame is displayed even if its timer has not fired."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()
    event = next(iter(useq.MDASequence(stage_positions=[(0, 0)])))
    explorer._frame_relay.submit(np.full((16, 16), 1, dtype=np.uint16), event, {})
    explorer._frame_relay.submit(np.full((16, 16), 9, dtype=np.uint16), event, {})

    explorer._on_sequence_finished()

    assert len(explorer._tiles) == 1
    node = next(iter(explorer._tiles.values()))
    assert node._data[0, 0] == 9


def test_frame_ready_dedup_by_position_index(qtbot: QtBot) -> None:
    """Revisiting the same (p, g) index reuses one node instead of stacking.

    Regression test: the explorer used to build a brand-new scene node for
    *every* frame, so a long timelapse at a fixed position accumulated one
    GPU-backed node per frame -- unbounded memory growth and an ever slower
    canvas. Frames belonging to the same MDA position/grid index must now
    share a single node, refreshed in place.
    """
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()

    seq = useq.MDASequence(
        stage_positions=[(0, 0)],
        time_plan={"interval": 0, "loops": 5},
    )
    for event in seq:
        explorer._on_frame_ready(TILE_IMG, event)

    assert len(explorer._tiles) == 1
    assert len(list(explorer._stage_viewer._get_images())) == 1


def test_frame_ready_distinct_positions_create_distinct_tiles(qtbot: QtBot) -> None:
    """Distinct (p, g) indices each get their own node."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()

    seq = useq.MDASequence(stage_positions=[(0, 0), (500, 0), (1000, 0)])
    for event in seq:
        explorer._on_frame_ready(TILE_IMG, event)

    assert len(explorer._tiles) == 3
    assert len(list(explorer._stage_viewer._get_images())) == 3


def test_frame_ready_same_position_across_sequences_reuses_tile(qtbot: QtBot) -> None:
    """Sequence UUIDs must not duplicate the same physical map location."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()

    first = next(iter(useq.MDASequence(stage_positions=[(100, 200)])))
    second = next(iter(useq.MDASequence(stage_positions=[(100, 200)])))
    assert first.sequence.uid != second.sequence.uid

    explorer._on_frame_ready(TILE_IMG, first)
    explorer._on_sequence_finished()
    explorer._on_frame_ready(TILE_IMG, second)

    assert len(explorer._tiles) == 1
    assert len(list(explorer._stage_viewer._get_images())) == 1


def test_frame_ready_same_index_at_new_position_creates_tile(qtbot: QtBot) -> None:
    """Equal position indices in separate sequences are not physical identity."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()

    first = next(iter(useq.MDASequence(stage_positions=[(0, 0)])))
    second = next(iter(useq.MDASequence(stage_positions=[(500, 0)])))
    explorer._on_frame_ready(TILE_IMG, first)
    explorer._on_sequence_finished()
    explorer._on_frame_ready(TILE_IMG, second)

    assert len(explorer._tiles) == 2


def test_snap_dedup_within_tolerance(qtbot: QtBot) -> None:
    """Two snaps close enough together (stage repeatability error) share a tile."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer._add_image_and_update_widget(TILE_IMG, 0.0, 0.0)
    explorer._add_image_and_update_widget(TILE_IMG, 0.05, -0.05)  # well within tol
    assert len(explorer._tiles) == 1


def test_snap_no_dedup_beyond_tolerance(qtbot: QtBot) -> None:
    """Two snaps far enough apart become separate tiles, not merged."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer._add_image_and_update_widget(TILE_IMG, 0.0, 0.0)
    explorer._add_image_and_update_widget(TILE_IMG, 500.0, 0.0)
    assert len(explorer._tiles) == 2


def test_redraw_throttle_coalesces_same_location_bursts(qtbot: QtBot) -> None:
    """Frames hitting the same location faster than the redraw interval coalesce.

    The first frame applies immediately (no added latency for a snap or a
    slow acquisition); a rapid follow-up for the *same* location is buffered
    and only reaches the GPU on the next tick, with the newest data winning.
    """
    explorer = StageExplorer()
    qtbot.addWidget(explorer)

    first = np.full((64, 64), 100, dtype=np.uint16)
    second = np.full((64, 64), 200, dtype=np.uint16)
    explorer._add_image_and_update_widget(first, 0.0, 0.0)
    node = next(iter(explorer._tiles.values()))
    assert node._data[0, 0] == 100

    explorer._add_image_and_update_widget(second, 0.0, 0.0)
    # Still buffered -- no second node, and the node's data hasn't jumped yet.
    assert len(explorer._tiles) == 1
    assert explorer._pending_tiles
    assert node._data[0, 0] == 100

    qtbot.waitUntil(lambda: not explorer._pending_tiles, timeout=1000)
    assert node._data[0, 0] == 200


def test_sequence_finished_flushes_pending_tile(qtbot: QtBot) -> None:
    """A throttled frame isn't stranded -- sequenceFinished force-flushes it."""
    explorer = StageExplorer()
    qtbot.addWidget(explorer)

    first = np.full((64, 64), 1, dtype=np.uint16)
    last = np.full((64, 64), 9, dtype=np.uint16)
    explorer._add_image_and_update_widget(first, 0.0, 0.0)
    explorer._add_image_and_update_widget(last, 0.0, 0.0)
    node = next(iter(explorer._tiles.values()))
    assert explorer._pending_tiles

    explorer._on_sequence_finished()
    assert not explorer._pending_tiles
    assert node._data[0, 0] == 9


# ---------------------------------------------------------------------------
# Viewport culling
# ---------------------------------------------------------------------------


def test_culling_hides_offscreen_shows_onscreen(qtbot: QtBot) -> None:
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(700, 700)
    viewer.show()

    near = viewer.add_image(TILE_IMG, _build_transform_matrix(0, 0).T)
    far = viewer.add_image(TILE_IMG, _build_transform_matrix(100_000, 100_000).T)
    viewer.view.camera.set_range(x=(-50, 100), y=(-50, 100), margin=0)
    viewer.cull_to_view()

    assert near.visible is True
    assert far.visible is False


def test_culling_accounts_for_non_square_viewport_padding(qtbot: QtBot) -> None:
    """A tile between camera.rect and the aspect-padded render extent stays visible.

    Regression test: this widget's camera uses aspect=1 (square pixels), so on
    any non-square viewport (virtually every real dock or window) vispy pads
    the requested range on one axis to preserve that aspect ratio -- what's
    actually rendered is wider/taller than `camera.rect` alone reports.
    Culling used to read only `camera.rect`, hiding tiles that were still
    genuinely on screen inside that padded region.
    """
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(1000, 500)  # deliberately non-square
    viewer.show()
    qtbot.wait(50)  # let the native widget actually resize before zooming

    viewer.add_image(TILE_IMG, _build_transform_matrix(0, 0).T)
    viewer.zoom_to_fit(margin=0.05)

    cam = viewer.view.camera
    real_rect = getattr(cam, "_real_rect", None)
    assert real_rect is not None, "test assumes vispy still exposes _real_rect"
    assert real_rect.right > cam.rect.right, "viewport must actually be padded"

    # place a probe strictly between the unpadded and padded right edges
    probe_x = (cam.rect.right + real_rect.right) / 2
    probe = viewer.add_image(
        np.full((30, 30), 65535, dtype=np.uint16),
        _build_transform_matrix(probe_x - 15, -15).T,
    )
    assert probe.visible is True


# ---------------------------------------------------------------------------
# Map memory budget
# ---------------------------------------------------------------------------


def test_map_memory_limit_refuses_new_location_over_budget(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()
    explorer.max_map_memory_mb = TILE_IMG.nbytes * 2 / 1e6  # room for exactly 1 tile

    explorer._add_image_and_update_widget(TILE_IMG, 0.0, 0.0)
    assert len(explorer._tiles) == 1
    assert not explorer._memory_banner.isVisible()

    explorer._add_image_and_update_widget(TILE_IMG, 5000.0, 0.0)
    assert len(explorer._tiles) == 1, "second, new location must be refused"
    assert explorer._memory_banner.isVisible()
    assert "low on free memory" not in explorer._memory_banner._label.text()

    # nothing already on the map was touched, and it keeps updating
    explorer._add_image_and_update_widget(TILE_IMG, 0.0, 0.0)
    assert len(explorer._tiles) == 1


def test_clear_action_resets_memory_budget_state(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()
    explorer.max_map_memory_mb = TILE_IMG.nbytes * 2 / 1e6
    explorer._add_image_and_update_widget(TILE_IMG, 0.0, 0.0)
    explorer._add_image_and_update_widget(TILE_IMG, 5000.0, 0.0)
    assert explorer._memory_banner.isVisible()

    explorer._toolbar.clear_action.trigger()
    assert not explorer._memory_banner.isVisible()
    assert not explorer._tiles

    explorer._add_image_and_update_widget(TILE_IMG, 5000.0, 0.0)
    assert len(explorer._tiles) == 1


def test_low_system_memory_blocks_new_location_regardless_of_own_limit(
    qtbot: QtBot,
) -> None:
    """A live system-RAM shortage blocks new tiles even under a generous limit.

    Regression scenario: the map's own limit is a static number, possibly set
    (or defaulted) from whatever RAM was free when the explorer opened.
    Something else on the machine can claim RAM afterwards -- the map's own
    bookkeeping alone would stay well under its limit and keep growing
    regardless. This must be caught independently of the per-map limit.
    """
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()
    explorer.max_map_memory_mb = 10_000.0  # nowhere near being hit

    low_vm = MagicMock(available=100 * 1024**2)  # 100 MB free
    with patch.object(stage_explorer_mod.psutil, "virtual_memory", return_value=low_vm):
        explorer._add_image_and_update_widget(TILE_IMG, 0.0, 0.0)

    assert not explorer._tiles
    assert explorer._memory_banner.isVisible()
    assert "low on free memory" in explorer._memory_banner._label.text()


def test_memory_banner_clears_once_a_location_actually_succeeds(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()
    explorer.max_map_memory_mb = 10_000.0

    low_vm = MagicMock(available=100 * 1024**2)
    with patch.object(stage_explorer_mod.psutil, "virtual_memory", return_value=low_vm):
        explorer._add_image_and_update_widget(TILE_IMG, 0.0, 0.0)
    assert explorer._memory_banner.isVisible()

    healthy_vm = MagicMock(available=8 * 1024**3)  # 8 GB free
    with patch.object(
        stage_explorer_mod.psutil, "virtual_memory", return_value=healthy_vm
    ):
        explorer._add_image_and_update_widget(TILE_IMG, 5000.0, 0.0)
    assert len(explorer._tiles) == 1
    assert not explorer._memory_banner.isVisible()


def test_tile_allocation_failure_degrades_gracefully(qtbot: QtBot) -> None:
    """A resource failure while adding a tile must not crash the app.

    The map-memory checks only see *system* RAM; GPU texture memory is a
    separate pool on most non-unified-memory hardware, so a genuine
    allocation failure there is a distinct, uncatchable-in-advance failure
    mode. It must degrade the same way as running low on system memory
    (refuse further locations, nothing already drawn is touched) instead of
    propagating and taking the whole app down over one tile.
    """
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()

    with patch.object(
        StageViewer, "add_image", side_effect=RuntimeError("simulated GL failure")
    ):
        explorer._add_image_and_update_widget(TILE_IMG, 0.0, 0.0)  # must not raise

    assert not explorer._tiles
    assert explorer._memory_banner.isVisible()

    # and the widget is fully usable again once the failure clears
    explorer._add_image_and_update_widget(TILE_IMG, 0.0, 0.0)
    assert len(explorer._tiles) == 1
    assert not explorer._memory_banner.isVisible()


# ---------------------------------------------------------------------------
# MapMemoryMenu
# ---------------------------------------------------------------------------


def test_map_memory_defaults_range_bounded_by_total_ram() -> None:
    vm = MagicMock(total=16 * 1024**3, available=4 * 1024**3)
    with patch.object(stage_explorer_mod.psutil, "virtual_memory", return_value=vm):
        lo, hi, default = stage_explorer_mod._map_memory_defaults()
    assert lo == 0.1
    assert hi == 16.0
    assert default == 0.8  # 20% of 4 GB available


def test_map_memory_defaults_floored_under_severe_memory_pressure() -> None:
    """Genuine scarcity shouldn't round the default to nothing.

    The floor keeps the *default* usable under genuine scarcity
    without touching the live per-add check (LOW_SYSTEM_MEMORY_FLOOR_MB),
    which still runs independently of whatever this default gets set to.
    """
    vm = MagicMock(total=16 * 1024**3, available=2 * 1024**3)  # 20% would be 0.4 GB
    with patch.object(stage_explorer_mod.psutil, "virtual_memory", return_value=vm):
        _, _, default = stage_explorer_mod._map_memory_defaults()
    assert default == stage_explorer_mod.MAP_MEMORY_DEFAULT_FLOOR_GB


def test_map_memory_defaults_scale_with_available_ram() -> None:
    """The default follows *available* RAM, not just total.

    Total RAM is only used for the spinbox's upper bound, not the default
    itself, so a machine with a lot of *total* RAM but little currently
    *free* still gets a modest default -- raising it is the user's call, not
    something assumed on the app's behalf just because the hardware could
    technically support it.
    """
    vm = MagicMock(total=64 * 1024**3, available=20 * 1024**3)
    with patch.object(stage_explorer_mod.psutil, "virtual_memory", return_value=vm):
        lo, hi, default = stage_explorer_mod._map_memory_defaults()
    assert (lo, hi) == (0.1, 64.0)
    assert default == 4.0  # 20% of 20 GB available, not of the 64 GB total


def test_map_memory_menu_property_sync_both_directions(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    menu = explorer._toolbar.map_memory_menu

    # UI -> property
    with qtbot.waitSignal(menu.valueChanged):
        menu._limit_spin.setValue(5.0)
    assert explorer.max_map_memory_mb == 5000.0

    # property -> UI
    explorer.max_map_memory_mb = 7500.0
    assert menu.value() == 7.5


def test_map_memory_action_is_instant_popup(qtbot: QtBot) -> None:
    """The whole button opens the menu -- unlike poll/scan, it has no other job.

    poll_stage_action/scan_action use MenuButtonPopup because each has a
    primary action distinct from its menu (toggle polling, start a scan);
    the split-button look communicates that. This button's only job is
    showing the limit editor, so InstantPopup is correct here even though it
    looks different from those two -- a real click isn't simulated (that
    enters a native event loop that only returns once the popup is
    dismissed, which would hang a headless test), just the wiring that
    produces that behavior.
    """
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    tb = explorer._toolbar
    memory_btn = tb.widgetForAction(tb.map_memory_action)

    assert memory_btn.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    assert memory_btn.menu() is tb.map_memory_menu
