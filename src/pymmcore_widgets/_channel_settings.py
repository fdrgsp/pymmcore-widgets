"""Per-channel settings shared by the channel tables: exposure and light source."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from pymmcore_plus import PropertyType

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pymmcore_plus import CMMCorePlus


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
    """

    exposure: float
    light_source: str
    intensity: float


def settings_properties(
    core: CMMCorePlus,
    settings: ChannelSettings,
    light_sources: Mapping[str, Iterable[tuple[str, str]]],
) -> list[tuple[str, str, str, float | int]]:
    """Return the `(group, device, property, value)` that `settings` sets.

    That is each property of its light source (`group` is the light source label,
    see `pymmcore_widgets._light_sources.find_light_sources`). A light source not in
    `light_sources` sets nothing.
    """
    props: list[tuple[str, str, str, float | int]] = []
    source = settings["light_source"]
    for device, prop in light_sources.get(source, ()):
        intensity: float | int = settings["intensity"]
        if core.getPropertyType(device, prop) is PropertyType.Integer:
            intensity = int(intensity)
        props.append((source, device, prop, intensity))
    return props


def apply_channel_settings(
    core: CMMCorePlus,
    settings: ChannelSettings,
    light_sources: Mapping[str, Iterable[tuple[str, str]]],
) -> None:
    """Set the exposure and light source of `settings`."""
    core.setExposure(settings["exposure"])
    for _, device, prop, value in settings_properties(core, settings, light_sources):
        core.setProperty(device, prop, value)
