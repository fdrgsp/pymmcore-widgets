from __future__ import annotations

from typing import TYPE_CHECKING

import useq

from pymmcore_widgets import MDAWidget, MDAWidgetCollapsible
from pymmcore_widgets.mda import (
    CollapsibleCoreMDATabs,
    SectionMetrics,
)

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


def test_collapsible_is_mda_widget(qtbot: QtBot) -> None:
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)
    # It IS an MDAWidget: same behavior, different presentation.
    assert isinstance(wdg, MDAWidget)
    assert isinstance(wdg.tab_wdg, CollapsibleCoreMDATabs)
    assert wdg.tabs is wdg.tab_wdg
    # The axes are laid out as collapsible sections, plus Saving + Settings.
    assert [s.title for s in wdg.tabs.sections] == [
        "Channels",
        "Positions",
        "Grid / Tile Scan",
        "Z Stack",
        "Time Series",
        "Saving",
        "Settings",
    ]
    assert wdg.tabs.saving_section is wdg.tabs.sections[-2]
    assert wdg.tabs.settings_section is wdg.tabs.sections[-1]
    assert wdg.tabs.tabBar().isHidden()


def test_collapsible_value_parity_with_mda_widget(qtbot: QtBot) -> None:
    """The collapsible presentation must build the identical sequence."""
    ref = MDAWidget()
    qtbot.addWidget(ref)
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)

    ref.setValue(MDA)
    wdg.setValue(MDA)

    assert wdg.value() == ref.value()
    # per-axis inclusion mirrors the reference
    for axis in "cpgzt":
        assert wdg.tabs.isAxisUsed(axis) == ref.tab_wdg.isAxisUsed(axis)
    # per-position autofocus offset preserved
    restored = wdg.value().stage_positions
    assert restored[0].sequence is not None
    assert restored[0].sequence.autofocus_plan is not None
    assert restored[0].sequence.autofocus_plan.autofocus_motor_offset == 25.0


def test_collapsible_disables_editors_during_run(qtbot: QtBot) -> None:
    wdg = MDAWidgetCollapsible()
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
        section = tabs.section(axis)
        assert section.checkbox is not None
        assert not section.checkbox.isEnabled()
        assert not widget.isEnabled()
    assert not tabs.settings_section._body.isEnabled()
    assert not wdg.save_info.isEnabled()

    wdg._enable_widgets(True)
    assert wdg.channels.isEnabled()
    assert tabs.settings_section._body.isEnabled()
    assert wdg.save_info.isEnabled()


def test_collapsible_runs_acquisition(qtbot: QtBot) -> None:
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)
    wdg.setValue(MDA)

    with qtbot.waitSignal(wdg._mmc.mda.events.sequenceFinished):
        wdg.control_btns.run_btn.click()

    assert wdg.control_btns.run_btn.isEnabled()
    wdg.control_btns._disconnect()
    wdg._disconnect()


def test_collapsible_section_metrics(qtbot: QtBot) -> None:
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)

    wdg.set_section_metrics(SectionMetrics(header_height=44, disclosure_width=30))
    for section in wdg.tabs.sections:
        assert section._header.minimumHeight() == 44
        assert section._disclosure.width() == 30
