"""One traversal for "what does this patchset read off disk", used everywhere.

The installer, the GUI validator and the release build contract each composed a
patchset source's path independently, in three styles: string concatenation with a
`startswith("/")` test, string concatenation against the payload root, and `Path`
joins. Any one of them could be changed without the others, so the build could go
green while an install aborted, or the reverse.

`iter_patchset_sources` answers the question once. These tests pin what it must
return, prove the installer and validator actually behave that way, and guard
against the traversal being written a fourth time.
"""

import tempfile
import types
import unittest

from pathlib import Path
from unittest import mock

from opencore_legacy_patcher import constants
from opencore_legacy_patcher.sys_patch import sys_patch
from opencore_legacy_patcher.sys_patch.patchsets import (
    COPY_OPERATIONS,
    DynamicPatchset,
    PayloadSourceError,
    format_missing_sources,
    is_runtime_sourced,
    iter_patchset_sources,
)
from opencore_legacy_patcher.sys_patch.patchsets.base import PatchType

from ci_tooling.build_modules.payload_contract import PayloadContract


REPO_ROOT: Path = Path(__file__).resolve().parents[1]
EXTENSIONS_PATH: str = "/System/Library/Extensions"

# Every consumer of the shared traversal, and the file whose composition it replaced.
CONSUMERS: tuple[str, ...] = (
    "opencore_legacy_patcher/sys_patch/sys_patch.py",
    "opencore_legacy_patcher/support/validation.py",
    "ci_tooling/build_modules/payload_contract.py",
)


class PatchsetSourceTraversalTests(unittest.TestCase):
    def test_source_path_joins_version_destination_and_filename(self) -> None:
        patches = {
            "Example": {
                PatchType.OVERWRITE_SYSTEM_VOLUME: {
                    EXTENSIONS_PATH: {"Example.kext": "13.7.2-25"},
                }
            }
        }
        self.assertEqual(
            list(iter_patchset_sources(patches)),
            [("Example", "13.7.2-25/System/Library/Extensions/Example.kext", True)],
        )

    def test_path_matches_the_composition_it_replaced(self) -> None:
        """The old string concatenation and the `Path` join name the same file."""
        patches = {
            "Example": {
                PatchType.MERGE_SYSTEM_VOLUME: {
                    "/usr/libexec": {"wifip2pd": "13.7.2-25"},
                }
            }
        }
        _, source, _ = next(iter(iter_patchset_sources(patches)))
        self.assertEqual(source, "13.7.2-25/usr/libexec/wifip2pd")
        self.assertEqual(source, "13.7.2-25" + "/usr/libexec" + "/" + "wifip2pd")

    def test_only_copy_operations_are_walked(self) -> None:
        """`REMOVE_*` deletes and `EXECUTE` runs a process; neither names a source."""
        patches = {
            "Example": {
                PatchType.EXECUTE: {"echo hello": True},
                PatchType.REMOVE_SYSTEM_VOLUME: {EXTENSIONS_PATH: {"Gone.kext": "1.0"}},
                PatchType.REMOVE_DATA_VOLUME: {EXTENSIONS_PATH: {"Gone.kext": "1.0"}},
            }
        }
        self.assertEqual(list(iter_patchset_sources(patches)), [])
        self.assertEqual(
            set(COPY_OPERATIONS),
            {
                PatchType.OVERWRITE_SYSTEM_VOLUME,
                PatchType.OVERWRITE_DATA_VOLUME,
                PatchType.MERGE_SYSTEM_VOLUME,
                PatchType.MERGE_DATA_VOLUME,
            },
        )

    def test_runtime_computed_sources_are_skipped_by_member_and_by_value(self) -> None:
        """They name no path yet, so they must not be reported as a missing file."""
        patches = {
            "Metal 3802": {
                PatchType.MERGE_SYSTEM_VOLUME: {
                    EXTENSIONS_PATH: {
                        "ByMember.metallib": DynamicPatchset.MetallibSupportPkg,
                        "ByValue.metallib": "MetallibSupportPkg",
                    },
                }
            }
        }
        self.assertEqual(list(iter_patchset_sources(patches)), [])
        self.assertTrue(is_runtime_sourced(DynamicPatchset.MetallibSupportPkg))
        self.assertTrue(is_runtime_sourced("MetallibSupportPkg"))

    def test_an_unhashable_source_is_not_a_runtime_source(self) -> None:
        """The predecessor raised `TypeError` here and swallowed it with `pass`."""
        self.assertFalse(is_runtime_sourced(["not", "a", "path"]))
        self.assertFalse(is_runtime_sourced("13.7.2-25"))

    def test_sources_outside_the_payload_are_flagged_and_left_out_of_it(self) -> None:
        """A `/`-prefixed source is read from the booted root, not the payload."""
        patches = {
            "Root Sourced": {
                PatchType.OVERWRITE_SYSTEM_VOLUME: {
                    EXTENSIONS_PATH: {"Example.kext": "/System/Library/Frameworks"},
                }
            }
        }
        self.assertEqual(
            list(iter_patchset_sources(patches)),
            [
                (
                    "Root Sourced",
                    "/System/Library/Frameworks/System/Library/Extensions/Example.kext",
                    False,
                )
            ],
        )
        self.assertEqual(
            list(PayloadContract.iter_root_patch_sources(patches)),
            [],
            "a payload check must never require a file the payload does not ship",
        )

    def test_message_names_every_missing_source_and_its_patchset(self) -> None:
        message = format_missing_sources(
            [("Modern Wireless", "b"), ("Modern Audio", "a")]
        )
        self.assertIn("Modern Audio: a", message)
        self.assertIn("Modern Wireless: b", message)
        self.assertLess(
            message.index("Modern Audio: a"),
            message.index("Modern Wireless: b"),
            "the list is sorted so the message is stable between runs",
        )


class InstallerPreflightTests(unittest.TestCase):
    """`_preflight_checks` is the runtime half; these exercise the real method."""

    def _patcher(self) -> sys_patch.PatchSysVolume:
        patcher = sys_patch.PatchSysVolume.__new__(sys_patch.PatchSysVolume)
        patcher.constants = constants.Constants()
        # Read while building the kernel-cache helper's arguments
        patcher.mount_location_data = "/System/Volumes/Data"
        patcher.skip_root_kmutil_requirement = False
        return patcher

    def _allow_completion(self, patcher: sys_patch.PatchSysVolume) -> None:
        """Neutralise everything `_preflight_checks` does after the source check."""
        patcher._clean_skylight_plugins = mock.Mock()
        patcher._delete_nonmetal_enforcement = mock.Mock()
        patcher._merge_kdk_with_root = mock.Mock()

    def test_installer_reports_every_missing_source_at_once(self) -> None:
        """The old behaviour aborted on the first miss, hiding the rest."""
        patches = {
            "Modern Wireless": {
                PatchType.MERGE_SYSTEM_VOLUME: {
                    "/usr/libexec": {"wifip2pd": "13.7.2-25"},
                    EXTENSIONS_PATH: {"AirportBrcmFixup.kext": "13.7.2-25"},
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PayloadSourceError) as raised:
                self._patcher()._preflight_checks(patches, directory)

        message = str(raised.exception)
        self.assertIn("Modern Wireless: 13.7.2-25/usr/libexec/wifip2pd", message)
        self.assertIn(
            "Modern Wireless: 13.7.2-25/System/Library/Extensions/AirportBrcmFixup.kext",
            message,
        )

    def test_installer_finds_sources_beneath_the_payload_root(self) -> None:
        patches = {
            "Modern Wireless": {
                PatchType.MERGE_SYSTEM_VOLUME: {
                    "/usr/libexec": {"wifip2pd": "13.7.2-25"},
                }
            }
        }
        patcher = self._patcher()
        self._allow_completion(patcher)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "13.7.2-25/usr/libexec").mkdir(parents=True)
            (Path(directory) / "13.7.2-25/usr/libexec/wifip2pd").touch()
            with mock.patch.object(sys_patch.kernelcache, "KernelCacheSupport"):
                self.assertEqual(patcher._preflight_checks(patches, directory), patches)

    def test_installer_checks_a_root_sourced_file_where_it_actually_lives(self) -> None:
        """Root-sourced entries were never prefixed with the payload root."""
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "usr/libexec").mkdir(parents=True)
            (Path(directory) / "usr/libexec/wifip2pd").touch()
            patches = {
                "Root Sourced": {
                    PatchType.MERGE_SYSTEM_VOLUME: {
                        "/usr/libexec": {"wifip2pd": directory},
                    }
                }
            }
            patcher = self._patcher()
            self._allow_completion(patcher)
            with mock.patch.object(sys_patch.kernelcache, "KernelCacheSupport"):
                self.assertEqual(patcher._preflight_checks(patches, "/nonexistent"), patches)

    def test_installer_resolves_runtime_sources_before_checking_them(self) -> None:
        """A `DynamicPatchset` names no path until it is resolved, and it is absolute."""
        patches = {
            "Metal 3802": {
                PatchType.MERGE_SYSTEM_VOLUME: {
                    EXTENSIONS_PATH: {"default.metallib": DynamicPatchset.MetallibSupportPkg},
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            patcher = self._patcher()
            self._allow_completion(patcher)
            with mock.patch.object(
                sys_patch.PatchSysVolume,
                "_resolve_dynamic_patchset",
                return_value=f"{directory}/MetallibSupportPkg",
            ), mock.patch.object(sys_patch.kernelcache, "KernelCacheSupport"):
                (Path(directory) / "MetallibSupportPkg/System/Library/Extensions").mkdir(
                    parents=True
                )
                (
                    Path(directory)
                    / "MetallibSupportPkg/System/Library/Extensions/default.metallib"
                ).touch()
                self.assertEqual(patcher._preflight_checks(patches, directory), patches)

            self.assertEqual(
                patches["Metal 3802"][PatchType.MERGE_SYSTEM_VOLUME][EXTENSIONS_PATH][
                    "default.metallib"
                ],
                f"{directory}/MetallibSupportPkg",
                "the entry is rewritten in place, so the installer later reads the same path",
            )

    def test_installer_still_fails_a_runtime_source_that_cannot_be_resolved(self) -> None:
        patches = {
            "Metal 3802": {
                PatchType.MERGE_SYSTEM_VOLUME: {
                    EXTENSIONS_PATH: {"default.metallib": DynamicPatchset.MetallibSupportPkg},
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            patcher = self._patcher()
            self._allow_completion(patcher)
            with mock.patch.object(
                sys_patch.PatchSysVolume,
                "_resolve_dynamic_patchset",
                return_value=f"{directory}/Nowhere",
            ), self.assertRaises(PayloadSourceError):
                patcher._preflight_checks(patches, directory)


class SingleTraversalTests(unittest.TestCase):
    """The traversal must stay single: these fail if a fourth copy is written."""

    def test_every_consumer_uses_the_shared_traversal(self) -> None:
        for relative_path in CONSUMERS:
            with self.subTest(consumer=relative_path):
                source = (REPO_ROOT / relative_path).read_text()
                self.assertIn(
                    "iter_patchset_sources",
                    source,
                    f"{relative_path} no longer uses the shared traversal",
                )

    def test_no_consumer_composes_a_source_path_itself(self) -> None:
        offenders = [
            relative_path
            for relative_path in CONSUMERS
            if '+ "/" +' in (REPO_ROOT / relative_path).read_text()
            or ".lstrip(" in (REPO_ROOT / relative_path).read_text()
        ]
        self.assertEqual(
            offenders,
            [],
            "compose source paths through iter_patchset_sources instead",
        )

    def test_no_file_re_enumerates_the_copy_operations(self) -> None:
        literal = (
            "OVERWRITE_DATA_VOLUME, PatchType.MERGE_SYSTEM_VOLUME, "
            "PatchType.MERGE_DATA_VOLUME"
        )
        roots = (REPO_ROOT / "opencore_legacy_patcher", REPO_ROOT / "ci_tooling")
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for root in roots
            for path in sorted(root.rglob("*.py"))
            if literal in path.read_text()
        ]
        self.assertEqual(offenders, [], "use COPY_OPERATIONS instead")

    def test_validation_keeps_active_patchset_files_inside_the_payload(self) -> None:
        """`_find_unused_files` resolves these against the payload root, so a
        root-sourced absolute path would be reported as dead weight."""
        source = (REPO_ROOT / "opencore_legacy_patcher/support/validation.py").read_text()
        block = source.split("missing: list[tuple[str, str]] = []")[1].split("if missing:")[0]
        self.assertIn(
            "if not from_payload:",
            block,
            "a root-sourced path must not reach active_patchset_files",
        )
        self.assertIn("resolved = payload_root / source_file", block)
        self.assertIn("self.active_patchset_files.append(str(resolved))", block)


if __name__ == "__main__":
    unittest.main()
