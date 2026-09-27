"""
modern_audio.py: Modern Audio patch set for macOS 26+
"""

from ..base import BaseHardware, HardwareVariant
from ...base import PatchType
from .....constants import constants
from .....datasets.os_data import os_data


class ModernAudio(BaseHardware):
    """
    Optimized Modern Audio patch class for improved performance on resource-constrained systems
    """
    
    def __init__(self, xnu_major, xnu_minor, os_build, global_constants: constants.Constants) -> None:
        super().__init__(xnu_major, xnu_minor, os_build, global_constants)
        self._apple_hda_cache = None

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

    def _apple_hda_already_present(self) -> bool:
        """
        Optimized check for existing AppleHDA to avoid unnecessary patches
        """
        if self._apple_hda_cache is None:
            self._apple_hda_cache = self._check_apple_hda_presence()
        return self._apple_hda_cache

    def _check_apple_hda_presence(self) -> bool:
        """
        Internal method to check AppleHDA presence
        """
        try:
            import subprocess
            result = subprocess.run(
                ["kextstat", "-l"],
                capture_output=True,
                text=True,
                timeout=2
            )
            return "AppleHDA" in result.stdout or "AppleALC" in result.stdout
        except Exception:
            return False

    def _modern_audio_patches(self) -> dict:
        """
        Optimized patches for Modern Audio with minimal memory footprint
        """
        patches = {
            "Modern Audio": {
                PatchType.OVERWRITE_SYSTEM_VOLUME: {
                    "/System/Library/Extensions": {},
                },
            },
        }
        
        if not self._apple_hda_already_present():
            patches["Modern Audio"][PatchType.OVERWRITE_SYSTEM_VOLUME]["/System/Library/Extensions"]["AppleHDA.kext"] = "26.0 Beta 1"
        
        return patches

    def patches(self) -> dict:
        """
        Optimized patches for modern audio with early exit optimization
        """
        if self.native_os():
            return {}
        
        return self._modern_audio_patches()
