from __future__ import annotations

from typing import TYPE_CHECKING

import useq
from qtpy.QtCore import QPoint, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QWidget

from pymmcore_widgets import MDAWidget, MDAWidgetSidebar
from pymmcore_widgets.mda import CAMERA_ROI_METADATA_KEY, SidebarMDATabs
from pymmcore_widgets.useq_widgets import PYMMCW_METADATA_KEY

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot

MDA = useq.MDASequence(
    time_plan=useq.TIntervalLoops(interval=0.01, loops=2),
    stage_positions=[
        useq.AbsolutePosition(
            x=0,
            y=1,
            z=2,
            name="P1",
            sequence=useq.MDASequence(
                autofocus_plan=useq.AxesBasedAF(
                    autofocus_motor_offset=25.0, axes=("p",)
                )
            ),
        ),
        useq.Position(x=42, y=0, z=3),
    ],
    channels=[
        {"config": "DAPI", "exposure": 1, "acquire_every": 2, "z_offset": 1.5},
        {"config": "FITC", "exposure": 2},
    ],
    z_plan=useq.ZRangeAround(range=1, step=0.3),
    grid_plan=useq.GridRowsColumns(rows=2, columns=1),
    axis_order="tpgzc",
    keep_shutter_open_across=("z",),
)


def test_sidebar_is_mda_widget(qtbot: QtBot) -> None:
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    # It IS an MDAWidget: same behavior, different presentation.
    assert isinstance(wdg, MDAWidget)
    assert isinstance(wdg.tab_wdg, SidebarMDATabs)
    assert wdg.tabs is wdg.tab_wdg
    # The axes are followed by non-axis ROI and Saving rows. Global settings
    # (axis order, keep shutter open, autofocus axis) are not a sidebar row --
    # they're inline in the widget's own footer instead.
    assert [r.title for r in wdg.tabs.rows] == [
        "Channels",
        "Positions",
        "Grid / Tile Scan",
        "Z Stack",
        "Time Series",
        "Camera ROI",
        "Saving",
    ]
    assert wdg.tabs.roi_row is wdg.tabs.rows[-2]
    assert wdg.tabs.saving_row is wdg.tabs.rows[-1]
    assert wdg.tabs.tabBar().isHidden()
    assert not wdg.camera_roi.snap_checkbox.isHidden()
    assert wdg.camera_roi.snap_checkbox.isChecked()
    assert wdg._settings_group.isAncestorOf(wdg.axis_order)
    assert wdg._settings_group.isAncestorOf(wdg.keep_shutter_open)
    assert wdg._settings_group.isAncestorOf(wdg.af_axis)

    # Enabling the supporting row must not imply a cropped ROI.
    assert wdg.camera_roi.camera_roi_combo.currentText() == "Full Chip"
    wdg.tabs.roi_row.set_checked(True)
    assert wdg.tabs.roi_row.checked
    assert wdg.camera_roi.camera_roi_combo.currentText() == "Full Chip"
    assert wdg.camera_roi.roiValue() == {
        "camera": "Camera",
        "x": 0,
        "y": 0,
        "width": 512,
        "height": 512,
    }


def test_sidebar_selects_channels_by_default(qtbot: QtBot) -> None:
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    tabs = wdg.tabs
    assert tabs._stack.currentWidget() is tabs._stack.widget(
        tabs._visual_index_by_widget[wdg.channels]
    )


def test_sidebar_row_click_selects_without_toggling_checkbox(qtbot: QtBot) -> None:
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    wdg.show()
    tabs = wdg.tabs
    positions_row = tabs.row("p")

    assert not positions_row.checked
    QTest.mouseClick(
        positions_row._frame,
        Qt.MouseButton.LeftButton,
        pos=QPoint(5, positions_row._frame.height() // 2),
    )
    assert (
        tabs._stack.currentIndex() == tabs._visual_index_by_widget[wdg.stage_positions]
    )
    # Clicking the row background must not have toggled its checkbox.
    assert not positions_row.checked


def test_sidebar_checkbox_toggles_without_changing_selection(qtbot: QtBot) -> None:
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    wdg.show()
    tabs = wdg.tabs
    # start on Channels (the default selection)
    before_index = tabs._stack.currentIndex()

    positions_row = tabs.row("p")
    QTest.mouseClick(positions_row.checkbox, Qt.MouseButton.LeftButton)
    assert positions_row.checked
    assert tabs._stack.currentIndex() == before_index


def test_sidebar_disabled_row_stays_selectable(qtbot: QtBot) -> None:
    """A disabled dimension is dimmed but its editor remains viewable."""
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    tabs = wdg.tabs
    assert not tabs.row("p").checked
    tabs.select_axis(wdg.stage_positions)
    assert (
        tabs._stack.currentIndex() == tabs._visual_index_by_widget[wdg.stage_positions]
    )
    assert not wdg.stage_positions.isEnabled()


def test_sidebar_value_parity_with_mda_widget(qtbot: QtBot) -> None:
    """The sidebar presentation must build the identical sequence."""
    ref = MDAWidget()
    qtbot.addWidget(ref)
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)

    ref.setValue(MDA)
    wdg.setValue(MDA)

    sidebar_value = wdg.value()
    reference_value = ref.value()
    assert sidebar_value.replace(metadata={}) == reference_value.replace(metadata={})
    assert (
        sidebar_value.metadata[PYMMCW_METADATA_KEY][CAMERA_ROI_METADATA_KEY]["enabled"]
        is False
    )
    # per-axis inclusion mirrors the reference
    for axis in "cpgzt":
        assert wdg.tabs.isAxisUsed(axis) == ref.tab_wdg.isAxisUsed(axis)
    # per-position autofocus offset preserved
    restored = wdg.value().stage_positions
    assert restored[0].sequence is not None
    assert restored[0].sequence.autofocus_plan is not None
    assert restored[0].sequence.autofocus_plan.autofocus_motor_offset == 25.0


def test_sidebar_settings_file_actions_are_in_execution_footer(qtbot: QtBot) -> None:
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    wdg.resize(800, 700)
    wdg.show()

    footer = wdg.findChild(QWidget, "mdaExecutionFooter")
    assert footer is not None
    assert footer.isAncestorOf(wdg._settings_group)
    for button in (wdg._save_button, wdg._load_button):
        assert footer.isAncestorOf(button)
        assert not wdg._settings_group.isAncestorOf(button)

    qtbot.wait(1)
    save_center = wdg._save_button.mapTo(footer, wdg._save_button.rect().center())
    load_center = wdg._load_button.mapTo(footer, wdg._load_button.rect().center())
    run = wdg.control_btns.run_btn
    run_center = run.mapTo(footer, run.rect().center())
    footer_layout = footer.layout()
    assert footer_layout is not None
    actions_row = None
    for i in range(footer_layout.count()):
        item_layout = footer_layout.itemAt(i).layout()
        if item_layout is not None and item_layout.indexOf(wdg._save_button) != -1:
            actions_row = item_layout
            break
    assert actions_row is not None
    assert actions_row.indexOf(wdg._save_button) < actions_row.indexOf(wdg._load_button)
    assert actions_row.indexOf(wdg._load_button) < actions_row.indexOf(wdg.control_btns)
    assert abs(save_center.y() - run_center.y()) <= 2
    assert abs(load_center.y() - run_center.y()) <= 2
    assert save_center.x() < load_center.x() < run_center.x()


def test_sidebar_disables_editors_during_run(qtbot: QtBot) -> None:
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    tabs = wdg.tabs
    for axis in "pgzt":
        tabs.setChecked(axis, True)

    wdg._enable_widgets(False)
    for axis, widget in {
        "c": wdg.channels,
        "p": wdg.stage_positions,
        "g": wdg.grid_plan,
        "z": wdg.z_plan,
        "t": wdg.time_plan,
    }.items():
        row = tabs.row(axis)
        assert row.checkbox is not None
        assert not row.checkbox.isEnabled()
        assert not widget.isEnabled()
    assert not wdg._settings_group.isEnabled()
    assert not tabs.roi_row.checkbox.isEnabled()
    assert not wdg.camera_roi.isEnabled()
    assert not wdg.save_info.isEnabled()
    assert not wdg._save_button.isEnabled()
    assert not wdg._load_button.isEnabled()

    wdg._enable_widgets(True)
    assert wdg.channels.isEnabled()
    assert wdg._settings_group.isEnabled()
    assert tabs.roi_row.checkbox.isEnabled()
    assert wdg.save_info.isEnabled()
    assert wdg._save_button.isEnabled()
    assert wdg._load_button.isEnabled()


def test_sidebar_roi_round_trip_without_hardware_change(qtbot: QtBot) -> None:
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    before = tuple(wdg._mmc.getROI("Camera"))
    roi = {
        "camera": "Camera",
        "x": 13,
        "y": 19,
        "width": 211,
        "height": 173,
    }
    wdg.camera_roi.setRoiValue(roi)
    wdg.tabs.roi_row.set_checked(True)

    sequence = wdg.value()
    assert sequence.metadata[PYMMCW_METADATA_KEY][CAMERA_ROI_METADATA_KEY] == {
        "enabled": True,
        **roi,
    }
    assert tuple(wdg._mmc.getROI("Camera")) == before

    restored = MDAWidgetSidebar(mmcore=wdg._mmc)
    qtbot.addWidget(restored)
    restored.setValue(sequence)
    assert restored.tabs.roi_row.checked
    assert restored.camera_roi.roiValue() == roi
    assert tuple(wdg._mmc.getROI("Camera")) == before


def test_sidebar_restoring_disabled_roi_does_not_touch_hardware(
    qtbot: QtBot,
) -> None:
    # Regression test: unchecking the ROI row interactively now resets
    # hardware to full chip immediately, but restoring a previously saved
    # sequence -- which sets the checkbox programmatically -- must not reach
    # out and change live hardware as a side effect.
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    roi = {
        "camera": "Camera",
        "x": 7,
        "y": 11,
        "width": 123,
        "height": 97,
    }
    wdg.camera_roi.setRoiValue(roi)
    wdg.tabs.roi_row.set_checked(True)
    sequence = wdg.value()

    other = MDAWidgetSidebar()
    qtbot.addWidget(other)
    before = tuple(other._mmc.getROI("Camera"))
    # enabled=False: the exact state that now triggers a hardware reset when
    # the checkbox is toggled interactively.
    sequence = sequence.replace(
        metadata={
            **sequence.metadata,
            PYMMCW_METADATA_KEY: {
                **sequence.metadata[PYMMCW_METADATA_KEY],
                CAMERA_ROI_METADATA_KEY: {**roi, "enabled": False},
            },
        }
    )
    other.setValue(sequence)
    assert not other.tabs.roi_row.checked
    assert other.camera_roi.roiValue() == roi
    assert tuple(other._mmc.getROI("Camera")) == before


def test_sidebar_applies_roi_once_during_preflight(qtbot: QtBot) -> None:
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    roi = {
        "camera": "Camera",
        "x": 10,
        "y": 20,
        "width": 200,
        "height": 180,
    }
    wdg.camera_roi.setRoiValue(roi)
    wdg.tabs.roi_row.set_checked(True)

    with qtbot.waitSignal(wdg._mmc.events.roiSet):
        assert wdg.prepare_mda() is None
    assert tuple(wdg._mmc.getROI("Camera")) == (10, 20, 200, 180)

    # A second preflight is idempotent.
    wdg.prepare_mda()

    # Unchecking the row restores full chip immediately -- Live shouldn't have
    # to wait for the next preflight to reflect "not using an ROI" -- but keeps
    # the planned ROI in the editor so it can still be re-applied.
    with qtbot.waitSignal(wdg._mmc.events.roiSet):
        wdg.tabs.roi_row.set_checked(False)
    assert tuple(wdg._mmc.getROI("Camera")) == (0, 0, 512, 512)
    assert wdg.camera_roi.roiValue() == roi
    assert wdg.value().metadata[PYMMCW_METADATA_KEY][CAMERA_ROI_METADATA_KEY] == {
        "enabled": False,
        **roi,
    }

    # A preflight while disabled is then idempotent too -- hardware already
    # matches, so no further roiSet fires.
    assert wdg.prepare_mda() is None
    assert tuple(wdg._mmc.getROI("Camera")) == (0, 0, 512, 512)

    # Re-enabling the row applies the retained plan.
    wdg.tabs.roi_row.set_checked(True)
    with qtbot.waitSignal(wdg._mmc.events.roiSet):
        assert wdg.prepare_mda() is None
    assert tuple(wdg._mmc.getROI("Camera")) == (10, 20, 200, 180)


def test_sidebar_run_preserves_axis_order(qtbot: QtBot) -> None:
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    wdg.setValue(MDA)

    combo = wdg.axis_order
    before = combo.currentText()
    assert before == "tpgzc"

    with qtbot.waitSignal(wdg._mmc.mda.events.sequenceFinished):
        wdg.control_btns.run_btn.click()

    assert combo.currentText() == before
    wdg.control_btns._disconnect()
    wdg._disconnect()


def test_sidebar_runs_acquisition(qtbot: QtBot) -> None:
    wdg = MDAWidgetSidebar()
    qtbot.addWidget(wdg)
    wdg.setValue(MDA)

    with qtbot.waitSignal(wdg._mmc.mda.events.sequenceFinished):
        wdg.control_btns.run_btn.click()

    assert wdg.control_btns.run_btn.isEnabled()
    wdg.control_btns._disconnect()
    wdg._disconnect()
