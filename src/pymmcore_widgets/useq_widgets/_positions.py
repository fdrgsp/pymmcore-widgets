from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import useq
from qtpy.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon
from superqt.utils import signals_blocked

from pymmcore_widgets._util import GREEN, RED, use_combo_row_height
from pymmcore_widgets._z_surface import METHODS, SurfaceStage, ZSurface

from ._column_info import ColumnInfo, FloatColumn, TextColumn
from ._data_table import DataTableWidget
from ._record_model import ColumnDelegate, RecordTableModel, RecordTableView, _column

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    from qtpy.QtGui import QIcon, QShowEvent

    from ._record_model import Button, Index, Record
    from ._z_surface_dialog import ZSurfaceDialog

OK_CANCEL = QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
NULL_SEQUENCE = useq.MDASequence()
MAX = 9999999
CLEAR_BTN_SPACING = 4
CLEAR_BTN_WIDTH = 24
GRID_BTN_TOOLTIP = "Set a Grid Plan for this position"
SUB_SEQ_GRID_TOOLTIP = "X/Y defined by the absolute grid in the sub-sequence."
GLOBAL_GRID_TOOLTIP = "X/Y defined by the global absolute grid plan."
AF_PER_POS_TOOLTIP = (
    "If checked, the user can set a different Hardware Autofocus Offset for each "
    "Position in the table."
)
SURFACE_TOOLTIP = (
    "Take each position's Z (and each grid tile's) from a surface through manually "
    "focused reference points.\nSet the points up with the settings button."
)
SURFACE_SETTINGS_TOOLTIP = "Set up the focus surface."
SURFACE_INCLUDE_Z_TOOLTIP = "Cannot use while 'Include Z' is checked."
SURFACE_HAF_TOOLTIP = "Cannot use while 'HAF Offset' is checked."
SURFACE_CHECKED_TOOLTIP = "Cannot use while 'Surface Focus' is checked."


class _MDAPopup(QDialog):
    def __init__(
        self,
        value: useq.MDASequence | None = None,
        parent: QWidget | None = None,
    ) -> None:
        from pymmcore_widgets.mda._core_tabs import CoreMDATabs

        from ._mda_sequence import MDATabs

        super().__init__(parent)
        self.setWindowTitle("Grid Plan")

        # make the same type of MDA tab widget that we are currently inside of (if
        # possible), but a core-connected one may be a subclass with a different
        # presentation (e.g. CollapsibleCoreMDATabs): every axis but the grid is
        # removed below, so there is nothing left for it to organize.
        tab_type: type[MDATabs] = MDATabs
        wdg = self.parent()
        while wdg is not None:
            if isinstance(wdg, MDATabs):
                tab_type = CoreMDATabs if isinstance(wdg, CoreMDATabs) else type(wdg)
                break
            wdg = wdg.parent()
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
        self._fitted = False

    def showEvent(self, event: QShowEvent | None) -> None:
        super().showEvent(event)
        # Fit the editor on the first show: only then does it report its full
        # size (e.g. with the Polygon option a polygon plan adds).
        if not self._fitted:
            self._fitted = True
            self.resize(self.size().expandedTo(self._fitting_size()))

    def _fitting_size(self) -> QSize:
        """Return a size that shows the whole grid editor, e.g. its Polygon option.

        The editor sits in a scroll area, whose size hint Qt caps (and its
        horizontal scroll bar is off), so the dialog's own size hint can be
        too small for it. Add back what the cap cut off.
        """
        hint = self.sizeHint()
        page = self.mda_tabs._page_for.get(self.mda_tabs.grid_plan)
        scroll = page.findChild(QScrollArea) if page is not None else None
        if scroll is None or (content := scroll.widget()) is None:
            return hint  # pragma: no cover
        frame = 2 * scroll.frameWidth()
        cut = content.sizeHint() + QSize(frame, frame) - scroll.sizeHint()
        return hint + QSize(max(0, cut.width()), max(0, cut.height()))


@dataclass(frozen=True)
class SubSeqColumn(ColumnInfo):
    """Column of a position's grid-plan sub-sequence, edited in a pop-up."""

    data_type: type = useq.MDASequence


def _has_sub_sequence(seq: useq.MDASequence | None) -> bool:
    # an empty sub-sequence (useq.MDASequence()) is treated as none
    return bool(seq and seq != NULL_SEQUENCE)


class PositionTableModel(RecordTableModel):
    """The positions: an absolute grid plan, of a row or global, sets X/Y."""

    def __init__(self, columns: Sequence[ColumnInfo], rows: int = 0) -> None:
        super().__init__(columns, rows)
        self._xy_locked = False
        # the columns that an absolute grid plan disables
        self.xy_keys = {PositionTable.X.key, PositionTable.Y.key}

    def setXYLocked(self, locked: bool) -> None:
        """Disable X/Y in every row, as a global absolute grid plan sets them."""
        self._xy_locked = locked
        self._all_changed()

    def value(self, row: int, column: ColumnInfo) -> Any:
        # an absolute grid plan's first FOV, purely for display: PositionTable.value()
        # drops x/y (useq.AbsolutePosition rejects them alongside an absolute grid)
        if column.key in (PositionTable.X.key, PositionTable.Y.key) and (
            xy := _grid_first_point(self._rows[row].get(PositionTable.SEQ.key))
        ):
            return xy[column.key == PositionTable.Y.key]
        return super().value(row, column)

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.ToolTipRole:
            if tip := self._xy_tooltip(index):
                return tip
            if self._columns[index.column()].key == PositionTable.SEQ.key:
                return GRID_BTN_TOOLTIP
        return super().data(index, role)

    def flags(self, index: Index) -> Qt.ItemFlag:
        flags = super().flags(index)
        if self._xy_tooltip(index):
            flags &= ~Qt.ItemFlag.ItemIsEnabled
        return flags

    def _xy_tooltip(self, index: Index) -> str:
        """Return why a grid plan sets the X/Y at `index`, or `""`."""
        if self._columns[index.column()].key not in self.xy_keys:
            return ""
        if _seq_has_absolute_grid(self._rows[index.row()].get(PositionTable.SEQ.key)):
            return SUB_SEQ_GRID_TOOLTIP
        return GLOBAL_GRID_TOOLTIP if self._xy_locked else ""


class PositionDelegate(ColumnDelegate):
    """Also paint the Grid column: a grid plan button and, once set, a clear one."""

    # the row whose grid plan button was clicked
    editRequested = Signal(int)

    def __init__(self, view: RecordTableView) -> None:
        super().__init__(view)
        self.setIcons(
            QIconifyIcon("mdi:grid"),
            QIconifyIcon("mdi:grid", color="green"),
            QIconifyIcon("mdi:close-circle", color="red"),
        )

    def setIcons(self, empty: QIcon, configured: QIcon, clear: QIcon) -> None:
        """Set the icons of the grid plan button, without and with a plan, and clear."""
        self._icons = (empty, configured, clear)
        self._view.viewport().update()

    def fixedWidth(self, column: ColumnInfo) -> int | None:
        if not isinstance(column, SubSeqColumn):
            return super().fixedWidth(column)
        # widen for the clear button only while some row shows it
        model = self._view.model()
        rows = range(model.rowCount())
        if any(_has_sub_sequence(model.value(row, column)) for row in rows):
            return self._view.buttonWidth() + CLEAR_BTN_SPACING + CLEAR_BTN_WIDTH
        return self._view.buttonWidth()

    def buttons(self, index: QModelIndex, rect: QRect) -> list[Button]:
        if not isinstance(_column(index), SubSeqColumn):
            return super().buttons(index, rect)
        # the grid line takes a pixel from each cell, as from a ButtonColumn's
        width = self._view.buttonWidth() - int(self._view.showGrid())
        grid = QRect(rect.x(), rect.y(), width, rect.height())
        empty, configured, clear_icon = self._icons
        if not _has_sub_sequence(index.data(Qt.ItemDataRole.EditRole)):
            return [(grid, empty)]
        x = grid.right() + 1 + CLEAR_BTN_SPACING
        clear = QRect(x, rect.y(), CLEAR_BTN_WIDTH, rect.height())
        return [(grid, configured), (clear, clear_icon)]

    def buttonClicked(self, index: QModelIndex, button: int) -> None:
        if not isinstance(_column(index), SubSeqColumn):
            super().buttonClicked(index, button)
        elif button:
            index.model().setData(index, None)
        else:
            self.editRequested.emit(index.row())


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
        use_combo_row_height(self._table)

        # the open grid plan pop-ups, with the index of their row's Grid cell
        self._popups: dict[_MDAPopup, QPersistentModelIndex] = {}
        model = self.table().model()
        model.rowsRemoved.connect(self._close_orphan_popups)
        model.modelReset.connect(self._close_orphan_popups)
        self.delegate().editRequested.connect(self.editSubSequence)

        self.include_z = QCheckBox("Include Z")
        self.include_z.setChecked(True)
        self.include_z.toggled.connect(self._on_include_z_toggled)

        self.af_per_position = QCheckBox("HAF Offset")
        self.af_per_position.setToolTip(AF_PER_POS_TOOLTIP)
        self.af_per_position.toggled.connect(self._on_af_per_position_toggled)
        self._on_af_per_position_toggled(self.af_per_position.isChecked())

        # the button beside it is its label: a click on it opens the setup
        self.surface_focus = QCheckBox()
        self.surface_focus.setAccessibleName("Surface Focus")
        self.surface_focus.toggled.connect(self.valueChanged)
        self.surface_focus.clicked.connect(self._on_surface_focus_clicked)
        self._surface_settings = QPushButton("Surface Focus")
        # the gear tells whether a setup started can be used
        self._gears = {
            "": QIconifyIcon("tabler:settings"),
            "valid": QIconifyIcon("tabler:settings-check", color=GREEN),
            "error": QIconifyIcon("tabler:settings-x", color=RED),
        }
        # the gear on the right
        self._surface_settings.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._surface_settings.clicked.connect(self._edit_surface)
        self._surface = ZSurface()
        self._surface_dialog: ZSurfaceDialog | None = None
        self._check_surface_on_ok = False
        # why the owning MDA widget's settings rule Surface Focus out, if they do
        self._surface_conflict = ""
        self.include_z.toggled.connect(self._update_surface_focus)
        self.af_per_position.toggled.connect(self._update_surface_focus)
        self.surface_focus.toggled.connect(self._update_include_z)

        self._save_button = QPushButton("Save")
        self._save_button.setIcon(QIconifyIcon("mdi:content-save-outline"))
        self._save_button.setToolTip("Save positions")
        self._save_button.clicked.connect(self.save)
        self._load_button = QPushButton("Load")
        self._load_button.setIcon(QIconifyIcon("material-symbols:upload-rounded"))
        self._load_button.setToolTip("Load positions")
        self._load_button.clicked.connect(self.load)

        file_buttons = QHBoxLayout()
        file_buttons.setSpacing(5)
        file_buttons.addWidget(self._save_button)
        file_buttons.addWidget(self._load_button)

        self._position_actions = QHBoxLayout()
        self._position_actions.setSpacing(10)
        self._position_actions.addLayout(file_buttons)

        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(15)
        self._btn_row.addWidget(self.include_z)
        self._btn_row.addWidget(self.af_per_position)
        surface_row = QHBoxLayout()
        surface_row.setSpacing(5)
        surface_row.addWidget(self.surface_focus)
        surface_row.addWidget(self._surface_settings)
        self._btn_row.addLayout(surface_row)
        self._btn_row.addStretch()
        self._btn_row.addLayout(self._position_actions)

        layout = cast("QVBoxLayout", self.layout())
        layout.addLayout(self._btn_row)
        self._update_surface_focus()

    # ------------------------- Public API -------------------------

    def table(self) -> RecordTableView:
        return cast("RecordTableView", self._table)

    def delegate(self) -> PositionDelegate:
        """Return the delegate that paints the table, e.g. to `setIcons`."""
        return cast("PositionDelegate", self._table.itemDelegate())

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
            # only a hardware autofocus plan carries a per-position motor offset
            if v.sequence is not None and isinstance(
                v.sequence.autofocus_plan, useq.AxesBasedAF
            ):
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

            # the sub-sequence as is: model_dump would make it a dict
            seq = v.sequence
            _values.append({**v.model_dump(exclude_unset=True), **_af, "sequence": seq})

        self._set_records(_values, _include_z, _use_af)

    def editSubSequence(self, row: int) -> None:
        """Open the grid plan editor of the position at `row` (or raise it)."""
        table = self.table()
        index = QPersistentModelIndex(table.model().index(row, table.indexOf(self.SEQ)))
        popup = next((p for p, i in self._popups.items() if i == index), None)
        if popup is None:
            # not modal: the app stays usable, e.g. to set grid bounds in live mode
            popup = _MDAPopup(index.data(Qt.ItemDataRole.EditRole), table)
            popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self._popups[popup] = index
            # bound methods, not closures holding the popup: those keep it (and
            # its children) alive after it is deleted, and crash PySide6
            popup.accepted.connect(self._on_popup_accepted)
            popup.finished.connect(self._on_popup_finished)
        popup.show()
        popup.raise_()
        popup.activateWindow()

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

    def surface(self) -> ZSurface:
        """Return the focus surface used when Surface Focus is checked."""
        return self._surface

    def setSurface(self, surface: ZSurface) -> None:
        """Set the focus surface used when Surface Focus is checked."""
        self._surface = surface
        if self._surface_dialog is not None:
            self._surface_dialog.setValue(surface)
        self._update_surface_focus()
        self.valueChanged.emit()

    def setSurfaceFocusConflict(self, reason: str) -> None:
        """Rule Surface Focus out for `reason` (e.g. hardware autofocus), or `""`."""
        self._surface_conflict = reason
        self._update_surface_focus()

    def setXYEnabled(self, enabled: bool) -> None:
        """Disable or enable X/Y columns for all rows (e.g. global absolute grid)."""
        cast("PositionTableModel", self.table().model()).setXYLocked(not enabled)

    # ------------------------- Private API -------------------------

    def _create_table(self, rows: int) -> RecordTableView:
        model = PositionTableModel(self.COLUMNS, rows)
        return RecordTableView(model, self, PositionDelegate)

    def _set_records(
        self, records: Sequence[Record], include_z: bool, use_af: bool
    ) -> None:
        super().setValue(records)
        with signals_blocked(self):
            # with Surface Focus, the Z is kept but not used
            self.include_z.setChecked(include_z and not self.surface_focus.isChecked())
            self.af_per_position.setChecked(use_af)
        self.valueChanged.emit()

    def _swap_rows(self, row: int, other: int) -> None:
        # moving keeps all of the row, e.g. its check state
        self.table().model().moveRow(
            QModelIndex(), max(row, other), QModelIndex(), min(row, other)
        )

    def _on_popup_accepted(self) -> None:
        popup = cast("_MDAPopup", self.sender())
        # ignored if the row is gone
        self.table().model().setData(self._popups[popup], popup.mda_tabs.value())

    def _on_popup_finished(self) -> None:
        self._popups.pop(cast("_MDAPopup", self.sender()), None)

    def _close_orphan_popups(self) -> None:
        """Close the pop-ups of the rows that are gone."""
        for popup, index in list(self._popups.items()):
            if not index.isValid():
                popup.reject()

    def _surface_focus_problem(self) -> str:
        """Return why Surface Focus cannot be used now, or `""`."""
        if self._is_active(self.include_z):
            return SURFACE_INCLUDE_Z_TOOLTIP
        if self._is_active(self.af_per_position):
            return SURFACE_HAF_TOOLTIP
        return self._surface_conflict

    def _include_z_problem(self) -> str:
        """Return why Include Z cannot be used now, or `""`."""
        return SURFACE_CHECKED_TOOLTIP if self.surface_focus.isChecked() else ""

    def _update_include_z(self) -> None:
        """Uncheck and disable Include Z while it cannot be used."""
        problem = self._include_z_problem()
        if problem:
            self.include_z.setChecked(False)
        self.include_z.setEnabled(not problem)
        self.include_z.setToolTip(problem)

    def _is_active(self, checkbox: QCheckBox) -> bool:
        # explicit states only: an ancestor is disabled while an MDA runs
        return bool(
            checkbox.isChecked()
            and checkbox.isEnabledTo(self)
            and checkbox.isVisibleTo(self)
        )

    def _update_surface_focus(self) -> None:
        """Uncheck Surface Focus while it cannot be used, keeping its surface."""
        conflict = self._surface_focus_problem()
        incomplete = self._surface.problem()
        if conflict or incomplete:
            self.surface_focus.setChecked(False)
        self.surface_focus.setEnabled(not conflict)
        # the surface cannot be set up either while Surface Focus cannot be used
        self._surface_settings.setEnabled(not conflict)
        # until the surface is set up, a click opens the setup dialog instead
        self.surface_focus.setCheckable(not incomplete)
        method, n = METHODS[self._surface.method], len(self._surface.points)
        status = incomplete or f"{method} through {n} points."
        self.surface_focus.setToolTip(conflict or f"{SURFACE_TOOLTIP}\n\n{status}")
        # once a setup is started
        badge = ""
        if n and not conflict:
            badge = "error" if incomplete else "valid"
        self._surface_settings.setIcon(self._gears[badge])
        settings = f"{SURFACE_SETTINGS_TOOLTIP}\n\n{status}" if badge else ""
        self._surface_settings.setToolTip(
            conflict or settings or SURFACE_SETTINGS_TOOLTIP
        )

    def _on_surface_focus_clicked(self) -> None:
        if not self.surface_focus.isCheckable():
            self._check_surface_on_ok = True
            self._edit_surface()

    def _surface_stage(self) -> SurfaceStage | None:
        """Return the stage to set up and test the surface with, if there is one."""
        return None

    def _edit_surface(self) -> None:
        """Show the surface setup, leaving the app usable (e.g. to move and focus)."""
        from ._z_surface_dialog import ZSurfaceDialog

        if (dialog := self._surface_dialog) is None:
            dialog = self._surface_dialog = ZSurfaceDialog(
                self._surface,
                stage=self._surface_stage(),
                parent=self,
                # while it is used, the surface cannot be left unusable
                require_valid=self.surface_focus.isChecked(),
            )
            self.surface_focus.toggled.connect(dialog.setRequireValid)
            dialog.finished.connect(self._on_surface_dialog_finished)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_surface_dialog_finished(self, result: int) -> None:
        dialog, self._surface_dialog = self._surface_dialog, None
        check, self._check_surface_on_ok = self._check_surface_on_ok, False
        if dialog is None:  # pragma: no cover
            return
        self.surface_focus.toggled.disconnect(dialog.setRequireValid)
        dialog.deleteLater()
        if result:
            self.setSurface(dialog.value())
            if check:
                self.surface_focus.setChecked(True)  # no-op if still incomplete

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


def _grid_first_point(seq: useq.MDASequence | None) -> tuple[float, float] | None:
    """Return the (x, y) of the first FOV an absolute grid plan will acquire.

    Only the first point is pulled from the plan, so this stays O(1) no
    matter how many tiles the grid has.  That matters: a plan built with a
    zero FOV -- which StageExplorer produces whenever no pixel size is
    configured, since CMMCorePlus.getPixelSizeUm() then returns 0.0 -- steps
    one micron at a time and enumerates millions of points for an ordinary
    ROI, far too many to walk synchronously in a Qt slot.

    The first point is also always a real tile center, and it honours the
    plan's scan `mode`, so it's exactly where the acquisition will start.
    """
    grid = seq.grid_plan if seq is not None else None
    if grid is None or grid.is_relative:
        return None
    first = next(iter(grid), None)
    if first is None or first.x is None or first.y is None:
        return None
    return first.x, first.y
