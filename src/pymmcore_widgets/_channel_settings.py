"""Per-channel settings shared by the channel tables: exposure, light, camera."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict, cast

from pymmcore_plus import PropertyType

from pymmcore_widgets.useq_widgets import ComboColumn
from pymmcore_widgets.useq_widgets._column_info import (
    ColumnInfo,
    TableDoubleSpinBox,
    WdgGetSet,
    WidgetColumn,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pymmcore_plus import CMMCorePlus
    from qtpy.QtWidgets import QTableWidget, QWidget

CameraValue = float | int | str

# prefixes a camera property name to make its column key
CAMERA_KEY_PREFIX = "camera:"
# shown in a camera property cell that leaves the property unchanged
UNSET_TEXT = "—"
UNSET_TOOLTIP = f"{UNSET_TEXT} leaves the camera's current value unchanged."

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
    """The settings of one channel.

    Attributes
    ----------
    exposure : float
        Exposure time in ms.
    light_source : str
        Light source label, as in
        [`ChannelSettingsTable.lightSources`][pymmcore_widgets.ChannelSettingsTable.lightSources]
        (`""` for none).
    intensity : float
        Value for the light source property (or properties).
    camera_properties : dict[str, float | int | str]
        `{property name: value}` for the camera device. Properties not listed are
        left unchanged.
    """

    exposure: float
    light_source: str
    intensity: float
    camera_properties: dict[str, CameraValue]


def camera_key(prop: str) -> str:
    """Return the column key of camera property `prop`."""
    return f"{CAMERA_KEY_PREFIX}{prop}"


class OptionalSpinBox(TableDoubleSpinBox):
    """Spin box whose lowest value means "unset", shown as `UNSET_TEXT`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSpecialValueText(UNSET_TEXT)
        self.setToolTip(UNSET_TOOLTIP)

    def setBounds(self, lower: float, upper: float, decimals: int) -> None:
        """Allow `lower`..`upper`, plus one step below for "unset" (the default)."""
        self.setDecimals(decimals)
        self.setRange(lower - 10**-decimals, upper)
        self.setValue(self.minimum())

    def optionalValue(self) -> float | None:
        value = self.value()
        return None if value == self.minimum() else value

    def setOptionalValue(self, value: float | None) -> None:
        self.setValue(self.minimum() if value is None else float(value))


TableOptionalWidget = WdgGetSet(
    OptionalSpinBox,
    OptionalSpinBox.optionalValue,
    OptionalSpinBox.setOptionalValue,
    lambda w, cb: w.valueChanged.connect(cb),
)


@dataclass(frozen=True)
class OptionalFloatColumn(WidgetColumn):
    """Column of numbers that may be unset (`None`)."""

    data_type: WdgGetSet = TableOptionalWidget
    minimum: float = -999_999
    maximum: float = 999_999
    decimals: int = 2

    def _init_widget(self) -> OptionalSpinBox:
        wdg = OptionalSpinBox()
        wdg.setBounds(self.minimum, self.maximum, self.decimals)
        return wdg

    def set_cell_data(
        self, table: QTableWidget, row: int, col: int, value: Any
    ) -> None:
        # unlike WidgetColumn, None is a value here: it unsets the cell
        if wdg := table.cellWidget(row, col):
            cast("OptionalSpinBox", wdg).setOptionalValue(value)


def camera_property_columns(
    core: CMMCorePlus, camera: str, names: Iterable[str]
) -> dict[str, ColumnInfo]:
    """Return `{property: column}` for the per-channel properties `camera` has.

    Only properties that can be written after initialization are included. Every
    cell starts unset, i.e. leaving the property unchanged.
    """
    columns: dict[str, ColumnInfo] = {}
    if not camera:
        return columns
    for prop in names:
        if (
            not core.hasProperty(camera, prop)
            or core.isPropertyReadOnly(camera, prop)
            or core.isPropertyPreInit(camera, prop)
        ):
            continue
        key = camera_key(prop)
        ptype = core.getPropertyType(camera, prop)
        if allowed := core.getAllowedPropertyValues(camera, prop):
            if len(allowed) < 2:  # nothing to choose from
                continue
            columns[prop] = ComboColumn(
                key=key, header=prop, default="", allowed_values=("", *allowed)
            )
        elif ptype in (PropertyType.Integer, PropertyType.Float):
            if core.hasPropertyLimits(camera, prop):
                lower = core.getPropertyLowerLimit(camera, prop)
                upper = core.getPropertyUpperLimit(camera, prop)
            else:
                lower, upper = -999_999, 999_999
            columns[prop] = OptionalFloatColumn(
                key=key,
                header=prop,
                minimum=lower,
                maximum=upper,
                decimals=0 if ptype is PropertyType.Integer else 2,
            )
    return columns


def read_camera_cell(
    core: CMMCorePlus, camera: str, prop: str, column: ColumnInfo, raw: Any
) -> CameraValue | None:
    """Cast a camera property cell's value; `None` when it is unset.

    Properties with allowed values keep their string (it must match the choice
    exactly, e.g. "1.0000"); numeric ones are numbers.
    """
    if raw is None or raw == "":
        return None
    if isinstance(column, ComboColumn):
        return str(raw)
    if core.getPropertyType(camera, prop) is PropertyType.Integer:
        return int(float(raw))
    return float(raw)


def write_camera_cell(
    table: QTableWidget, row: int, column: ColumnInfo, value: CameraValue | None
) -> None:
    """Set a camera property cell; `None` unsets it."""
    col = table.indexOf(column)  # type: ignore[attr-defined]
    if isinstance(column, ComboColumn):
        column.set_cell_data(table, row, col, "" if value is None else str(value))
    else:
        column.set_cell_data(table, row, col, value)


def settings_properties(
    core: CMMCorePlus,
    settings: ChannelSettings,
    light_sources: Mapping[str, Iterable[tuple[str, str]]],
) -> list[tuple[str, str, str, CameraValue]]:
    """Return the `(group, device, property, value)` that `settings` sets.

    That is each property of its light source (`group` is the light source label,
    see `pymmcore_widgets._light_sources.find_light_sources`), and each of its
    camera properties the current camera can write (`group` is
    `camera_key(property)`). A light source not in `light_sources` sets nothing.
    """
    props: list[tuple[str, str, str, CameraValue]] = []
    source = settings["light_source"]
    for device, prop in light_sources.get(source, ()):
        intensity: float | int = settings["intensity"]
        if core.getPropertyType(device, prop) is PropertyType.Integer:
            intensity = int(intensity)
        props.append((source, device, prop, intensity))
    if camera := core.getCameraDevice():
        for prop, value in settings["camera_properties"].items():
            if core.hasProperty(camera, prop) and not core.isPropertyReadOnly(
                camera, prop
            ):
                props.append((camera_key(prop), camera, prop, value))
    return props


def apply_channel_settings(
    core: CMMCorePlus,
    settings: ChannelSettings,
    light_sources: Mapping[str, Iterable[tuple[str, str]]],
) -> None:
    """Set the exposure, light source and camera properties of `settings`."""
    core.setExposure(settings["exposure"])
    for _, device, prop, value in settings_properties(core, settings, light_sources):
        core.setProperty(device, prop, value)
