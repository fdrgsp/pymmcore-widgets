"""Per-preset channel settings: exposure, light source and camera properties."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QEvent, QItemSelectionModel, QPointF, Qt, QTimer
from qtpy.QtGui import QBrush, QColor, QPainter, QPen, QShowEvent
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked

from pymmcore_widgets._channel_settings import (
    CAMERA_PROPERTIES,
    ChannelSettings,
    apply_channel_settings,
    camera_property_columns,
    read_camera_cell,
    write_camera_cell,
)
from pymmcore_widgets._light_sources import (
    INTENSITY_KEY,
    LIGHT_SOURCE_KEY,
    NO_LIGHT_SOURCE,
    IntensityColumn,
    IntensitySpinBox,
    find_light_sources,
    light_source_limits,
)
from pymmcore_widgets._util import YELLOW, disable_wheel_scroll
from pymmcore_widgets.useq_widgets import ComboColumn
from pymmcore_widgets.useq_widgets._column_fit import ColumnFitter
from pymmcore_widgets.useq_widgets._column_info import (
    ColumnInfo,
    FloatColumn,
    TextColumn,
)
from pymmcore_widgets.useq_widgets._data_table import DataTableWidget

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from qtpy.QtCore import QAbstractItemModel, QModelIndex, SignalInstance
    from qtpy.QtWidgets import QStyleOptionViewItem

DEFAULT_EXP = 100.0
CURRENT_KEY = "_current"
PRESET_KEY = "config"
EXPOSURE_KEY = "exposure"


@dataclass(frozen=True)
class _PresetColumn(TextColumn):
    """Read-only preset names: the rows are the group's presets, fixed."""

    def init_cell(
        self, table: QTableWidget, row: int, col: int, change_signal: SignalInstance
    ) -> None:
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setToolTip("Click to apply this preset to the microscope")
        table.setItem(row, col, item)


_ACTIVE_ROLE = Qt.ItemDataRole.UserRole
_CURRENT_COL_WIDTH = 30
# fraction of the cell's shorter side the dot/ring's diameter occupies
_CURRENT_DOT_FRACTION = 0.4


class CurrentPresetDelegate(QStyledItemDelegate):
    """Paints the *current* column: a filled dot on the active row, a ring elsewhere.

    Drawn with `QPainter` rather than "●"/"○" text glyphs, which some fonts (e.g.
    Windows' Segoe UI) render at visibly different sizes.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active_color = QColor(YELLOW)

    def setActiveColor(self, color: QColor | str) -> None:
        """Set the fill of the active dot (default: yellow, as in pymmcore-gui)."""
        self._active_color = QColor(color)

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        super().paint(painter, option, index)
        if painter is None:  # pragma: no cover
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        radius = min(rect.width(), rect.height()) * _CURRENT_DOT_FRACTION / 2
        center = QPointF(rect.center())
        if index.data(_ACTIVE_ROLE):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self._active_color))
        else:
            ring = option.palette.text().color()
            ring.setAlpha(140)
            painter.setPen(QPen(ring, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius, radius)
        painter.restore()


@dataclass(frozen=True)
class _CurrentColumn(ColumnInfo):
    """Narrow indicator of the preset currently active on the microscope.

    Clicking a cell applies that row's preset (see `ChannelSettingsTable`).
    """

    key: str = CURRENT_KEY
    data_type: type = str  # unused; cells are plain QTableWidgetItems
    # left blank: the column is too narrow for a label; the header has a tooltip
    header: str | None = ""

    def init_cell(
        self, table: QTableWidget, row: int, col: int, change_signal: SignalInstance
    ) -> None:
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setToolTip("Click to apply this preset to the microscope")
        item.setData(_ACTIVE_ROLE, False)
        table.setItem(row, col, item)

    def get_cell_data(self, table: QTableWidget, row: int, col: int) -> dict[str, Any]:
        return {}  # not a setting

    def set_cell_data(
        self, table: QTableWidget, row: int, col: int, value: Any
    ) -> None:
        if item := table.item(row, col):
            item.setData(_ACTIVE_ROLE, bool(value))


class ChannelSettingsTable(DataTableWidget):
    """Table of the settings to use for each preset of a channel group.

    Picking a group in the *Group* combo lists every preset of that group, one per
    row, each with an exposure, a light source (the same choices as
    [`CoreConnectedChannelTable`][pymmcore_widgets.mda.CoreConnectedChannelTable])
    and intensity, and a column for each per-channel camera property the current
    camera has (see `CAMERA_PROPERTIES`). The *Light Sources* and *Camera*
    checkboxes show or hide those columns (see `setLightSourcesVisible` and
    `setCameraVisible`); the exposure is always shown. A camera property cell
    showing "—" leaves that property unchanged.

    Settings are remembered for every group, not just the one shown, so switching
    back to a group restores what was entered for it. Use
    [`channelSettings`][pymmcore_widgets.ChannelSettingsTable.channelSettings]
    to look up the settings of a preset (e.g. to fill in a channel added to an MDA),
    and `allChannelSettings` / `setAllChannelSettings` to persist them.

    The first column marks the preset currently active on the microscope with a
    filled dot (kept in sync with the core), and its row is highlighted; clicking
    another row's ring or preset name applies that preset and its settings with
    `applyChannel`. Clicking the other columns' cells only edits them.

    Parameters
    ----------
    mmcore : CMMCorePlus | None
        Optional [`CMMCorePlus`][pymmcore_plus.CMMCorePlus] micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new)
        [`CMMCorePlus.instance`][pymmcore_plus.core._mmcore_plus.CMMCorePlus.instance].
    parent : QWidget | None
        Optional parent widget, by default None.
    """

    CAMERA_PROPERTIES: ClassVar[tuple[str, ...]] = CAMERA_PROPERTIES

    # fmt: off
    CURRENT = _CurrentColumn()
    PRESET = _PresetColumn(key=PRESET_KEY, header="Preset")
    EXPOSURE = FloatColumn(key=EXPOSURE_KEY, header="Exposure [ms]", default=DEFAULT_EXP, minimum=0.01)  # noqa: E501
    LIGHT_SOURCE = ComboColumn(key=LIGHT_SOURCE_KEY, header="Light Source", default=NO_LIGHT_SOURCE, allowed_values=(NO_LIGHT_SOURCE,))  # noqa: E501
    INTENSITY = IntensityColumn(key=INTENSITY_KEY, header="Intensity", default=0.0)
    # fmt: on

    def __init__(
        self, mmcore: CMMCorePlus | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(0, parent)
        self._mmc = mmcore or CMMCorePlus.instance()

        # {group: {preset: settings}}, for every group seen so far
        self._settings: dict[str, dict[str, ChannelSettings]] = {}
        self._light_sources: dict[str, list[tuple[str, str]]] = {}
        self._light_source_column: ComboColumn = self.LIGHT_SOURCE
        self._camera = ""
        self._camera_property_names: tuple[str, ...] = self.CAMERA_PROPERTIES
        # {property name: column} for the camera properties shown
        self._camera_columns: dict[str, ColumnInfo] = {}
        self._syncing = False
        # the light source columns are shown by default, the camera property ones
        # hidden; see setLightSourcesVisible and setCameraVisible
        self._light_sources_visible = True
        self._camera_visible = False
        # row of the preset active on the microscope, or -1
        self._active_row = -1

        # rows are the group's presets: there is nothing to add, move or remove
        self._group_combo = QComboBox()
        disable_wheel_scroll(self._group_combo)
        self._group_combo.currentTextChanged.connect(self._on_group_changed)
        group_wdg = QWidget()
        group_layout = QHBoxLayout(group_wdg)
        group_layout.setContentsMargins(5, 5, 5, 5)
        group_layout.addWidget(QLabel("Group:"))
        group_layout.addWidget(self._group_combo)
        group_layout.addStretch()
        self._toolbar.clear()
        self._toolbar.addWidget(group_wdg)

        self.light_sources = QCheckBox("Light Sources")
        self.light_sources.setToolTip(
            "Show the Light Source and Intensity columns.\n"
            "While unchecked those columns are hidden, but keep their values."
        )
        self.light_sources.setChecked(self._light_sources_visible)
        self.light_sources.toggled.connect(self.setLightSourcesVisible)
        self.camera = QCheckBox("Camera")
        self.camera.setToolTip(
            "Show the per-channel camera property columns (e.g. Gain, Offset, "
            "Readout Mode).\n"
            "While unchecked those columns are hidden, but keep their values."
        )
        self.camera.setChecked(self._camera_visible)
        self.camera.toggled.connect(self.setCameraVisible)
        self._btn_row = QHBoxLayout()
        self._btn_row.setContentsMargins(5, 5, 5, 5)
        self._btn_row.setSpacing(15)
        self._btn_row.addWidget(self.light_sources)
        self._btn_row.addWidget(self.camera)
        self._btn_row.addStretch()
        cast("QVBoxLayout", self.layout()).addLayout(self._btn_row)

        # the table is as tall as its rows, and the widget as tall as its contents:
        # no vertical scrolling, resize grip or resizing
        self._resize_grip.hide()
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._fit_height_timer = QTimer(self)
        self._fit_height_timer.setSingleShot(True)
        self._fit_height_timer.setInterval(0)
        self._fit_height_timer.timeout.connect(self._fit_height)
        if (model := self._table.model()) is not None:
            # right away, so a dock wrapping this panel sizes for it on insertion
            model.rowsInserted.connect(self._fit_height)
            model.rowsRemoved.connect(self._fit_height)
        if (h_bar := self._table.horizontalScrollBar()) is not None:
            h_bar.rangeChanged.connect(self._fit_height_timer.start)

        # update the stored settings before anyone is told of the change
        self._table.valueChanged.disconnect(self.valueChanged)
        self._table.valueChanged.connect(self._on_table_changed)

        self._current_delegate = CurrentPresetDelegate(self._table)
        self._table.cellClicked.connect(self._on_cell_clicked)
        # the row highlight marks the active preset (see _set_active_row), so
        # clicking a cell or focusing its editor never selects anything; this
        # leaves every click to Qt, which the cell widgets need to take focus
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        # columns are fitted to their contents, resizable (except the current
        # column) and span the table's width
        self._fitter = ColumnFitter(self._table)

        ev = self._mmc.events
        ev.systemConfigurationLoaded.connect(self._on_configs_changed)
        ev.configGroupDeleted.connect(self._on_configs_changed)
        ev.configDefined.connect(self._on_configs_changed)
        ev.configDeleted.connect(self._on_configs_changed)
        ev.channelGroupChanged.connect(self._on_channel_group_changed)
        ev.propertyChanged.connect(self._on_property_changed)
        ev.configSet.connect(self._on_config_set)
        self.destroyed.connect(self._disconnect)

        self.refresh()

    # ------------------- public API -------------------

    def channelGroup(self) -> str:
        """Return the group whose presets are shown."""
        return str(self._group_combo.currentText())

    def setChannelGroup(self, group: str) -> None:
        """Show the presets of `group`."""
        self._group_combo.setCurrentText(group)

    def channelSettings(
        self, preset: str, group: str | None = None
    ) -> ChannelSettings | None:
        """Return the settings stored for `preset` of `group`, or None if unset.

        `group` defaults to the group shown. Presets of the shown group always have
        settings; those of other groups only once that group has been shown, or
        set with `setAllChannelSettings`.
        """
        group = self.channelGroup() if group is None else group
        settings = self._settings.get(group, {}).get(preset)
        return copy.deepcopy(settings) if settings is not None else None

    def value(self) -> dict[str, ChannelSettings]:  # type: ignore[override]
        """Return `{preset: settings}` for the group shown."""
        return copy.deepcopy(self._settings.get(self.channelGroup(), {}))

    def setValue(self, value: Mapping[str, ChannelSettings]) -> None:  # type: ignore[override]
        """Set the settings of the shown group's presets from `{preset: settings}`.

        Like [`DataTableWidget.setValue`][], this does not emit `valueChanged`.
        """
        group = self.channelGroup()
        self._settings.setdefault(group, {}).update(copy.deepcopy(dict(value)))
        self._populate(emit=False)

    def allChannelSettings(self) -> dict[str, dict[str, ChannelSettings]]:
        """Return `{group: {preset: settings}}` for every group seen so far.

        The result is JSON-serializable, to persist the settings across sessions.
        """
        return copy.deepcopy(self._settings)

    def setAllChannelSettings(
        self, value: Mapping[str, Mapping[str, ChannelSettings]]
    ) -> None:
        """Replace all stored settings with `{group: {preset: settings}}`.

        Like [`DataTableWidget.setValue`][], this does not emit `valueChanged`.
        """
        self._settings = {
            group: copy.deepcopy(dict(presets)) for group, presets in value.items()
        }
        self._populate(emit=False)

    def applyChannel(self, preset: str, group: str | None = None) -> None:
        """Set `preset` of `group` on the core, along with its stored settings."""
        group = self.channelGroup() if group is None else group
        self._mmc.setConfig(group, preset)
        if (settings := self._settings.get(group, {}).get(preset)) is not None:
            apply_channel_settings(self._mmc, settings, self._light_sources)

    def activePreset(self) -> str:
        """Return the shown group's preset active on the microscope, or `""`."""
        if self._active_row < 0:
            return ""
        return self._preset_at(self._active_row)

    def currentPresetDelegate(self) -> CurrentPresetDelegate:
        """Return the delegate painting the current column, e.g. to theme it."""
        return self._current_delegate

    def lightSources(self) -> Mapping[str, list[tuple[str, str]]]:
        """Return available light sources as ``{label: [(device, property), ...]}``.

        Single-property sources (keyed ``"<device> · <property>"``) have a
        one-element list. Single-preset config-group sources are keyed by the
        group name and list every ``(device, property)`` pair in the preset --
        setting the intensity broadcasts the same value to all of them.
        """
        return dict(self._light_sources)

    def setLightSourcesVisible(self, visible: bool) -> None:
        """Show or hide the Light Source and Intensity columns. On by default.

        This is a pure view toggle: the columns keep their values, which are stored
        and applied regardless.
        """
        self._light_sources_visible = bool(visible)
        with signals_blocked(self.light_sources):
            self.light_sources.setChecked(self._light_sources_visible)
        self._apply_column_visibility()
        self._fitter.fit()

    def lightSourcesVisible(self) -> bool:
        """Return whether the Light Source and Intensity columns are shown."""
        return self._light_sources_visible

    def setCameraVisible(self, visible: bool) -> None:
        """Show or hide the camera property columns. Off by default.

        This is a pure view toggle: the columns keep their values, which are stored
        and applied regardless.
        """
        self._camera_visible = bool(visible)
        with signals_blocked(self.camera):
            self.camera.setChecked(self._camera_visible)
        self._apply_column_visibility()
        self._fitter.fit()

    def cameraVisible(self) -> bool:
        """Return whether the camera property columns are shown."""
        return self._camera_visible

    def cameraProperties(self) -> tuple[str, ...]:
        """Return the camera properties shown as columns, for the current camera."""
        return tuple(self._camera_columns)

    def setCameraPropertyNames(self, names: Iterable[str]) -> None:
        """Choose which camera properties are offered (default `CAMERA_PROPERTIES`).

        Of these, only the ones the current camera has and can write become columns.
        """
        self._camera_property_names = tuple(names)
        self.refresh()

    def refresh(self) -> None:
        """Re-read the groups, light sources and camera properties from the core.

        Normally kept up to date by core signals; see
        [`CoreConnectedChannelTable.refresh`][pymmcore_widgets.mda.CoreConnectedChannelTable.refresh].
        """
        self._light_sources = find_light_sources(self._mmc)
        self._camera = self._mmc.getCameraDevice()
        self._camera_columns = camera_property_columns(
            self._mmc, self._camera, self._camera_property_names
        )

        groups = list(self._mmc.getAvailableConfigGroups())
        current = self.channelGroup()
        if current not in groups:
            ch_group = self._mmc.getChannelGroup()
            current = ch_group if ch_group in groups else (groups[0] if groups else "")
        with signals_blocked(self._group_combo):
            self._group_combo.clear()
            self._group_combo.addItems(groups)
            self._group_combo.setCurrentText(current)

        table = self._table
        with signals_blocked(table):
            table.setRowCount(0)
            while table.columnCount():
                table.removeColumn(0)
            self._light_source_column = ComboColumn(
                key=LIGHT_SOURCE_KEY,
                header="Light Source",
                default=NO_LIGHT_SOURCE,
                allowed_values=(NO_LIGHT_SOURCE, *self._light_sources),
            )
            for info in (
                self.CURRENT,
                self.PRESET,
                self.EXPOSURE,
                self._light_source_column,
                self.INTENSITY,
                *self._camera_columns.values(),
            ):
                table.addColumn(info)
            for prop, info in self._camera_columns.items():
                col = table.indexOf(info)
                if header_item := table.horizontalHeaderItem(col):
                    header_item.setToolTip(f"{self._camera} · {prop}")
            self._apply_column_visibility()
            current_col = table.indexOf(self.CURRENT)
            table.setItemDelegateForColumn(current_col, self._current_delegate)
            if (header := table.horizontalHeader()) is not None:
                header.setSectionResizeMode(current_col, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(current_col, _CURRENT_COL_WIDTH)
            if header_item := table.horizontalHeaderItem(current_col):
                header_item.setToolTip("Preset currently active on the microscope")
        self._populate()

    # ------------------- Private API -------------------

    def _apply_column_visibility(self) -> None:
        table = self._table
        for info in (self._light_source_column, self.INTENSITY):
            if (col := table.indexOf(info)) >= 0:
                table.setColumnHidden(col, not self._light_sources_visible)
        for cam_info in self._camera_columns.values():
            if (col := table.indexOf(cam_info)) >= 0:
                table.setColumnHidden(col, not self._camera_visible)

    def _default_settings(self) -> ChannelSettings:
        return ChannelSettings(
            exposure=self._mmc.getExposure() if self._camera else DEFAULT_EXP,
            light_source=NO_LIGHT_SOURCE,
            intensity=0.0,
            camera_properties={},
        )

    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        # the style is applied on show, which may change the rows' heights
        self._fit_height_timer.start()

    def changeEvent(self, a0: QEvent | None) -> None:
        super().changeEvent(a0)
        if a0 is not None and a0.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
        ):
            self._fit_height_timer.start()

    def _fit_height(self, *_: Any) -> None:
        """Fit the table to its header and rows, and this widget to its contents."""
        table = self._table
        header = cast("QHeaderView", table.horizontalHeader())
        height = 2 * table.frameWidth() + max(
            header.height(), header.sizeHint().height()
        )
        height += sum(table.rowHeight(row) for row in range(table.rowCount()))
        # a horizontal scroll bar, shown when the columns do not fit, takes room
        if (h_bar := table.horizontalScrollBar()) is not None and h_bar.maximum() > 0:
            height += h_bar.sizeHint().height()
        table.setFixedHeight(height)
        if (layout := self.layout()) is not None:
            layout.activate()
        self.setFixedHeight(self.sizeHint().height())

    def _populate(self, emit: bool = True) -> None:
        """Fill the table with the shown group's presets and their settings."""
        group = self.channelGroup()
        presets = list(self._mmc.getAvailableConfigs(group)) if group else []
        stored = self._settings.get(group, {})
        table = self._table
        with signals_blocked(table):
            self._active_row = -1
            table.setRowCount(0)
            table.setRowCount(len(presets))
            for row, preset in enumerate(presets):
                self._set_row(
                    row, preset, stored.get(preset) or self._default_settings()
                )
        self._update_active_row()
        self._fitter.fit()
        self._on_table_changed(emit=emit)

    def _set_row(self, row: int, preset: str, settings: ChannelSettings) -> None:
        table = self._table
        source = settings["light_source"]
        if source not in self._light_sources:
            source = NO_LIGHT_SOURCE
        table.setRowData(
            row,
            {
                PRESET_KEY: preset,
                EXPOSURE_KEY: settings["exposure"],
                LIGHT_SOURCE_KEY: source,
            },
        )
        # range must be set before the value, or it would be clamped away
        int_col = table.indexOf(self.INTENSITY)
        self._configure_intensity(row, int_col, source)
        self.INTENSITY.set_cell_data(table, row, int_col, settings["intensity"])

        stored_cam = settings["camera_properties"]
        for prop, info in self._camera_columns.items():
            write_camera_cell(table, row, info, stored_cam.get(prop))

    def _configure_intensity(self, row: int, col: int, source: str) -> None:
        """Range the spin box at `row` for `source`, starting at its current value."""
        wdg = cast("IntensitySpinBox | None", self._table.cellWidget(row, col))
        if wdg is None:  # pragma: no cover
            return
        if not (dev_props := self._light_sources.get(source)):
            wdg.setPropertyLimits(NO_LIGHT_SOURCE, None, False)
            return
        lower, upper, is_int = light_source_limits(self._mmc, dev_props)
        wdg.setPropertyLimits(source, (lower, upper), is_int)
        device, prop = dev_props[0]
        try:
            wdg.setValue(float(self._mmc.getProperty(device, prop)))
        except (RuntimeError, ValueError):  # pragma: no cover
            pass

    def _row_settings(self, row: int) -> tuple[str, ChannelSettings]:
        rec = self._table.rowData(row)
        camera_properties = {}
        for prop, info in self._camera_columns.items():
            value = read_camera_cell(
                self._mmc, self._camera, prop, info, rec.get(info.key)
            )
            if value is not None:
                camera_properties[prop] = value
        return str(rec.get(PRESET_KEY) or ""), ChannelSettings(
            exposure=float(rec.get(EXPOSURE_KEY) or DEFAULT_EXP),
            light_source=str(rec.get(LIGHT_SOURCE_KEY) or NO_LIGHT_SOURCE),
            intensity=float(rec.get(INTENSITY_KEY) or 0.0),
            camera_properties=camera_properties,
        )

    def _on_table_changed(self, emit: bool = True) -> None:
        """Re-range intensity spin boxes for new light sources and store the rows."""
        if self._syncing:
            return
        table = self._table
        ls_col = table.indexOf(self._light_source_column)
        int_col = table.indexOf(self.INTENSITY)
        self._syncing = True
        try:
            with signals_blocked(table):
                for row in range(table.rowCount()):
                    data = self._light_source_column.get_cell_data(table, row, ls_col)
                    source = data.get(LIGHT_SOURCE_KEY) or NO_LIGHT_SOURCE
                    wdg = cast(
                        "IntensitySpinBox | None", table.cellWidget(row, int_col)
                    )
                    if wdg is not None and wdg.group() != source:
                        self._configure_intensity(row, int_col, source)

            if group := self.channelGroup():
                stored = self._settings.setdefault(group, {})
                for row in range(table.rowCount()):
                    preset, settings = self._row_settings(row)
                    # keep values of properties only another camera has
                    if old := stored.get(preset):
                        settings["camera_properties"] = {
                            **{
                                prop: value
                                for prop, value in old["camera_properties"].items()
                                if prop not in self._camera_columns
                            },
                            **settings["camera_properties"],
                        }
                    stored[preset] = settings
        finally:
            self._syncing = False
        if emit:
            self.valueChanged.emit()

    def _preset_at(self, row: int) -> str:
        table = self._table
        data = self.PRESET.get_cell_data(table, row, table.indexOf(self.PRESET))
        return str(data.get(PRESET_KEY) or "")

    def _set_active_row(self, row: int) -> None:
        """Mark `row` active (-1 for none) and move the row highlight to it."""
        self._active_row = row
        table = self._table
        if (col := table.indexOf(self.CURRENT)) < 0:  # pragma: no cover
            return
        with signals_blocked(table):
            for r in range(table.rowCount()):
                self.CURRENT.set_cell_data(table, r, col, r == row)
        if (sel_model := table.selectionModel()) is None:  # pragma: no cover
            return
        if row < 0:
            sel_model.clearSelection()
            return
        # selected through the model: the view's NoSelection mode ignores selectRow
        sel_model.select(
            cast("QAbstractItemModel", table.model()).index(row, col),
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )

    def _update_active_row(self) -> None:
        """Mark the row of the preset the core reports as current."""
        group = self.channelGroup()
        try:
            preset = self._mmc.getCurrentConfigFromCache(group) if group else ""
        except RuntimeError:  # pragma: no cover
            preset = ""
        rows = range(self._table.rowCount())
        row = next((r for r in rows if preset and self._preset_at(r) == preset), -1)
        if row != self._active_row:
            self._set_active_row(row)

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if col in self._selector_columns():
            self.applyChannel(self._preset_at(row))
            self._update_active_row()

    def _selector_columns(self) -> tuple[int, int]:
        """Return the columns whose cells apply their row's preset when clicked."""
        return self._table.indexOf(self.CURRENT), self._table.indexOf(self.PRESET)

    def _on_config_set(self, group: str, preset: str) -> None:
        if group == self.channelGroup():
            self._update_active_row()

    def _on_group_changed(self, group: str) -> None:
        self._populate()

    def _on_configs_changed(self, *_: Any) -> None:
        # accepts (and ignores) the varying args of the config* core signals
        self.refresh()

    def _on_channel_group_changed(self, group: str) -> None:
        if self._group_combo.findText(group) >= 0:
            self.setChannelGroup(group)

    def _on_property_changed(self, device: str, prop: str, value: str) -> None:
        if device == "Core" and prop == "Camera" and value != self._camera:
            self.refresh()
        else:
            # a device in a preset may have been moved directly
            self._update_active_row()

    def _disconnect(self) -> None:
        ev = self._mmc.events
        ev.systemConfigurationLoaded.disconnect(self._on_configs_changed)
        ev.configGroupDeleted.disconnect(self._on_configs_changed)
        ev.configDefined.disconnect(self._on_configs_changed)
        ev.configDeleted.disconnect(self._on_configs_changed)
        ev.channelGroupChanged.disconnect(self._on_channel_group_changed)
        ev.propertyChanged.disconnect(self._on_property_changed)
        ev.configSet.disconnect(self._on_config_set)
