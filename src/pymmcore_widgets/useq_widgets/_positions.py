from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, cast

import useq
from fonticon_mdi6 import MDI6
from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from superqt.fonticon import icon

from ._column_info import FloatColumn, TextColumn, WdgGetSet, WidgetColumn
from ._data_table import DataTableWidget

OK_CANCEL = QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel


class _MDAPopup(QDialog):
    def __init__(
        self, value: useq.MDASequence | None = None, parent: QWidget | None = None
    ) -> None:
        from ._mda_sequence import MDATabs

        super().__init__(parent)

        # create a new MDA tab widget without the stage positions tab
        self.mda_tabs = MDATabs(self)
        self.mda_tabs.removeTab(self.mda_tabs.indexOf(self.mda_tabs.stage_positions))

        # use the parent's channel groups if possible
        par = self.parent()
        while par:
            if isinstance(par, MDATabs):
                self.mda_tabs.channels.setChannelGroups(par.channels.channelGroups())
                break
            par = par.parent()

        # set the value if provided
        if value:
            self.mda_tabs.setValue(value)

        # create ok and cancel buttons
        self._btns = QDialogButtonBox(OK_CANCEL)
        self._btns.accepted.connect(self.accept)
        self._btns.rejected.connect(self.reject)

        # create layout
        layout = QVBoxLayout(self)
        layout.addWidget(self.mda_tabs)
        layout.addWidget(self._btns)


class MDAButton(QWidget):
    valueChanged = Signal()
    _value: useq.MDASequence | None

    def __init__(self) -> None:
        super().__init__()
        self.seq_btn = QPushButton()
        self.seq_btn.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self.seq_btn.clicked.connect(self._on_click)
        self.seq_btn.setIcon(icon(MDI6.axis))

        self.clear_btn = QPushButton()
        self.clear_btn.setIcon(icon(MDI6.close_circle, color="red"))
        self.clear_btn.setFixedWidth(20)
        self.clear_btn.hide()
        self.clear_btn.clicked.connect(lambda: self.setValue(None))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        layout.addWidget(self.seq_btn)
        layout.addWidget(self.clear_btn)

        self.setValue(None)

    def _on_click(self) -> None:
        dialog = _MDAPopup(self._value, self)
        if dialog.exec():
            self.setValue(dialog.mda_tabs.value())

    def value(self) -> useq.MDASequence | None:
        return self._value

    def setValue(self, value: useq.MDASequence | None) -> None:
        old_val, self._value = getattr(self, "_value", None), value
        if old_val != value:
            if value is not None:
                self.seq_btn.setIcon(icon(MDI6.axis_arrow, color="green"))
            else:
                self.seq_btn.setIcon(icon(MDI6.axis))
            self.valueChanged.emit()
            if value is None:
                self.clear_btn.hide()
            else:
                self.clear_btn.show()


_MDAButton = WdgGetSet(
    MDAButton,
    MDAButton.value,
    MDAButton.setValue,
    lambda w, cb: w.valueChanged.connect(cb),
)


@dataclass(frozen=True)
class SubSeqColumn(WidgetColumn):
    """Column for editing a `useq.MDASequence`."""

    data_type: WdgGetSet = _MDAButton


class PositionTable(DataTableWidget):
    """Table for editing a list of `useq.Positions`."""

    NAME = TextColumn(key="name", default=None, is_row_selector=True)
    X = FloatColumn(key="x", header="X [mm]", default=0.0)
    Y = FloatColumn(key="y", header="Y [mm]", default=0.0)
    Z = FloatColumn(key="z", header="Z [mm]", default=0.0)
    SEQ = SubSeqColumn(key="sequence", header="Sub-Sequence", default=None)

    def __init__(self, rows: int = 0, parent: QWidget | None = None):
        super().__init__(rows, parent)

        layout = cast("QVBoxLayout", self.layout())

        self._save_button = QPushButton("Save...")
        self._save_button.clicked.connect(self.save)
        self._load_button = QPushButton("Load...")
        self._load_button.clicked.connect(self.load)
        # self._custom_button = QPushButton("Custom...")
        # self._custom_button.clicked.connect(self._custom)
        # self._optimize_button = QPushButton("Optimize...")
        # self._optimize_button.clicked.connect(self._optimize)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._save_button)
        btn_row.addWidget(self._load_button)
        # btn_row.addWidget(self._custom_button)
        # btn_row.addWidget(self._optimize_button)

        layout.addLayout(btn_row)

    def value(self, exclude_unchecked: bool = True) -> tuple[useq.Position, ...]:
        """Return the current value of the table as a list of channels."""
        out = []
        for r in self.table().iterRecords(exclude_unchecked=exclude_unchecked):
            if not r.get("name", True):
                r.pop("name", None)
            out.append(useq.Position(**r))
        return tuple(out)

    def setValue(self, value: Sequence[useq.Position]) -> None:  # type: ignore
        """Set the current value of the table."""
        _values = []
        for v in value:
            if not isinstance(v, useq.Position):  # pragma: no cover
                raise TypeError(f"Expected useq.Position, got {type(v)}")
            _values.append(v.model_dump(exclude_unset=True))
        super().setValue(_values)

    def save(self, file: str | Path | None = None) -> None:
        """Save the current positions to a file."""
        if not isinstance(file, (str, Path)):
            file, _ = QFileDialog.getSaveFileName(
                self, "Save MDASequence and filename.", "", "json(*.json)"
            )
            if not file:
                return  # pragma: no cover

        dest = Path(file)
        if not dest.suffix:
            dest = dest.with_suffix(".json")

        if dest.suffix != ".json":  # pragma: no cover
            raise ValueError(f"Invalid file extension: {dest.suffix!r}, expected .json")

        # doing it this way because model_json_dump knows how to serialize everything.
        inner = ",\n".join([x.model_dump_json() for x in self.value()])
        dest.write_text(f"[\n{inner}\n]\n")

    def load(self, file: str | Path | None = None) -> None:
        """Load positions from a file."""
        if not isinstance(file, (str, Path)):
            file, _ = QFileDialog.getOpenFileName(
                self, "Select an MDAsequence file.", "", "json(*.json)"
            )
            if not file:
                return  # pragma: no cover

        src = Path(file)
        if not src.is_file():  # pragma: no cover
            raise FileNotFoundError(f"File not found: {src}")

        try:
            data = json.loads(src.read_text())
            self.setValue([useq.Position(**d) for d in data])
        except Exception as e:  # pragma: no cover
            raise ValueError(f"Failed to load MDASequence file: {src}") from e

    # def _custom(self) -> None:
    #     """Customize positions."""
    #     print("Not Implemented")

    # def _optimize(self) -> None:
    #     """Optimize positions."""
    #     print("Not Implemented")