from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import useq
from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon
from superqt.utils import signals_blocked

from ._column_info import FloatColumn, TextColumn, WdgGetSet, WidgetColumn
from ._data_table import DataTableWidget

if TYPE_CHECKING:
    from collections.abc import Sequence

OK_CANCEL = QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
NULL_SEQUENCE = useq.MDASequence()
MAX = 9999999
AF_PER_POS_TOOLTIP = (
    "If checked, the user can set a different Hardware Autofocus Offset for each "
    "Position in the table."
)


class _MDAPopup(QDialog):
    def __init__(
        self,
        value: useq.MDASequence | None = None,
        parent: QWidget | None = None,
    ) -> None:
        from ._mda_sequence import MDATabs

        super().__init__(parent)
        self.setWindowTitle("Grid Plan")

        # Find the enclosing main MDA tab widget, if any, so the grid editor
        # matches its type (e.g. a core-connected grid with live stage bounds).
        main_tabs: MDATabs | None = None
        wdg = self.parent()
        while wdg is not None:
            if isinstance(wdg, MDATabs):
                main_tabs = wdg
                break
            wdg = wdg.parent()

        # Create a new MDA tab widget of the same type as the main one (if
        # any), except the collapsible sections presentation
        # (CollapsibleCoreMDATabs, used by MDAWidgetCollapsible): every axis
        # but the grid is removed below, so its disclosure/expand affordance
        # has nothing left to collapse against. Fall back to its
        # non-collapsible, still core-connected base class instead.
        try:
            from pymmcore_widgets.mda._core_mda import CoreMDATabs
        except ImportError:  # pragma: no cover
            CoreMDATabs = None  # type: ignore[assignment,misc]

        if (
            main_tabs is not None
            and CoreMDATabs is not None
            and isinstance(main_tabs, CoreMDATabs)
        ):
            tab_type: type[MDATabs] = CoreMDATabs
        elif main_tabs is not None:
            tab_type = type(main_tabs)
        else:
            tab_type = MDATabs
        self.mda_tabs = tab_type(self)

        # set the value if provided
        if value:
            self.mda_tabs.setValue(value)

        # A position sub-sequence cannot itself contain another position list,
        # and a grid plan is the only axis a position sub-sequence currently
        # supports (e.g. required for OME file writers), so remove every other
        # axis and leave only the grid editor. Do this after restoring the
        # value so an incoming sequence cannot re-enable them.
        for axis_widget in (
            self.mda_tabs.stage_positions,
            self.mda_tabs.channels,
            self.mda_tabs.z_plan,
            self.mda_tabs.time_plan,
        ):
            self.mda_tabs.removeTab(self.mda_tabs.indexOf(axis_widget))
        # Leave the grid checkbox as restored by setValue above: unchecked
        # (and the editor disabled) unless the incoming value already had a
        # grid plan, matching how every other axis checkbox behaves.

        # Bring the grid editor into view -- it's the only thing left to edit.
        self.mda_tabs.setCurrentIndex(self.mda_tabs.indexOf(self.mda_tabs.grid_plan))

        # create ok and cancel buttons
        self._btns = QDialogButtonBox(OK_CANCEL)
        self._btns.accepted.connect(self.accept)
        self._btns.rejected.connect(self.reject)

        # create layout
        layout = QVBoxLayout(self)
        layout.addWidget(self.mda_tabs)
        layout.addWidget(self._btns)

        self.resize(600, 350)


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
        self.seq_btn.setIcon(QIconifyIcon("mdi:grid"))
        self.seq_btn.setToolTip("Set a Grid Plan for this position")

        self.clear_btn = QPushButton()
        self.clear_btn.setIcon(QIconifyIcon("mdi:close-circle", color="red"))
        # Fixed width keeps it narrower than seq_btn (a secondary action next to
        # the primary one), but match its vertical policy so it grows to the
        # same height instead of shrinking to the button's small default size.
        self.clear_btn.setFixedWidth(24)
        self.clear_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
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

    def setValue(self, value: useq.MDASequence | dict | None) -> None:
        if isinstance(value, dict):
            value = useq.MDASequence(**value)
        elif value and not isinstance(value, useq.MDASequence):  # pragma: no cover
            raise TypeError(f"Expected useq.MDASequence, got {type(value)}")
        old_val, self._value = getattr(self, "_value", None), value
        if old_val != value:
            # if sub-sequence is equal to the null sequence (useq.MDASequence())
            # treat it as None
            if value and value != NULL_SEQUENCE:
                self.seq_btn.setIcon(QIconifyIcon("mdi:grid", color="green"))
                self.clear_btn.show()
            else:
                self.seq_btn.setIcon(QIconifyIcon("mdi:grid"))
                self.clear_btn.hide()
            self.valueChanged.emit()


_MDAButton = WdgGetSet(
    MDAButton,
    MDAButton.value,
    MDAButton.setValue,
    lambda w, cb: w.valueChanged.connect(cb),
)


@dataclass(frozen=True)
class SubSeqColumn(WidgetColumn):
    """Column for editing a position's grid-plan sub-sequence."""

    data_type: WdgGetSet = _MDAButton


class PositionTable(DataTableWidget):
    """Table to edit a list of [useq.Position](https://pymmcore-plus.github.io/useq-schema/schema/axes/#useq.Position)."""

    NAME = TextColumn(key="name", default=None, is_row_selector=True)
    X = FloatColumn(key="x", header="X [µm]", default=0.0, maximum=MAX, minimum=-MAX)
    Y = FloatColumn(key="y", header="Y [µm]", default=0.0, maximum=MAX, minimum=-MAX)
    Z = FloatColumn(key="z", header="Z [µm]", default=0.0, maximum=MAX, minimum=-MAX)
    AF = FloatColumn(key="af", header="AF", default=0.0, maximum=MAX, minimum=-MAX)
    SEQ = SubSeqColumn(key="sequence", header="Grid", default=None)

    def __init__(self, rows: int = 0, parent: QWidget | None = None):
        super().__init__(rows, parent)

        # track whether a global absolute grid disables all x/y
        self._global_xy_disabled = False

        # when a sub-sequence changes, update x/y enabled state for that row
        if model := self.table().model():
            model.rowsInserted.connect(self._on_table_rows_inserted)
            if (rows := self.table().rowCount()) > 0:
                self._on_table_rows_inserted(None, 0, rows - 1)

        self.include_z = QCheckBox("Include Z")
        self.include_z.setChecked(True)
        self.include_z.toggled.connect(self._on_include_z_toggled)

        self.af_per_position = QCheckBox("Set AF Offset per Position")
        self.af_per_position.setToolTip(AF_PER_POS_TOOLTIP)
        self.af_per_position.toggled.connect(self._on_af_per_position_toggled)
        self._on_af_per_position_toggled(self.af_per_position.isChecked())

        self._save_button = QPushButton("Save...")
        self._save_button.clicked.connect(self.save)
        self._load_button = QPushButton("Load...")
        self._load_button.clicked.connect(self.load)

        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(15)
        self._btn_row.addWidget(self.include_z)
        self._btn_row.addWidget(self.af_per_position)
        self._btn_row.addStretch()
        self._btn_row.addWidget(self._save_button)
        self._btn_row.addWidget(self._load_button)

        layout = cast("QVBoxLayout", self.layout())
        layout.addLayout(self._btn_row)

    # ------------------------- Public API -------------------------

    def value(
        self, exclude_unchecked: bool = True, exclude_hidden_cols: bool = True
    ) -> Sequence[useq.Position]:
        """Return the current value of the table as a tuple of [useq.Position](https://pymmcore-plus.github.io/useq-schema/schema/axes/#useq.Position).

        Note that `exclude_hidden_cols` has the result of:
            - excluding the Z position in each of the Positions if
              `include_z.isChecked()` is False
            - excluding the AF offset in each of the Positions if
              `af_per_position.isChecked()` is False

        Parameters
        ----------
        exclude_unchecked : bool, optional
            Exclude unchecked rows, by default True
        exclude_hidden_cols : bool, optional
            Exclude hidden columns, by default True

        Returns
        -------
        tuple[useq.Position, ...]
            A tuple of [useq.Position](https://pymmcore-plus.github.io/useq-schema/schema/axes/#useq.Position).
        """
        out: list[useq.Position] = []
        for r in self.table().iterRecords(
            exclude_unchecked=exclude_unchecked, exclude_hidden_cols=exclude_hidden_cols
        ):
            if not r.get(self.NAME.key, True):
                r.pop(self.NAME.key, None)

            if self.af_per_position.isChecked() and self.af_per_position.isEnabled():
                af_offset = r.get(self.AF.key, None)
                if af_offset is not None:
                    # get the current sub-sequence as dict or create a new one
                    sub_seq = r.get("sequence")
                    sub_seq = (
                        sub_seq.model_dump()
                        if isinstance(sub_seq, useq.MDASequence)
                        else {}
                    )
                    # add the autofocus plan to the sub-sequence
                    sub_seq["autofocus_plan"] = useq.AxesBasedAF(
                        autofocus_motor_offset=af_offset, axes=("p",)
                    )
                    # update the sub-sequence dict in the record
                    r["sequence"] = sub_seq

            # If a sub-sequence uses an absolute grid plan, x/y on the
            # position are meaningless (the grid defines them). Clear them
            # to avoid useq validation warnings.
            sub = r.get("sequence")
            if isinstance(sub, useq.MDASequence) and sub.grid_plan is not None:
                if not sub.grid_plan.is_relative:
                    r.pop("x", None)
                    r.pop("y", None)

            pos = useq.Position(**r)
            out.append(pos)

        return tuple(out)

    def setValue(self, value: Sequence[useq.Position]) -> None:  # type: ignore [override]
        """Set the current value of the table from a Sequence of [useq.Position](https://pymmcore-plus.github.io/useq-schema/schema/axes/#useq.Position).

        Parameters
        ----------
        value : Sequence[useq.Position]
            A Sequence of [useq.Position](https://pymmcore-plus.github.io/useq-schema/schema/axes/#useq.Position).
        """
        _values = []
        _use_af = False
        value = [useq.Position.model_validate(v) for v in value]

        n_pos_with_z = sum(1 for v in value if v.z is not None)
        if (_include_z := n_pos_with_z > 0) and n_pos_with_z < len(value):
            warnings.warn(
                "Only some positions have a z-position set. Z will be included, "
                "but missing z-positions will be set to 0.",
                stacklevel=2,
            )

        for v in value:
            _af = {}
            if v.sequence is not None and v.sequence.autofocus_plan is not None:
                # set sub-sequence to None if empty or we simply exclude the af plan
                sub_seq: useq.MDASequence | None = useq.MDASequence(
                    **v.sequence.model_dump(exclude={"autofocus_plan"})
                )
                if sub_seq == NULL_SEQUENCE:
                    sub_seq = None

                # get autofocus plan device name and offset
                _af_offset = v.sequence.autofocus_plan.autofocus_motor_offset

                # set the autofocus offset that will be added to the table
                _af = {self.AF.key: _af_offset}

                # remopve autofocus plan from sub-sequence
                v = v.replace(sequence=sub_seq)

                _use_af = True

            _values.append({**v.model_dump(exclude_unset=True), **_af})

        super().setValue(_values)
        with signals_blocked(self):
            self.include_z.setChecked(_include_z)
            self.af_per_position.setChecked(_use_af)
        self.valueChanged.emit()

    def save(self, file: str | Path | None = None) -> None:
        """Save the current positions to a JSON file."""
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
        """Load positions from a JSON file and set the table value."""
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

    def setXYEnabled(self, enabled: bool) -> None:
        """Disable or enable X/Y columns for all rows (e.g. global absolute grid)."""
        self._global_xy_disabled = not enabled
        table = self.table()
        seq_col = table.indexOf(self.SEQ)
        tip = "X/Y defined by the global absolute grid plan." if not enabled else ""
        for row in range(table.rowCount()):
            # skip rows that have their own absolute sub-sequence grid
            if enabled:
                wdg = table.cellWidget(row, seq_col)
                if isinstance(wdg, MDAButton) and _seq_has_absolute_grid(wdg.value()):
                    continue
            self._set_row_xy_enabled(row, enabled, tip)

    # ------------------- sub-sequence grid helpers -------------------

    def _on_table_rows_inserted(self, parent: object, start: int, end: int) -> None:
        """Connect MDAButton.valueChanged for newly inserted rows."""
        table = self.table()
        seq_col = table.indexOf(self.SEQ)
        tip = "X/Y defined by the global absolute grid plan."
        for row in range(start, end + 1):
            wdg = table.cellWidget(row, seq_col)
            if isinstance(wdg, MDAButton):
                wdg.valueChanged.connect(self._on_sub_seq_changed)
            # apply global disable to newly added rows
            if self._global_xy_disabled:
                self._set_row_xy_enabled(row, False, tip)

    def _on_sub_seq_changed(self) -> None:
        """Disable x/y for the row if its sub-sequence has an absolute grid."""
        btn = self.sender()
        if not isinstance(btn, MDAButton):
            return
        table = self.table()
        seq_col = table.indexOf(self.SEQ)
        for row in range(table.rowCount()):
            if table.cellWidget(row, seq_col) is btn:
                has_abs = _seq_has_absolute_grid(btn.value())
                # global disable takes precedence
                if self._global_xy_disabled and not has_abs:
                    return
                tip = (
                    "X/Y defined by the absolute grid in the sub-sequence."
                    if has_abs
                    else ""
                )
                self._set_row_xy_enabled(row, not has_abs, tip)
                break

    def _set_row_xy_enabled(self, row: int, enabled: bool, tip: str = "") -> None:
        """Enable/disable x/y widgets for a single row."""
        table = self.table()
        for col_info in (self.X, self.Y):
            if wdg := table.cellWidget(row, table.indexOf(col_info)):
                wdg.setEnabled(enabled)
                wdg.setToolTip(tip)

    # ------------------------- Private API -------------------------

    def _on_include_z_toggled(self, checked: bool) -> None:
        z_col = self.table().indexOf(self.Z)
        self.table().setColumnHidden(z_col, not checked)
        self.valueChanged.emit()

    def _on_af_per_position_toggled(self, checked: bool) -> None:
        af_col = self.table().indexOf(self.AF)
        self.table().setColumnHidden(af_col, not checked)
        self.valueChanged.emit()


def _seq_has_absolute_grid(seq: useq.MDASequence | None) -> bool:
    """Return True if the sequence has an absolute grid plan."""
    return bool(seq and seq.grid_plan and not seq.grid_plan.is_relative)
