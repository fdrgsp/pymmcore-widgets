from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import useq
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QMessageBox
from superqt.utils import signals_blocked

from pymmcore_widgets._z_surface import ZSurface
from pymmcore_widgets.mda import (
    MDAWidget,
    MDAWidgetCollapsible,
    to_channel_properties_sequence,
)
from pymmcore_widgets.mda._core_positions import (
    HCS_SURFACE_Z_INFO,
    HCS_Z_INFO,
    SURFACE_UNAVAILABLE,
)
from pymmcore_widgets.useq_widgets._mda_sequence import (
    AF_AXIS_TOOLTIP,
    AF_PER_POS_SOFTWARE_TOOLTIP,
    PYMMCW_METADATA_KEY,
    SURFACE_ABSOLUTE_Z_TOOLTIP,
    SURFACE_HARDWARE_AF_TOOLTIP,
    SURFACE_HARDWARE_TOOLTIP,
    SURFACE_NO_SOFTWARE_AF_TOOLTIP,
    TOP_BOTTOM_AF_TOOLTIP,
    Z_SURFACE_KEY,
    MDASequenceWidget,
    Mode,
)
from pymmcore_widgets.useq_widgets._positions import SURFACE_CHECKED_TOOLTIP

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot

SURFACE = ZSurface(
    points=[
        (x, y, 0.01 * x)
        for x, y in [(-1000, -1000), (1000, -1000), (-1000, 1000), (1000, 1000)]
    ]
)


def _surface_wdg(
    qtbot: QtBot, seq: useq.MDASequence, widget_cls: type[MDAWidget] = MDAWidget
) -> MDAWidget:
    wdg = widget_cls()
    qtbot.addWidget(wdg)
    wdg.setValue(seq)
    wdg.stage_positions.setSurface(SURFACE)
    wdg.stage_positions.include_z.setChecked(False)
    wdg.stage_positions.surface_focus.setChecked(True)
    assert wdg.stage_positions.surface_focus.isChecked()
    return wdg


def _zs(seq: useq.MDASequence) -> list[float]:
    return [round(e.z_pos, 2) for e in seq]


@pytest.fixture(autouse=True)
def _no_messages() -> Iterator[None]:
    """Settings that rule each other out are kept from being selected: no popup."""
    with patch.object(QMessageBox, "warning") as warning:
        yield
    warning.assert_not_called()


@pytest.mark.parametrize("widget_cls", [MDAWidget, MDAWidgetCollapsible])
def test_surface_focus_value_and_reload(
    qtbot: QtBot, widget_cls: type[MDAWidget]
) -> None:
    grid = {"grid_plan": {"rows": 1, "columns": 2, "fov_width": 10, "fov_height": 10}}
    seq = useq.MDASequence(
        stage_positions=[
            {"x": 100, "y": 0, "z": 7, "name": "A", "sequence": grid},
            {"x": 500, "y": 0, "z": 7},
        ],
        channels=[{"config": "DAPI", "exposure": 1}, {"config": "FITC", "z_offset": 1}],
        z_plan=useq.ZRangeAround(range=2, step=2),
        # any order: the Z of each event is computed when iterating
        axis_order="tpcgz",
    )
    wdg = _surface_wdg(qtbot, seq, widget_cls)

    value = wdg.value()
    assert value.focus_surface == SURFACE.focus_surface()
    # the positions are kept as they are, without Z
    assert [(p.x, p.y, p.z) for p in value.stage_positions] == [
        (100, 0, None),
        (500, 0, None),
    ]
    assert value.stage_positions[0].sequence.grid_plan is not None
    # each event: surface Z at its XY, plus its Z stack and channel offsets
    events = list(value)
    assert [(e.x_pos, e.channel.config) for e in events[:4:2]] == [
        (95, "DAPI"),
        (105, "DAPI"),
    ]
    assert _zs(value)[:8] == [-0.05, 1.95, 0.05, 2.05, 0.95, 2.95, 1.05, 3.05]
    # the table and its (hidden) Z are left alone
    records = wdg.stage_positions.value(exclude_hidden_cols=False)
    assert [p.z for p in records] == [7, 7]

    wdg2 = widget_cls()
    qtbot.addWidget(wdg2)
    wdg2.setValue(value)
    pos = wdg2.stage_positions
    assert pos.surface_focus.isChecked()
    assert not pos.include_z.isChecked()
    assert pos.surface() == SURFACE
    assert wdg2.value() == value

    # unchecked, the surface setup is still saved, and the events are left alone
    pos.surface_focus.setChecked(False)
    value = wdg2.value()
    assert value.focus_surface is None
    assert value.metadata[PYMMCW_METADATA_KEY][Z_SURFACE_KEY] == SURFACE.model_dump(
        mode="json"
    )
    wdg2.setValue(value)
    assert pos.surface() == SURFACE
    assert not pos.surface_focus.isChecked()
    # legacy settings clear it
    wdg2.setValue(seq)
    assert pos.surface() == ZSurface()
    assert Z_SURFACE_KEY not in wdg2.value().metadata[PYMMCW_METADATA_KEY]


def test_surface_focus_with_channel_properties(qtbot: QtBot) -> None:
    wdg = _surface_wdg(qtbot, useq.MDASequence(stage_positions=[(500, 0)]))
    assert _zs(to_channel_properties_sequence(wdg.value())) == [5]


def test_surface_focus_excludes_hardware_autofocus(qtbot: QtBot) -> None:
    wdg = _surface_wdg(qtbot, useq.MDASequence(stage_positions=[(0, 0)]))
    pos, af = wdg.stage_positions, wdg.af_axis
    pos.surface_focus.setChecked(False)
    assert af.isSoftwareAvailable()

    af.setKind("hardware")
    wdg.valueChanged.emit()
    assert not pos.surface_focus.isEnabled()
    assert pos.surface_focus.toolTip() == SURFACE_HARDWARE_AF_TOOLTIP
    af.use_software.setChecked(True)
    assert pos.surface_focus.isEnabled()

    pos.surface_focus.setChecked(True)
    for wdg_ in (af.use_hardware, pos.af_per_position):
        assert not wdg_.isEnabled()
        assert wdg_.toolTip() == SURFACE_HARDWARE_TOOLTIP
    assert af.kind() == "software"

    # the Hardware mode selected while Autofocus is off is replaced with Software
    # when Surface Focus is checked: checking Autofocus then uses Software
    pos.surface_focus.setChecked(False)
    af.setChecked(False)
    af.use_hardware.setChecked(True)
    pos.surface_focus.setChecked(True)
    assert af.use_software.isChecked()
    af.setChecked(True)
    assert af.kind() == "software"

    # unchecking Surface Focus does not switch the mode back
    pos.surface_focus.setChecked(False)
    assert af.use_hardware.isEnabled()
    assert af.kind() == "software"
    assert pos.af_per_position.toolTip() == AF_PER_POS_SOFTWARE_TOOLTIP
    af.use_hardware.setChecked(True)
    assert pos.af_per_position.isEnabled()


def test_surface_focus_without_software_autofocus(qtbot: QtBot) -> None:
    # not connected to a core, which would tell what the microscope can run
    wdg = MDASequenceWidget()
    qtbot.addWidget(wdg)
    pos, af = wdg.stage_positions, wdg.af_axis
    pos.include_z.setChecked(False)
    pos.setSurface(SURFACE)
    af.setSoftwareAvailable(False)
    # Hardware is ruled out, and there is no Software kind: Autofocus cannot be
    # checked, rather than being checked to do nothing
    pos.surface_focus.setChecked(True)
    assert not af.isEnabled()
    assert af.toolTip() == SURFACE_NO_SOFTWARE_AF_TOOLTIP
    pos.surface_focus.setChecked(False)
    assert af.isEnabled()
    assert af.toolTip() == AF_AXIS_TOOLTIP


def _z_tab_on(wdg: MDAWidget) -> None:
    wdg.tab_wdg.setChecked(wdg.z_plan, True)


def test_surface_focus_blocks_top_bottom(qtbot: QtBot) -> None:
    wdg = _surface_wdg(qtbot, useq.MDASequence(stage_positions=[(0, 0)]))
    pos, z_plan = wdg.stage_positions, wdg.z_plan
    _z_tab_on(wdg)
    top_bottom = z_plan._btn_top_bot
    default_tooltip = z_plan._mode_tooltips[Mode.TOP_BOTTOM]
    # the absolute Z mode cannot be selected, and says why
    assert z_plan.isModeBlocked(Mode.TOP_BOTTOM)
    assert top_bottom.toolTip() == SURFACE_CHECKED_TOOLTIP
    qtbot.mouseClick(top_bottom, Qt.MouseButton.LeftButton)
    assert z_plan.mode() == Mode.RANGE_AROUND
    assert pos.surface_focus.isChecked()
    # until Surface Focus is unchecked
    pos.surface_focus.setChecked(False)
    assert not z_plan.isModeBlocked(Mode.TOP_BOTTOM)
    assert top_bottom.toolTip() == default_tooltip
    qtbot.mouseClick(top_bottom, Qt.MouseButton.LeftButton)
    assert z_plan.mode() == Mode.TOP_BOTTOM
    # which in turn rules Surface Focus out
    assert not pos.surface_focus.isEnabled()
    assert pos.surface_focus.toolTip() == SURFACE_ABSOLUTE_Z_TOOLTIP


def test_autofocus_and_surface_focus_block_top_bottom(qtbot: QtBot) -> None:
    wdg = _surface_wdg(qtbot, useq.MDASequence(stage_positions=[(0, 0)]))
    af = wdg.af_axis
    af.setKind("software")
    af.setValue(("p",))
    _z_tab_on(wdg)
    # both reasons, one per line
    reasons = f"{SURFACE_CHECKED_TOOLTIP}\n{TOP_BOTTOM_AF_TOOLTIP}"
    assert wdg.z_plan._btn_top_bot.toolTip() == reasons
    wdg.stage_positions.surface_focus.setChecked(False)
    assert wdg.z_plan._btn_top_bot.toolTip() == TOP_BOTTOM_AF_TOOLTIP
    af.setValue(())
    assert not wdg.z_plan.isModeBlocked(Mode.TOP_BOTTOM)


def test_surface_focus_replaces_a_hidden_top_bottom(qtbot: QtBot) -> None:
    wdg = _surface_wdg(qtbot, useq.MDASequence(stage_positions=[(0, 0)]))
    pos = wdg.stage_positions
    pos.surface_focus.setChecked(False)
    # selected with the Z tab unchecked: no Z plan, so Surface Focus can be checked
    wdg.z_plan.setMode(Mode.TOP_BOTTOM)
    pos.surface_focus.setChecked(True)
    assert pos.surface_focus.isChecked()
    # the mode it rules out is not kept, to be found once the Z tab is checked
    assert wdg.z_plan.mode() == Mode.RANGE_AROUND
    _z_tab_on(wdg)
    assert pos.surface_focus.isChecked()
    assert wdg.value().focus_surface is not None


def test_surface_focus_value_with_top_bottom_just_picked(qtbot: QtBot) -> None:
    wdg = _surface_wdg(qtbot, useq.MDASequence(stage_positions=[(0, 0)]))
    _z_tab_on(wdg)
    # as a slot of valueChanged may see a mode set from code: before Surface Focus
    # is turned off for it
    with signals_blocked(wdg.z_plan):
        wdg.z_plan.setMode(Mode.TOP_BOTTOM)
    assert wdg.stage_positions.surface_focus.isChecked()
    value = wdg.value()  # no ValidationError
    assert value.focus_surface is None
    assert value.z_plan is not None and not value.z_plan.is_relative


def test_surface_focus_absolute_z(qtbot: QtBot) -> None:
    wdg = _surface_wdg(qtbot, useq.MDASequence(stage_positions=[(0, 0)]))
    wdg.setValue(
        useq.MDASequence(
            stage_positions=[(0, 0)], z_plan={"top": 1, "bottom": 0, "step": 1}
        )
    )
    pos = wdg.stage_positions
    pos.setSurface(SURFACE)
    pos.include_z.setChecked(False)
    assert not pos.surface_focus.isChecked()
    assert pos.surface_focus.toolTip() == SURFACE_ABSOLUTE_Z_TOOLTIP


def test_surface_focus_load_with_hardware_autofocus(qtbot: QtBot) -> None:
    wdg = _surface_wdg(qtbot, useq.MDASequence(stage_positions=[(0, 0)]))
    value = useq.MDASequence(
        stage_positions=[(0, 0)],
        autofocus_plan=useq.AxesBasedAF(axes=("p",)),
        focus_surface=SURFACE.focus_surface(),
    )
    with pytest.warns(UserWarning, match="hardware autofocus"):
        wdg.setValue(value)
    assert not wdg.stage_positions.surface_focus.isChecked()
    assert wdg.stage_positions.surface() == SURFACE
    assert wdg.af_axis.kind() == "hardware"

    unused = {PYMMCW_METADATA_KEY: {Z_SURFACE_KEY: {"method": "cubic"}}}
    with pytest.warns(UserWarning, match="invalid Surface Focus"):
        wdg.setValue(useq.MDASequence(metadata=unused))
    assert wdg.stage_positions.surface() == ZSurface()


def test_surface_focus_grid_without_positions(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    wdg = _surface_wdg(
        qtbot,
        useq.MDASequence(
            grid_plan=useq.GridRowsColumns(
                rows=1, columns=2, fov_width=10, fov_height=10
            )
        ),
    )
    global_mmcore.setXYPosition(100, 0)
    global_mmcore.waitForSystem()
    x = global_mmcore.getXPosition()
    # the grid is centered on the current position
    events = list(wdg.value())
    assert [e.x_pos for e in events] == [x - 5, x + 5]
    assert [e.z_pos for e in events] == pytest.approx([0.95, 1.05], abs=0.01)


def test_surface_focus_plate(qtbot: QtBot) -> None:
    plate = useq.WellPlatePlan(
        plate=96, a1_center_xy=(0, 0), selected_wells=([0], [0, 1])
    )
    seq = useq.MDASequence(
        stage_positions=plate, z_plan=useq.ZRangeAround(range=2, step=2)
    )
    wdg = _surface_wdg(qtbot, seq)
    pos = wdg.stage_positions
    assert pos._hcs_z_info.text() == HCS_SURFACE_Z_INFO[0]
    # the plate plan is kept, and its relative Z stack is not anchored to the
    # current Z: it is centered on the surface Z of each image
    value = wdg._anchor_plate_z_plan(wdg.value())
    assert value.stage_positions == plate
    assert _zs(value) == [-1, 1, 89, 91]
    wdg.setValue(value)
    assert isinstance(pos.value(), useq.WellPlatePlan)
    assert pos.surface_focus.isChecked()
    pos.surface_focus.setChecked(False)
    assert pos._hcs_z_info.text() == HCS_Z_INFO[0]


def test_surface_focus_devices(global_mmcore: CMMCorePlus, qtbot: QtBot) -> None:
    wdg = _surface_wdg(qtbot, useq.MDASequence(stage_positions=[(0, 0)]))
    pos = wdg.stage_positions
    global_mmcore.setXYPosition(10, 20)
    global_mmcore.setZPosition(30)
    global_mmcore.waitForSystem()
    stage = pos._surface_stage()
    assert stage is not None
    assert stage.position() == pytest.approx((10, 20, 30), abs=0.01)
    stage.move_z(40)
    global_mmcore.waitForSystem()
    assert global_mmcore.getZPosition() == pytest.approx(40, abs=0.01)
    stage.move_xy(50, 60)
    global_mmcore.waitForSystem()
    assert global_mmcore.getXYPosition() == pytest.approx((50, 60), abs=0.01)

    # checked, it disables "Include Z"; without a focus device, that one says so
    assert not pos.include_z.isEnabled()
    global_mmcore.setProperty("Core", "Focus", "")
    assert not pos.surface_focus.isChecked()
    assert pos.surface_focus.toolTip() == SURFACE_UNAVAILABLE
    assert not pos.include_z.isEnabled()
    assert pos.include_z.toolTip() == "Focus device unavailable."
    assert pos._surface_stage() is None
    assert pos.surface() == SURFACE


def test_surface_focus_run(global_mmcore: CMMCorePlus, qtbot: QtBot) -> None:
    wdg = _surface_wdg(qtbot, useq.MDASequence(stage_positions=[(500, 0)]))
    sf = wdg.stage_positions.surface_focus
    events: list[useq.MDAEvent] = []
    global_mmcore.mda.events.eventStarted.connect(events.append)
    with qtbot.waitSignal(global_mmcore.mda.events.sequenceFinished):
        wdg.execute_mda(None)
    assert [e.z_pos for e in events] == pytest.approx([5])
    qtbot.waitUntil(wdg.stage_positions.isEnabled, timeout=2000)
    assert sf.isEnabled() and sf.isChecked()


def test_surface_dialog_disabled_while_running(qtbot: QtBot) -> None:
    wdg = _surface_wdg(qtbot, useq.MDASequence(stage_positions=[(0, 0)]))
    wdg.stage_positions._surface_settings.click()
    dialog = wdg.stage_positions._surface_dialog
    assert dialog is not None and dialog.isEnabled()
    # it can move the stage: not while an acquisition runs
    wdg._on_mda_started()
    assert not dialog.isEnabled()
    wdg._enable_widgets(True)
    assert dialog.isEnabled()
    dialog.reject()
