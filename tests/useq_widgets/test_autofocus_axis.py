"""The MDA autofocus section: kind selection, Z search, and every-N timepoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

import useq

from pymmcore_widgets.useq_widgets import MDASequenceWidget
from pymmcore_widgets.useq_widgets._mda_sequence import AutofocusAxis

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


def _wdg(qtbot: QtBot) -> MDASequenceWidget:
    wdg = MDASequenceWidget()
    qtbot.addWidget(wdg)
    return wdg


# ------------------------------- kind selection -------------------------------


def test_autofocus_is_off_until_enabled(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    assert not af.enabled.isChecked()
    assert af.kind() is None
    # ... and its controls are not editable while it is off
    assert not af._body.isEnabled()

    af.enabled.setChecked(True)
    assert af.kind() == "hardware"  # the default kind
    assert af._body.isEnabled()


def test_kinds_are_mutually_exclusive(qtbot: QtBot) -> None:
    """An acquisition carries one autofocus plan, so only one kind can be chosen."""
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.enabled.setChecked(True)
    af.setSoftwareMethods(["oughtafocus"])  # otherwise the option is disabled

    af.use_software.setChecked(True)
    assert not af.use_hardware.isChecked()
    assert af.kind() == "software"

    af.use_hardware.setChecked(True)
    assert not af.use_software.isChecked()
    assert af.kind() == "hardware"

    # switching the group off leaves no autofocus at all
    af.enabled.setChecked(False)
    assert af.kind() is None


def test_software_disabled_until_methods_exist(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.enabled.setChecked(True)
    assert not af.use_software.isEnabled()

    af.setSoftwareMethods(["oughtafocus", "jaf_hp"])
    assert af.use_software.isEnabled()

    # losing the methods must not leave software autofocus selected
    af.use_software.setChecked(True)
    af.setSoftwareMethods([])
    assert not af.use_software.isEnabled()
    assert af.kind() == "hardware"


def test_each_kind_shows_only_its_own_options(qtbot: QtBot) -> None:
    """The Z search recovers a hardware lock; skipping time points saves images."""
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.enabled.setChecked(True)
    af.setSoftwareMethods(["oughtafocus"])

    assert not af.search_below_um.isHidden()
    assert af.every_n_timepoints.isHidden()

    af.use_software.setChecked(True)
    assert af.search_below_um.isHidden()
    assert af.search_above_um.isHidden()
    assert af.search_step_um.isHidden()
    assert not af.every_n_timepoints.isHidden()

    af.use_hardware.setChecked(True)
    assert not af.search_below_um.isHidden()
    assert af.every_n_timepoints.isHidden()


def test_kind_change_emits_value_changed(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.enabled.setChecked(True)
    af.setSoftwareMethods(["oughtafocus"])
    with qtbot.waitSignal(af.valueChanged):
        af.use_software.setChecked(True)


def test_enabling_emits_value_changed(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    with qtbot.waitSignal(af.valueChanged):
        af.enabled.setChecked(True)


# --------------------------------- Z search ---------------------------------


def test_widget_defaults_enable_search(qtbot: QtBot) -> None:
    """The GUI pre-fills 10 µm either way; the schema default is 0 (no search)."""
    wdg = _wdg(qtbot)
    wdg.af_axis.enabled.setChecked(True)
    wdg.af_axis.use_af_p.setChecked(True)

    plan = wdg.value().autofocus_plan
    assert isinstance(plan, useq.AxesBasedAF)
    assert plan.axes == ("p",)
    assert plan.search_below_um == 10.0
    assert plan.search_above_um == 10.0
    assert plan.search_step_um == 5.0
    assert plan.every_n_timepoints == 1
    # ... while a plan built in code searches nothing
    assert useq.AxesBasedAF(axes=("p",)).search_below_um == 0.0


def test_search_values_round_trip(qtbot: QtBot) -> None:
    wdg = _wdg(qtbot)
    plan = useq.AxesBasedAF(
        axes=("p",), search_below_um=20.0, search_above_um=30.0, search_step_um=2.5
    )
    wdg.setValue(useq.MDASequence(stage_positions=[(0, 0)], autofocus_plan=plan))

    assert wdg.af_axis.search_below_um.value() == 20.0
    assert wdg.af_axis.search_above_um.value() == 30.0
    assert wdg.af_axis.search_step_um.value() == 2.5
    assert wdg.value().autofocus_plan == plan


def test_search_is_editable(qtbot: QtBot) -> None:
    wdg = _wdg(qtbot)
    wdg.af_axis.enabled.setChecked(True)
    wdg.af_axis.use_af_p.setChecked(True)
    wdg.af_axis.search_below_um.setValue(0.0)
    wdg.af_axis.search_above_um.setValue(0.0)

    plan = wdg.value().autofocus_plan
    assert plan is not None
    assert plan.search_below_um == 0.0
    assert plan.search_above_um == 0.0


# ----------------------------- every N timepoints -----------------------------


def test_every_n_timepoints_round_trips_for_software(qtbot: QtBot) -> None:
    """Skipping time points is a software-autofocus setting: each run costs images."""
    wdg = _wdg(qtbot)
    plan = useq.SoftwareAxesBasedAF(
        axes=("t",), method="oughtafocus", every_n_timepoints=3
    )
    wdg.setValue(useq.MDASequence(stage_positions=[(0, 0)], autofocus_plan=plan))
    assert wdg.af_axis.every_n_timepoints.value() == 3
    assert wdg.value().autofocus_plan == plan

    wdg.af_axis.every_n_timepoints.setValue(5)
    out = wdg.value().autofocus_plan
    assert out is not None
    assert out.every_n_timepoints == 5


def test_hardware_plan_keeps_its_own_every_n(qtbot: QtBot) -> None:
    """The widget does not offer this for hardware, so it must not overwrite it."""
    wdg = _wdg(qtbot)
    plan = useq.AxesBasedAF(axes=("t",), every_n_timepoints=4)
    wdg.setValue(useq.MDASequence(stage_positions=[(0, 0)], autofocus_plan=plan))
    assert wdg.value().autofocus_plan == plan


# ----------------------------- software plans -----------------------------


def test_software_plan_is_preserved(qtbot: QtBot) -> None:
    """Loading a software plan must not silently turn it into a hardware one."""
    wdg = _wdg(qtbot)
    plan = useq.SoftwareAxesBasedAF(
        axes=("p",),
        method="oughtafocus",
        focus_device="ZPiezo",
        settings={"search_range_um": 15.0},
        every_n_timepoints=2,
    )
    wdg.setValue(useq.MDASequence(stage_positions=[(0, 0)], autofocus_plan=plan))

    assert wdg.af_axis.kind() == "software"
    assert wdg.af_axis.use_software.isEnabled()
    assert wdg.value().autofocus_plan == plan


def test_hardware_plan_stays_hardware(qtbot: QtBot) -> None:
    wdg = _wdg(qtbot)
    plan = useq.AxesBasedAF(axes=("p",), autofocus_motor_offset=10.0)
    wdg.setValue(useq.MDASequence(stage_positions=[(0, 0)], autofocus_plan=plan))

    assert wdg.af_axis.kind() == "hardware"
    assert wdg.value().autofocus_plan == plan


def test_no_plan_without_axes(qtbot: QtBot) -> None:
    wdg = _wdg(qtbot)
    assert wdg.af_axis.kind() is None
    assert wdg.value().autofocus_plan is None


# --------------------------------- tooltips ---------------------------------


def test_every_control_explains_itself(qtbot: QtBot) -> None:
    """Autofocus has real trade-offs, so each control has to say what it does."""
    af = AutofocusAxis()
    qtbot.addWidget(af)
    controls = (
        af.enabled,
        af.label,
        af.use_af_p,
        af.use_af_t,
        af.use_af_g,
        af.use_hardware,
        af.use_software,
        af.search_below_um,
        af.search_above_um,
        af.search_step_um,
        af.every_n_timepoints,
    )
    for wdg in controls:
        assert len(wdg.toolTip()) > 20, wdg


def test_mode_tooltips_describe_the_trade_off(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.setSoftwareMethods(["oughtafocus"])

    hardware = af.use_hardware.toolTip()
    software = af.use_software.toolTip()
    assert hardware != software
    # hardware: fast, no images, but follows the coverslip and has a limited range
    assert "coverslip" in hardware
    # software: finds the sharpest image, but costs images and exposes the sample
    assert "images" in software


def test_software_tooltip_says_why_it_is_unavailable(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    assert "No software autofocus methods" in af.use_software.toolTip()
    af.setSoftwareMethods(["oughtafocus"])
    assert "No software autofocus methods" not in af.use_software.toolTip()


def test_search_tooltips_are_specific_to_each_field(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    tips = {
        af.search_below_um.toolTip(),
        af.search_above_um.toolTip(),
        af.search_step_um.toolTip(),
    }
    assert len(tips) == 3  # not the same text pasted three times
    assert "below" in af.search_below_um.toolTip()
    assert "above" in af.search_above_um.toolTip()
