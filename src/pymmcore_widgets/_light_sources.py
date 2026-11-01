"""Light sources: device properties that set a channel's illumination level.

Shared by the MDA channel table and the channel settings table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pymmcore_plus import PropertyType

from pymmcore_widgets.useq_widgets._column_info import (
    TableDoubleSpinBox,
    WdgGetSet,
    WidgetColumn,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pymmcore_plus import CMMCorePlus
    from qtpy.QtWidgets import QWidget

# keys of the columns that are *not* useq.Channel fields
LIGHT_SOURCE_KEY = "light_source"
INTENSITY_KEY = "intensity"

# value of the light source combo meaning "this channel sets no property"
NO_LIGHT_SOURCE = ""

# joins device label and property name into a light source combo entry
PROPERTY_SEPARATOR = " · "


def find_light_sources(core: CMMCorePlus) -> dict[str, list[tuple[str, str]]]:
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
    properties = core.iterProperties(
        property_type=(PropertyType.Integer, PropertyType.Float),
        has_limits=True,
        is_read_only=False,
        as_object=False,
    )
    sources: dict[str, list[tuple[str, str]]] = {
        f"{dev}{PROPERTY_SEPARATOR}{prop}": [(str(dev), str(prop))]
        for dev, prop in properties
        if not core.isPropertyPreInit(dev, prop)
    }

    for group in core.getAvailableConfigGroups():
        presets = core.getAvailableConfigs(group)
        if len(presets) != 1:
            continue
        (preset,) = presets
        dev_props: list[tuple[str, str]] = []
        valid = True
        try:
            for device, prop, _value in core.getConfigData(group, preset):
                ptype = core.getPropertyType(device, prop)
                if (
                    ptype not in (PropertyType.Integer, PropertyType.Float)
                    or not core.hasPropertyLimits(device, prop)
                    or core.isPropertyReadOnly(device, prop)
                    or core.isPropertyPreInit(device, prop)
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


def light_source_limits(
    core: CMMCorePlus, dev_props: Iterable[tuple[str, str]]
) -> tuple[float, float, bool]:
    """Return `(lower, upper, is_integer)` for driving all of `dev_props` at once.

    For a multi-property source, the intersection of all limits is used so that a
    single value is always valid for every underlying property.
    """
    dev_props = list(dev_props)
    lower = max(core.getPropertyLowerLimit(dev, prop) for dev, prop in dev_props)
    upper = min(core.getPropertyUpperLimit(dev, prop) for dev, prop in dev_props)
    is_int = all(
        core.getPropertyType(dev, prop) is PropertyType.Integer
        for dev, prop in dev_props
    )
    return lower, upper, is_int


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
