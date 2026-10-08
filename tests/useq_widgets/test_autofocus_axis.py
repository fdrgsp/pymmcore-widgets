"""The MDA autofocus section: kind selection, Z search, and every-N timepoints."""

from __future__ import annotations

from dataclasses import dataclass
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


def test_methods_are_offered_out_of_the_box(qtbot: QtBot) -> None:
    """The routines the acquisition engine can run, without any wiring."""
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.enabled.setChecked(True)
    assert af.method.count() > 0
    assert af.use_software.isEnabled()


def test_software_disabled_until_methods_exist(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.enabled.setChecked(True)
    af.setSoftwareMethods({})  # as if none were installed
    assert not af.use_software.isEnabled()

    af.setSoftwareMethods(["oughtafocus", "jaf_hp"])
    assert af.use_software.isEnabled()

    # losing the methods disables the option; the mode is deliberately not switched
    # for the user, so no plan is built for something that cannot run
    af.use_software.setChecked(True)
    af.setSoftwareMethods([])
    assert not af.use_software.isEnabled()
    assert af.kind() is None


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


def test_no_search_by_default(qtbot: QtBot) -> None:
    """Autofocus is only attempted where it starts until a search is asked for."""
    wdg = _wdg(qtbot)
    wdg.af_axis.enabled.setChecked(True)
    wdg.af_axis.use_af_p.setChecked(True)

    plan = wdg.value().autofocus_plan
    assert isinstance(plan, useq.AxesBasedAF)
    assert plan.axes == ("p",)
    assert plan.search_below_um == 0.0
    assert plan.search_above_um == 0.0
    assert plan.every_n_timepoints == 1


def test_asking_for_a_range_fills_in_a_step(qtbot: QtBot) -> None:
    """A range with no step is not a search, and the schema refuses to build one."""
    wdg = _wdg(qtbot)
    wdg.af_axis.enabled.setChecked(True)
    wdg.af_axis.use_af_p.setChecked(True)
    assert wdg.af_axis.search_step_um.value() == 0.0

    wdg.af_axis.search_below_um.setValue(20.0)
    assert wdg.af_axis.search_step_um.value() > 0.0

    plan = wdg.value().autofocus_plan
    assert plan is not None
    assert plan.search_below_um == 20.0
    assert plan.search_step_um == wdg.af_axis.search_step_um.value()


def test_a_step_already_set_is_left_alone(qtbot: QtBot) -> None:
    wdg = _wdg(qtbot)
    wdg.af_axis.enabled.setChecked(True)
    wdg.af_axis.search_step_um.setValue(1.5)
    wdg.af_axis.search_above_um.setValue(8.0)
    assert wdg.af_axis.search_step_um.value() == 1.5


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
    wdg.af_axis.search_below_um.setValue(12.0)
    wdg.af_axis.search_above_um.setValue(4.0)
    wdg.af_axis.search_step_um.setValue(2.0)

    plan = wdg.value().autofocus_plan
    assert plan is not None
    assert plan.search_below_um == 12.0
    assert plan.search_above_um == 4.0
    assert plan.search_step_um == 2.0


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
    af.setSoftwareMethods({})  # as if none were installed
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


# ------------------------------ the method picker ------------------------------


@dataclass(frozen=True)
class _DemoSettings:
    """A demo routine."""

    span_um: float = 5.0


def test_methods_populate_the_picker(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.enabled.setChecked(True)
    af.setSoftwareMethods({"oughtafocus": _DemoSettings, "jaf": _DemoSettings})

    assert [af.method.itemText(i) for i in range(af.method.count())] == [
        "oughtafocus",
        "jaf",
    ]
    assert af.use_software.isEnabled()
    assert af.settings_button.isEnabled()


def test_methods_without_a_model_offer_no_settings(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.enabled.setChecked(True)
    af.setSoftwareMethods(["mystery"])
    assert af.use_software.isEnabled()
    assert not af.settings_button.isEnabled()


def test_software_needs_the_right_devices(qtbot: QtBot) -> None:
    """A routine needs a camera and a focus drive, whatever methods are installed."""
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.enabled.setChecked(True)
    af.setSoftwareMethods({"oughtafocus": _DemoSettings})
    assert af.use_software.isEnabled()

    af.setSoftwareAvailable(False, "Software autofocus needs a camera.")
    assert not af.use_software.isEnabled()
    assert af.use_software.toolTip() == "Software autofocus needs a camera."

    af.setSoftwareAvailable(True)
    assert af.use_software.isEnabled()


def test_the_chosen_method_and_settings_reach_the_plan(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.enabled.setChecked(True)
    af.setSoftwareMethods({"oughtafocus": _DemoSettings, "jaf": _DemoSettings})
    af.use_software.setChecked(True)
    af.method.setCurrentText("jaf")
    af._method_settings["jaf"] = {"span_um": 12.0}

    plan = af.plan(("p",))
    assert isinstance(plan, useq.SoftwareAxesBasedAF)
    assert plan.method == "jaf"
    assert plan.settings == {"span_um": 12.0}


def test_each_method_keeps_its_own_settings(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.enabled.setChecked(True)
    af.setSoftwareMethods({"a": _DemoSettings, "b": _DemoSettings})
    af.use_software.setChecked(True)
    af._method_settings["a"] = {"span_um": 1.0}
    af._method_settings["b"] = {"span_um": 2.0}

    af.method.setCurrentText("a")
    assert af.plan(("p",)).settings == {"span_um": 1.0}
    af.method.setCurrentText("b")
    assert af.plan(("p",)).settings == {"span_um": 2.0}
    af.method.setCurrentText("a")
    assert af.plan(("p",)).settings == {"span_um": 1.0}


def test_a_method_this_installation_lacks_is_kept(qtbot: QtBot) -> None:
    """Otherwise opening someone else's sequence would silently change the routine."""
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.setSoftwareMethods({"oughtafocus": _DemoSettings})
    plan = useq.SoftwareAxesBasedAF(
        axes=("p",), method="their_custom_method", settings={"x": 1}
    )
    af.setPlan(plan)

    assert af.softwareMethod() == "their_custom_method"
    assert af.plan(("p",)).method == "their_custom_method"
    assert af.plan(("p",)).settings == {"x": 1}
    # ... but there is no form for a routine we do not have
    assert not af.settings_button.isEnabled()


def test_changing_method_emits_value_changed(qtbot: QtBot) -> None:
    af = AutofocusAxis()
    qtbot.addWidget(af)
    af.setSoftwareMethods({"a": _DemoSettings, "b": _DemoSettings})
    with qtbot.waitSignal(af.valueChanged):
        af.method.setCurrentText("b")
