"""MDA widgets."""

from ._channel_properties import (
    CHANNEL_PROPERTIES_KEY,
    ChannelPropertiesSequence,
    ChannelProperty,
    channel_properties,
    to_channel_properties_sequence,
)
from ._core_channels import CoreConnectedChannelTable
from ._core_mda import MDAWidget

__all__ = [
    "CHANNEL_PROPERTIES_KEY",
    "ChannelPropertiesSequence",
    "ChannelProperty",
    "CoreConnectedChannelTable",
    "MDAWidget",
    "channel_properties",
    "to_channel_properties_sequence",
]
