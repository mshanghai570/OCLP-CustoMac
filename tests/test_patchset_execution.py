"""`_execute_patchset` is the copy half of root patching, and it had no direct test.

`_preflight_checks` decides whether every source a patchset names exists; the copy
loop then reads those sources. Both compose the path through the shared traversal
(`iter_patchset_sources` / `source_entry_path`), and if they ever disagree the build
stays green while an install aborts on a user's machine. These tests drive the real
loop against a real payload tree, so the agreement is exercised instead of assumed.

The volume routing, the removal-before-install ordering and the AuxKC relocation
rewrite (which moves a patchset entry to a different directory mid-loop) are pinned
here too; each was reachable only through a full root patch before.
"""

import plistlib
import tempfile
import types
import unittest

from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from opencore_legacy_patcher.datasets import os_data
from opencore_legacy_patcher.sys_patch import sys_patch
from opencore_legacy_patcher.sys_patch.kernelcache.kernel_collection import support as kernel_collection_support
from opencore_legacy_patcher.sys_patch.patchsets.base import PatchType


EXTENSIONS_PATH: str = "/System/Library/Extensions"
FRAMEWORKS_PATH: str = "/System/Library/Frameworks"
USB_PATH: str = "/usr/libexec"
LIBRARY_EXTENSIONS_PATH: str = "/Library/Extensions"

MOUNT_SYSTEM: str = "/System/Volumes/Update/mnt1"
MOUNT_DATA: str = "/System/Volumes/Data"

VERSION: str = "12.5"


def _identity_relocation(install_file, source, directory, destination):
    """Stand in for an AuxKC helper that leaves the destination alone."""

    return destination


class PatchsetExecutionTests(unittest.TestCase):
    def test_auxkc_support_closes_kext_info_plist_on_read_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "AppleTest.kext/Contents/Info.plist"
            source.parent.mkdir(parents=True)
            with source.open("wb") as plist_file:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.apple.driver.Test",
                        "OSBundleRequired": "Root",
                    },
                    plist_file,
                )
            instance = kernel_collection_support.KernelCacheSupport.__new__(
                kernel_collection_support.KernelCacheSupport
            )
            instance.skip_root_kmutil_requirement = True
            instance.detected_os = os_data.os_data.ventura
            instance.mount_location_data = str(root / "mounted-data")

            with mock.patch.object(
                kernel_collection_support.plistlib,
                "load",
                wraps=plistlib.load,
            ) as plist_load, mock.patch.object(
                kernel_collection_support.plistlib,
                "dump",
                wraps=plistlib.dump,
            ) as plist_dump:
                destination = instance.add_auxkc_support(
                    "AppleTest.kext",
                    str(root),
                    "/System/Library/Extensions",
                    "/System/Library/Extensions",
                )

            self.assertEqual(destination, f"{root}/mounted-data/Library/Extensions")
            self.assertEqual(plist_load.call_count, 1)
            self.assertEqual(plist_dump.call_count, 1)
            self.assertTrue(plist_load.call_args.args[0].closed)
            self.assertTrue(plist_dump.call_args.args[1].closed)
            with source.open("rb") as plist_file:
                self.assertEqual(plistlib.load(plist_file)["OSBundleRequired"], "Auxiliary")

    def test_non_apple_auxkc_support_closes_plist_when_no_write_is_needed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "ThirdParty.kext/Contents/Info.plist"
            source.parent.mkdir(parents=True)
            with source.open("wb") as plist_file:
                plistlib.dump({"CFBundleIdentifier": "org.example.driver"}, plist_file)
            instance = kernel_collection_support.KernelCacheSupport.__new__(
                kernel_collection_support.KernelCacheSupport
            )
            instance.skip_root_kmutil_requirement = True
            instance.detected_os = os_data.os_data.ventura
            instance.mount_location_data = str(root / "mounted-data")

            with mock.patch.object(
                kernel_collection_support.plistlib,
                "load",
                wraps=plistlib.load,
            ) as plist_load:
                instance.add_auxkc_support(
                    "ThirdParty.kext",
                    str(root),
                    "/System/Library/Extensions",
                    "/System/Library/Extensions",
                )

            self.assertTrue(plist_load.call_args.args[0].closed)

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.payload_root = Path(self._temporary.name)

    def _patcher(self, skip_kmutil_requirement: bool = False) -> sys_patch.PatchSysVolume:
        patcher = sys_patch.PatchSysVolume.__new__(sys_patch.PatchSysVolume)
        # The copy loop and the preflight only read these three off Constants, and
        # the real `payload_local_binaries_root_path` is a read-only property that
        # would point at the shipped payload.
        patcher.constants = types.SimpleNamespace(
            payload_local_binaries_root_path=self.payload_root,
            detected_os=os_data.os_data.tahoe,
            needs_to_open_preferences=False,
        )
        patcher.mount_location = MOUNT_SYSTEM
        patcher.mount_location_data = MOUNT_DATA
        patcher.skip_root_kmutil_requirement = skip_kmutil_requirement
        patcher.needs_kmutil_exemptions = False
        # Everything the real preflight does after the source check.
        patcher._clean_skylight_plugins = mock.Mock()
        patcher._delete_nonmetal_enforcement = mock.Mock()
        patcher._merge_kdk_with_root = mock.Mock()
        patcher._write_patchset = mock.Mock()
        return patcher

    def _write_source(self, source_version: str, destination: str, filename: str) -> Path:
        """Place a source where a patchset says it lives, and return that folder."""

        directory = self.payload_root / source_version / destination.lstrip("/")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).touch()
        return directory

    @contextmanager
    def _patched_patcher(self, patcher, relocate=None, authentication: bool = False):
        with mock.patch.object(sys_patch, "install_new_file") as install_file, \
             mock.patch.object(sys_patch, "remove_file") as remove_file, \
             mock.patch.object(sys_patch.kernelcache, "KernelCacheSupport") as kc_class:
            instance = kc_class.return_value
            instance.add_auxkc_support.side_effect = relocate or _identity_relocation
            instance.check_kexts_needs_authentication.return_value = authentication
            yield types.SimpleNamespace(
                install_file=install_file,
                remove_file=remove_file,
                kernel_cache=instance,
                patcher=patcher,
            )

    def test_install_reads_a_source_through_the_path_the_preflight_validated(self) -> None:
        """The payload is populated only at the path the preflight checked, so a
        copy composed differently would read outside it."""

        patches = {
            "Modern Wireless": {
                PatchType.OVERWRITE_SYSTEM_VOLUME: {
                    EXTENSIONS_PATH: {"AppleIntelSKLGraphics.kext": VERSION},
                },
                PatchType.MERGE_SYSTEM_VOLUME: {
                    FRAMEWORKS_PATH: {"Metal.framework": f"{VERSION}-3802"},
                },
            }
        }
        system_directory = self._write_source(VERSION, EXTENSIONS_PATH, "AppleIntelSKLGraphics.kext")
        framework_directory = self._write_source(f"{VERSION}-3802", FRAMEWORKS_PATH, "Metal.framework")

        with self._patched_patcher(self._patcher()) as patched:
            patched.patcher._execute_patchset(patches)

        self.assertEqual(
            patched.install_file.call_args_list,
            [
                mock.call(
                    str(system_directory),
                    MOUNT_SYSTEM + EXTENSIONS_PATH,
                    "AppleIntelSKLGraphics.kext",
                    PatchType.OVERWRITE_SYSTEM_VOLUME,
                ),
                mock.call(
                    str(framework_directory),
                    MOUNT_SYSTEM + FRAMEWORKS_PATH,
                    "Metal.framework",
                    PatchType.MERGE_SYSTEM_VOLUME,
                ),
            ],
        )

    def test_install_routes_each_patch_type_to_its_own_volume(self) -> None:
        patches = {
            "Modern Audio": {
                PatchType.OVERWRITE_SYSTEM_VOLUME: {EXTENSIONS_PATH: {"System.kext": VERSION}},
                PatchType.MERGE_SYSTEM_VOLUME: {FRAMEWORKS_PATH: {"System.framework": VERSION}},
                PatchType.OVERWRITE_DATA_VOLUME: {USB_PATH: {"Data": VERSION}},
                PatchType.MERGE_DATA_VOLUME: {LIBRARY_EXTENSIONS_PATH: {"Data.kext": VERSION}},
            }
        }
        for source_version, destination, filename in (
            (VERSION, EXTENSIONS_PATH, "System.kext"),
            (VERSION, FRAMEWORKS_PATH, "System.framework"),
            (VERSION, USB_PATH, "Data"),
            (VERSION, LIBRARY_EXTENSIONS_PATH, "Data.kext"),
        ):
            self._write_source(source_version, destination, filename)

        with self._patched_patcher(self._patcher()) as patched:
            patched.patcher._execute_patchset(patches)

        destinations = {
            call.args[2]: call.args[1] for call in patched.install_file.call_args_list
        }
        self.assertEqual(
            destinations,
            {
                "System.kext": MOUNT_SYSTEM + EXTENSIONS_PATH,
                "System.framework": MOUNT_SYSTEM + FRAMEWORKS_PATH,
                "Data": MOUNT_DATA + USB_PATH,
                "Data.kext": MOUNT_DATA + LIBRARY_EXTENSIONS_PATH,
            },
        )

    def test_a_kext_patched_into_the_data_volume_extensions_needs_kmutil_exemptions(self) -> None:
        """`/Library/Extensions` is the path whose rebuild must bypass kmutil."""

        patches = {
            "Nvidia Web Driver": {
                PatchType.OVERWRITE_DATA_VOLUME: {
                    LIBRARY_EXTENSIONS_PATH: {"GeForceWeb.kext": VERSION},
                }
            }
        }
        self._write_source(VERSION, LIBRARY_EXTENSIONS_PATH, "GeForceWeb.kext")
        patcher = self._patcher()

        with self._patched_patcher(patcher) as patched:
            patched.patcher._execute_patchset(patches)

        self.assertTrue(patcher.needs_kmutil_exemptions)

    def test_removals_run_before_installs_and_from_their_own_volume(self) -> None:
        """Deleting the old kext only after copying the new one would race the cache."""

        patches = {
            "Legacy Audio": {
                PatchType.REMOVE_SYSTEM_VOLUME: {EXTENSIONS_PATH: ["AppleVirtIO.kext"]},
                PatchType.REMOVE_DATA_VOLUME: {LIBRARY_EXTENSIONS_PATH: ["OldAudio.kext"]},
                PatchType.OVERWRITE_SYSTEM_VOLUME: {EXTENSIONS_PATH: {"AppleHDA.kext": VERSION}},
            }
        }
        self._write_source(VERSION, EXTENSIONS_PATH, "AppleHDA.kext")
        order: list[str] = []

        with self._patched_patcher(self._patcher()) as patched:
            patched.install_file.side_effect = lambda *arguments: order.append("install")
            patched.remove_file.side_effect = lambda *arguments: order.append("remove")
            patched.patcher._execute_patchset(patches)

        self.assertEqual(order, ["remove", "remove", "install"])
        self.assertEqual(
            patched.remove_file.call_args_list,
            [
                mock.call(MOUNT_SYSTEM + EXTENSIONS_PATH, "AppleVirtIO.kext"),
                mock.call(MOUNT_DATA + LIBRARY_EXTENSIONS_PATH, "OldAudio.kext"),
            ],
        )

    def test_auxkc_relocation_moves_the_entry_without_copying_twice(self) -> None:
        """The helper hands back a new directory, so the loop rewrites the patchset
        entry in place; the directory the entry came from must not be visited again."""

        patches = {
            "Intel Skylake": {
                PatchType.OVERWRITE_SYSTEM_VOLUME: {
                    EXTENSIONS_PATH: {
                        "AppleIntelSKLGraphics.kext": VERSION,
                        "AppleIntelSKLGraphicsVADriver.bundle": VERSION,
                    }
                }
            }
        }
        self._write_source(VERSION, EXTENSIONS_PATH, "AppleIntelSKLGraphics.kext")
        self._write_source(VERSION, EXTENSIONS_PATH, "AppleIntelSKLGraphicsVADriver.bundle")
        auxkc_path = MOUNT_DATA + LIBRARY_EXTENSIONS_PATH

        def relocate(install_file, source, directory, destination):
            return auxkc_path if install_file.endswith(".kext") else destination

        patcher = self._patcher(skip_kmutil_requirement=True)
        with self._patched_patcher(patcher, relocate=relocate) as patched:
            patched.patcher._execute_patchset(patches)

        self.assertEqual(
            {call.args[2]: call.args[1] for call in patched.install_file.call_args_list},
            {
                "AppleIntelSKLGraphics.kext": auxkc_path,
                "AppleIntelSKLGraphicsVADriver.bundle": MOUNT_SYSTEM + EXTENSIONS_PATH,
            },
        )
        self.assertEqual(
            patched.install_file.call_count,
            2,
            "the relocated entry must not be copied a second time from its old directory",
        )
        self.assertEqual(
            patches["Intel Skylake"][PatchType.OVERWRITE_SYSTEM_VOLUME],
            {
                EXTENSIONS_PATH: {"AppleIntelSKLGraphicsVADriver.bundle": VERSION},
                auxkc_path: {"AppleIntelSKLGraphics.kext": VERSION},
            },
            "the recorded patchset must name where each file actually landed",
        )

    def test_a_missing_source_aborts_before_anything_is_touched(self) -> None:
        """The preflight covers every source, so a partial install is impossible."""

        patches = {
            "Modern Wireless": {
                PatchType.REMOVE_SYSTEM_VOLUME: {EXTENSIONS_PATH: ["Gone.kext"]},
                PatchType.OVERWRITE_SYSTEM_VOLUME: {
                    EXTENSIONS_PATH: {
                        "Present.kext": VERSION,
                        "Absent.kext": VERSION,
                    }
                },
            }
        }
        self._write_source(VERSION, EXTENSIONS_PATH, "Present.kext")
        patcher = self._patcher()

        with self._patched_patcher(patcher) as patched:
            with self.assertRaises(Exception) as raised:
                patched.patcher._execute_patchset(patches)

        self.assertIn("Absent.kext", str(raised.exception))
        self.assertEqual(patched.install_file.call_count, 0)
        self.assertEqual(patched.remove_file.call_count, 0)
        patcher._write_patchset.assert_not_called()

    def test_execute_runs_named_processes_with_and_without_root(self) -> None:
        """The boolean value picks the elevation path; the non-root path needs a shell."""

        patches = {
            "Example": {
                PatchType.EXECUTE: {
                    "/bin/rm -rf /tmp/thing": True,
                    "false || echo hi": False,
                }
            }
        }
        with mock.patch.object(sys_patch, "subprocess_wrapper") as wrapper, \
             mock.patch.object(sys_patch.kernelcache, "KernelCacheSupport"):
            self._patcher()._execute_patchset(patches)

        self.assertEqual(
            wrapper.run_as_root_and_verify.call_args.args[0],
            ["/bin/rm", "-rf", "/tmp/thing"],
        )
        self.assertEqual(wrapper.run_and_verify.call_args.args[0], "false || echo hi")
        self.assertTrue(
            wrapper.run_and_verify.call_args.kwargs["shell"],
            "a non-root command is passed through a shell, unlike the elevated one",
        )


if __name__ == "__main__":
    unittest.main()
