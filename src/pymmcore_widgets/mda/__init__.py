"""MDA widgets."""

from ._channel_properties import (
    CHANNEL_PROPERTIES_KEY,
    ChannelPropertiesSequence,
    ChannelProperty,
    channel_properties,
    to_channel_properties_sequence,
)
from ._collapsible_mda import (
    CollapsibleAcquisitionSection,
    CollapsibleCoreMDATabs,
    MDAWidgetCollapsible,
    SectionMetrics,
)
from ._core_channels import CoreConnectedChannelTable
from ._core_mda import MDAWidget

__all__ = [
    "CHANNEL_PROPERTIES_KEY",
    "ChannelPropertiesSequence",
    "ChannelProperty",
    "CollapsibleAcquisitionSection",
    "CollapsibleCoreMDATabs",
    "CoreConnectedChannelTable",
    "MDAWidget",
    "MDAWidgetCollapsible",
    "SectionMetrics",
    "channel_properties",
    "to_channel_properties_sequence",
]
