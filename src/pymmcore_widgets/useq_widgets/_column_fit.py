"""Keep a table's columns fitted to their contents while filling its width."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from qtpy.QtCore import QEvent, QObject, QTimer
from qtpy.QtWidgets import QHeaderView, QTableWidget

if TYPE_CHECKING:
    from qtpy.QtCore import QModelIndex

# fitting a column to its contents never makes it wider than this (e.g. a combo
# box sized for its longest choice); the user can still widen it
MAX_FIT_WIDTH = 220


class ColumnFitter(QObject):
    """Size `table`'s columns to their contents, resizable, spanning its width.

    Every visible column that is not in `QHeaderView.ResizeMode.Fixed` mode is
    user-resizable and starts as wide as its contents (capped at `max_fit_width`).
    Leftover width is spread evenly over those columns, and is re-spread when the
    table is resized. Whatever width a user's drag frees or takes is absorbed by
    the last of them, so the table keeps its width -- which also means a column can
    only be dragged as wide as the last one, at its content width, allows.

    Columns are re-fitted when they are added, removed, shown or hidden, and when
    rows are added to an empty table. Call `fit` after other structural changes.
    """

    def __init__(self, table: QTableWidget, max_fit_width: int = MAX_FIT_WIDTH):
        super().__init__(table)
        self._table = table
        self._max_fit_width = max_fit_width
        # True while we set column widths ourselves
        self._sizing = False

        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self.fit)

        header = self._header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.sectionResized.connect(self._on_section_resized)
        if (viewport := table.viewport()) is not None:
            viewport.installEventFilter(self)
        if (model := table.model()) is not None:
            model.columnsInserted.connect(self.scheduleFit)
            model.columnsRemoved.connect(self.scheduleFit)
            model.rowsInserted.connect(self._on_rows_inserted)

    def fit(self) -> None:
        """Size the resizable columns to their contents, then fill the width."""
        self._fit_timer.stop()
        self._sizing = True
        try:
            for col in self.resizableColumns():
                self._table.setColumnWidth(col, self.contentWidth(col))
        finally:
            self._sizing = False
        self.fill()

    def scheduleFit(self, *_: Any) -> None:
        """Fit once control returns to the event loop.

        Cell widgets of a new row or column are created only after the model
        reports it, so fitting right away would measure empty cells.
        """
        self._fit_timer.start()

    def fill(self) -> None:
        """Grow or shrink the resizable columns so they span the viewport.

        Extra width is shared evenly; when narrowing, columns give up only the width
        they have beyond their contents, so they never get cut off.
        """
        cols = self.resizableColumns()
        if not cols or (viewport := self._table.viewport()) is None:
            return
        self._sizing = True
        try:
            self._spread(cols, viewport.width() - self.usedWidth())
        finally:
            self._sizing = False

    def resizableColumns(self) -> list[int]:
        """Return the visible columns that are not fixed-width."""
        table = self._table
        header = self._header()
        return [
            col
            for col in range(table.columnCount())
            if not table.isColumnHidden(col)
            and header.sectionResizeMode(col) != QHeaderView.ResizeMode.Fixed
        ]

    def usedWidth(self) -> int:
        """Total width of the visible columns.

        Summed rather than read from `QHeaderView.length`, which lags behind while
        a section is being dragged.
        """
        table = self._table
        return sum(
            table.columnWidth(col)
            for col in range(table.columnCount())
            if not table.isColumnHidden(col)
        )

    def contentWidth(self, col: int) -> int:
        """Width `col` needs for its header and cells, capped at `max_fit_width`."""
        hint = max(
            self._table.sizeHintForColumn(col), self._header().sectionSizeHint(col)
        )
        return min(hint, self._max_fit_width)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if (
            a1 is not None
            and a1.type() == QEvent.Type.Resize
            and a0 is self._table.viewport()
        ):
            self.fill()
        return False

    # ------------------- Private API -------------------

    def _header(self) -> QHeaderView:
        return cast("QHeaderView", self._table.horizontalHeader())

    def _spread(self, cols: list[int], extra: int) -> None:
        table = self._table
        if extra > 0:
            share, rest = divmod(extra, len(cols))
            for i, col in enumerate(cols):
                bonus = share + (1 if i < rest else 0)
                table.setColumnWidth(col, table.columnWidth(col) + bonus)
        elif extra < 0:
            slack = {
                col: max(0, table.columnWidth(col) - self.contentWidth(col))
                for col in cols
            }
            if not (total := sum(slack.values())):
                return
            cut = min(-extra, total)
            for col, s in slack.items():
                table.setColumnWidth(col, table.columnWidth(col) - cut * s // total)

    def _on_rows_inserted(self, _parent: QModelIndex, first: int, last: int) -> None:
        # only the first rows change what the columns need: before them, the
        # columns were sized for their headers alone
        if first == 0 and last == self._table.rowCount() - 1:
            self.scheduleFit()

    def _on_section_resized(self, col: int, old: int, new: int) -> None:
        """Let the last column absorb a user's drag, so the table keeps its width."""
        if self._sizing:
            return
        if old == 0 or new == 0:
            # a column was shown or hidden
            self.scheduleFit()
            return
        cols = self.resizableColumns()
        if col not in cols or (viewport := self._table.viewport()) is None:
            return
        table = self._table
        last = cols[-1]
        min_last = self.contentWidth(last)
        width = table.columnWidth(last) + viewport.width() - self.usedWidth()
        self._sizing = True
        try:
            if width < min_last and col != last:
                # dragged wider than the room left: stop before the last column
                # gets cut off
                overflow = min_last - width
                table.setColumnWidth(
                    col, min(new, max(self.contentWidth(col), new - overflow))
                )
                width = table.columnWidth(last) + viewport.width() - self.usedWidth()
            width = max(min_last, width)
            if width != table.columnWidth(last):
                table.setColumnWidth(last, width)
        finally:
            self._sizing = False
