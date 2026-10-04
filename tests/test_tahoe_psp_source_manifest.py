"""Tahoe root-patch payload coverage, checked without the PatcherSupportPkg image.

The build payload contract already fails a package build when an *enabled*
hardware family names a source the pinned PatcherSupportPkg release does not
publish. That check needs the downloaded image, so it only protects families
registered in `HardwarePatchsetDetection.hardware_variants()`.

This module closes the gap for the dormant families. `PUBLISHED` is generated
from the pinned release and `UNPUBLISHED` is written by hand with a reason, so a
patch class naming a source that exists in no release fails here, at write time,
instead of when someone tries to enable that family.
"""

import contextlib
import io
import tempfile
import unittest.mock
import unittest

from pathlib import Path

from ci_tooling import generate_psp_source_manifest
from ci_tooling import psp_tahoe_source_manifest
from ci_tooling import tahoe_probe_hardware
from ci_tooling.build_modules import payload_contract
from opencore_legacy_patcher import constants
from opencore_legacy_patcher.datasets.os_data import os_data
from opencore_legacy_patcher.sys_patch.patchsets.base import DynamicPatchset, PatchType
from opencore_legacy_patcher.sys_patch.patchsets.detect import HardwarePatchsetDetection
from opencore_legacy_patcher.sys_patch.patchsets.hardware.graphics import (
    amd_legacy_gcn,
    amd_polaris,
    amd_vega,
)
from opencore_legacy_patcher.sys_patch.patchsets.hardware.misc import legacy_audio


UNPUBLISHED: dict[str, str] = {
    "13.2.1-25/System/Library/Frameworks/Metal.framework":
        "Only 13.2.1, 13.2.1-22, 13.2.1-23, 13.2.1-24 and 13.2.1-3802 are published. "
        "Blocks Graphics: Intel Ivy Bridge, Graphics: Intel Haswell and Graphics: Nvidia "
        "Kepler, which share the Metal 3802 downgrade.",
    "12.7.2-25/System/Library/Frameworks/CoreWLAN.framework":
        "Only 12.7.2, 12.7.2-22, 12.7.2-23 and 12.7.2-24 are published. Blocks "
        "Networking: Legacy Wireless.",
    "12.7.2-25/System/Library/PrivateFrameworks/CoreWiFi.framework":
        "Same 12.7.2-25 gap. Blocks Networking: Legacy Wireless.",
    "12.7.2-25/System/Library/PrivateFrameworks/IO80211.framework":
        "Same 12.7.2-25 gap. Blocks Networking: Legacy Wireless.",
    "12.7.2-25/System/Library/PrivateFrameworks/WiFiPeerToPeer.framework":
        "Same 12.7.2-25 gap. Blocks Networking: Legacy Wireless.",
    "12.7.2-25/usr/libexec/wifip2pd":
        "Same 12.7.2-25 gap. Blocks Networking: Legacy Wireless.",
    "12.7.2-25/usr/libexec/wps":
        "Same 12.7.2-25 gap. Blocks Networking: Legacy Wireless.",
    "13.7.1-25/System/Library/Frameworks/LocalAuthentication.framework/Support/SharedUtils.framework":
        "Only 13.7.1-22, 13.7.1-23 and 13.7.1-24 are published. Blocks "
        "Miscellaneous: T1 Security Chip.",
    "10.13.6-25/System/Library/Frameworks/CoreDisplay.framework":
        "The Non-Metal stack needs Tahoe-suffixed copies that the release does not publish; "
        "only the unsuffixed 10.13.6 directory exists. Blocks NonMetalCoreDisplay.",
    "10.13.6-25/System/Library/PrivateFrameworks/IOAccelerator.framework":
        "Same 10.13.6-25 gap. Blocks NonMetalIOAccelerator.",
    "10.14.4-25/System/Library/Frameworks/CoreDisplay.framework":
        "Same gap as 10.13.6-25, at Mojave's version. Blocks NonMetal.",
    "10.14.6-25/System/Library/Frameworks/IOSurface.framework":
        "Same 10.14.6-25 gap. Blocks NonMetalIOAccelerator.",
    "10.14.6-25/System/Library/PrivateFrameworks/SkyLight.framework":
        "Same 10.14.6-25 gap. Blocks NonMetal.",
    "10.15.7-25/System/Library/Frameworks/IOSurface.framework":
        "Same 10.15.7-25 gap. Blocks NonMetal.",
    "10.15.7-25/System/Library/Frameworks/QuartzCore.framework":
        "Same 10.15.7-25 gap. Blocks NonMetal.",
}

# No `12.5-25` path appears above any more, and that is not a fix: the Metal
# driver resolver used to name that nonexistent directory for five graphics
# families, and now omits the entry instead. Omitting makes the payload checks
# pass, so the incompleteness is asserted in
# `tests/test_tahoe_metal_driver_resolver.py`, which records that those families
# lose exactly their Metal driver on Tahoe and therefore must stay dormant.

# Families whose patches still cannot be computed, even with fixture hardware.
# Empty on purpose: every registered family now probes, so anything listed here
# would mean a family regressed into being unenumerable.
UNPROBEABLE: dict[str, str] = {}


class TahoePspSourceManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = payload_contract.PayloadContract()
        cls.sources, cls.probe_failures = tahoe_probe_hardware.probe_tahoe_hardware()
        cls.shared_sources, cls.shared_failures = cls.contract.probe_tahoe_shared_patches()
        cls.required = {
            path
            for group in (cls.sources, cls.shared_sources)
            for paths in group.values()
            for path in paths
        }

    @staticmethod
    def _tahoe_constants():
        """Mirror the probe, which tells the family the host runs Tahoe."""
        detected = constants.Constants()
        detected.detected_os_version = "26.0"
        return detected

    @classmethod
    def _probe_constants(cls):
        """Tahoe constants carrying the fixture hardware the probe profiles supply."""
        detected = cls._tahoe_constants()
        detected.computer = tahoe_probe_hardware.TAHOE_PROBE_PROFILES[0].computer()
        return detected

    def test_manifest_is_generated_from_the_pinned_release(self) -> None:
        self.assertEqual(
            psp_tahoe_source_manifest.PACKAGE_VERSION,
            constants.Constants().patcher_support_pkg_version,
        )

    def test_source_directories_cover_every_published_path(self) -> None:
        """The directory vocabulary must describe the paths recorded beside it."""
        directories = psp_tahoe_source_manifest.SOURCE_DIRECTORIES
        self.assertTrue(directories)
        self.assertEqual(
            {
                path.split("/", 1)[0]
                for path in psp_tahoe_source_manifest.PUBLISHED
            } - directories,
            set(),
        )

    def test_every_tahoe_source_is_published_or_explicitly_unpublished(self) -> None:
        known = set(psp_tahoe_source_manifest.PUBLISHED) | set(UNPUBLISHED)
        offenders = {
            f"{group} -> {path}"
            for group in (self.sources, self.shared_sources)
            for name, paths in group.items()
            for path in paths
            if path not in known
        }
        self.assertEqual(
            offenders,
            set(),
            "These Tahoe sources exist in no pinned release. If the source is genuinely "
            "published, run ci_tooling/generate_psp_source_manifest.py; if it is not, "
            "record it in UNPUBLISHED with the reason it cannot ship.",
        )

    def test_published_set_matches_the_current_patchsets(self) -> None:
        expected = {path for path in self.required if path not in UNPUBLISHED}
        self.assertEqual(set(psp_tahoe_source_manifest.PUBLISHED), expected)

    def test_unpublished_entries_are_still_referenced(self) -> None:
        stale = sorted(set(UNPUBLISHED) - self.required)
        self.assertEqual(
            stale,
            [],
            "These paths are recorded as unpublished but no patchset references them "
            "anymore; drop them from UNPUBLISHED.",
        )

    def test_every_shared_patch_module_probes_cleanly(self) -> None:
        self.assertEqual(self.shared_failures, {})

    def test_shared_patch_modules_are_probed_directly(self) -> None:
        """The module that carried the RenderBox-25 bug sits outside every probing family."""
        self.assertIn("LegacyMetal31001", self.shared_sources)
        self.assertIn("NonMetal", self.shared_sources)
        self.assertTrue(self.shared_sources)

    def test_shared_modules_extend_the_family_coverage(self) -> None:
        self.assertTrue({
            path
            for paths in self.shared_sources.values()
            for path in paths
        } - {
            path
            for paths in self.sources.values()
            for path in paths
        })

    def test_published_and_unpublished_never_overlap(self) -> None:
        self.assertEqual(
            set(psp_tahoe_source_manifest.PUBLISHED) & set(UNPUBLISHED),
            set(),
        )

    def test_enabled_families_need_no_unpublished_source(self) -> None:
        """The enabled families must survive the image-backed contract."""
        for variant in HardwarePatchsetDetection.hardware_variants():
            self.assertNotIn(variant.__name__, self.probe_failures)

        enabled_names = {
            variant(os_data.tahoe.value, 0, "25A5316i", self._tahoe_constants()).name()
            for variant in HardwarePatchsetDetection.hardware_variants()
        }
        self.assertTrue(enabled_names)

        enabled_sources = {
            path
            for family, paths in self.sources.items()
            if family in enabled_names
            for path in paths
        }
        self.assertTrue(enabled_sources)
        self.assertEqual({path for path in enabled_sources if path in UNPUBLISHED}, set())

    def test_unprobeable_families_match_the_recorded_set(self) -> None:
        self.assertEqual(set(self.probe_failures), set(UNPROBEABLE))

    def test_dormant_gpu_and_audio_families_are_probed(self) -> None:
        """These four needed fixture hardware before they could be enumerated at all."""
        detected = self._probe_constants()
        for variant in (
            amd_legacy_gcn.AMDLegacyGCN,
            amd_polaris.AMDPolaris,
            amd_vega.AMDVega,
            legacy_audio.LegacyAudio,
        ):
            family = variant(os_data.tahoe.value, 0, "25A5316i", detected).name()
            self.assertIn(family, self.sources, f"{variant.__name__} must be probed")

    def test_probe_accounts_for_every_hardware_family(self) -> None:
        """Every registered family is either probed or native at Tahoe."""
        detected = self._probe_constants()
        accounted = []
        for variant in HardwarePatchsetDetection.all_hardware_variants():
            hardware = variant(os_data.tahoe.value, 0, "25A5316i", detected)
            if hardware.native_os():
                continue
            accounted.append(hardware.name())

        self.assertEqual(sorted(accounted), sorted(self.sources))
        self.assertEqual(len(accounted), len(set(accounted)), "duplicate family display names")

    def test_root_patch_source_iteration_skips_root_and_dynamic_sources(self) -> None:
        patches = {
            "Fixture": {
                PatchType.OVERWRITE_SYSTEM_VOLUME: {
                    "/System/Library/Extensions": {
                        "Bundled.kext": "12.5-24",
                        "FromRoot.kext": "/System/Library/Extensions",
                    }
                },
                PatchType.MERGE_DATA_VOLUME: {
                    "/Library/Extensions": {
                        "Merged.kext": "13.7.2-24",
                        "FromMetallibPkg.metallib": DynamicPatchset.MetallibSupportPkg,
                    }
                },
            }
        }
        self.assertEqual(
            sorted(self.contract.iter_root_patch_sources(patches)),
            [
                ("Fixture", "12.5-24/System/Library/Extensions/Bundled.kext"),
                ("Fixture", "13.7.2-24/Library/Extensions/Merged.kext"),
            ],
        )


class PspSourceManifestGeneratorTests(unittest.TestCase):
    @staticmethod
    def _run_main(arguments: list[str]) -> int:
        """Run the generator's CLI without letting its report pollute test output."""
        with contextlib.redirect_stdout(io.StringIO()):
            return generate_psp_source_manifest.main(arguments)

    @staticmethod
    def _inventory_text(entries) -> str:
        """A path listing of the whole image root, directories included."""
        return "\n".join(f"Universal-Binaries/{entry}" for entry in sorted(entries))

    def test_fetch_inventory_closes_commit_and_tree_responses(self) -> None:
        commit_response = unittest.mock.Mock()
        commit_response.json.return_value = {"sha": "abc123"}
        tree_response = unittest.mock.Mock()
        tree_response.json.return_value = {
            "truncated": False,
            "tree": [
                {"path": "Universal-Binaries/12.5-24/System/file"},
                {"path": "metadata.json"},
            ],
        }
        session = unittest.mock.Mock()
        session.get.side_effect = [commit_response, tree_response]

        self.assertEqual(
            generate_psp_source_manifest.fetch_inventory("2.0.0", session=session),
            ("abc123", {"12.5-24/System/file"}),
        )

        commit_response.close.assert_called_once_with()
        tree_response.close.assert_called_once_with()

    def test_fetch_inventory_closes_commit_response_on_failure(self) -> None:
        response = unittest.mock.Mock()
        response.raise_for_status.side_effect = RuntimeError("HTTP 503")
        session = unittest.mock.Mock()
        session.get.return_value = response

        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            generate_psp_source_manifest.fetch_inventory("2.0.0", session=session)

        response.close.assert_called_once_with()
        session.get.assert_called_once()

    def test_fetch_inventory_closes_tree_response_when_tree_is_truncated(self) -> None:
        commit_response = unittest.mock.Mock()
        commit_response.json.return_value = {"sha": "abc123"}
        tree_response = unittest.mock.Mock()
        tree_response.json.return_value = {"truncated": True, "tree": []}
        session = unittest.mock.Mock()
        session.get.side_effect = [commit_response, tree_response]

        with self.assertRaisesRegex(RuntimeError, "Truncated tree"):
            generate_psp_source_manifest.fetch_inventory("2.0.0", session=session)

        commit_response.close.assert_called_once_with()
        tree_response.close.assert_called_once_with()

    def test_parse_inventory_strips_the_image_root(self) -> None:
        self.assertEqual(
            generate_psp_source_manifest.parse_inventory(
                "# comment\n\nUniversal-Binaries/12.5-24/x\nplain/path\n"
            ),
            {"12.5-24/x", "plain/path"},
        )

    def test_published_sources_is_a_sorted_intersection(self) -> None:
        self.assertEqual(
            generate_psp_source_manifest.published_sources(
                ["b", "a", "c"], {"c", "a", "z"}
            ),
            ["a", "c"],
        )

    def test_read_inventory_walks_a_mounted_image_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "12.5-24" / "System").mkdir(parents=True)
            (root / "12.5-24" / "System" / "file").touch()
            self.assertEqual(
                generate_psp_source_manifest.read_inventory(root),
                {"12.5-24", "12.5-24/System", "12.5-24/System/file"},
            )

    def test_read_inventory_accepts_a_text_listing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            listing = Path(directory) / "inventory.txt"
            listing.write_text("Universal-Binaries/12.5-24/x\n")
            self.assertEqual(generate_psp_source_manifest.read_inventory(listing), {"12.5-24/x"})

    def test_replace_frozenset_block_preserves_surrounding_lines(self) -> None:
        source = (
            '"""doc"""\n\n'
            'PACKAGE_VERSION: str = "old"\n\n'
            "PUBLISHED: frozenset[str] = frozenset({\n"
            '    "stale",\n'
            "})\n"
        )
        updated = generate_psp_source_manifest.replace_frozenset_block(
            source, "PUBLISHED", ["fresh"]
        )
        self.assertEqual(
            updated,
            '"""doc"""\n\n'
            'PACKAGE_VERSION: str = "old"\n\n'
            "PUBLISHED: frozenset[str] = frozenset({\n"
            '    "fresh",\n'
            "})\n",
        )

    def test_replace_scalar_only_touches_its_own_line(self) -> None:
        source = 'PACKAGE_VERSION: str = "1"\nTAG_COMMIT: str = "abc"\n'
        self.assertEqual(
            generate_psp_source_manifest.replace_scalar(source, "TAG_COMMIT", "def"),
            'PACKAGE_VERSION: str = "1"\nTAG_COMMIT: str = "def"\n',
        )

    def test_extract_frozenset_reads_the_named_set(self) -> None:
        self.assertEqual(
            generate_psp_source_manifest.extract_frozenset(
                'PUBLISHED: frozenset[str] = frozenset({\n    "a",\n    "b",\n})\n',
                "PUBLISHED",
            ),
            frozenset({"a", "b"}),
        )

    def test_render_is_idempotent_on_the_checked_in_manifest(self) -> None:
        source = generate_psp_source_manifest.TARGET_FILE.read_text()
        self.assertEqual(
            generate_psp_source_manifest.render_manifest(
                source,
                psp_tahoe_source_manifest.PACKAGE_VERSION,
                psp_tahoe_source_manifest.PUBLISHED,
                psp_tahoe_source_manifest.SOURCE_DIRECTORIES,
                psp_tahoe_source_manifest.TAG_COMMIT,
            ),
            source,
        )

    def test_describe_changes_reports_both_directions(self) -> None:
        self.assertEqual(
            generate_psp_source_manifest.describe_changes(frozenset({"gone"}), ["added"]),
            ["  removed  gone", "  added    added"],
        )

    def test_check_passes_against_an_inventory_of_the_published_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "inventory.txt"
            inventory.write_text(
                self._inventory_text(
                    psp_tahoe_source_manifest.PUBLISHED
                    | psp_tahoe_source_manifest.SOURCE_DIRECTORIES
                )
            )
            self.assertEqual(
                self._run_main(
                    [
                        "--check",
                        "--file", str(generate_psp_source_manifest.TARGET_FILE),
                        "--inventory", str(inventory),
                    ]
                ),
                0,
            )

    def test_check_fails_when_the_inventory_is_missing_a_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "inventory.txt"
            published = sorted(psp_tahoe_source_manifest.PUBLISHED)
            inventory.write_text(
                self._inventory_text(
                    (set(published) - {published[0]})
                    | psp_tahoe_source_manifest.SOURCE_DIRECTORIES
                )
            )
            self.assertEqual(
                self._run_main(
                    [
                        "--check",
                        "--file", str(generate_psp_source_manifest.TARGET_FILE),
                        "--inventory", str(inventory),
                    ]
                ),
                1,
            )

    def test_file_rewrite_preserves_the_rest_of_the_module(self) -> None:
        marker = "# a hand-written trailing note\n"
        source = generate_psp_source_manifest.TARGET_FILE.read_text()
        emptied = generate_psp_source_manifest.replace_frozenset_block(
            source, "PUBLISHED", []
        ) + marker

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "manifest.py"
            target.write_text(emptied)
            inventory = Path(directory) / "inventory.txt"
            inventory.write_text(
                self._inventory_text(
                    psp_tahoe_source_manifest.PUBLISHED
                    | psp_tahoe_source_manifest.SOURCE_DIRECTORIES
                )
            )
            self.assertEqual(
                self._run_main(["--file", str(target), "--inventory", str(inventory)]),
                0,
            )
            rewritten = target.read_text()

        self.assertTrue(rewritten.endswith(marker))
        self.assertTrue(rewritten.startswith(source.splitlines()[0]))
        self.assertEqual(
            generate_psp_source_manifest.extract_frozenset(rewritten, "PUBLISHED"),
            psp_tahoe_source_manifest.PUBLISHED,
        )


if __name__ == "__main__":
    unittest.main()
