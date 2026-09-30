from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from qtpy.QtGui import QIcon
from superqt.iconify import QIconifyIcon
from superqt.utils import signals_blocked
from useq import MultiPhaseTimePlan, TDurationLoops, TIntervalDuration, TIntervalLoops

from ._column_info import IntColumn, TextColumn, TimeDeltaColumn
from ._data_table import DataTableWidget

if TYPE_CHECKING:
    from qtpy.QtWidgets import QWidget


class TimePlanWidget(DataTableWidget):
    """Table to edit a [useq.TimePlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#time-plans)."""

    PHASE = TextColumn(key="phase", default=None, is_row_selector=True)
    INTERVAL = TimeDeltaColumn(
        key="interval", default="1 s", minimum_unit="milliseconds"
    )
    DURATION = TimeDeltaColumn(key="duration", default="0 s", minimum_unit="seconds")
    LOOPS = IntColumn(key="loops", default=1, minimum=1)

    def __init__(self, rows: int = 0, parent: QWidget | None = None):
        super().__init__(rows, parent)
        self._emitting = False
        self._mode_column: int | None = None

        h_header = self.table().horizontalHeader()
        h_header.setSectionsClickable(True)
        h_header.sectionClicked.connect(self._set_mode_column)

        self._set_mode_column(self.table().indexOf(self.LOOPS))

        self.valueChanged.connect(self._on_value_changed)

    # ------------------------- Public API -------------------------

    def value(
        self, exclude_unchecked: bool = True
    ) -> MultiPhaseTimePlan | TIntervalLoops | TIntervalDuration:
        """Return the current value of the table as a [useq.TimePlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#time-plans).

        Returns
        -------
        MultiPhaseTimePlan | TIntervalLoops | TIntervalDuration
            The current [useq.TimePlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#time-plans)
            value of the table.
        """
        duration_col = self.table().indexOf(self.DURATION)
        duration_mode = self._mode_column == duration_col
        phases = []
        for p in self.table().iterRecords(exclude_unchecked=exclude_unchecked):
            # duration = interval * loops can't be divided back out of a 0
            # interval, so always fall back to loops for such a phase -
            # regardless of which column is the table's active one - to avoid
            # handing useq a TIntervalDuration whose .loops divides by zero.
            active_key = "duration" if duration_mode and p["interval"] else "loops"
            phases.append({"interval": p["interval"], active_key: p[active_key]})
        plan = MultiPhaseTimePlan(phases=phases)
        return plan.phases[0] if len(plan.phases) == 1 else plan  # type: ignore

    def setValue(self, value: Any) -> None:
        """Set the current value of the table from a [useq.TimePlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#time-plans).

        Parameters
        ----------
        value : MultiPhaseTimePlan | TIntervalLoops | TDurationLoops | TIntervalDuration | None
            The
            [useq.TimePlan](https://pymmcore-plus.github.io/useq-schema/schema/axes/#time-plans)
            to set.
        """  # noqa: E501
        if isinstance(value, MultiPhaseTimePlan):
            _phases = value.phases
        elif isinstance(value, (TDurationLoops, TIntervalLoops, TIntervalDuration)):
            _phases = [value]
        elif value is None:
            _phases = []
        else:
            raise TypeError(f"Expected useq.TimePlan or None, got {type(value)}.")
        if not _phases:
            self.table().setRowCount(0)
            return

        super().setValue([p.model_dump(exclude_unset=True) for p in _phases])

        table = self.table()
        for row in range(table.rowCount()):
            row_data = table.rowData(row)
            if self.INTERVAL.key in row_data:
                self._set_duration_enabled(row, bool(row_data[self.INTERVAL.key]))

        col_idx = table.indexOf(
            self.DURATION if isinstance(_phases[0], TIntervalDuration) else self.LOOPS
        )
        table.setCurrentCell(table.rowCount() - 1, col_idx)
        self._resolve_duration()

    # ------------------------- Private API -------------------------

    def _on_value_changed(self) -> None:
        self._resolve_duration()

    def _resolve_duration(self) -> None:
        """Resolve interval, loops, duration based on which column changed.

        The rules are:
        total duration = interval * loops
        """
        if self._emitting:
            return  # pragma: no cover

        _current_col = self.table().currentColumn()
        _current_row = self.table().currentRow()
        self._set_mode_column(_current_col)

        table = self.table()
        loop_col = table.indexOf(self.LOOPS)
        duration_col = table.indexOf(self.DURATION)
        interval_col = table.indexOf(self.INTERVAL)

        data = self.table().rowData(_current_row)
        if self.INTERVAL.key not in data:
            return

        # duration = interval * loops, so an interval of 0 can't be divided out
        # of a duration to recover loops; disable duration and drive off loops
        # instead, rather than silently failing to update on a ZeroDivisionError.
        interval_is_zero = not data[self.INTERVAL.key]
        self._set_duration_enabled(_current_row, not interval_is_zero)
        if interval_is_zero:
            if self._mode_column == duration_col:
                self._set_mode_column(loop_col)
            return

        plan: TIntervalDuration | TIntervalLoops
        try:
            if self._mode_column == duration_col:
                plan = TIntervalDuration(
                    interval=data[self.INTERVAL.key], duration=data[self.DURATION.key]
                )
            else:
                plan = TIntervalLoops(
                    interval=data[self.INTERVAL.key], loops=data[self.LOOPS.key]
                )
        except KeyError:
            return

        if _current_col == loop_col:
            self.DURATION.set_cell_data(
                table, _current_row, duration_col, plan.duration
            )
        elif _current_col == duration_col:
            self.LOOPS.set_cell_data(table, _current_row, loop_col, plan.loops)
        elif _current_col == interval_col:
            if self._mode_column == duration_col:
                self.LOOPS.set_cell_data(table, _current_row, loop_col, plan.loops)
            else:
                self.DURATION.set_cell_data(
                    table, _current_row, duration_col, plan.duration
                )

    def _set_duration_enabled(self, row: int, enabled: bool) -> None:
        """Enable/disable + tooltip the duration cell for `row`.

        Duration can't be derived when interval is 0 (division by zero), so the
        cell is disabled and pinned to 0 instead of being left showing a stale
        value. (0 is the only value consistent with an interval of 0: duration =
        interval * loops.) A blank field is avoided since it isn't a parseable
        time value and would break round-tripping through rowData()/value().
        """
        table = self.table()
        wdg = table.cellWidget(row, table.indexOf(self.DURATION))
        if wdg is None:
            return
        wdg.setEnabled(enabled)
        if enabled:
            wdg.setToolTip("")
        else:
            wdg.setToolTip(
                "Duration can't be set when interval is 0.\nSet loops instead."
            )
            with signals_blocked(wdg):
                wdg.setValue(timedelta(0))

    def _current_row_interval_is_zero(self) -> bool:
        table = self.table()
        row = table.currentRow()
        row = 0 if row < 0 else row
        if row >= table.rowCount():
            return False
        data = table.rowData(row)
        return self.INTERVAL.key in data and not data[self.INTERVAL.key]

    def _set_mode_column(self, col_idx: int) -> None:
        table = self.table()
        duration_col = table.indexOf(self.DURATION)
        # only duration and loops can be set as active
        if col_idx < duration_col or not table.columnInfo(col_idx):
            return
        # duration is disabled while interval is 0 (see _set_duration_enabled);
        # e.g. clicking directly on the Duration header must not select it as
        # the active column in that case.
        if col_idx == duration_col and self._current_row_interval_is_zero():
            return

        previous, self._mode_column = self._mode_column, col_idx
        if previous != self._mode_column:
            with signals_blocked(self):
                for col in range(table.columnCount()):
                    if header := table.horizontalHeaderItem(col):
                        header.setIcon(
                            QIconifyIcon("mdi:flag") if col == col_idx else QIcon()
                        )
            self._emitting = True
            try:
                self.valueChanged.emit()
            finally:
                self._emitting = False
