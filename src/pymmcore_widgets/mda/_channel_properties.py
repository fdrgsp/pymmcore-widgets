"""Per-channel device properties (e.g. light source intensity) for an MDASequence.

[`useq.Channel`][] has no place to store a device property, so the per-channel
settings entered in the channel table are stashed in the sequence metadata under
`metadata[PYMMCW_METADATA_KEY][CHANNEL_PROPERTIES_KEY]` and turned into
[`useq.MDAEvent.properties`][] at iteration time by
[`ChannelPropertiesSequence`][pymmcore_widgets.mda.ChannelPropertiesSequence].

Note that this deliberately stays an `MDASequence` subclass rather than a generator
of events: `MDARunner.run` does ``sequence = events if isinstance(events,
MDASequence) else GeneratorMDASequence()``, so handing it a bare generator would
discard the sequence (and its metadata, which the save widget relies on).

TODO: if `useq.Channel` ever grows a `properties` field (emitted on c-index change,
mirroring the existing `Position.properties` support in `useq._iter_sequence`), this
module can be replaced by putting the `PropertyTuple` directly on the channel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

import useq
from useq import Axis

from pymmcore_widgets.useq_widgets import PYMMCW_METADATA_KEY

if TYPE_CHECKING:
    from collections.abc import Iterator

CHANNEL_PROPERTIES_KEY = "channel_properties"


class ChannelProperty(TypedDict):
    """A single device property to apply while acquiring a given channel.

    Attributes
    ----------
    channel_index : int
        Index of the channel in `MDASequence.channels` that this applies to.
    config : str
        Name of that channel's config preset. Informational; `channel_index` is what
        is matched against at iteration time.
    group : str
        Label the property was selected under in the widget, e.g.
        `"LumencorSola · White_Level"`. Used to restore the widget state, not to
        execute; `device`/`property` are what get applied.
    device : str
        Device label, e.g. `"LumencorSola"`.
    property : str
        Property name, e.g. `"White_Level"`.
    value : float | int
        The value to set.
    """

    channel_index: int
    config: str
    group: str
    device: str
    property: str
    value: float | int


def channel_properties(sequence: useq.MDASequence) -> list[ChannelProperty]:
    """Return the per-channel properties stored in `sequence`'s metadata."""
    meta = sequence.metadata.get(PYMMCW_METADATA_KEY, {})
    return list(meta.get(CHANNEL_PROPERTIES_KEY, []))


class ChannelPropertiesSequence(useq.MDASequence):
    """An [`useq.MDASequence`][] that applies per-channel device properties.

    Events for a channel listed in `metadata[PYMMCW_METADATA_KEY]["channel_properties"]`
    get the corresponding [`useq.PropertyTuple`][] appended to `MDAEvent.properties`,
    which the `pymmcore_plus` MDA engine applies before acquiring the frame.
    """

    def iter_events(self) -> Iterator[useq.MDAEvent]:
        from collections import defaultdict

        props: dict[int, list[useq.PropertyTuple]] = defaultdict(list)
        for entry in channel_properties(self):
            props[entry["channel_index"]].append(
                useq.PropertyTuple(entry["device"], entry["property"], entry["value"])
            )
        if not props:
            yield from super().iter_events()
            return

        for event in super().iter_events():
            c_idx = event.index.get(Axis.CHANNEL)
            if c_idx is not None and (prop_list := props.get(c_idx)):
                event = event.model_copy(
                    update={"properties": [*(event.properties or ()), *prop_list]}
                )
            yield event


def to_channel_properties_sequence(
    sequence: useq.MDASequence,
) -> ChannelPropertiesSequence:
    """Return `sequence` as a [`ChannelPropertiesSequence`][], preserving its fields."""
    if isinstance(sequence, ChannelPropertiesSequence):
        return sequence
    # same idiom as MDASequence.replace: pass the field *objects* through validation
    # so that e.g. a WellPlatePlan in stage_positions is preserved.
    data = {k: getattr(sequence, k) for k in type(sequence).model_fields if k != "uid"}
    return ChannelPropertiesSequence.model_validate(data)


__all__ = [
    "CHANNEL_PROPERTIES_KEY",
    "ChannelPropertiesSequence",
    "ChannelProperty",
    "channel_properties",
    "to_channel_properties_sequence",
]
