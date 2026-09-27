from __future__ import annotations

from typing import TYPE_CHECKING

import useq
from qtpy.QtWidgets import QWidget

from pymmcore_widgets import MDAWidget, MDAWidgetCollapsible
from pymmcore_widgets.mda import CollapsibleCoreMDATabs, SectionMetrics
from pymmcore_widgets.useq_widgets._positions import MDAButton, _MDAPopup

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
    # The axes are followed by the non-axis Saving section. Global
    # settings (axis order, keep shutter open, autofocus axis) are not a
    # section: they always apply, so they live in a card between the sections
    # and the footer (see MDAWidgetCollapsible._install_layout).
    assert [s.title for s in wdg.tabs.sections] == [
        "Channels",
        "Positions",
        "Grid/Tiles",
        "Z Stack",
        "Time Series",
        "Saving",
    ]
    assert wdg.tabs.saving_section is wdg.tabs.sections[-1]
    assert wdg.tabs.tabBar().isHidden()
    assert not wdg.tabs.isAncestorOf(wdg._settings_box)
    assert wdg._settings_box.isAncestorOf(wdg.axis_order)
    assert wdg._settings_box.isAncestorOf(wdg.keep_shutter_open)
    assert wdg._settings_box.isAncestorOf(wdg.af_axis)


def test_collapsible_settings_sit_between_the_sections_and_the_footer(
    qtbot: QtBot,
) -> None:
    """Global settings are a card of their own, outside sections and footer.

    They always apply, so there is nothing for a disclosure affordance to
    reveal -- and keeping them out of the scrollable body means they stay
    visible however the sections are scrolled.
    """
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)
    wdg.resize(500, 800)
    wdg.show()
    qtbot.waitExposed(wdg)

    footer = wdg.findChild(QWidget, "mdaExecutionFooter")
    assert footer is not None
    assert not footer.isAncestorOf(wdg._settings_box)
    assert not wdg.tabs.isAncestorOf(wdg._settings_box)

    # sections above, then the settings card, then the footer
    tabs_bottom = wdg.tabs.mapTo(wdg, wdg.tabs.rect().bottomLeft()).y()
    box = wdg._settings_box
    box_top = box.mapTo(wdg, box.rect().topLeft()).y()
    box_bottom = box.mapTo(wdg, box.rect().bottomLeft()).y()
    footer_top = footer.mapTo(wdg, footer.rect().topLeft()).y()
    assert tabs_bottom <= box_top
    assert box_bottom <= footer_top


def test_collapsible_value_parity_with_mda_widget(qtbot: QtBot) -> None:
    """The collapsible presentation must build the identical sequence."""
    ref = MDAWidget()
    qtbot.addWidget(ref)
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)

    ref.setValue(MDA)
    wdg.setValue(MDA)

    collapsible_value = wdg.value()
    reference_value = ref.value()
    assert collapsible_value.replace(metadata={}) == reference_value.replace(
        metadata={}
    )
    # per-axis inclusion mirrors the reference
    for axis in "cpgzt":
        assert wdg.tabs.isAxisUsed(axis) == ref.tab_wdg.isAxisUsed(axis)
    # per-position autofocus offset preserved
    restored = wdg.value().stage_positions
    assert restored[0].sequence is not None
    assert restored[0].sequence.autofocus_plan is not None
    assert restored[0].sequence.autofocus_plan.autofocus_motor_offset == 25.0


def test_collapsible_hcs_positions_refresh_summary(qtbot: QtBot) -> None:
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)
    wdg.tabs.setChecked("p", True)
    section = wdg.tabs.section("p")
    assert section.summary == "On · 1 position"

    plan = useq.WellPlatePlan(
        plate="96-well",
        a1_center_xy=(0, 0),
        selected_wells=((0, 0), (0, 1)),
    )
    wdg.stage_positions._update_table_positions(plan)

    assert len(wdg.stage_positions.value()) == 2
    assert section.summary == "On · 2 positions"


def test_settings_file_actions_are_in_execution_footer(qtbot: QtBot) -> None:
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)
    wdg.resize(800, 700)
    wdg.show()

    footer = wdg.findChild(QWidget, "mdaExecutionFooter")
    assert footer is not None
    # Settings is its own card above the footer, not footer content.
    assert not footer.isAncestorOf(wdg._settings_box)
    for button in (wdg._save_button, wdg._load_button):
        assert footer.isAncestorOf(button)
        assert not wdg._settings_box.isAncestorOf(button)

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


def test_unchecked_axis_summaries_only_show_off(qtbot: QtBot) -> None:
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)
    wdg.setValue(MDA)

    for axis in "cpgzt":
        section = wdg.tabs.section(axis)
        wdg.tabs.setChecked(axis, False)
        assert section.summary == "Off"

        # Refreshing after editor values change must not expose inactive details.
        wdg.tabs.refresh_summaries()
        assert section.summary == "Off"

        wdg.tabs.setChecked(axis, True)
        assert section.summary.startswith("On · ")


def test_collapsible_position_subsequence_popup_only_exposes_grid(
    qtbot: QtBot,
) -> None:
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)
    assert wdg.tabs.section("c").expanded
    table = wdg.stage_positions.table()
    button = table.cellWidget(0, table.indexOf(wdg.stage_positions.SEQ))
    assert isinstance(button, MDAButton)

    popup = _MDAPopup(
        useq.MDASequence(stage_positions=[useq.Position(x=1, y=2)]),
        button,
    )
    qtbot.addWidget(popup)

    # The collapsible sections presentation has nothing left to collapse once
    # every axis but the grid is removed, so the popup falls back to the
    # plain, still core-connected tab widget instead -- no disclosure/expand
    # affordance for a single remaining tab.
    assert not isinstance(popup.mda_tabs, CollapsibleCoreMDATabs)
    # a position sub-sequence can only carry a grid plan; every other axis is
    # removed entirely and cannot be checked/used.
    for axis_widget in (
        popup.mda_tabs.stage_positions,
        popup.mda_tabs.channels,
        popup.mda_tabs.z_plan,
        popup.mda_tabs.time_plan,
    ):
        assert popup.mda_tabs.indexOf(axis_widget) == -1
    # no grid plan was supplied, so the grid checkbox starts unchecked
    assert not popup.mda_tabs.isChecked(popup.mda_tabs.grid_plan)
    assert popup.mda_tabs.value().stage_positions == ()
    assert not popup.mda_tabs.value().channels


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
    assert not wdg._settings_box.isEnabled()
    assert not wdg.save_info.isEnabled()
    assert not wdg._save_button.isEnabled()
    assert not wdg._load_button.isEnabled()

    wdg._enable_widgets(True)
    assert wdg.channels.isEnabled()
    assert wdg._settings_box.isEnabled()
    assert wdg.save_info.isEnabled()
    assert wdg._save_button.isEnabled()
    assert wdg._load_button.isEnabled()


def test_collapsible_run_preserves_axis_order(qtbot: QtBot) -> None:
    """Running an acquisition must not silently change the axis order combo.

    Disabling the editors at sequenceStarted (set_editor_enabled) disables the
    whole Positions widget, which used to be misread by
    CoreConnectedPositionTable.eventFilter as its "Set AF Offset per Position"
    checkbox itself losing its enabled state -- toggling that checkbox off and
    repopulating (and thus resetting) the axis-order combo as a side effect.
    """
    wdg = MDAWidgetCollapsible()
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


def test_collapsible_sections_have_card_frame(qtbot: QtBot) -> None:
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)
    for section in wdg.tabs.sections:
        # each section is wrapped in a bordered card so they read as distinct
        assert section._card.objectName() == "mdaSectionCard"


def test_collapsible_table_editors_keep_min_height(qtbot: QtBot) -> None:
    """Row-based editors must not collapse to a single row when several sections
    are expanded together."""
    wdg = MDAWidgetCollapsible()
    qtbot.addWidget(wdg)
    for axis in ("c", "p", "t"):  # channel / position / time tables
        table = wdg.tabs.section(axis).content_widget.table()
        # tall enough for the header + a few rows
        assert table.minimumHeight() >= 3 * table.verticalHeader().defaultSectionSize()
