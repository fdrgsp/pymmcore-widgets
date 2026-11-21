"""A table of records shown with a model and a view, for tables with many rows.

Unlike [`DataTable`][pymmcore_widgets.useq_widgets.DataTable], which holds a widget
per cell, the rows are plain dicts: Qt only reads the rows it shows, and an editor
exists only while a cell is edited.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from qtpy.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    Qt,
    Signal,
)
from qtpy.QtGui import QCursor, QIcon, QMouseEvent, QPalette, QPixmap
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QHeaderView,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QTableView,
    QWidget,
)
from superqt.iconify import QIconifyIcon

from ._column_info import ButtonColumn, ColumnInfo, WidgetColumn

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from typing import TypeAlias

    from qtpy.QtCore import QAbstractItemModel, QObject, QPoint
    from qtpy.QtGui import QPainter
    from qtpy.QtWidgets import QStyleOptionViewItem

    Record: TypeAlias = dict[str, Any]
    Index: TypeAlias = QModelIndex | QPersistentModelIndex
    # the rect and icon of a button painted in a cell
    Button: TypeAlias = tuple[QRect, QIcon]

_ROOT = QModelIndex()
_PRESS = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)
Role = Qt.ItemDataRole
Flag = Qt.ItemFlag


class RecordTableModel(QAbstractTableModel):
    """Rows as records (`{column key: value}`), columns as `ColumnInfo`.

    A record may leave out a key, or set it to None, for its column's default. The
    row selector column's check states are kept beside the records.
    """

    # a value, a check state, or the rows changed
    valueChanged = Signal()

    def __init__(
        self,
        columns: Iterable[ColumnInfo] = (),
        rows: int = 0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._columns = list(columns)
        self._rows: list[Record] = [{} for _ in range(rows)]
        self._checked = [True] * rows
        self._read_only = False
        self._read_only_tip = ""

    # ------------------------- columns -------------------------

    def columns(self) -> tuple[ColumnInfo, ...]:
        return tuple(self._columns)

    def columnInfo(self, col: int) -> ColumnInfo:
        return self._columns[col]

    def addColumn(self, column: ColumnInfo, col: int) -> None:
        self.beginInsertColumns(_ROOT, col, col)
        self._columns.insert(col, column)
        self.endInsertColumns()

    # ------------------------- records -------------------------

    def value(self, row: int, column: ColumnInfo) -> Any:
        """Return the value of `column` in `row`, or the column's default."""
        value = self._rows[row].get(column.key)
        return column.default_value() if value is None else value

    def record(self, row: int) -> Record:
        return {c.key: self.value(row, c) for c in self._columns if c.stores_value()}

    def setRecords(self, records: Iterable[Record]) -> None:
        """Replace all rows, checked, with `records`."""
        rows = [dict(r) for r in records]
        self.beginResetModel()
        self._rows = rows
        self._checked = [True] * len(rows)
        self.endResetModel()
        self.valueChanged.emit()

    def setRecord(self, row: int, data: Record) -> None:
        """Update the values in `data`, leaving the others in `row` as they are."""
        record = self._rows[row]
        if any(record.get(key) != value for key, value in data.items()):
            record.update(data)
            # the whole row: a value can change how others show (e.g. a grid plan)
            last = self.columnCount() - 1
            self.dataChanged.emit(self.index(row, 0), self.index(row, last))
            self.valueChanged.emit()

    def setColumnValue(self, key: str, value: Any) -> None:
        """Set the value of the column `key` in every row."""
        for record in self._rows:
            record[key] = value
        self._all_changed()
        self.valueChanged.emit()

    def isChecked(self, row: int) -> bool:
        return self._checked[row]

    def setAllChecked(self, checked: bool) -> None:
        self._checked = [checked] * len(self._rows)
        self._all_changed()
        self.valueChanged.emit()

    def setReadOnly(self, read_only: bool, tooltip: str = "") -> None:
        """Make the cells read-only, leaving the row selector selectable."""
        self._read_only, self._read_only_tip = read_only, tooltip
        self._all_changed()

    def _all_changed(self) -> None:
        if self._rows and self._columns:
            last = self.index(len(self._rows) - 1, len(self._columns) - 1)
            self.dataChanged.emit(self.index(0, 0), last)

    # ------------------------- QAbstractTableModel -------------------------

    def rowCount(self, parent: Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Role.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Role.DisplayRole:
            return self._columns[section].header_text()
        return None

    def data(self, index: Index, role: int = Role.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, column = index.row(), self._columns[index.column()]
        if role == Role.DisplayRole:
            return column.display(self.value(row, column))
        if role == Role.EditRole:
            return self.value(row, column)
        if role == Role.CheckStateRole and column.is_row_selector:
            return (
                Qt.CheckState.Checked if self._checked[row] else Qt.CheckState.Unchecked
            )
        if role == Role.ToolTipRole and self._read_only:
            return self._read_only_tip
        return None

    def setData(self, index: Index, value: Any, role: int = Role.EditRole) -> bool:
        if not index.isValid():
            return False
        row, column = index.row(), self._columns[index.column()]
        if role == Role.CheckStateRole and column.is_row_selector:
            self._checked[row] = Qt.CheckState(value) == Qt.CheckState.Checked
            self.dataChanged.emit(index, index)
            self.valueChanged.emit()
            return True
        if role == Role.EditRole and column.stores_value():
            self.setRecord(row, {column.key: value})
            return True
        return False

    def flags(self, index: Index) -> Qt.ItemFlag:
        column = self._columns[index.column()]
        if self._read_only:
            # a row can still be selected, e.g. to move the stage to it
            if column.is_row_selector:
                return Flag.ItemIsEnabled | Flag.ItemIsSelectable
            return Flag.ItemIsEnabled
        flags = Flag.ItemIsEnabled | Flag.ItemIsSelectable
        if column.editable():
            flags |= Flag.ItemIsEditable
        if column.is_row_selector:
            flags |= Flag.ItemIsUserCheckable
        return flags

    def insertRows(self, row: int, count: int, parent: Index = _ROOT) -> bool:
        if parent.isValid() or count < 1 or not 0 <= row <= len(self._rows):
            return False
        self.beginInsertRows(_ROOT, row, row + count - 1)
        self._rows[row:row] = [{} for _ in range(count)]
        self._checked[row:row] = [True] * count
        self.endInsertRows()
        self.valueChanged.emit()
        return True

    def removeRows(self, row: int, count: int, parent: Index = _ROOT) -> bool:
        if parent.isValid() or count < 1 or row < 0 or row + count > len(self._rows):
            return False
        self.beginRemoveRows(_ROOT, row, row + count - 1)
        del self._rows[row : row + count]
        del self._checked[row : row + count]
        self.endRemoveRows()
        self.valueChanged.emit()
        return True

    def moveRows(
        self,
        source_parent: Index,
        source_row: int,
        count: int,
        destination_parent: Index,
        destination_child: int,
    ) -> bool:
        last = source_row + count - 1
        if (
            source_parent.isValid()
            or destination_parent.isValid()
            or source_row < 0
            or last >= len(self._rows)
            or not 0 <= destination_child <= len(self._rows)
            or not self.beginMoveRows(_ROOT, source_row, last, _ROOT, destination_child)
        ):
            return False
        # destination_child counts the moved rows when it is below them
        dest = destination_child - (count if destination_child > source_row else 0)
        for items in (self._rows, self._checked):
            moved = items[source_row : last + 1]
            del items[source_row : last + 1]
            items[dest:dest] = moved  # type: ignore[assignment]
        self.endMoveRows()
        self.valueChanged.emit()
        return True


def _column(index: Index) -> ColumnInfo:
    return cast("RecordTableModel", index.model()).columnInfo(index.column())


class ColumnDelegate(QStyledItemDelegate):
    """Edit a cell with its column's editor; paint `ButtonColumn` cells as buttons.

    The buttons take their look from the view's `buttonTemplate()`, and a click on
    one calls the column's `on_click(row, col)`. Subclasses paint more buttons by
    extending `buttons`, `buttonClicked` and `fixedWidth`.
    """

    def __init__(self, view: RecordTableView) -> None:
        super().__init__(view)
        self._view = view
        # the cell and the button in it under a mouse press
        self._pressed: tuple[QPersistentModelIndex, int] | None = None
        self._icons_cache: dict[tuple[str, str], QIcon] = {}

    def fixedWidth(self, column: ColumnInfo) -> int | None:
        """Return the width of a column of buttons, or None for a stretching one."""
        return self._view.buttonWidth() if isinstance(column, ButtonColumn) else None

    def buttons(self, index: QModelIndex, rect: QRect) -> list[Button]:
        """Return the buttons painted in the cell at `index`, which covers `rect`."""
        column = _column(index)
        if isinstance(column, ButtonColumn):
            return [(rect, self._glyph_icon(column.glyph))]
        return []

    def _glyph_icon(self, glyph: str) -> QIcon:
        """Return `glyph` in the buttons' text color, which follows the theme."""
        if not glyph:
            return QIcon()
        template = self._view.buttonTemplate()
        color = template.palette().color(QPalette.ColorRole.ButtonText).name()
        if (key := (glyph, color)) not in self._icons_cache:
            self._icons_cache[key] = QIconifyIcon(glyph, color=color)
        return self._icons_cache[key]

    def buttonClicked(self, index: QModelIndex, button: int) -> None:
        """Respond to a click on the `button`-th button of the cell at `index`."""
        column = _column(index)
        if isinstance(column, ButtonColumn) and callable(column.on_click):
            column.on_click(index.row(), index.column())

    # ------------------------- painting -------------------------

    def initStyleOption(self, option: QStyleOptionViewItem, index: Index) -> None:
        super().initStyleOption(option, index)
        # as the editor (e.g. a TableDoubleSpinBox) shows it
        if isinstance(_column(index), WidgetColumn):
            option.displayAlignment = Qt.AlignmentFlag.AlignCenter

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        if not (buttons := self.buttons(index, option.rect)):
            super().paint(painter, option, index)
            return
        view = self._view
        style = view.style()
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, view
        )
        template = view.buttonTemplate()
        template.ensurePolished()
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        cursor = view.viewport().mapFromGlobal(QCursor.pos())
        pressed = self._pressed if QApplication.mouseButtons() else None
        for i, (rect, icon) in enumerate(buttons):
            opt = QStyleOptionButton()
            opt.initFrom(template)
            opt.rect, opt.icon, opt.iconSize = rect, icon, template.iconSize()
            opt.state &= ~(
                QStyle.StateFlag.State_Enabled
                | QStyle.StateFlag.State_MouseOver
                | QStyle.StateFlag.State_HasFocus
            )
            opt.state |= QStyle.StateFlag.State_Raised
            if enabled and template.isEnabled():
                opt.state |= QStyle.StateFlag.State_Enabled
            if pressed == (index, i):
                opt.state |= QStyle.StateFlag.State_Sunken
            elif hovered and rect.contains(cursor):
                opt.state |= QStyle.StateFlag.State_MouseOver
            template.style().drawControl(
                QStyle.ControlElement.CE_PushButton, opt, painter, template
            )

    def editorEvent(
        self,
        event: QEvent,
        model: QAbstractItemModel,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        if (
            not isinstance(event, QMouseEvent)
            or event.button() != Qt.MouseButton.LeftButton
            or not index.flags() & Flag.ItemIsEnabled
            or not (buttons := self.buttons(index, option.rect))
        ):
            return super().editorEvent(event, model, option, index)  # type: ignore[no-any-return]
        pos = event.position().toPoint()
        hit = next(
            (i for i, (rect, _) in enumerate(buttons) if rect.contains(pos)), None
        )
        if event.type() in _PRESS:
            self._pressed = None if hit is None else (QPersistentModelIndex(index), hit)
            return hit is not None
        if event.type() == QEvent.Type.MouseButtonRelease:
            pressed, self._pressed = self._pressed, None
            if hit is not None and pressed == (index, hit):
                self.buttonClicked(index, hit)
            return pressed is not None
        return False

    # ------------------------- editing -------------------------

    def createEditor(
        self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QWidget:
        return _column(index).create_editor(parent)

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:
        _column(index).set_editor_value(editor, index.data(Role.EditRole))

    def setModelData(
        self, editor: QWidget, model: QAbstractItemModel, index: QModelIndex
    ) -> None:
        column = _column(index)
        if isinstance(editor, QAbstractSpinBox):
            editor.interpretText()  # keyboard tracking is off
        value = column.editor_value(editor)
        # unless the user changed what the editor showed, leave the value as it is
        # (e.g. not rounded to the editor's decimals)
        if column.display(value) != column.display(index.data(Role.EditRole)):
            model.setData(index, value)


class RecordTableView(QTableView):
    """A `QTableView` of a `RecordTableModel`, with the API of `DataTable`.

    `DataTableWidget` (its toolbar) and the tables built on it use that API, so they
    work with either.
    """

    valueChanged = Signal()

    def __init__(
        self,
        model: RecordTableModel,
        parent: QWidget | None = None,
        delegate: type[ColumnDelegate] = ColumnDelegate,
    ) -> None:
        super().__init__(parent)
        # the painted buttons take their look (style, palette, font, properties)
        # from it, and it is icon-only as they are, for their size
        self._button_template = QPushButton(self)
        self._button_template.setIcon(QIcon(QPixmap(1, 1)))
        self._button_template.hide()

        self.setModel(model)
        self.setItemDelegate(delegate(self))
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        # hover on the painted buttons
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        model.valueChanged.connect(self.valueChanged)
        model.valueChanged.connect(self._fit_columns)
        model.columnsInserted.connect(self._fit_columns)
        self._fit_columns()

    def model(self) -> RecordTableModel:
        return cast("RecordTableModel", super().model())

    def buttonTemplate(self) -> QPushButton:
        """Return the hidden button whose look the painted buttons take."""
        return self._button_template

    def buttonWidth(self) -> int:
        """Return the width of an icon-only button."""
        return self._button_template.sizeHint().width()  # type: ignore[no-any-return]

    # ------------------------- DataTable API -------------------------

    def addColumn(self, column_info: ColumnInfo, position: int | None = None) -> None:
        count = self.model().columnCount()
        if position is None:
            col = count
        elif position < 0:
            col = position + count + 1
        else:
            col = int(position)
        self.model().addColumn(column_info, col)
        if column_info.hidden:
            self.setColumnHidden(col, True)

    def indexOf(self, header: str | ColumnInfo) -> int:
        for col, info in enumerate(self.model().columns()):
            if isinstance(header, ColumnInfo):
                if info.key == header.key:
                    return col
            elif info.header_text() == header:
                return col
        return -1

    def rowCount(self) -> int:
        return self.model().rowCount()

    def columnCount(self) -> int:
        return self.model().columnCount()

    def insertRow(self, row: int) -> None:
        self.model().insertRows(row, 1)

    def removeRow(self, row: int) -> None:
        self.model().removeRows(row, 1)

    def setRowCount(self, rows: int) -> None:
        model, count = self.model(), self.model().rowCount()
        if rows < count:
            model.removeRows(rows, count - rows)
        elif rows > count:
            model.insertRows(count, rows - count)

    def rowData(self, row: int, exclude_hidden_cols: bool = False) -> Record:
        record = self.model().record(row)
        if exclude_hidden_cols:
            for key in self._hidden_keys():
                record.pop(key, None)
        return record

    def setRowData(self, row: int, data: Record) -> None:
        self.model().setRecord(row, data)

    def iterRecords(
        self, exclude_unchecked: bool = False, exclude_hidden_cols: bool = False
    ) -> Iterator[Record]:
        """Return an iterator over the rows as records."""
        model = self.model()
        hidden = self._hidden_keys() if exclude_hidden_cols else ()
        for row in range(model.rowCount()):
            if not exclude_unchecked or model.isChecked(row):
                record = model.record(row)
                for key in hidden:
                    record.pop(key, None)
                yield record

    def setValue(self, records: Iterable[Record]) -> None:
        self.model().setRecords(records)

    def checkAllRows(self) -> None:
        self.model().setAllChecked(True)

    def clearChecks(self) -> None:
        self.model().setAllChecked(False)

    # ------------------------- private -------------------------

    def mousePressEvent(self, e: QMouseEvent | None) -> None:
        super().mousePressEvent(e)
        # one click edits a widget column's value (e.g. a coordinate), as its
        # widget would in a DataTable
        if e is not None and e.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(e.position().toPoint())
            if (
                index.isValid()
                and index.flags() & Flag.ItemIsEditable
                and isinstance(_column(index), WidgetColumn)
            ):
                self.edit(index)

    def _hidden_keys(self) -> list[str]:
        columns = self.model().columns()
        return [c.key for col, c in enumerate(columns) if self.isColumnHidden(col)]

    def _fit_columns(self) -> None:
        """Fix the width of the columns of buttons; the others stretch."""
        header = self.horizontalHeader()
        delegate = cast("ColumnDelegate", self.itemDelegate())
        for col, column in enumerate(self.model().columns()):
            if (width := delegate.fixedWidth(column)) is not None:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
                self.setColumnWidth(col, width)

    def _show_context_menu(self, pos: QPoint) -> None:
        if menu := self._context_menu(self.indexAt(pos)):
            menu.exec(self.viewport().mapToGlobal(pos))

    def _context_menu(self, index: QModelIndex) -> QMenu | None:
        """Return the menu of a `ButtonColumn`'s `context_actions` at `index`."""
        if not index.isValid() or not index.flags() & Flag.ItemIsEnabled:
            return None
        column = _column(index)
        if not isinstance(column, ButtonColumn) or not column.context_actions:
            return None
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        row, col = index.row(), index.column()
        for text, callback in column.context_actions:
            action = menu.addAction(text)
            action.triggered.connect(lambda _=False, cb=callback: cb(row, col))
        return menu
