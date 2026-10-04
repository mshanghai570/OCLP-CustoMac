"""Tahoe root-patch behavior for the Broadwell MacBook Air target.

Intel Broadwell is deliberately dormant on this branch: the published product
covers Modern Wireless and Modern Audio only, and the pinned PatcherSupportPkg
release does not publish every Tahoe resource the Broadwell patch class names.
These tests pin both facts so that dormant does not quietly mean forgotten.
"""

from __future__ import annotations

import types
import unittest

from unittest import mock

from opencore_legacy_patcher.datasets import pci_data
from opencore_legacy_patcher.datasets import smbios_data
from opencore_legacy_patcher.detections import device_probe
from opencore_legacy_patcher.detections.amfi_detect import AmfiConfigDetectLevel
from opencore_legacy_patcher.sys_patch.patchsets import detect
from opencore_legacy_patcher.sys_patch.patchsets.base import PatchType
from opencore_legacy_patcher.sys_patch.patchsets.hardware.base import BaseHardware
from opencore_legacy_patcher.sys_patch.patchsets.hardware.graphics import intel_broadwell
from opencore_legacy_patcher.sys_patch.root_selection import (
    RootPatchSelection,
    SelectableRootPatch,
)


TARGET_MODEL = "MacBookAir7,2"
TAHOE_XNU_MAJOR = 25
TAHOE_XNU_MINOR = 0
TAHOE_BUILD = "25A123"
TAHOE_VERSION = "26.0"

HD_6000_DEVICE_ID = 0x1626

BROADWELL_PATCHSET_NAME = "Graphics: Intel Broadwell"
BROADWELL_SHARED_PATCH_NAMES = {
    "Monterey GVA",
    "Monterey OpenCL",
}


def _macbook_air_7_2_tahoe_constants() -> types.SimpleNamespace:
    """Constants stub describing a MacBookAir7,2 running macOS Tahoe."""
    gpu = device_probe.Intel(
        vendor_id=device_probe.Intel.VENDOR_ID,
        device_id=HD_6000_DEVICE_ID,
        class_code=0x030000,
    )
    return types.SimpleNamespace(
        detected_os=TAHOE_XNU_MAJOR,
        detected_os_minor=TAHOE_XNU_MINOR,
        detected_os_build=TAHOE_BUILD,
        detected_os_version=TAHOE_VERSION,
        computer=types.SimpleNamespace(gpus=[gpu]),
    )


class _FakeHardware(BaseHardware):
    patchset_name = ""
    patch_name = ""
    kdk_required = False

    def name(self) -> str:
        return self.patchset_name

    def present(self) -> bool:
        return True

    def native_os(self) -> bool:
        return False

    def required_system_integrity_protection_configurations(self) -> list[str]:
        return []

    def required_amfi_level(self) -> AmfiConfigDetectLevel:
        return AmfiConfigDetectLevel.NO_CHECK

    def requires_kernel_debug_kit(self) -> bool:
        return self.kdk_required

    def patches(self) -> dict:
        return {self.patch_name: {}}


class _FakeWireless(_FakeHardware):
    patchset_name = "Networking: Modern Wireless"
    patch_name = "Modern Wireless"


class _FakeAudio(_FakeHardware):
    patchset_name = "Miscellaneous: Modern Audio"
    patch_name = "Modern Audio"
    kdk_required = True


class _DeterministicDetection(detect.HardwarePatchsetDetection):
    def _validation_check_unsupported_host_os(self) -> bool:
        return False

    def _validation_check_missing_network_connection(self) -> bool:
        return False

    def _validation_check_filevault_is_enabled(self) -> bool:
        return False

    def _validation_check_system_integrity_protection_enabled(self, configs: list[str]) -> bool:
        return False

    def _validation_check_secure_boot_model_enabled(self) -> bool:
        return False

    def _validation_check_amfi_enabled(self, level: AmfiConfigDetectLevel) -> bool:
        return False

    def _validation_check_whatevergreen_missing(self) -> bool:
        return False

    def _validation_check_force_opengl_missing(self) -> bool:
        return False

    def _validation_check_force_compat_missing(self) -> bool:
        return False

    def _validation_check_nvda_drv_missing(self) -> bool:
        return False


class TahoeBroadwellGraphicsDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.constants = _macbook_air_7_2_tahoe_constants()

    def _detect(self, selection: RootPatchSelection) -> _DeterministicDetection:
        with mock.patch.object(detect.modern_wireless, "ModernWireless", _FakeWireless), \
             mock.patch.object(detect.modern_audio, "ModernAudio", _FakeAudio):
            return _DeterministicDetection(
                self.constants,
                validation=True,
                patch_selection=selection,
                check_kdk_status=False,
            )

    def _broadwell_patches(self) -> dict:
        return intel_broadwell.IntelBroadwell(
            TAHOE_XNU_MAJOR, TAHOE_XNU_MINOR, TAHOE_BUILD, self.constants
        ).patches()

    def _detect_all_selected(self) -> _DeterministicDetection:
        first = self._detect(RootPatchSelection.initialize(()))
        return self._detect(RootPatchSelection.initialize(first.applicable_patchsets))

    def test_target_fixture_matches_smbios_broadwell_graphics(self) -> None:
        gpu = self.constants.computer.gpus[0]
        self.assertIsInstance(gpu, device_probe.Intel)
        self.assertEqual(gpu.device_id, HD_6000_DEVICE_ID)
        self.assertIn(gpu.device_id, pci_data.intel_ids.broadwell_ids)
        self.assertIs(gpu.arch, device_probe.Intel.Archs.Broadwell)
        self.assertEqual(
            smbios_data.smbios_dictionary[TARGET_MODEL]["Stock GPUs"],
            [device_probe.Intel.Archs.Broadwell],
        )

    def test_broadwell_graphics_variant_is_dormant_but_still_in_source(self) -> None:
        registered = [
            variant.__name__ for variant in self._detect_all_selected()._hardware_variants
        ]
        self.assertNotIn(intel_broadwell.IntelBroadwell.__name__, registered)
        self.assertIn(
            intel_broadwell.IntelBroadwell,
            detect.HardwarePatchsetDetection.all_hardware_variants(),
        )

    def test_broadwell_patchset_reports_present_on_tahoe(self) -> None:
        patchset = intel_broadwell.IntelBroadwell(
            TAHOE_XNU_MAJOR, TAHOE_XNU_MINOR, TAHOE_BUILD, self.constants
        )
        self.assertTrue(patchset.present())
        self.assertFalse(patchset.native_os())
        self.assertEqual(patchset.name(), BROADWELL_PATCHSET_NAME)

    def test_tahoe_detection_does_not_select_broadwell_patchset(self) -> None:
        result = self._detect_all_selected()
        self.assertNotIn(BROADWELL_PATCHSET_NAME, result.applicable_patchsets)
        self.assertFalse(result.device_properties.get(BROADWELL_PATCHSET_NAME))

    def test_tahoe_detection_emits_no_broadwell_or_shared_graphics_patches(self) -> None:
        result = self._detect_all_selected()
        self.assertNotIn("Intel Broadwell", result.patches)
        for patch_name in BROADWELL_SHARED_PATCH_NAMES:
            self.assertNotIn(patch_name, result.patches)

    def test_tahoe_broadwell_payload_versions(self) -> None:
        patches = self._broadwell_patches()
        extensions = patches["Intel Broadwell"][PatchType.OVERWRITE_SYSTEM_VOLUME][
            "/System/Library/Extensions"
        ]
        self.assertEqual(extensions["AppleIntelBDWGraphics.kext"], "12.5-23.4")
        self.assertEqual(extensions["AppleIntelBDWGraphicsFramebuffer.kext"], "12.5-23.4")
        self.assertEqual(extensions["AppleIntelBDWGraphicsGLDriver.bundle"], "12.5")
        # No pinned release publishes a 12.5-25 Metal driver, so the resolver
        # omits the entry rather than naming a directory that does not exist.
        # The patch set is therefore incomplete on Tahoe, which is why Broadwell
        # stays dormant; see tests/test_tahoe_metal_driver_resolver.py.
        self.assertNotIn("AppleIntelBDWGraphicsMTLDriver.bundle", extensions)
        self.assertEqual(extensions["AppleIntelBDWGraphicsVADriver.bundle"], "12.5")
        self.assertEqual(extensions["AppleIntelBDWGraphicsVAME.bundle"], "12.5")
        self.assertEqual(extensions["AppleIntelGraphicsShared.bundle"], "12.5")

        # No RenderBox-<major> source is published for Tahoe in any pinned
        # PatcherSupportPkg release, so the shared Metal 31001 patch must not
        # reference one. Tahoe metallibs come from the metallib support package.
        self.assertNotIn("Metal 31001 Common", patches)

    def test_broadwell_patchset_declares_no_kdk_or_metallib_requirement(self) -> None:
        patchset = intel_broadwell.IntelBroadwell(
            TAHOE_XNU_MAJOR, TAHOE_XNU_MINOR, TAHOE_BUILD, self.constants
        )
        self.assertFalse(patchset.requires_kernel_debug_kit())
        self.assertFalse(patchset.requires_metallib_support_pkg())

    def test_deselecting_selectable_families_never_adds_broadwell_patches(self) -> None:
        result = self._detect_all_selected()
        selection = RootPatchSelection.initialize(result.applicable_patchsets)
        selection = selection.with_selection(SelectableRootPatch.MODERN_WIFI, False)
        filtered = self._detect(selection)
        self.assertNotIn("Modern Wireless", filtered.patches)
        self.assertIn("Modern Audio", filtered.patches)
        self.assertNotIn("Intel Broadwell", filtered.patches)
        for patch_name in BROADWELL_SHARED_PATCH_NAMES:
            self.assertNotIn(patch_name, filtered.patches)

    def test_kdk_requirement_is_derived_from_selected_families(self) -> None:
        result = self._detect_all_selected()
        self.assertTrue(result.device_properties[detect.HardwarePatchsetSettings.KERNEL_DEBUG_KIT_REQUIRED])

        without_audio = RootPatchSelection.initialize(result.applicable_patchsets).with_selection(
            SelectableRootPatch.MODERN_AUDIO,
            False,
        )
        reduced = self._detect(without_audio)
        self.assertNotIn("Intel Broadwell", reduced.patches)
        self.assertFalse(
            reduced.device_properties[detect.HardwarePatchsetSettings.KERNEL_DEBUG_KIT_REQUIRED]
        )


if __name__ == "__main__":
    unittest.main()
