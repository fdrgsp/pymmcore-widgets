"""A settings form built from a software autofocus routine's settings dataclass.

Which routines exist is decided by the acquisition engine, not here, so this builds
a form from whatever dataclass a routine declares.  That way a routine someone
registers themselves gets a form too, without this widget knowing about it.
"""

from __future__ import annotations

import dataclasses
import enum
import types
from typing import (
    TYPE_CHECKING,
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
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pymmcore_widgets._util import disable_wheel_scroll

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["AutofocusSettingsDialog", "SettingsForm"]

UNCHANGED = "unchanged"
_MAX = 1_000_000.0


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


class SettingsForm(QWidget):
    """Edit the settings of one routine, as a form of plain controls.

    Only the field types a routine realistically uses are supported: numbers,
    booleans, strings, and a fixed set of choices.  A field of any other type is
    shown as a read-only note rather than silently dropped, so its value survives a
    round trip untouched.
    """

    valueChanged = Signal()

    def __init__(
        self, model: type | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._model: type | None = None
        self._controls: dict[str, QWidget] = {}
        self._unsupported: dict[str, Any] = {}
        self._layout = QFormLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
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

        if inner is str:
            line = QLineEdit()
            if optional:
                line.setPlaceholderText(UNCHANGED)
            if default:
                line.setText(str(default))
            line.textChanged.connect(self.valueChanged)
            return line

        return None

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
    if isinstance(control, QComboBox):
        return control.currentData() or control.currentText()
    if isinstance(control, QCheckBox):
        return control.isChecked()
    if isinstance(control, (QSpinBox, QDoubleSpinBox)):
        value = control.value()
        return None if optional and value == control.minimum() else value
    if isinstance(control, QLineEdit):
        return control.text() or (None if optional else "")
    raise TypeError(f"Unhandled control {type(control)}.")  # pragma: no cover


def _set_control_value(control: QWidget, value: Any) -> None:
    if isinstance(control, QComboBox):
        control.setCurrentText(str(getattr(value, "value", value)))
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
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{method} settings" if method else "Autofocus settings")
        self.form = SettingsForm(model)
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
