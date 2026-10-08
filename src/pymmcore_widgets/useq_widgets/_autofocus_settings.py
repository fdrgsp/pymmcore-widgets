"""A settings form built from a software autofocus routine's settings dataclass.

Which routines exist is decided by the acquisition engine, not here, so this builds
a form from whatever dataclass a routine declares.  That way a routine someone
registers themselves gets a form too, without this widget knowing about it.
"""

from __future__ import annotations

import dataclasses
import enum
import types
from collections.abc import Callable, Mapping, Sequence
from typing import (
    Any,
    Literal,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked

from pymmcore_widgets._util import disable_wheel_scroll

__all__ = ["AutofocusSettingsDialog", "SettingsForm"]

UNCHANGED = "unchanged"
_MAX = 1_000_000.0


class _Unset:
    """Marker for a combo entry meaning "leave this setting alone"."""


_UNSET = _Unset()


def _config_choices(field: str, values: Mapping[str, Any]) -> list[str] | None:
    """Offer the microscope's config groups and the chosen group's presets.

    A channel setting is only useful as one of the presets actually loaded, and the
    presets depend on which group is selected -- so the two are offered together and
    the second follows the first.

    Returns `None` for a field this does not handle, which is how a field is marked
    as free text.  An *empty list* still means "choose one of these", so a preset
    field stays a chooser before a group has been picked.
    """
    if not (field.endswith("channel_group") or field.endswith("channel")):
        return None
    try:
        from pymmcore_plus import CMMCorePlus
    except ImportError:  # pragma: no cover
        return None
    core = CMMCorePlus.instance()
    if field.endswith("channel_group"):
        return list(core.getAvailableConfigGroups())
    group = values.get("channel_group") or core.getChannelGroup()
    return list(core.getAvailableConfigs(group)) if group else []


def _is_optional(hint: Any) -> tuple[bool, Any]:
    """Return `(True, inner)` for `X | None`, else `(False, hint)`."""
    # `X | None` is a types.UnionType, `Optional[X]` a typing.Union; which one turns
    # up depends on how the dataclass was written
    if get_origin(hint) in (Union, types.UnionType):
        args = get_args(hint)
        inner = [a for a in args if a is not type(None)]
        if len(inner) == 1 and len(args) == 2:
            return True, inner[0]
    return False, hint


_UNITS = {"um": "\u00b5m", "ms": "ms", "pct": "%", "s": "s"}
_ACRONYMS = {"fft": "FFT", "af": "AF", "roi": "ROI"}


def _humanize(name: str) -> str:
    """`search_range_um` -> `Search range (um)`; `fft_lower_pct` -> `FFT lower (%)`."""
    words = name.split("_")
    unit = _UNITS.get(words[-1]) if len(words) > 1 else None
    if unit:
        words = words[:-1]
    text = " ".join(_ACRONYMS.get(w, w) for w in words)
    if text[:1].islower():
        text = text[0].upper() + text[1:]
    return f"{text} ({unit})" if unit else text


class _StepEditor(QWidget):
    """Pick one routine and edit its settings: one step of a routine built of others.

    Shown for a settings field that names another routine, so something like `duo`
    can be set up here rather than only in code.
    """

    valueChanged = Signal()

    def __init__(
        self,
        methods: Mapping[str, type],
        descriptions: Mapping[str, str] | None = None,
        exclude: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # a routine cannot be a step of itself
        self._methods = {k: v for k, v in methods.items() if k != exclude}
        self._descriptions = dict(descriptions or {})
        self._settings: dict[str, dict[str, Any]] = {}

        self.method = QComboBox()
        self.method.addItems(list(self._methods))
        for i, name in enumerate(self._methods):
            if doc := self._descriptions.get(name):
                self.method.setItemData(i, doc, Qt.ItemDataRole.ToolTipRole)
        self.settings_button = QPushButton("Settings...")
        self.settings_button.setToolTip("Edit this routine's own settings.")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(self.method, 1)
        layout.addWidget(self.settings_button)

        self.method.currentTextChanged.connect(self._on_method_changed)
        self.settings_button.clicked.connect(self._edit)
        self._on_method_changed()

    def _on_method_changed(self) -> None:
        name = self.method.currentText()
        self.method.setToolTip(self._descriptions.get(name, ""))
        self.settings_button.setEnabled(name in self._methods)
        self.valueChanged.emit()

    def _edit(self) -> None:
        name = self.method.currentText()
        if (model := self._methods.get(name)) is None:  # pragma: no cover
            return
        dialog = AutofocusSettingsDialog(
            model,
            self._settings.get(name, {}),
            method=name,
            parent=self,
            methods=self._methods,
            descriptions=self._descriptions,
        )
        if dialog.exec():
            self._settings[name] = dialog.value()
            self.valueChanged.emit()

    def value(self) -> dict[str, Any]:
        """Return `{"method": ..., "settings": {...}}` for the chosen routine."""
        name = self.method.currentText()
        return {"method": name, "settings": dict(self._settings.get(name, {}))}

    def setValue(self, step: Any) -> None:
        """Select the routine `step` names and remember its settings."""
        if not isinstance(step, Mapping) or not (name := step.get("method")):
            return
        name = str(name)
        if self.method.findText(name) < 0:
            # a routine this installation does not have: keep it rather than
            # silently running a different one
            self.method.addItem(name)
        self.method.setCurrentText(name)
        self._settings[name] = dict(step.get("settings") or {})


class SettingsForm(QWidget):
    """Edit the settings of one routine, as a form of plain controls.

    Only the field types a routine realistically uses are supported: numbers,
    booleans, strings, and a fixed set of choices.  A field of any other type is
    shown as a read-only note rather than silently dropped, so its value survives a
    round trip untouched.
    """

    valueChanged = Signal()

    def __init__(
        self,
        model: type | None = None,
        parent: QWidget | None = None,
        *,
        methods: Mapping[str, type] | None = None,
        descriptions: Mapping[str, str] | None = None,
        editing: str = "",
        choices: Callable[[str, Mapping[str, Any]], Sequence[str] | None] | None = None,
    ) -> None:
        super().__init__(parent)
        # what a string setting may be set to, asked for again whenever another
        # setting changes, since one choice can depend on another
        self._choices = choices or _config_choices
        self._dynamic: set[str] = set()
        # the other routines, for a field that names one (see `_StepEditor`)
        self._methods = dict(methods or {})
        self._descriptions = dict(descriptions or {})
        self._editing = editing
        self._model: type | None = None
        self._controls: dict[str, QWidget] = {}
        self._unsupported: dict[str, Any] = {}
        self._layout = QFormLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self.valueChanged.connect(self._refresh_dynamic_choices)
        self.setModel(model)

    def model(self) -> type | None:
        """The settings dataclass this form edits."""
        return self._model

    def setModel(self, model: type | None) -> None:
        """Rebuild the form for `model`'s fields."""
        while self._layout.count():
            if (item := self._layout.takeAt(0)) is None:  # pragma: no cover
                break
            if (w := item.widget()) is not None:
                w.deleteLater()
        self._controls.clear()
        self._unsupported.clear()
        self._dynamic.clear()
        self._model = model
        if model is None:
            return

        hints = get_type_hints(model)
        for field in dataclasses.fields(model):
            control = self._make_control(hints[field.name], field)
            if control is None:
                self._unsupported[field.name] = _default_of(field)
                continue
            if doc := _field_doc(model, field.name):
                control.setToolTip(doc)
            label = QLabel(_humanize(field.name))
            label.setToolTip(control.toolTip())
            self._layout.addRow(label, control)
            self._controls[field.name] = control

        if self._unsupported:
            note = QLabel(
                "Not editable here: " + ", ".join(sorted(self._unsupported)) + "."
            )
            note.setWordWrap(True)
            note.setEnabled(False)
            self._layout.addRow(note)

    def _make_control(self, hint: Any, field: Any) -> QWidget | None:
        optional, inner = _is_optional(hint)
        default = _default_of(field)

        if get_origin(inner) is Literal:
            combo = QComboBox()
            combo.addItems([str(a) for a in get_args(inner)])
            combo.setCurrentText(str(default))
            combo.currentTextChanged.connect(self.valueChanged)
            return combo

        if isinstance(inner, type) and issubclass(inner, enum.Enum):
            combo = QComboBox()
            for member in inner:
                combo.addItem(str(member.value), member.value)
            if default is not None:
                combo.setCurrentText(str(getattr(default, "value", default)))
            combo.currentTextChanged.connect(self.valueChanged)
            return combo

        if inner is bool:
            box = QCheckBox()
            box.setChecked(bool(default))
            box.toggled.connect(self.valueChanged)
            return box

        if inner is int:
            int_spin = QSpinBox()
            int_spin.setRange(0, int(_MAX))
            if optional:
                int_spin.setSpecialValueText(UNCHANGED)
            int_spin.setValue(int(default) if default is not None else 0)
            int_spin.valueChanged.connect(self.valueChanged)
            disable_wheel_scroll(int_spin)
            return int_spin

        if inner is float:
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(0.0, _MAX)
            if optional:
                # 0 is not a meaningful exposure or interval, so it can stand for
                # "leave whatever is already set"
                spin.setSpecialValueText(UNCHANGED)
            spin.setValue(float(default) if default is not None else 0.0)
            spin.valueChanged.connect(self.valueChanged)
            disable_wheel_scroll(spin)
            return spin

        if (inner is dict or get_origin(inner) is dict) and isinstance(
            default, Mapping
        ):
            # a field that names another routine: offer the choice, not a dead end
            if not self._methods:  # pragma: no cover
                return None
            step = _StepEditor(self._methods, self._descriptions, exclude=self._editing)
            step.setValue(default)
            step.valueChanged.connect(self.valueChanged)
            return step

        if inner is str and (offered := self._choices(field.name, {})) is not None:
            options = list(offered)
            combo = QComboBox()
            if optional:
                combo.addItem(UNCHANGED, _UNSET)
            for option in options:
                combo.addItem(option, option)
            if default:
                _select(combo, str(default))
            combo.currentTextChanged.connect(self.valueChanged)
            self._dynamic.add(field.name)
            return combo

        if inner is str:
            line = QLineEdit()
            if optional:
                line.setPlaceholderText(UNCHANGED)
            if default:
                line.setText(str(default))
            line.textChanged.connect(self.valueChanged)
            return line

        return None

    def _refresh_dynamic_choices(self) -> None:
        """Re-offer the choices that depend on another setting (presets on a group)."""
        if not self._dynamic:
            return
        current = self.value()
        for name in self._dynamic:
            combo = self._controls.get(name)
            if not isinstance(combo, QComboBox):  # pragma: no cover
                continue
            options = list(self._choices(name, current) or ())
            existing = [
                combo.itemText(i)
                for i in range(combo.count())
                if combo.itemData(i) is not _UNSET
            ]
            if existing == options:
                continue
            keep = combo.currentText()
            with signals_blocked(combo):
                combo.clear()
                if UNCHANGED not in options:
                    combo.addItem(UNCHANGED, _UNSET)
                for option in options:
                    combo.addItem(option, option)
                _select(combo, keep)

    def value(self) -> dict[str, Any]:
        """Return the settings, leaving out any left at their default.

        Carrying only what was changed means a routine is free to move its defaults
        later without every saved sequence pinning the old ones.
        """
        if self._model is None:
            return {}
        out: dict[str, Any] = {}
        hints = get_type_hints(self._model)
        for field in dataclasses.fields(self._model):
            default = _plain(_default_of(field))
            if (control := self._controls.get(field.name)) is not None:
                optional, _inner = _is_optional(hints[field.name])
                value = _plain(_control_value(control, optional))
            elif field.name in self._unsupported:
                value = self._unsupported[field.name]
            else:  # pragma: no cover
                continue
            if value != default:
                out[field.name] = value
        return out

    def setValue(self, settings: Mapping[str, Any]) -> None:
        """Apply `settings`; fields it does not mention go back to their defaults."""
        if self._model is None:
            return
        for field in dataclasses.fields(self._model):
            value = settings.get(field.name, _default_of(field))
            if (control := self._controls.get(field.name)) is not None:
                _set_control_value(control, value)
            elif field.name in self._unsupported:
                self._unsupported[field.name] = value
        self.valueChanged.emit()


def _select(combo: QComboBox, text: str) -> None:
    """Select `text`, adding it if the microscope does not offer it.

    A setting naming a preset this configuration lacks is kept rather than silently
    replaced by whichever one happens to be first.
    """
    if combo.findText(text) < 0:
        combo.addItem(text, text)
    combo.setCurrentText(text)


def _plain(value: Any) -> Any:
    """Reduce an enum member to its value, so it compares equal to a combo's."""
    return value.value if isinstance(value, enum.Enum) else value


def _default_of(field: Any) -> Any:
    if field.default is not dataclasses.MISSING:
        return field.default
    if field.default_factory is not dataclasses.MISSING:
        return field.default_factory()
    return None


def _field_doc(model: type, name: str) -> str:
    """Pull a field's description out of the numpydoc Attributes section."""
    doc = model.__doc__ or ""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{name} ") and ":" in line:
            body = []
            for follow in lines[i + 1 :]:
                if not follow.strip() or not follow.startswith("        "):
                    break
                body.append(follow.strip())
            return " ".join(body)
    return ""


def _control_value(control: QWidget, optional: bool) -> Any:
    if isinstance(control, _StepEditor):
        return control.value()
    if isinstance(control, QComboBox):
        data = control.currentData()
        if data is _UNSET:  # "leave this alone"
            return None
        return control.currentText() if data is None else data
    if isinstance(control, QCheckBox):
        return control.isChecked()
    if isinstance(control, (QSpinBox, QDoubleSpinBox)):
        value = control.value()
        return None if optional and value == control.minimum() else value
    if isinstance(control, QLineEdit):
        return control.text() or (None if optional else "")
    raise TypeError(f"Unhandled control {type(control)}.")  # pragma: no cover


def _set_control_value(control: QWidget, value: Any) -> None:
    if isinstance(control, _StepEditor):
        control.setValue(value)
    elif isinstance(control, QComboBox):
        if value is None and control.findData(_UNSET) >= 0:
            control.setCurrentIndex(control.findData(_UNSET))
        else:
            _select(control, str(getattr(value, "value", value)))
    elif isinstance(control, QCheckBox):
        control.setChecked(bool(value))
    elif isinstance(control, QSpinBox):
        # the minimum doubles as "unchanged" for an optional field
        control.setValue(int(value) if value is not None else control.minimum())
    elif isinstance(control, QDoubleSpinBox):
        control.setValue(float(value) if value is not None else control.minimum())
    elif isinstance(control, QLineEdit):
        control.setText("" if value is None else str(value))


class AutofocusSettingsDialog(QDialog):
    """A modal editor for one routine's settings."""

    def __init__(
        self,
        model: type,
        settings: Mapping[str, Any] | None = None,
        method: str = "",
        parent: QWidget | None = None,
        *,
        methods: Mapping[str, type] | None = None,
        descriptions: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{method} settings" if method else "Autofocus settings")
        # `methods` lets a routine built from others (such as `duo`) be configured
        # here; `method` is excluded from those choices, so it cannot contain itself.
        self.form = SettingsForm(
            model, methods=methods, descriptions=descriptions, editing=method
        )
        if settings:
            self.form.setValue(settings)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        if doc := _summary(model):
            summary = QLabel(doc)
            summary.setWordWrap(True)
            summary.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(summary)
        layout.addWidget(self.form)
        layout.addWidget(buttons)

    def value(self) -> dict[str, Any]:
        """Return the edited settings."""
        return self.form.value()


def _summary(model: type) -> str:
    """The first paragraph of the model's docstring."""
    lines: list[str] = []
    for line in (model.__doc__ or "").strip().splitlines():
        if not line.strip():
            break
        lines.append(line.strip())
    return " ".join(lines)
