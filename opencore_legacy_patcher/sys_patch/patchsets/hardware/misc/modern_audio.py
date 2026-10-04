"""
modern_audio.py: Modern Audio patch set for macOS 26+
"""

from ..base import BaseHardware, HardwareVariant
from ...base import PatchType
from .....constants import Constants
from .....datasets.os_data import os_data


class ModernAudio(BaseHardware):
    """
    Optimized Modern Audio patch class for improved performance on resource-constrained systems
    """

    def __init__(self, xnu_major, xnu_minor, os_build, global_constants: Constants) -> None:
        super().__init__(xnu_major, xnu_minor, os_build, global_constants)

    def name(self) -> str:
        """
        Display name for end users
        """
        return f"{self.hardware_variant()}: Modern Audio"

    def present(self) -> bool:
        """
        AppleHDA was outright removed in macOS 26, so this patch set is always present if OS requires it
        """
        return True

    def native_os(self) -> bool:
        """
        Optimized native OS detection for faster processing
        """
        if self._xnu_major < os_data.tahoe.value:
            return True

        if self._os_build == "25A5279m":
            return True

        return False

    def requires_kernel_debug_kit(self) -> bool:
        """
        Apple no longer provides standalone kexts in the base OS
        """
        return True

    def hardware_variant(self) -> HardwareVariant:
        """
        Type of hardware variant
        """
        return HardwareVariant.MISCELLANEOUS

    def _modern_audio_patches(self) -> dict:
        """
        Patches for Modern Audio
        """
        return {
            "Modern Audio": {
                PatchType.OVERWRITE_SYSTEM_VOLUME: {
                    "/System/Library/Extensions": {
                        "AppleHDA.kext": "26.0 Beta 1",
                    },
                },
            },
        }

    def patches(self) -> dict:
        """
        Optimized patches for modern audio with early exit optimization
        """
        if self.native_os():
            return {}

        return self._modern_audio_patches()
