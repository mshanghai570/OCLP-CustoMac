"""One resolver for the generation-suffixed Metal driver sources, and its limits.

Five graphics families each named a Metal driver source as `12.5-<xnu_major>`. That
is correct while the generation's directory is published, and it silently named a
directory that exists in no release once the generation advanced to Darwin 25.

The resolver in `patchsets/hardware/base.py` answers from a single table instead,
and omits the entry when the generation has no published driver. Omitting makes
the payload checks pass, which is exactly why the omission is asserted here: the
patch set is incomplete, so these families must stay dormant until a `12.5-25`
directory is published.
"""

import unittest

from pathlib import Path

from ci_tooling import psp_tahoe_source_manifest
from ci_tooling import tahoe_probe_hardware
from opencore_legacy_patcher import constants
from opencore_legacy_patcher.sys_patch.patchsets.base import PatchType
from opencore_legacy_patcher.sys_patch.patchsets.detect import HardwarePatchsetDetection
from opencore_legacy_patcher.sys_patch.patchsets.hardware.base import (
    METAL_DRIVER_GENERATIONS,
    BaseHardware,
)
from opencore_legacy_patcher.sys_patch.patchsets.hardware.graphics import (
    amd_navi,
    amd_polaris,
    amd_vega,
    intel_broadwell,
    intel_skylake,
)


REPO_ROOT: Path = Path(__file__).resolve().parents[1]
PATCHSETS_ROOT: Path = REPO_ROOT / "opencore_legacy_patcher/sys_patch/patchsets"

TAHOE_GENERATION: int = 25
SEQUOIA_GENERATION: int = 24

# `(display name, patchset class, Metal driver bundle)` for every family that
# names a generation-suffixed Metal driver.
METAL_DRIVER_FAMILIES: tuple[tuple[str, type[BaseHardware], str], ...] = (
    ("Graphics: Intel Broadwell", intel_broadwell.IntelBroadwell, "AppleIntelBDWGraphicsMTLDriver.bundle"),
    ("Graphics: Intel Skylake", intel_skylake.IntelSkylake, "AppleIntelSKLGraphicsMTLDriver.bundle"),
    ("Graphics: AMD Polaris", amd_polaris.AMDPolaris, "AMDMTLBronzeDriver.bundle"),
    ("Graphics: AMD Vega", amd_vega.AMDVega, "AMDRadeonX5000MTLDriver.bundle"),
    ("Graphics: AMD Navi", amd_navi.AMDNavi, "AMDRadeonX6000MTLDriver.bundle"),
)

EXTENSIONS_PATH: str = "/System/Library/Extensions"


class TahoeMetalDriverResolverTests(unittest.TestCase):
    @staticmethod
    def _constants():
        """Constants carrying the fixture hardware the payload probe uses."""
        detected = constants.Constants()
        detected.detected_os_version = "26.0"
        detected.computer = tahoe_probe_hardware.TAHOE_PROBE_PROFILES[0].computer()
        return detected

    def _extensions(self, patchset: type[BaseHardware], generation: int) -> dict:
        """The single patchset's root-volume extension entries at `generation`."""
        patches = patchset(
            generation, 0, "test", self._constants()
        )._model_specific_patches()
        self.assertEqual(len(patches), 1, f"expected one patchset, got {sorted(patches)}")
        operations = next(iter(patches.values()))
        return operations[PatchType.OVERWRITE_SYSTEM_VOLUME][EXTENSIONS_PATH]

    def test_table_only_names_directories_the_release_provides(self) -> None:
        """The resolver's whole guarantee: every value is a directory that exists."""
        for generation, directory in METAL_DRIVER_GENERATIONS.items():
            self.assertIn(
                directory,
                psp_tahoe_source_manifest.SOURCE_DIRECTORIES,
                f"{directory} (generation {generation}) is not a source directory the "
                "pinned release provides",
            )

    def test_table_omits_generations_with_no_published_driver(self) -> None:
        for generation in (TAHOE_GENERATION, 26):
            self.assertNotIn(generation, METAL_DRIVER_GENERATIONS)

    def test_the_directory_tahoe_would_need_is_absent(self) -> None:
        """The omission is a payload gap, not a resolver bug."""
        self.assertNotIn("12.5-25", psp_tahoe_source_manifest.SOURCE_DIRECTORIES)

    def test_resolver_matches_previous_behaviour_where_a_driver_exists(self) -> None:
        """Ventura, Sonoma and Sequoia must resolve exactly as `12.5-<major>` did."""
        for generation in (22, 23, SEQUOIA_GENERATION):
            hardware = intel_broadwell.IntelBroadwell(generation, 0, "test", self._constants())
            self.assertEqual(
                hardware._metal_driver_patch("Example.bundle"),
                {"Example.bundle": f"12.5-{generation}"},
            )

    def test_resolver_emits_nothing_without_a_published_driver(self) -> None:
        for generation in (TAHOE_GENERATION, 26):
            hardware = intel_broadwell.IntelBroadwell(generation, 0, "test", self._constants())
            self.assertEqual(hardware._metal_driver_patch("Example.bundle"), {})

    def test_families_lose_only_the_metal_driver_on_tahoe(self) -> None:
        for name, patchset, driver in METAL_DRIVER_FAMILIES:
            with self.subTest(family=name):
                tahoe = self._extensions(patchset, TAHOE_GENERATION)
                self.assertNotIn(driver, tahoe, "no published 12.5-25 driver exists")
                self.assertTrue(tahoe, f"{name} lost more than its Metal driver")

                sequoia = self._extensions(patchset, SEQUOIA_GENERATION)
                self.assertEqual(sequoia[driver], "12.5-24")
                self.assertLessEqual(
                    set(sequoia) - set(tahoe),
                    {driver},
                    f"{name} changed entries other than the Metal driver",
                )
                self.assertLessEqual(
                    set(tahoe) - set(sequoia),
                    set(),
                    f"{name} gained entries on Tahoe",
                )

    def test_omitting_families_are_not_enabled(self) -> None:
        """A patch set missing its Metal driver must not be able to ship."""
        enabled = {
            variant.__name__ for variant in HardwarePatchsetDetection.hardware_variants()
        }
        for name, patchset, _ in METAL_DRIVER_FAMILIES:
            with self.subTest(family=name):
                self.assertNotIn(
                    patchset.__name__, enabled, f"{name} would ship an incomplete patch"
                )
                hardware = patchset(TAHOE_GENERATION, 0, "test", self._constants())
                self.assertFalse(
                    hardware.native_os(), f"{name} should be non-native at Tahoe"
                )

    def test_no_patchset_interpolates_its_generation_for_a_metal_driver(self) -> None:
        """Guard against reintroducing `f"12.5-{self._xnu_major}"` anywhere."""
        offenders = [
            str(path.relative_to(PATCHSETS_ROOT))
            for path in sorted(PATCHSETS_ROOT.rglob("*.py"))
            if 'f"12.5-{' in path.read_text()
        ]
        self.assertEqual(offenders, [], "use BaseHardware._metal_driver_patch instead")

    def test_every_metal_driver_family_uses_the_resolver(self) -> None:
        for name, patchset, _ in METAL_DRIVER_FAMILIES:
            with self.subTest(family=name):
                module_path = REPO_ROOT / (patchset.__module__.replace(".", "/") + ".py")
                self.assertTrue(module_path.is_file(), module_path)
                self.assertIn("_metal_driver_patch(", module_path.read_text())


if __name__ == "__main__":
    unittest.main()
