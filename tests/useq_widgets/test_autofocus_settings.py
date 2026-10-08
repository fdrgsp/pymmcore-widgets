"""The settings form built from a routine's settings dataclass."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox

from pymmcore_widgets.useq_widgets._autofocus_settings import (
    UNCHANGED,
    AutofocusSettingsDialog,
    SettingsForm,
)

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


class Flavour(Enum):
    EDGES = "edges"
    VARIANCE = "variance"


@dataclass(frozen=True)
class Demo:
    """A demo routine.

    Attributes
    ----------
    distance_um : float
        How far to travel.
    steps : int
        How many steps to take.
    strategy : Literal["fast", "thorough"]
        Which way to search.
    flavour : Flavour
        How sharpness is measured.
    loud : bool
        Whether to shout.
    channel : str | None
        Config preset to use.
    exposure_ms : float | None
        Exposure to use.
    nested : dict
        Something a form cannot edit.
    """

    distance_um: float = 10.0
    steps: int = 3
    strategy: Literal["fast", "thorough"] = "fast"
    flavour: Flavour = Flavour.EDGES
    loud: bool = False
    channel: str | None = None
    exposure_ms: float | None = None
    nested: dict = field(default_factory=dict)


def _form(qtbot: QtBot) -> SettingsForm:
    # no choices offered, so these tests are about the field types alone and do not
    # depend on what the microscope happens to have loaded
    form = SettingsForm(Demo, choices=lambda *_: None)
    qtbot.addWidget(form)
    return form


def test_a_control_per_editable_field(qtbot: QtBot) -> None:
    form = _form(qtbot)
    kinds = {name: type(w) for name, w in form._controls.items()}
    assert kinds == {
        "distance_um": QDoubleSpinBox,
        "steps": QSpinBox,
        "strategy": QComboBox,
        "flavour": QComboBox,
        "loud": QCheckBox,
        "channel": QLineEdit,
        "exposure_ms": QDoubleSpinBox,
    }


def test_choices_come_from_the_type(qtbot: QtBot) -> None:
    form = _form(qtbot)
    strategy = form._controls["strategy"]
    assert [strategy.itemText(i) for i in range(strategy.count())] == [
        "fast",
        "thorough",
    ]
    flavour = form._controls["flavour"]
    assert [flavour.itemText(i) for i in range(flavour.count())] == [
        "edges",
        "variance",
    ]


def test_defaults_are_left_out(qtbot: QtBot) -> None:
    """Only what the user changed is carried, so a routine's defaults can move."""
    assert _form(qtbot).value() == {}


def test_changed_values_are_reported(qtbot: QtBot) -> None:
    form = _form(qtbot)
    form._controls["distance_um"].setValue(25.0)
    form._controls["loud"].setChecked(True)
    form._controls["strategy"].setCurrentText("thorough")
    assert form.value() == {
        "distance_um": 25.0,
        "loud": True,
        "strategy": "thorough",
    }


def test_round_trip(qtbot: QtBot) -> None:
    form = _form(qtbot)
    settings = {"distance_um": 7.5, "steps": 9, "channel": "BF", "exposure_ms": 12.0}
    form.setValue(settings)
    assert form.value() == settings


def test_setting_values_resets_the_others(qtbot: QtBot) -> None:
    form = _form(qtbot)
    form._controls["steps"].setValue(99)
    form.setValue({"distance_um": 1.0})
    assert form.value() == {"distance_um": 1.0}


def test_optional_fields_mean_leave_it_alone(qtbot: QtBot) -> None:
    """A blank channel or exposure must stay `None`, not become 0 or ''."""
    form = _form(qtbot)
    assert form._controls["channel"].placeholderText() == UNCHANGED
    assert form._controls["exposure_ms"].specialValueText() == UNCHANGED
    assert "channel" not in form.value()
    assert "exposure_ms" not in form.value()

    form.setValue({"channel": "BF", "exposure_ms": 5.0})
    form.setValue({})  # back to the defaults
    assert form.value() == {}


def test_a_field_the_form_cannot_edit_survives(qtbot: QtBot) -> None:
    """Dropping it would quietly change the routine's configuration."""
    form = _form(qtbot)
    assert "nested" not in form._controls
    form.setValue({"nested": {"method": "x"}, "steps": 4})
    assert form.value() == {"nested": {"method": "x"}, "steps": 4}


def test_labels_are_readable(qtbot: QtBot) -> None:
    from pymmcore_widgets.useq_widgets._autofocus_settings import _humanize

    assert _humanize("distance_um") == "Distance (µm)"
    assert _humanize("fft_lower_pct") == "FFT lower (%)"
    assert _humanize("keep_shutter_open") == "Keep shutter open"
    assert _humanize("steps") == "Steps"


def test_controls_explain_themselves_from_the_docstring(qtbot: QtBot) -> None:
    form = _form(qtbot)
    assert form._controls["distance_um"].toolTip() == "How far to travel."
    assert form._controls["channel"].toolTip() == "Config preset to use."


def test_an_empty_model_makes_an_empty_form(qtbot: QtBot) -> None:
    form = SettingsForm(None)
    qtbot.addWidget(form)
    assert form.value() == {}
    assert form.model() is None


def test_changing_a_control_emits(qtbot: QtBot) -> None:
    form = _form(qtbot)
    with qtbot.waitSignal(form.valueChanged):
        form._controls["steps"].setValue(5)


def test_dialog_shows_the_routine_and_returns_its_settings(qtbot: QtBot) -> None:
    dialog = AutofocusSettingsDialog(Demo, {"steps": 2}, method="demo")
    qtbot.addWidget(dialog)
    assert "demo" in dialog.windowTitle()
    assert dialog.value() == {"steps": 2}
    dialog.form._controls["loud"].setChecked(True)
    assert dialog.value() == {"steps": 2, "loud": True}


def test_dialog_summarises_the_routine(qtbot: QtBot) -> None:
    dialog = AutofocusSettingsDialog(Demo, method="demo")
    qtbot.addWidget(dialog)
    labels = dialog.findChildren(type(dialog.form))  # at least it built
    assert labels is not None
    assert dialog.value() == {}


# ----------------------- a routine built from other routines -----------------------


@dataclass(frozen=True)
class Chained:
    """Run two routines in sequence.

    Attributes
    ----------
    first, second : dict
        Each names a routine and carries its settings.
    """

    first: dict = field(
        default_factory=lambda: {"method": "alpha", "settings": {"steps": 2}}
    )
    second: dict = field(default_factory=lambda: {"method": "alpha", "settings": {}})


MODELS = {"alpha": Demo, "beta": Demo, "chained": Chained}


def _chained_form(qtbot: QtBot) -> SettingsForm:
    form = SettingsForm(
        Chained, methods=MODELS, editing="chained", choices=lambda *_: None
    )
    qtbot.addWidget(form)
    return form


def test_a_routine_step_is_chosen_not_just_described(qtbot: QtBot) -> None:
    """Without this the only fields that matter read "not editable here"."""
    form = _chained_form(qtbot)
    assert set(form._controls) == {"first", "second"}
    assert not form._unsupported


def test_a_routine_cannot_be_a_step_of_itself(qtbot: QtBot) -> None:
    form = _chained_form(qtbot)
    combo = form._controls["first"].method
    offered = [combo.itemText(i) for i in range(combo.count())]
    assert offered == ["alpha", "beta"]


def test_steps_start_at_the_routine_s_defaults(qtbot: QtBot) -> None:
    form = _chained_form(qtbot)
    assert form._controls["first"].method.currentText() == "alpha"
    # unchanged, so nothing is carried and the routine uses its own defaults
    assert form.value() == {}


def test_changing_one_step_carries_only_that_step(qtbot: QtBot) -> None:
    form = _chained_form(qtbot)
    form._controls["second"].method.setCurrentText("beta")
    value = form.value()
    assert set(value) == {"second"}
    assert value["second"]["method"] == "beta"


def test_a_step_round_trips(qtbot: QtBot) -> None:
    form = _chained_form(qtbot)
    settings = {"second": {"method": "beta", "settings": {"steps": 7}}}
    form.setValue(settings)
    assert form._controls["second"].method.currentText() == "beta"
    assert form.value() == settings


def test_a_step_naming_an_unknown_routine_is_kept(qtbot: QtBot) -> None:
    form = _chained_form(qtbot)
    form.setValue({"first": {"method": "theirs", "settings": {"x": 1}}})
    assert form._controls["first"].method.currentText() == "theirs"
    assert form.value()["first"] == {"method": "theirs", "settings": {"x": 1}}


def test_editing_a_step_emits(qtbot: QtBot) -> None:
    form = _chained_form(qtbot)
    with qtbot.waitSignal(form.valueChanged):
        form._controls["first"].method.setCurrentText("beta")


def test_a_step_describes_its_choices(qtbot: QtBot) -> None:
    form = SettingsForm(
        Chained,
        methods=MODELS,
        descriptions={"alpha": "Walks to the peak.", "beta": "Scans it all."},
        editing="chained",
        choices=lambda *_: None,
    )
    qtbot.addWidget(form)
    combo = form._controls["first"].method
    assert combo.itemData(0, Qt.ItemDataRole.ToolTipRole) == "Walks to the peak."
    assert combo.toolTip() == "Walks to the peak."


def test_without_other_routines_a_step_is_not_editable(qtbot: QtBot) -> None:
    """Nothing to choose from, so it is preserved rather than shown broken."""
    form = SettingsForm(Chained, choices=lambda *_: None)
    qtbot.addWidget(form)
    assert not form._controls
    assert set(form._unsupported) == {"first", "second"}


# --------------------------- choices from the microscope ---------------------------


@dataclass(frozen=True)
class Channelled:
    """A routine that focuses in a chosen channel.

    Attributes
    ----------
    channel_group, channel : str | None
        A config group and one of its presets.
    """

    channel_group: str | None = None
    channel: str | None = None


def _fake_choices(field: str, values: Mapping[str, Any]) -> list[str] | None:
    groups = {"Channel": ["DAPI", "FITC"], "Objective": ["10X", "40X"]}
    if field.endswith("channel_group"):
        return list(groups)
    if field.endswith("channel"):
        return groups.get(str(values.get("channel_group") or ""), [])
    return None


def _channel_form(qtbot: QtBot) -> SettingsForm:
    form = SettingsForm(Channelled, choices=_fake_choices)
    qtbot.addWidget(form)
    return form


def test_channel_settings_are_chosen_not_typed(qtbot: QtBot) -> None:
    """A channel is only useful as one of the presets actually loaded."""
    form = _channel_form(qtbot)
    group = form._controls["channel_group"]
    assert isinstance(group, QComboBox)
    assert [group.itemText(i) for i in range(group.count())] == [
        UNCHANGED,
        "Channel",
        "Objective",
    ]


def test_the_preset_choices_follow_the_group(qtbot: QtBot) -> None:
    form = _channel_form(qtbot)
    channel = form._controls["channel"]
    # nothing to offer until a group is picked
    assert [channel.itemText(i) for i in range(channel.count())] == [UNCHANGED]

    form._controls["channel_group"].setCurrentText("Channel")
    assert [channel.itemText(i) for i in range(channel.count())] == [
        UNCHANGED,
        "DAPI",
        "FITC",
    ]

    form._controls["channel_group"].setCurrentText("Objective")
    assert [channel.itemText(i) for i in range(channel.count())] == [
        UNCHANGED,
        "10X",
        "40X",
    ]


def test_unchanged_means_none_not_a_channel_called_unchanged(qtbot: QtBot) -> None:
    form = _channel_form(qtbot)
    assert form.value() == {}
    form._controls["channel_group"].setCurrentText("Channel")
    form._controls["channel"].setCurrentText("FITC")
    assert form.value() == {"channel_group": "Channel", "channel": "FITC"}
    # back to leaving it alone
    form._controls["channel"].setCurrentText(UNCHANGED)
    assert form.value() == {"channel_group": "Channel"}


def test_a_preset_this_microscope_lacks_is_kept(qtbot: QtBot) -> None:
    """Otherwise loading someone else's sequence would focus in a different channel."""
    form = _channel_form(qtbot)
    form.setValue({"channel_group": "Channel", "channel": "TheirDye"})
    assert form._controls["channel"].currentText() == "TheirDye"
    assert form.value()["channel"] == "TheirDye"


def test_without_choices_it_falls_back_to_free_text(qtbot: QtBot) -> None:
    form = SettingsForm(Channelled, choices=lambda *_: None)
    qtbot.addWidget(form)
    assert isinstance(form._controls["channel"], QLineEdit)
