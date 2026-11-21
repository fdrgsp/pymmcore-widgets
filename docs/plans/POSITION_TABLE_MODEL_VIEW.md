# Position table on model/view

Plan, 2026-11-21. Moves `PositionTable` (and `CoreConnectedPositionTable`) from a
`QTableWidget` full of cell widgets to a `QAbstractTableModel` shown in a
`QTableView` with delegates, so that a large position list loads without
freezing the GUI. The channel, time and Surface Focus point tables keep the
current widget-based `DataTable`.

## Problem

Every cell of the position table is a live widget: a `TableDoubleSpinBox` for
X, Y, Z and AF, a `QPushButton` for each "set from core" arrow, and an
`MDAButton` (two buttons and a layout) for the sub-sequence. Filling the table
costs O(rows × columns) widget creation, theming and layout, all on the GUI
thread.

Measured in pymmcore-gui with an HCS import of a 96-well plate, 5 × 5 points per
well (2,400 positions):

| Phase | Time | Where |
|---|---|---|
| Create every row's cell widgets | 4.1 s | `DataTable.setValue` → `setRowCount` → `_on_rows_inserted` → `_populate_new_row` → `init_cell`: 19,200 `setCellWidget` calls, 9,600 spin boxes, 2,400 `MDAButton`s |
| Fill in the values | 0.4 s | `setRowData` loop, the only part `progress()` reports |
| Show the table again | 2.9 s | `batchUpdate()` re-shows the table: Qt polishes and lays out all 19,200 widgets (2.4 s in `show`) |
| Layout and paint afterwards | 0.9 s | back in the event loop |

About 8.5 s frozen in total, and it grows linearly (384 positions: about 2 s).
Roughly 1–1.4 s of the creation phase is the app re-theming each new cell
(pymmcore-gui's `unstyle_widgets` and icon recoloring), which a model/view
table also removes.

The table holds nothing that needs a widget per cell. In HCS mode it is even
display-only: `CoreConnectedPositionTable.value()` returns the plate plan
itself.

## Goal

- `setValue` with N positions costs O(N) plain Python (building one dict per
  row) and a single model reset. Qt only reads the data of the visible rows.
- Measured floor with a prototype `QAbstractTableModel` + `QTableView`:

  | Positions | Iterate plate plan | Build records | Model reset + paint |
  |---|---|---|---|
  | 2,400 | 0.021 s | 0.004 s | 0.028 s |
  | 9,600 | 0.071 s | 0.013 s | 0.023 s |

- Targets: 2,400 positions from the HCS wizard in under 0.2 s end to end
  (including the wizard sync), 10,000 in under 0.5 s.
- No change to `PositionTable`'s public value API: `value()`, `setValue()`,
  `valueChanged`, `include_z`, `af_per_position`, `surface_focus`,
  `setXYEnabled()`, `surface()`/`setSurface()`, `save()`/`load()`.
- Editing looks and behaves as today: the cells already look like text
  (`TableDoubleSpinBox` has no frame and no buttons, and is centered), and the
  same spin box becomes the editor while a cell is edited.

Not in scope: the channel, time and Surface Focus point tables (they hold a
handful of rows), and replacing `DataTable` for them.

## Design

```
PositionTable(DataTableWidget)           toolbar, resize grip, checkboxes (unchanged)
└── PositionTableView(QTableView)        replaces DataTable for positions
    ├── RecordTableModel(QAbstractTableModel)   rows as list[dict]
    ├── ColumnDelegate(QStyledItemDelegate)     number and text editing
    ├── ButtonDelegate                          painted "set from core" arrows
    └── SubSequenceDelegate                     painted grid button + clear button
```

New code goes in `src/pymmcore_widgets/useq_widgets/_record_model.py` (model,
view, delegates). `_column_info.py` gains a small model-side protocol.
`_positions.py` and `mda/_core_positions.py` switch to the new view.

### 1. Column protocol (`_column_info.py`)

The `ColumnInfo` dataclasses stay the single description of a column. Add
methods that the model and delegates use; the existing
`init_cell`/`get_cell_data`/`set_cell_data` stay for `DataTable`.

```python
class ColumnInfo:
    def default_value(self) -> Any:
        return self.default_factory() if self.default_factory else self.default

    def display(self, value: Any) -> str:          # DisplayRole text
        return "" if value is None else str(value)

    def editable(self) -> bool:                    # adds ItemIsEditable
        return False

class WidgetColumn(ColumnInfo):
    def editable(self) -> bool:
        return True

    def create_editor(self, parent: QWidget) -> QWidget:
        editor = self._init_widget()               # same factory as today
        editor.setParent(parent)
        return editor

    def set_editor_value(self, editor: QWidget, value: Any) -> None:
        self.data_type.setter(editor, value)

    def editor_value(self, editor: QWidget) -> Any:
        return self.data_type.getter(editor)

class _RangeColumn(WidgetColumn):
    def display(self, value: Any) -> str:
        return "" if value is None else f"{value:.{self.decimals}f}"
```

Reusing `_init_widget()` keeps the editors identical: `TableDoubleSpinBox` with
the column's minimum, maximum and decimals, no wheel scrolling. `TextColumn`
(Name) edits with the view's default line edit; its row-selector checkbox
becomes `Qt.ItemDataRole.CheckStateRole`. `ButtonColumn` and `SubSeqColumn` are
not editable; their delegates paint them.

### 2. `RecordTableModel`

State:

- `_columns: list[ColumnInfo]`, set by `addColumn(info, position)`
  (`beginInsertColumns`), so `CoreConnectedPositionTable` can still insert its
  button columns at a given index.
- `_rows: list[dict[str, Any]]`, one record per row, keyed by `ColumnInfo.key`.
  Missing keys fall back to `column.default_value()`.
- `_checked: list[bool]`, the Name column's check state (the row selector).
- `_read_only: bool`, set while positions come from the HCS wizard.
- `_xy_locked: bool`, set by `setXYEnabled(False)` (a global absolute grid).

Roles:

| Role | Returns |
|---|---|
| `DisplayRole` | `column.display(value)`; for X/Y of a row whose sub-sequence has an absolute grid, the first grid point (`_grid_first_point`, O(1)) |
| `EditRole` | the raw value |
| `CheckStateRole` | Name column only |
| `ToolTipRole` | "X/Y defined by the global absolute grid plan." or "X/Y defined by the absolute grid in the sub-sequence." on locked X/Y cells; `HCS_LOCKED_TOOLTIP` in read-only mode |
| `TextAlignmentRole` | centered for number columns |

`flags(index)` is computed, not stored per cell. Without editable and enabled
flags a cell paints disabled and cannot open an editor:

- read-only (HCS): Name keeps `ItemIsEnabled | ItemIsSelectable`, so "Move
  Stage to Selected Point" still works; everything else is enabled only.
- X, Y and the XY arrow lose `ItemIsEnabled` when `_xy_locked`, or when the
  row's sub-sequence has an absolute grid (`_seq_has_absolute_grid`).

This replaces `_enable_table_items`, `_set_row_xy_enabled`,
`_on_table_rows_inserted` and `_on_sub_seq_changed`: per-row loops over widgets
become one flag, plus one `dataChanged` over the affected columns.

API used by the view and the tables:

```python
def setRecords(self, records: list[Record], checked: bool = True) -> None
    # beginResetModel / endResetModel: the only cost is the list
def record(self, row: int) -> Record
def setRecord(self, row: int, data: Record) -> None        # partial update
def setColumnValue(self, key: str, value: Any) -> None     # "Copy Z to all"
def insertRows(row, count, parent=...) / removeRows(...)   # defaults per column
def moveRows(src_parent, src, count, dst_parent, dst)      # move up / down
def setReadOnly(self, read_only: bool) -> None
def setXYLocked(self, locked: bool) -> None
```

`setData` and `setRecord` emit `dataChanged`. The view turns `dataChanged`,
`rowsInserted`, `rowsRemoved`, `rowsMoved` and `modelReset` into a single
`valueChanged`, as `DataTable` does with `itemChanged` and its row signals.

### 3. `PositionTableView`

A `QTableView` with the subset of `DataTable`'s API that `DataTableWidget`'s
toolbar code and the position tables call, so `DataTableWidget` can host
either table:

| `DataTable` call used today | `PositionTableView` |
|---|---|
| `rowCount()`, `columnCount()` | model counts |
| `indexOf(column)`, `columnInfo(col)`, `addColumn(info, pos)` | via the model's columns |
| `rowData(row, exclude_hidden_cols)`, `setRowData(row, data)` | model `record`/`setRecord`, skipping hidden columns as today |
| `iterRecords(exclude_unchecked, exclude_hidden_cols)` | same semantics, over the model's rows |
| `setValue(records)` | `model.setRecords(list(records))` |
| `insertRow(row)`, `removeRow(row)`, `setRowCount(0)` | model `insertRows`/`removeRows` |
| `checkAllRows()`, `clearChecks()` | set `_checked`, one `dataChanged` |
| `batchUpdate()` | kept as a no-op context manager: the model needs no batching |
| `valueChanged` | as above |
| `itemSelectionChanged`, `selectedItems()` | `selectionModel().selectionChanged`, `selectionModel().selectedRows()` |
| `cellDoubleClicked` | `doubleClicked(QModelIndex)` |
| `cellWidget(row, col)` | not provided: there are no cell widgets |

`DataTableWidget.__init__` gets a factory hook,
`self._table = self._create_table(rows)`, which returns a `DataTable` by
default and a `PositionTableView` in `PositionTable`. `_swap_rows` keeps
working through `rowData`/`setRowData`; switching it to `moveRows` would also
keep the selection model consistent and is preferred.

Type the shared surface as a `typing.Protocol` (`_Table`) in `_data_table.py`,
so `DataTableWidget.table()` stays typed for both.

View settings: `SelectRows`, stretch header as today, `use_combo_row_height`
(it only needs a `QTableView`), `setMouseTracking(True)` for hover on the
painted buttons, and edit triggers
`CurrentChanged | SelectedClicked | DoubleClicked | EditKeyPressed | AnyKeyPressed`,
so that one click edits a coordinate as it does today.

`_TableResizeGrip` and the collapsible MDA's `_apply_editor_min_heights`
already use only `QTableView` API; they need no change beyond
`_TableResizeGrip`'s type hint.

### 4. Delegates

`ColumnDelegate(QStyledItemDelegate)`, one per view, looks up the column from
`index.column()`:

- `createEditor` → `column.create_editor(parent)`.
- `setEditorData` → `column.set_editor_value(editor, index.data(EditRole))`.
- `setModelData` → `model.setData(index, column.editor_value(editor))`.
- Commit on `editingFinished` (spin boxes have keyboard tracking off, as today).

`ButtonDelegate` (the "set from core" arrows, `ButtonColumn`):

- Paints a `QStyleOptionButton` with the column's icon through
  `style.drawControl(CE_PushButton, option, painter, template)`, where
  `template` is a hidden `QPushButton` owned by the view. The app style reads
  the button's palette, font and dynamic properties (pymmcore-gui's `variant`)
  from that widget, so the painted buttons follow the app's theme. Expose it as
  `PositionTableView.buttonTemplate()`.
- `editorEvent`: a left release inside the cell calls `column.on_click(row,
  col)`, the same signature as today. Hover and pressed states come from
  `option.state`.
- `context_actions` ("Copy Z to All Positions", "Copy AF to All Positions"):
  the view's `customContextMenuRequested` builds a `QMenu` for the column under
  the cursor.
- Column width: fixed, from the template's size hint, as `ButtonColumn` does
  now.

`SubSequenceDelegate` (the Grid column):

- Paints the grid button (`mdi:grid`; green when the row has a sub-sequence)
  and, for configured rows, the clear button (`mdi:close-circle`) in its own
  sub-rectangle. Icons are set with `setIcons(empty, configured, clear)` so an
  app can theme them; pymmcore-gui does today in `_apply_position_icons`.
- Click on the grid button: open `_MDAPopup(value, parent=view)` non-modally,
  as `MDAButton._on_click` does. Track open pop-ups by
  `QPersistentModelIndex`, so a row moved or removed while its pop-up is open
  is still found (or the pop-up closes). On accept, `setData` the sequence.
- Click on the clear button: `setData(index, None)`.
- Column width: grid button alone, plus the clear button while any row has a
  sub-sequence (today's `SubSeqColumn.fitWidth`). Recompute on `dataChanged`
  and `modelReset` with one `any(...)` over the records.
- Public `PositionTable.editSubSequence(row)` opens the pop-up for a row, for
  tests and callers that clicked `MDAButton.seq_btn`.

### 5. `PositionTable` (`useq_widgets/_positions.py`)

- `_create_table` returns a `PositionTableView`.
- `setValue`: keep the conversion to records (autofocus offsets into the AF
  column, the "only some positions have a z" warning), then one
  `model.setRecords(...)`.
- `setXYEnabled(enabled)` → `model.setXYLocked(not enabled)`.
- `_on_include_z_toggled`, `_on_af_per_position_toggled`: unchanged
  (`setColumnHidden` is view API).
- Remove `_on_table_rows_inserted`, `_on_sub_seq_changed`, `_set_row_xy_enabled`
  and `SubSeqColumn.init_cell`/`fitWidth`: the model and delegate cover them.
- `MDAButton` and `_MDAPopup` stay: the pop-up is reused by the delegate.
  `MDAButton` remains until pymmcore-gui stops importing it, then goes.

### 6. `CoreConnectedPositionTable` (`mda/_core_positions.py`)

- `_set_position_table_editable(state)`: column visibility as today, then
  `model.setReadOnly(not state)` instead of `_enable_table_items`. Connect
  `doubleClicked` instead of `cellDoubleClicked`.
- `setValue` with a `WellPlatePlan`: build the records straight from the plan
  (`name`, `x`, `y` per position), skipping the `Position.model_validate` and
  `model_dump` round trip, which plate positions do not need. The table is
  display-only in this mode.
- `_on_selection_change`: read `selectionModel().selectedRows()`. Keep the
  rule that clicking a button column does not move the stage, and extend it to
  every editable column: today a click on a spin box does not select the row,
  so it never moves the stage. With delegates the click selects the row, so
  without this rule editing a coordinate would move the stage. Only a click on
  the Name column (or keyboard navigation) moves it, as today.
- `_copy_to_all` → `model.setColumnValue(key, value)`.
- `_update_fov_size`: rewrite the grid plans in the records and emit one
  `dataChanged` for the Grid column, instead of reading each `MDAButton`.
- `_set_row_xy_enabled` override: gone, since the XY arrow's enabled state
  comes from the same flags as X and Y.
- `_add_row`, `_set_xy_from_core`, `_set_z_from_core`, `_set_af_from_core`:
  unchanged, through `insertRow`/`setRowData`/`removeRow`.
- `_update_table_positions`: keep `busy()` for the message during the wizard
  sync; nothing reports `progress()` for positions any more.

## Behaviour to keep

Each item has a test today; the rewritten tests below must cover it again.

- Unchecked rows are left out of `value()`; hidden columns (Z without "Include
  Z", AF without "HAF Offset") are left out when `exclude_hidden_cols`.
- A row whose sub-sequence has an absolute grid shows its first grid point in
  X/Y, disabled, with its tooltip, and `value()` drops its x/y.
- A global absolute grid disables X/Y on every row except rows with their own
  absolute grid.
- HCS mode: positions read-only, Grid and XY-arrow columns hidden, toolbar
  actions disabled, Name still selectable, double-click asks to unlock, and
  `value()` returns the plan. Unlocking keeps the coordinates.
- Move up/down keeps each row's sub-sequence, check state and AF offset.
- "Copy Z/AF to All Positions" from the arrow's context menu.
- The Grid column widens only while some row has a sub-sequence.
- The sub-sequence pop-up is non-modal and only shows the grid editor.
- A camera or pixel-size change updates every grid plan's FOV.

## Behaviour changes

- `PositionTable.table()` returns a `PositionTableView`, not a `DataTable`.
  Code that reads position cell widgets (`cellWidget`, `MDAButton`) breaks.
  pymmcore-gui is the only known caller; `DataTable` itself is unchanged.
- The arrows and grid buttons are painted, not widgets: no per-button focus or
  accessibility object. The view keeps keyboard navigation across cells, which
  the cell widgets did not have.
- This is a larger divergence from upstream pymmcore-widgets than the fork's
  other changes; consider proposing it upstream once it has settled.

## pymmcore-gui follow-up

After the widgets change is locked in the gui:

- `widgets/_mda_widget.py`: drop `stage_positions` from the
  `_on_table_rows_inserted` connections, and replace `_apply_position_icons`
  and `_connect_position_icon_updates` with one call to the delegate's
  `setIcons(...)` from `_apply_themed_icons` (it runs on theme changes too).
  Remove the `MDAButton` import.
- Set the theme's button variant on `stage_positions.table().buttonTemplate()`
  where the app sets variants, so the painted buttons match the others.
- Tests: `tests/test_mda_positions.py` (cell theming on insert, HCS import
  visibility and theme, import errors) asserts on cell widgets; rewrite them
  against the model and the delegate's icons. The sub-sequence pop-up tests in
  `tests/test_gui.py` click `seq_btn`; use `editSubSequence(row)`.

## Tests (pymmcore-widgets)

Rewrite the position tests that reach into cell widgets (`cellWidget`,
`seq_btn`, `clear_btn`, `item()`):

- `tests/useq_widgets/test_useq_widgets.py`: `test_position_table`,
  `test_position_table_set_value`,
  `test_position_table_absolute_grid_shows_first_fov_disabled`,
  `test_position_table_degenerate_grid_does_not_hang`,
  `test_position_table_relative_grid_leaves_xy_enabled`,
  `test_position_load_save`. `test_data_table_move_rows` (a plain
  `DataTableWidget`) and `test_mda_popup_fits_grid_editor` (`_MDAPopup` on its
  own) stay as they are.
- `tests/test_useq_core_widgets.py`: the `test_core_connected_position_wdg*`,
  `test_position_table_*`, `test_core_position_table_*`,
  `test_core_mda_with_hcs_*`, `test_grid_plan_subsequence_fov_update` and
  `test_fov_refresh_preserves_unchecked_rows_and_editors` tests.
- `tests/useq_widgets/test_table_batch_update.py`: the HCS tests
  (`test_hcs_bulk_import_preserves_positions_and_read_only_state`,
  `test_make_editable_preserves_coordinates_and_existing_editors`) move to the
  model; the `DataTable` tests stay as they are.
- `tests/test_busy.py`: `test_position_import_reports_progress` goes (positions
  no longer report progress); `test_position_import_feedback_*` stays.

Simulate clicks on painted buttons with
`qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
pos=view.visualRect(index).center())`.

New tests:

- No cell widgets: after `setValue` with a plate plan, `view.indexWidget(index)`
  is `None` for every cell. This guards the performance without a flaky timing
  assertion.
- Editing through the delegate: open the editor on an X cell, set a value,
  commit; the record changes and `valueChanged` fires once.
- Flags: read-only mode, X/Y locked by a global and by a per-row absolute grid,
  Name still selectable in HCS mode.
- Button and grid delegates: a click calls `on_click(row, col)` for the clicked
  row; the clear button resets the sub-sequence; the context menu copies to all
  rows.
- Move Stage to Selected Point: clicking an X cell to edit does not move the
  stage; selecting a row by its Name does.
- One timing test with a generous bound (10,000 positions under 2 s), to catch
  a return to per-cell widgets.

## Steps

Each step leaves the suite green.

1. `_column_info.py` protocol, `RecordTableModel`, the delegates, and
   `PositionTableView` with its compatibility API, with model and delegate unit
   tests. Nothing uses them yet.
2. `DataTableWidget._create_table` hook and the `_Table` protocol. No
   behaviour change.
3. `PositionTable` on the new view, plus the `useq_widgets` position tests.
4. `CoreConnectedPositionTable` (HCS read-only, selection, arrows, FOV
   refresh), plus the core tests and the new tests above.
5. Measure the HCS import in pymmcore-gui against the targets, then the gui
   follow-up and its lock bump.
6. Remove `MDAButton` and `SubSeqColumn`'s widget code once nothing imports
   them.

## Open questions

- Edit trigger: `CurrentChanged` gives today's one-click editing but opens an
  editor on keyboard navigation too. The alternative, `SelectedClicked`, needs a
  second click. Try both in the app.
- Whether to keep `batchUpdate()` on `PositionTableView` as a no-op or remove
  it, once no caller needs it.
- Whether `MDASequenceWidget.load()` and the Stage Explorer's "Send to MDA"
  need any change: both go through `setValue`, so they should get the speed-up
  for free. Measure them after step 4.
