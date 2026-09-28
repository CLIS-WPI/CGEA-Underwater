"""Sionna extensions for underwater acoustic channels."""

from cgea.sionna_ext.channel_model import UnderwaterAcousticChannel, batched_link_quality_from_channel

__all__ = ["UnderwaterAcousticChannel", "batched_link_quality_from_channel"]
