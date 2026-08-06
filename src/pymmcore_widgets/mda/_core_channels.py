from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import useq
from pymmcore_plus import CMMCorePlus, PropertyType
from qtpy.QtCore import Slot
from qtpy.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget
from superqt.utils import signals_blocked

from pymmcore_widgets.useq_widgets import ChannelTable, ComboColumn
from pymmcore_widgets.useq_widgets._column_info import (
    TableDoubleSpinBox,
    WdgGetSet,
    WidgetColumn,
)

from ._channel_properties import ChannelProperty

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

DEFAULT_EXP = 100.0

# keys of the columns that are *not* useq.Channel fields
LIGHT_SOURCE_KEY = "light_source"
INTENSITY_KEY = "intensity"
_EXTRA_KEYS = frozenset({LIGHT_SOURCE_KEY, INTENSITY_KEY})

# value of the light source combo meaning "this channel sets no property"
NO_LIGHT_SOURCE = ""

# joins device label and property name into a light source combo entry
PROPERTY_SEPARATOR = " · "


class IntensitySpinBox(TableDoubleSpinBox):
    """Spin box for a light source value, ranged from the property's limits.

    The property differs from row to row (and may be absent), so unlike the other
    numeric columns the range/decimals are configured per cell rather than on the
    column.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # the light source group this spin box is currently configured for, used to
        # avoid needlessly re-ranging (which can clobber a value the user is editing)
        self._group: str = NO_LIGHT_SOURCE
        self.setEnabled(False)

    def group(self) -> str:
        """Return the light source group this spin box is configured for."""
        return self._group

    def setPropertyLimits(
        self, group: str, limits: tuple[float, float] | None, is_integer: bool
    ) -> None:
        """Configure this spin box for `group`'s underlying device property."""
        self._group = group
        if limits is None:
            self.setEnabled(False)
            self.setRange(0, 0)
            return

        self.setEnabled(True)
        self.setDecimals(0 if is_integer else 2)
        self.setRange(*limits)


TableIntensityWidget = WdgGetSet(
    IntensitySpinBox,
    IntensitySpinBox.value,
    IntensitySpinBox.setValue,
    lambda w, cb: w.valueChanged.connect(cb),
)


@dataclass(frozen=True)
class IntensityColumn(WidgetColumn):
    """Column of intensity spin boxes, ranged per row by the row's light source."""

    data_type: WdgGetSet = TableIntensityWidget


class CoreConnectedChannelTable(ChannelTable):
    """[ChannelTable](../ChannelTable#) connected to a Micro-Manager core instance.

    In addition to the standard [`useq.Channel`][] fields, this table has *Light
    Source* and *Intensity* columns, which let each channel set a single device
    property (typically an illumination intensity) while it is being acquired.

    The *Light Source* drop-down lists every writable numeric device property that
    has limits, as `"<device> · <property>"`. Which of those actually drives a light
    source cannot be known ahead of time -- the property name differs per adapter --
    so all of them are offered and the user picks. Choices are kept in sync with the
    core's loaded devices.

    The feature is on by default; use
    [`setLightSourceVisible`][pymmcore_widgets.mda.CoreConnectedChannelTable.setLightSourceVisible]
    to disable it. While off, both columns are hidden and set no properties.

    These values are not part of `useq.Channel`; see
    [`MDAWidget.value`][pymmcore_widgets.MDAWidget.value] for how they are carried on
    the sequence and applied at acquisition time.

    !!! note
        Within a hardware-sequenced batch, `pymmcore_plus`'s event combiner treats a
        property that an event does not mention as static, applying the first event's
        value for the whole batch. So if some channels set a light source and others
        do not, a sequenced batch may leave the previous channel's value applied.

    Parameters
    ----------
    rows : int
        Number of rows to initialize the table with, by default 0.
    mmcore : CMMCorePlus | None
        Optional [`CMMCorePlus`][pymmcore_plus.CMMCorePlus] micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new)
        [`CMMCorePlus.instance`][pymmcore_plus.core._mmcore_plus.CMMCorePlus.instance].
    parent : QWidget | None
        Optional parent widget, by default None.
    """

    # fmt: off
    LIGHT_SOURCE = ComboColumn(key=LIGHT_SOURCE_KEY, header="Light Source", default=NO_LIGHT_SOURCE, allowed_values=(NO_LIGHT_SOURCE,))  # noqa: E501
    INTENSITY = IntensityColumn(key=INTENSITY_KEY, header="Intensity [%]", default=0.0)
    # fmt: on

    def __init__(
        self,
        rows: int = 0,
        mmcore: CMMCorePlus | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(rows, parent)
        self._mmc = mmcore or CMMCorePlus.instance()

        # {combo label -> [(device, property), ...]} for the selectable properties.
        # Single-property sources have a one-element list; single-preset config-group
        # sources list every (device, property) in the preset (intensity is broadcast).
        self._light_sources: dict[str, list[tuple[str, str]]] = {}
        self._light_source_column: ComboColumn = self.LIGHT_SOURCE
        # guards _sync_intensity_widgets against re-entrancy
        self._syncing_intensity = False
        # the extra columns are opt-in by default visibility; see setLightSourceVisible
        self._light_source_visible = True
        # the advanced columns (Do Stack / Z Offset) are hidden by default; see
        # setAdvancedVisible. Do Stack additionally requires the Z-stack axis to
        # be active, tracked here and updated by the owning MDA tab widget.
        self._advanced_visible = False
        self._z_stack_active = False

        self.show_light_source = QCheckBox("Show Light Source")
        self.show_light_source.setToolTip(
            "Set a device property (e.g. a light source intensity) per channel, via "
            "the Light Source and Intensity columns.\n"
            "While unchecked those columns are hidden and set no properties."
        )
        self.show_light_source.setChecked(self._light_source_visible)
        self.show_light_source.toggled.connect(self.setLightSourceVisible)

        self.advanced = QCheckBox("Advanced")
        self.advanced.setToolTip(
            "Show the advanced per-channel columns: Z Offset, and Do Stack (the "
            "latter only while the Z Stack axis is active).\n"
            "While unchecked those columns are hidden."
        )
        self.advanced.setChecked(self._advanced_visible)
        self.advanced.toggled.connect(self.setAdvancedVisible)

        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(15)
        self._btn_row.addWidget(self.show_light_source)
        self._btn_row.addWidget(self.advanced)
        self._btn_row.addStretch()
        cast("QVBoxLayout", self.layout()).addLayout(self._btn_row)

        # connections
        self._mmc.events.systemConfigurationLoaded.connect(self._on_configs_changed)
        self._mmc.events.configGroupDeleted.connect(self._on_configs_changed)
        self._mmc.events.configDefined.connect(self._on_configs_changed)
        self._mmc.events.configDeleted.connect(self._on_configs_changed)
        self._mmc.events.channelGroupChanged.connect(self._update_channel_groups)
        self.valueChanged.connect(self._sync_intensity_widgets)

        self.destroyed.connect(self._disconnect)

        self._position_extra_columns()
        self._apply_advanced_visibility()
        self.refresh()

    # ------------------- public API -------------------

    def setLightSourceVisible(self, visible: bool) -> None:
        """Enable or disable the per-channel light source feature.

        This is an on/off switch, not just a view toggle: it shows/hides the *Light
        Source* and *Intensity* columns **and** determines whether they have any
        effect. While off, `channelProperties` is empty and no device property is
        applied. On by default.

        Turning it off keeps whatever the columns hold, so turning it back on
        restores the previous selections.
        """
        self._light_source_visible = bool(visible)
        with signals_blocked(self.show_light_source):
            self.show_light_source.setChecked(self._light_source_visible)
        self._apply_light_source_visibility()
        self.valueChanged.emit()

    def lightSourceVisible(self) -> bool:
        """Return whether the per-channel light source feature is enabled."""
        return self._light_source_visible

    def setAdvancedVisible(self, visible: bool) -> None:
        """Show or hide the advanced per-channel columns (Z Offset and Do Stack).

        This is a pure view toggle: the columns always keep their values (used at
        acquisition time regardless), it only controls whether they are shown. The
        *Do Stack* column is additionally shown only while the Z-stack axis is
        active. Off by default.
        """
        self._advanced_visible = bool(visible)
        with signals_blocked(self.advanced):
            self.advanced.setChecked(self._advanced_visible)
        self._apply_advanced_visibility()

    def advancedVisible(self) -> bool:
        """Return whether the advanced per-channel columns are shown."""
        return self._advanced_visible

    def setZStackActive(self, active: bool) -> None:
        """Record whether the Z-stack axis is active.

        The *Do Stack* column is only meaningful with a Z-stack, so it is shown
        only when the advanced columns are visible *and* the axis is active. The
        owning MDA tab widget calls this as the Z-stack axis is toggled.
        """
        self._z_stack_active = bool(active)
        self._apply_advanced_visibility()

    def refresh(self) -> None:
        """Re-read the channel groups and light sources from the core.

        Both are normally kept up to date by core signals, so calling this is not
        usually necessary. It exists for applications that rewrite the core's config
        groups in bulk with those signals suppressed (e.g. inside a
        `pymmcore_plus.core.events.block_core` block) and so cannot notify listeners
        the usual way; such an application should call this when the widget is next
        shown.
        """
        self._update_channel_groups()
        self._update_light_sources()

    def value(self, exclude_unchecked: bool = True) -> tuple[useq.Channel, ...]:
        """Return the current value of the table as a tuple of [useq.Channels](https://pymmcore-plus.github.io/useq-schema/schema/axes/#useq.Channel).

        The light source and intensity columns are not `useq.Channel` fields; use
        [`channelProperties`][pymmcore_widgets.mda.CoreConnectedChannelTable.channelProperties]
        to retrieve those.

        Parameters
        ----------
        exclude_unchecked : bool, optional
            Exclude unchecked rows, by default True
        """
        return tuple(
            useq.Channel(**{k: v for k, v in rec.items() if k not in _EXTRA_KEYS})
            for rec in self.table().iterRecords(exclude_unchecked=exclude_unchecked)
        )

    def channelProperties(
        self, exclude_unchecked: bool = True
    ) -> list[ChannelProperty]:
        """Return the device property set by each channel that has a light source.

        Empty while the feature is off (see `setLightSourceVisible`). Otherwise
        entries are sparse: channels with no light source selected are omitted. Each
        entry's `channel_index` indexes into the tuple returned by `value()` called
        with the same `exclude_unchecked`.
        """
        if not self._light_source_visible:
            return []

        props: list[ChannelProperty] = []
        records = self.table().iterRecords(exclude_unchecked=exclude_unchecked)
        for idx, rec in enumerate(records):
            group = rec.get(LIGHT_SOURCE_KEY) or NO_LIGHT_SOURCE
            if group not in self._light_sources:
                continue
            dev_props = self._light_sources[group]
            value = float(rec.get(INTENSITY_KEY) or 0.0)
            for device, prop in dev_props:
                cast_value: float | int = (
                    int(value)
                    if self._mmc.getPropertyType(device, prop) is PropertyType.Integer
                    else value
                )
                props.append(
                    ChannelProperty(
                        channel_index=idx,
                        config=str(rec.get("config", "")),
                        group=group,
                        device=device,
                        property=prop,
                        value=cast_value,
                    )
                )
        return props

    def setChannelProperties(self, value: Iterable[ChannelProperty]) -> None:
        """Restore the light source and intensity columns from `channelProperties`.

        Like [`DataTableWidget.setValue`][], this does not itself emit
        `valueChanged` - it is meant to be called as part of restoring state (e.g.
        from `MDAWidget.setValue`), not as a user edit.
        """
        table = self.table()
        ls_col = table.indexOf(self._light_source_column)
        int_col = table.indexOf(self.INTENSITY)
        if ls_col < 0 or int_col < 0:  # pragma: no cover
            return

        labels = {
            dev_prop: label
            for label, dev_props in self._light_sources.items()
            for dev_prop in dev_props
        }

        with signals_blocked(self):
            for entry in value:
                row = entry["channel_index"]
                if not (0 <= row < table.rowCount()):  # pragma: no cover
                    continue
                # Prefer the saved `group` when it is still a light source that
                # actually offers this (device, property): a single (device,
                # property) can be reachable under more than one label -- e.g. a
                # single-preset config group *and* the synthesized "Device ·
                # Property" entry both wrap it -- and `labels` is a plain dict,
                # so it silently keeps whichever of them `_light_sources`
                # happens to yield last. Resolving on `group` first makes the
                # round trip lossless and independent of that ordering.
                #
                # Otherwise fall back to (device, property), which is what
                # actually gets applied, so a sequence saved under a group that
                # no longer exists still restores its property rather than
                # being silently dropped.
                dev_prop = (entry["device"], entry["property"])
                group = entry["group"]
                if dev_prop in self._light_sources.get(group, ()):
                    label = group
                elif (resolved := labels.get(dev_prop)) is not None:
                    label = resolved
                elif group in self._light_sources:
                    label = group
                else:
                    continue
                self._light_source_column.set_cell_data(table, row, ls_col, label)
                # range must be set before the value, or it would be clamped away
                self._configure_intensity_widget(row, int_col, label)
                self.INTENSITY.set_cell_data(table, row, int_col, entry["value"])

    def lightSources(self) -> Mapping[str, list[tuple[str, str]]]:
        """Return available light sources as ``{label: [(device, property), ...]}``.

        Single-property sources (keyed ``"<device> · <property>"``) have a
        one-element list. Single-preset config-group sources are keyed by the
        group name and list every ``(device, property)`` pair in the preset --
        setting the intensity broadcasts the same value to all of them.
        """
        return dict(self._light_sources)

    # ------------------- Private API -------------------

    def _position_extra_columns(self) -> None:
        """Move the light source/intensity columns to just after Exposure.

        `DataTableWidget.__init_subclass__` orders columns by first appearance in the
        MRO, so columns declared here would otherwise land at the end of the table,
        after the (less frequently used) acquire_every/do_stack/z_offset ones.
        """
        table = self.table()
        if (exposure_col := table.indexOf(self.EXPOSURE)) < 0:  # pragma: no cover
            return

        with signals_blocked(self):
            for offset, info in enumerate(
                (self._light_source_column, self.INTENSITY), start=1
            ):
                current = table.indexOf(info)
                target = exposure_col + offset
                if current < 0 or current == target:  # pragma: no cover
                    continue
                table.removeColumn(current)
                table.addColumn(info, target)

    @Slot()
    def _on_configs_changed(self, *_: Any) -> None:
        # accepts (and ignores) the varying args of the config* core signals
        self.refresh()

    @Slot()
    def _update_channel_groups(self) -> None:
        """Update the channel groups when the system configuration is loaded."""
        self.setChannelGroups(
            {
                group: self._mmc.getAvailableConfigs(group)
                for group in self._mmc.getAvailableConfigGroups()
            }
        )

        ch_group = self._mmc.getChannelGroup()
        if ch_group and ch_group in self.channelGroups():
            self._group_combo.setCurrentText(ch_group)

    def _find_light_sources(self) -> dict[str, list[tuple[str, str]]]:
        """Return writable numeric range properties and eligible config groups.

        **Individual properties** — any writable, non-pre-init numeric (Integer
        or Float) property that has limits.  Keyed as ``"<device> · <property>"``.

        **Single-preset group sources** — a config group that has exactly one
        preset where *every* property in that preset is a writable, non-pre-init
        numeric property with limits.  Keyed by the group name.  Setting the
        intensity for such a group broadcasts the same value to all properties
        in the preset, so a multi-slider light source (e.g. Lumencor LIDA) can
        be driven by a single spin box.

        Micro-Manager gives no way to know ahead of time which property drives a
        light source -- the name varies by adapter (`Intensity`, `White_Level`,
        `Power`, ...) -- so any property that can be swept over a range is offered
        and the user picks the right one. Pre-init properties are excluded: they
        cannot be changed once the device is initialized.
        """
        # --- per-property sources ---
        properties = self._mmc.iterProperties(
            property_type=(PropertyType.Integer, PropertyType.Float),
            has_limits=True,
            is_read_only=False,
            as_object=False,
        )
        pairs = sorted(
            (
                (str(device), str(prop))
                for device, prop in properties
                if not self._mmc.isPropertyPreInit(device, prop)
            ),
            key=lambda pair: (pair[0].casefold(), pair[1].casefold()),
        )
        sources: dict[str, list[tuple[str, str]]] = {
            f"{dev}{PROPERTY_SEPARATOR}{prop}": [(dev, prop)] for dev, prop in pairs
        }

        # --- single-preset config-group sources ---
        for group in self._mmc.getAvailableConfigGroups():
            presets = self._mmc.getAvailableConfigs(group)
            if len(presets) != 1:
                continue
            (preset,) = presets
            dev_props: list[tuple[str, str]] = []
            valid = True
            try:
                for device, prop, _value in self._mmc.getConfigData(group, preset):
                    ptype = self._mmc.getPropertyType(device, prop)
                    if (
                        ptype not in (PropertyType.Integer, PropertyType.Float)
                        or not self._mmc.hasPropertyLimits(device, prop)
                        or self._mmc.isPropertyReadOnly(device, prop)
                        or self._mmc.isPropertyPreInit(device, prop)
                    ):
                        valid = False
                        break
                    dev_props.append((str(device), str(prop)))
            except RuntimeError:
                # A device referenced by the group has been unloaded; skip.
                valid = False
            if valid and dev_props and group not in sources:
                sources[group] = dev_props

        return dict(sorted(sources.items(), key=lambda kv: kv[0].casefold()))

    def _apply_light_source_visibility(self) -> None:
        table = self.table()
        for info in (self._light_source_column, self.INTENSITY):
            if (col := table.indexOf(info)) >= 0:
                table.setColumnHidden(col, not self._light_source_visible)

    def _apply_advanced_visibility(self) -> None:
        table = self.table()
        if (z_off_col := table.indexOf(self.Z_OFFSET)) >= 0:
            table.setColumnHidden(z_off_col, not self._advanced_visible)
        if (do_stack_col := table.indexOf(self.DO_STACK)) >= 0:
            show_do_stack = self._advanced_visible and self._z_stack_active
            table.setColumnHidden(do_stack_col, not show_do_stack)

    def _update_light_sources(self) -> None:
        """Rebuild the light source column's choices from the current configuration."""
        self._light_sources = self._find_light_sources()

        table = self.table()
        ls_col = table.indexOf(self._light_source_column)
        if ls_col < 0:  # pragma: no cover
            return

        # swap in a column with the new choices (same approach as _on_group_changed)
        with signals_blocked(self):
            table.removeColumn(ls_col)
            self._light_source_column = ComboColumn(
                key=LIGHT_SOURCE_KEY,
                header="Light Source",
                default=NO_LIGHT_SOURCE,
                allowed_values=(NO_LIGHT_SOURCE, *self._light_sources),
            )
            table.addColumn(self._light_source_column, ls_col)
            # the fresh column defaults to visible; restore the user's choice
            self._apply_light_source_visibility()
            self._sync_intensity_widgets(force=True)
        self.valueChanged.emit()

    def _configure_intensity_widget(self, row: int, col: int, group: str) -> None:
        """Range the spin box at `row` for `group`'s property or properties."""
        wdg = cast("IntensitySpinBox | None", self.table().cellWidget(row, col))
        if wdg is None:  # pragma: no cover
            return
        if (dev_props := self._light_sources.get(group)) is None:
            wdg.setPropertyLimits(NO_LIGHT_SOURCE, None, False)
            return
        # For a multi-property group, use the intersection of all limits so the
        # single spin-box value is always valid for every underlying property.
        lower = max(
            self._mmc.getPropertyLowerLimit(dev, prop) for dev, prop in dev_props
        )
        upper = min(
            self._mmc.getPropertyUpperLimit(dev, prop) for dev, prop in dev_props
        )
        is_int = all(
            self._mmc.getPropertyType(dev, prop) is PropertyType.Integer
            for dev, prop in dev_props
        )
        wdg.setPropertyLimits(group, (lower, upper), is_int)

    @Slot()
    def _sync_intensity_widgets(self, force: bool = False) -> None:
        """Re-range each row's intensity spin box to match its light source.

        Driven by `valueChanged` rather than by wiring each combo box, so that it
        keeps working across row insertion *and* wholesale column rebuilds.
        """
        if self._syncing_intensity:
            return

        table = self.table()
        ls_col = table.indexOf(self._light_source_column)
        int_col = table.indexOf(self.INTENSITY)
        if ls_col < 0 or int_col < 0:  # pragma: no cover
            return

        self._syncing_intensity = True
        try:
            with signals_blocked(table):
                for row in range(table.rowCount()):
                    data = self._light_source_column.get_cell_data(table, row, ls_col)
                    group = data.get(LIGHT_SOURCE_KEY) or NO_LIGHT_SOURCE
                    wdg = cast(
                        "IntensitySpinBox | None", table.cellWidget(row, int_col)
                    )
                    if wdg is None:  # pragma: no cover
                        continue
                    if force or wdg.group() != group:
                        self._configure_intensity_widget(row, int_col, group)
        finally:
            self._syncing_intensity = False

    def _disconnect(self) -> None:
        """Disconnect from the core instance."""
        self._mmc.events.systemConfigurationLoaded.disconnect(self._on_configs_changed)
        self._mmc.events.configGroupDeleted.disconnect(self._on_configs_changed)
        self._mmc.events.configDefined.disconnect(self._on_configs_changed)
        self._mmc.events.configDeleted.disconnect(self._on_configs_changed)
        self._mmc.events.channelGroupChanged.disconnect(self._update_channel_groups)
