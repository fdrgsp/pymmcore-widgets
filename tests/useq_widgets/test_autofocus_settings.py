"""The settings form built from a routine's settings dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal

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
    form = SettingsForm(Demo)
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
