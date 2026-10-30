"""Per-preset channel settings: exposure, light source and camera properties."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, TypedDict, cast

from pymmcore_plus import CMMCorePlus, PropertyType
from qtpy.QtCore import QEvent, QObject, QPointF, Qt, Signal
from qtpy.QtGui import QBrush, QColor, QPainter, QPen
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSlider,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked

from pymmcore_widgets._light_sources import (
    INTENSITY_KEY,
    LIGHT_SOURCE_KEY,
    NO_LIGHT_SOURCE,
    IntensitySpinBox,
    find_light_sources,
    light_source_limits,
)
from pymmcore_widgets._util import YELLOW, disable_wheel_scroll
from pymmcore_widgets.useq_widgets import ComboColumn
from pymmcore_widgets.useq_widgets._column_info import (
    ColumnInfo,
    FloatColumn,
    TextColumn,
    WdgGetSet,
    WidgetColumn,
)
from pymmcore_widgets.useq_widgets._data_table import DataTableWidget

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from qtpy.QtCore import QModelIndex, SignalInstance
    from qtpy.QtWidgets import QStyleOptionViewItem

DEFAULT_EXP = 100.0
CURRENT_KEY = "_current"
PRESET_KEY = "config"
EXPOSURE_KEY = "exposure"
# prefixes a camera property name to make its column key
CAMERA_KEY_PREFIX = "camera:"

# Camera properties that may sensibly differ from one channel to the next, gathered
# from the Micro-Manager camera adapters (standard keywords first, then
# adapter-specific names: Andor, AndorSDK3, PVCAM, PICAM, PCO, Hamamatsu, ...).
# Only those the current camera has, and that can be written after initialization,
# become columns. Binning, PixelType and BitDepth are deliberately left out: they
# change the image shape or dtype, which an acquisition cannot mix across channels.
CAMERA_PROPERTIES: tuple[str, ...] = (
    "Gain",  # MM keyword; EM gain on Andor, gain index on PVCAM
    "EMGain",  # MM keyword
    "EMSwitch",  # Andor
    "MultiplierGain",  # PVCAM, PICAM (EM gain)
    "EMIccdGain",  # PICAM
    "MCPGain",  # AndorSDK3 (iStar)
    "IntensifierGain",
    "DDGGain",  # Andor (iStar)
    "Pre-Amp-Gain",  # Andor
    "SimplePreAmpGainControl",  # AndorSDK3
    "GainMode",  # AndorSDK3
    "Offset",  # MM keyword
    "BlackLevel",  # GenICam-style (Basler, Spinnaker, ...)
    "ReadoutMode",  # MM keyword
    "ReadoutRate",  # PVCAM, PICAM, TSI
    "PixelReadoutRate",  # AndorSDK3
    "PixelRate",  # PCO
    "ScanMode",  # Hamamatsu, DemoCamera
    "Output_Amplifier",  # Andor
    "Port",  # PVCAM readout port
)


class ChannelSettings(TypedDict):
    """The settings stored for one channel preset.

    Attributes
    ----------
    exposure : float
        Exposure time in ms.
    light_source : str
        Light source label, as in
        [`CoreConnectedChannelTable.lightSources`][pymmcore_widgets.mda.CoreConnectedChannelTable.lightSources]
        (`""` for none).
    intensity : float
        Value for the light source property (or properties).
    camera_properties : dict[str, float | int | str]
        `{property name: value}` for the camera device.
    """

    exposure: float
    light_source: str
    intensity: float
    camera_properties: dict[str, float | int | str]


class IntensitySlider(QWidget):
    """A slider next to an [`IntensitySpinBox`][], ranged by the light source."""

    valueChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        disable_wheel_scroll(self._slider)
        self._spin = IntensitySpinBox()
        # slider steps per unit of the spin box value
        self._scale = 1

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._spin)

        self._slider.valueChanged.connect(self._on_slider_changed)
        self._spin.valueChanged.connect(self._on_spin_changed)
        self.setPropertyLimits(NO_LIGHT_SOURCE, None, False)

    def group(self) -> str:
        """Return the light source this widget is currently configured for."""
        return self._spin.group()

    def setPropertyLimits(
        self, group: str, limits: tuple[float, float] | None, is_integer: bool
    ) -> None:
        """Configure the slider and spin box for `group`'s device property."""
        with signals_blocked(self._spin), signals_blocked(self._slider):
            self._spin.setPropertyLimits(group, limits, is_integer)
            self._slider.setEnabled(limits is not None)
            self._scale = 1 if is_integer else 10 ** self._spin.decimals()
            lower, upper = limits or (0, 0)
            self._slider.setRange(
                round(lower * self._scale), round(upper * self._scale)
            )
            self._slider.setValue(round(self._spin.value() * self._scale))

    def value(self) -> float:
        return self._spin.value()

    def setValue(self, value: float) -> None:
        self._spin.setValue(value)

    def _on_slider_changed(self, value: int) -> None:
        self._spin.setValue(value / self._scale)

    def _on_spin_changed(self, value: float) -> None:
        with signals_blocked(self._slider):
            self._slider.setValue(round(value * self._scale))
        self.valueChanged.emit(value)


TableIntensitySlider = WdgGetSet(
    IntensitySlider,
    IntensitySlider.value,
    IntensitySlider.setValue,
    lambda w, cb: w.valueChanged.connect(cb),
)


@dataclass(frozen=True)
class IntensitySliderColumn(WidgetColumn):
    """Column of intensity sliders, ranged per row by the row's light source."""

    data_type: WdgGetSet = TableIntensitySlider


@dataclass(frozen=True)
class _PresetColumn(TextColumn):
    """Read-only preset names: the rows are the group's presets, fixed."""

    def init_cell(
        self, table: QTableWidget, row: int, col: int, change_signal: SignalInstance
    ) -> None:
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        table.setItem(row, col, item)


_ACTIVE_ROLE = Qt.ItemDataRole.UserRole
_CURRENT_COL_WIDTH = 30
# fitting a column to its contents never makes it wider than this (e.g. a light
# source combo sized for its longest choice); the user can still widen it
_MAX_FIT_WIDTH = 220
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
    camera has (see `CAMERA_PROPERTIES`). The camera property columns are hidden
    until the *Advanced* checkbox is checked; see `setAdvancedVisible`.

    Settings are remembered for every group, not just the one shown, so switching
    back to a group restores what was entered for it. Use
    [`channelSettings`][pymmcore_widgets.ChannelSettingsTable.channelSettings]
    to look up the settings of a preset (e.g. to fill in a channel added to an MDA),
    and `allChannelSettings` / `setAllChannelSettings` to persist them.

    The first column marks the preset currently active on the microscope with a
    filled dot (kept in sync with the core); clicking another row's ring applies
    that preset and its settings with `applyChannel`.

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
    INTENSITY = IntensitySliderColumn(key=INTENSITY_KEY, header="Intensity", default=0.0)  # noqa: E501
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
        # the camera property columns are hidden by default; see setAdvancedVisible
        self._advanced_visible = False
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

        self.advanced = QCheckBox("Advanced")
        self.advanced.setToolTip(
            "Show the per-channel camera property columns (e.g. Gain, Offset, "
            "Readout Mode).\n"
            "While unchecked those columns are hidden, but keep their values."
        )
        self.advanced.setChecked(self._advanced_visible)
        self.advanced.toggled.connect(self.setAdvancedVisible)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(5, 5, 5, 5)
        btn_row.addWidget(self.advanced)
        btn_row.addStretch()
        cast("QVBoxLayout", self.layout()).addLayout(btn_row)

        # update the stored settings before anyone is told of the change
        self._table.valueChanged.disconnect(self.valueChanged)
        self._table.valueChanged.connect(self._on_table_changed)

        self._current_delegate = CurrentPresetDelegate(self._table)
        self._table.cellClicked.connect(self._on_cell_clicked)
        # Qt makes a row "current" when one of its editors gains focus; keep the
        # row highlight on the active preset instead
        if (sel_model := self._table.selectionModel()) is not None:
            sel_model.currentRowChanged.connect(self._on_current_row_changed)

        # columns are fitted to their contents and resizable, except the current
        # column; leftover width is spread over them so the table fills the width,
        # and whatever the user frees or takes by dragging goes to the last column
        self._sizing = False  # True while we set column widths ourselves
        if (header := self._table.horizontalHeader()) is not None:
            header.setStretchLastSection(False)
            header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            header.sectionResized.connect(self._on_section_resized)
        if (viewport := self._table.viewport()) is not None:
            viewport.installEventFilter(self)

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
        return self._group_combo.currentText()

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
        if (settings := self._settings.get(group, {}).get(preset)) is None:
            return
        self._mmc.setExposure(settings["exposure"])
        for device, prop in self._light_sources.get(settings["light_source"], ()):
            intensity: float = settings["intensity"]
            if self._mmc.getPropertyType(device, prop) is PropertyType.Integer:
                intensity = int(intensity)
            self._mmc.setProperty(device, prop, intensity)
        if camera := self._mmc.getCameraDevice():
            for prop, value in settings["camera_properties"].items():
                if self._mmc.hasProperty(
                    camera, prop
                ) and not self._mmc.isPropertyReadOnly(camera, prop):
                    self._mmc.setProperty(camera, prop, value)

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

        Same labels as
        [`CoreConnectedChannelTable.lightSources`][pymmcore_widgets.mda.CoreConnectedChannelTable.lightSources].
        """
        return dict(self._light_sources)

    def setAdvancedVisible(self, visible: bool) -> None:
        """Show or hide the camera property columns. Off by default.

        This is a pure view toggle: the columns keep their values, which are stored
        and applied regardless.
        """
        self._advanced_visible = bool(visible)
        with signals_blocked(self.advanced):
            self.advanced.setChecked(self._advanced_visible)
        self._apply_advanced_visibility()
        self._fit_columns()

    def advancedVisible(self) -> bool:
        """Return whether the camera property columns are shown."""
        return self._advanced_visible

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
        self._camera_columns = self._make_camera_columns()

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
        self._sizing = True  # widths are fitted once the rows are in, below
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
            self._apply_advanced_visibility()
            current_col = table.indexOf(self.CURRENT)
            table.setItemDelegateForColumn(current_col, self._current_delegate)
            if (header := table.horizontalHeader()) is not None:
                header.setSectionResizeMode(current_col, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(current_col, _CURRENT_COL_WIDTH)
            if header_item := table.horizontalHeaderItem(current_col):
                header_item.setToolTip("Preset currently active on the microscope")
        self._sizing = False
        self._populate()

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if (
            a1 is not None
            and a1.type() == QEvent.Type.Resize
            and a0 is self._table.viewport()
        ):
            self._fill_width()
        return False

    # ------------------- Private API -------------------

    def _resizable_columns(self) -> list[int]:
        table = self._table
        current_col = table.indexOf(self.CURRENT)
        return [
            col
            for col in range(table.columnCount())
            if col != current_col and not table.isColumnHidden(col)
        ]

    def _used_width(self) -> int:
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

    def _content_width(self, col: int) -> int:
        table = self._table
        header = cast("QHeaderView", table.horizontalHeader())
        hint = max(table.sizeHintForColumn(col), header.sectionSizeHint(col))
        return min(hint, _MAX_FIT_WIDTH)

    def _fit_columns(self) -> None:
        """Size the resizable columns to their contents, then fill the width."""
        self._sizing = True
        try:
            for col in self._resizable_columns():
                self._table.setColumnWidth(col, self._content_width(col))
        finally:
            self._sizing = False
        self._fill_width()

    def _on_section_resized(self, col: int, old: int, new: int) -> None:
        """Let the last column absorb a user's drag, so the table keeps its width.

        The last column never shrinks below its contents: a column dragged wider
        than that allows stops there instead of pushing columns out of view.
        """
        cols = self._resizable_columns()
        if self._sizing or col not in cols:
            return
        table = self._table
        if (viewport := table.viewport()) is None:  # pragma: no cover
            return
        last = cols[-1]
        min_last = self._content_width(last)
        width = table.columnWidth(last) + viewport.width() - self._used_width()
        self._sizing = True
        try:
            if width < min_last and col != last:
                overflow = min_last - width
                capped = max(self._content_width(col), new - overflow)
                table.setColumnWidth(col, min(new, capped))
                width = table.columnWidth(last) + viewport.width() - self._used_width()
            width = max(min_last, width)
            if width != table.columnWidth(last):
                table.setColumnWidth(last, width)
        finally:
            self._sizing = False

    def _fill_width(self) -> None:
        """Grow or shrink the resizable columns so they span the viewport.

        Extra width is shared evenly; when narrowing, columns give up only the width
        they have beyond their contents, so they never get cut off.
        """
        table = self._table
        if (viewport := table.viewport()) is None:  # pragma: no cover
            return
        cols = self._resizable_columns()
        if not cols:
            return
        extra = viewport.width() - self._used_width()
        self._sizing = True
        try:
            self._spread(cols, extra)
        finally:
            self._sizing = False

    def _spread(self, cols: list[int], extra: int) -> None:
        table = self._table
        if extra > 0:
            share, rest = divmod(extra, len(cols))
            for i, col in enumerate(cols):
                bonus = share + (1 if i < rest else 0)
                table.setColumnWidth(col, table.columnWidth(col) + bonus)
        elif extra < 0:
            slack = {
                col: max(0, table.columnWidth(col) - self._content_width(col))
                for col in cols
            }
            total = sum(slack.values())
            if total == 0:
                return
            cut = min(-extra, total)
            for col, s in slack.items():
                table.setColumnWidth(col, table.columnWidth(col) - cut * s // total)

    def _apply_advanced_visibility(self) -> None:
        table = self._table
        for info in self._camera_columns.values():
            if (col := table.indexOf(info)) >= 0:
                table.setColumnHidden(col, not self._advanced_visible)

    def _make_camera_columns(self) -> dict[str, ColumnInfo]:
        columns: dict[str, ColumnInfo] = {}
        if not (cam := self._camera):
            return columns
        mmc = self._mmc
        for prop in self._camera_property_names:
            if (
                not mmc.hasProperty(cam, prop)
                or mmc.isPropertyReadOnly(cam, prop)
                or mmc.isPropertyPreInit(cam, prop)
            ):
                continue
            key = f"{CAMERA_KEY_PREFIX}{prop}"
            ptype = mmc.getPropertyType(cam, prop)
            if allowed := mmc.getAllowedPropertyValues(cam, prop):
                if len(allowed) < 2:  # nothing to choose from
                    continue
                columns[prop] = ComboColumn(
                    key=key, header=prop, allowed_values=tuple(allowed)
                )
            elif ptype in (PropertyType.Integer, PropertyType.Float):
                if mmc.hasPropertyLimits(cam, prop):
                    lower = mmc.getPropertyLowerLimit(cam, prop)
                    upper = mmc.getPropertyUpperLimit(cam, prop)
                else:
                    lower, upper = -999_999, 999_999
                columns[prop] = FloatColumn(
                    key=key,
                    header=prop,
                    minimum=lower,
                    maximum=upper,
                    decimals=0 if ptype is PropertyType.Integer else 2,
                )
        return columns

    def _camera_value(self, prop: str, value: Any) -> float | int | str:
        """Cast a cell (or core) value to what is stored for camera property `prop`.

        Props with allowed values keep their string (it must match the choice
        exactly, e.g. "1.0000"); numeric ones are numbers.
        """
        if isinstance(self._camera_columns.get(prop), ComboColumn):
            return str(value)
        if self._mmc.getPropertyType(self._camera, prop) is PropertyType.Integer:
            return int(float(value))
        return float(value)

    def _default_settings(self) -> ChannelSettings:
        return ChannelSettings(
            exposure=self._mmc.getExposure() if self._camera else DEFAULT_EXP,
            light_source=NO_LIGHT_SOURCE,
            intensity=0.0,
            camera_properties={},
        )

    def _populate(self, emit: bool = True) -> None:
        """Fill the table with the shown group's presets and their settings."""
        group = self.channelGroup()
        presets = list(self._mmc.getAvailableConfigs(group)) if group else []
        stored = self._settings.get(group, {})
        table = self._table
        with signals_blocked(table):
            table.setRowCount(0)
            table.setRowCount(len(presets))
            for row, preset in enumerate(presets):
                self._set_row(
                    row, preset, stored.get(preset) or self._default_settings()
                )
        self._update_active_row()
        self._fit_columns()
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
            raw = stored_cam.get(prop)
            if raw is None:
                raw = self._mmc.getProperty(self._camera, prop)
            value = self._camera_value(prop, raw)
            col = table.indexOf(info)
            if isinstance(info, ComboColumn):
                info.set_cell_data(table, row, col, str(value))
            else:
                info.set_cell_data(table, row, col, float(value))

    def _configure_intensity(self, row: int, col: int, source: str) -> None:
        """Range the slider at `row` for `source`, starting at its current value."""
        wdg = cast("IntensitySlider | None", self._table.cellWidget(row, col))
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
        camera_properties = {
            prop: self._camera_value(prop, rec[info.key])
            for prop, info in self._camera_columns.items()
            if info.key in rec
        }
        return str(rec.get(PRESET_KEY) or ""), ChannelSettings(
            exposure=float(rec.get(EXPOSURE_KEY) or DEFAULT_EXP),
            light_source=str(rec.get(LIGHT_SOURCE_KEY) or NO_LIGHT_SOURCE),
            intensity=float(rec.get(INTENSITY_KEY) or 0.0),
            camera_properties=camera_properties,
        )

    def _on_table_changed(self, emit: bool = True) -> None:
        """Re-range intensity sliders for new light sources and store the rows."""
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
                    wdg = cast("IntensitySlider | None", table.cellWidget(row, int_col))
                    if wdg is not None and wdg.group() != source:
                        self._configure_intensity(row, int_col, source)

            if group := self.channelGroup():
                stored = self._settings.setdefault(group, {})
                for row in range(table.rowCount()):
                    preset, settings = self._row_settings(row)
                    # keep values of properties another camera had
                    if old := stored.get(preset):
                        settings["camera_properties"] = {
                            **old["camera_properties"],
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
        if row < 0:
            table.clearSelection()
            if (sel_model := table.selectionModel()) is not None:
                sel_model.clearCurrentIndex()
            return
        table.setCurrentCell(row, col)
        table.selectRow(row)

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
        if col == self._table.indexOf(self.CURRENT):
            self.applyChannel(self._preset_at(row))
            self._update_active_row()

    def _on_current_row_changed(self, current: QModelIndex, *_: Any) -> None:
        if current.row() != self._active_row:
            self._set_active_row(self._active_row)

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
