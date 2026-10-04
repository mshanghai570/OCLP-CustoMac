"""Regression tests for plist file-handle lifetimes outside the core EFI builder."""

import io
import plistlib
import tempfile
import types
import unittest

from pathlib import Path
from unittest import mock

from opencore_legacy_patcher.detections import os_probe
from opencore_legacy_patcher.datasets import os_data
from opencore_legacy_patcher.support import (
    analytics_handler,
    arguments,
    commit_info,
    defaults,
    global_settings,
    utilities,
)
from opencore_legacy_patcher.sys_patch import sys_patch
from opencore_legacy_patcher.sys_patch.auto_patcher import install as auto_install
from opencore_legacy_patcher.sys_patch.utilities import kdk_merge


class PlistResourceHandleTests(unittest.TestCase):
    def test_global_settings_read_write_and_defaults_migration_close_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            shared = root / "private" / "settings.plist"
            defaults_file = root / "legacy.plist"
            with defaults_file.open("wb") as plist_file:
                plistlib.dump({"migrated": "yes"}, plist_file)
            defaults_file.chmod(0o600)
            settings = global_settings.GlobalEnviromentSettings(
                settings_path=shared, legacy_paths=(),
            )
            settings.write_property("keep", True)
            settings.write_property("remove", True)
            settings._legacy_paths = (defaults_file,)

            with mock.patch.object(
                global_settings.plistlib,
                "load",
                wraps=plistlib.load,
            ) as plist_load, mock.patch.object(
                global_settings.plistlib,
                "dump",
                wraps=plistlib.dump,
            ) as plist_dump:
                self.assertTrue(settings.read_property("keep"))
                settings.write_property("new", 42)
                settings.delete_property("remove")
                settings._convert_defaults_to_global_settings()

            self.assertTrue(all(call.args[0].closed for call in plist_load.call_args_list))
            self.assertTrue(all(call.args[1].closed for call in plist_dump.call_args_list))
            with shared.open("rb") as plist_file:
                final = plistlib.load(plist_file)
            self.assertEqual(final, {"Developed by Dortania": True, "keep": True, "new": 42, "migrated": "yes"})
            self.assertTrue(defaults_file.exists())

    def test_generate_defaults_closes_global_settings_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings_path = Path(temporary) / "settings.plist"
            with settings_path.open("wb") as plist_file:
                plistlib.dump({"GUI:oc_timeout": 9}, plist_file)
            target_constants = types.SimpleNamespace(oc_timeout=5)

            with mock.patch.object(
                defaults.plistlib,
                "load",
                wraps=plistlib.load,
            ) as plist_load:
                generator = defaults.GenerateDefaults.__new__(defaults.GenerateDefaults)
                generator.host_is_target = True
                generator.ignore_settings_file = False
                generator.constants = target_constants
                generator.settings_plist_path = settings_path
                generator._load_gui_defaults()

            self.assertEqual(target_constants.oc_timeout, 9)
            self.assertEqual(plist_load.call_count, 1)
            self.assertTrue(plist_load.call_args.args[0].closed)

    def test_analytics_country_read_closes_preferences_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            preferences = Path(temporary) / "global.plist"
            with preferences.open("wb") as plist_file:
                plistlib.dump({"Country": "CA"}, plist_file)

            with mock.patch.object(analytics_handler, "GLOBAL_PREFERENCES_PLIST", preferences), \
                 mock.patch.object(
                    analytics_handler.plistlib,
                    "load",
                    wraps=plistlib.load,
                 ) as plist_load:
                analytics = analytics_handler.Analytics.__new__(analytics_handler.Analytics)
                self.assertEqual(analytics._get_country(), "CA")

            self.assertTrue(plist_load.call_args.args[0].closed)

    def test_commit_info_read_closes_embedded_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "OpenCore-Patcher.app"
            binary = app / "Contents/MacOS/OpenCore-Patcher"
            info = app / "Contents/Info.plist"
            binary.parent.mkdir(parents=True)
            binary.touch()
            with info.open("wb") as plist_file:
                plistlib.dump(
                    {"Github": {
                        "Branch": "main",
                        "Commit Date": "2026-10-01",
                        "Commit URL": "https://example/commit/hash",
                        "Commit SHA": "hash",
                        "Repository": "https://example/repo",
                        "Project": "fixture",
                    }},
                    plist_file,
                )

            with mock.patch.object(
                commit_info.plistlib,
                "load",
                wraps=plistlib.load,
            ) as plist_load:
                result = commit_info.ParseCommitInfo(str(binary)).generate_commit_info()

            self.assertEqual(result[0], "main")
            self.assertTrue(plist_load.call_args.args[0].closed)

    def test_auto_patch_service_hashing_closes_files_and_handles_large_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = Path(temporary) / "service.plist"
            content = b"service entry" * 100_000
            service.write_bytes(content)

            digest = auto_install.InstallAutomaticPatchingServices._file_sha256(service)

            self.assertEqual(digest, auto_install.hashlib.sha256(content).hexdigest())

    def test_os_probe_closes_system_version_plist(self) -> None:
        handle = io.BytesIO(plistlib.dumps({"ProductBuildVersion": "25A1"}))
        probe = os_probe.OSProbe()

        with mock.patch.object(os_probe.Path, "open", autospec=True, return_value=handle):
            self.assertEqual(probe.detect_os_build(), "25A1")

        self.assertTrue(handle.closed)

    def test_os_probe_closes_system_version_plist_when_parsing_fails(self) -> None:
        handle = io.BytesIO(b"not a plist")
        probe = os_probe.OSProbe()

        with mock.patch.object(os_probe.Path, "open", autospec=True, return_value=handle):
            with self.assertRaisesRegex(RuntimeError, "Failed to detect OS build"):
                probe.detect_os_build()

        self.assertTrue(handle.closed)

    def test_staged_update_reader_closes_plist(self) -> None:
        handle = io.BytesIO(plistlib.dumps({
            "update-asset-attributes": {"Build": "25A1", "OSVersion": "26.0"},
        }))

        with mock.patch.object(utilities.Path, "exists", autospec=True, return_value=True):
            with mock.patch.object(utilities.Path, "open", autospec=True, return_value=handle):
                self.assertEqual(utilities.fetch_staged_update(), ("26.0", "25A1"))

        self.assertTrue(handle.closed)

    def test_staged_update_reader_closes_malformed_plist(self) -> None:
        handle = io.BytesIO(b"not a plist")

        with mock.patch.object(utilities.Path, "exists", autospec=True, return_value=True):
            with mock.patch.object(utilities.Path, "open", autospec=True, return_value=handle):
                self.assertEqual(utilities.fetch_staged_update(), (None, None))

        self.assertTrue(handle.closed)

    def test_clean_legacy_extensions_closes_kext_plist(self) -> None:
        kext = Path("/Library/Extensions/Fixture.kext")
        handle = io.BytesIO(plistlib.dumps({"GPUCompanionBundles": []}))
        cleaner = arguments.arguments.__new__(arguments.arguments)
        cleaner.constants = types.SimpleNamespace(detected_os=os_data.os_data.sonoma)

        with mock.patch.object(arguments.Path, "glob", autospec=True, return_value=[kext]):
            with mock.patch.object(arguments.Path, "exists", autospec=True, return_value=True):
                with mock.patch.object(arguments.Path, "open", autospec=True, return_value=handle):
                    with mock.patch.object(arguments.subprocess_wrapper, "run_as_root") as remove_kext:
                        cleaner._clean_le_handler()

        self.assertTrue(handle.closed)
        remove_kext.assert_called_once()

    def test_clean_legacy_extensions_closes_malformed_kext_plist(self) -> None:
        kext = Path("/Library/Extensions/Fixture.kext")
        handle = io.BytesIO(b"not a plist")
        cleaner = arguments.arguments.__new__(arguments.arguments)
        cleaner.constants = types.SimpleNamespace(detected_os=os_data.os_data.sonoma)

        with mock.patch.object(arguments.Path, "glob", autospec=True, return_value=[kext]):
            with mock.patch.object(arguments.Path, "exists", autospec=True, return_value=True):
                with mock.patch.object(arguments.Path, "open", autospec=True, return_value=handle):
                    with mock.patch.object(arguments.subprocess_wrapper, "run_as_root") as remove_kext:
                        cleaner._clean_le_handler()

        self.assertTrue(handle.closed)
        remove_kext.assert_not_called()

    def test_kdk_merge_metadata_reader_closes_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / "metadata.plist"
            with metadata.open("wb") as plist_file:
                plistlib.dump({"Kernel Debug Kit Used": "/KDK/fixture.kdk"}, plist_file)
            libkern = root / "System/Library/Extensions/System.kext/PlugIns/Libkern.kext/Libkern"
            libkern.parent.mkdir(parents=True)
            libkern.touch()
            merger = kdk_merge.KernelDebugKitMerge(
                types.SimpleNamespace(),
                str(root),
                False,
            )

            with mock.patch.object(kdk_merge, "ROOT_PATCH_METADATA_PATH", metadata):
                with mock.patch.object(kdk_merge.plistlib, "load", wraps=plistlib.load) as plist_load:
                    self.assertTrue(merger._matching_kdk_already_merged("/KDK/fixture.kdk"))

        self.assertEqual(plist_load.call_count, 1)
        self.assertTrue(plist_load.call_args.args[0].closed)

    def test_kdk_merge_metadata_reader_closes_malformed_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / "metadata.plist"
            metadata.touch()
            libkern = root / "System/Library/Extensions/System.kext/PlugIns/Libkern.kext/Libkern"
            libkern.parent.mkdir(parents=True)
            libkern.touch()
            merger = kdk_merge.KernelDebugKitMerge(
                types.SimpleNamespace(),
                str(root),
                False,
            )
            handle = io.BytesIO(b"not a plist")

            with mock.patch.object(kdk_merge, "ROOT_PATCH_METADATA_PATH", metadata):
                with mock.patch.object(kdk_merge.Path, "open", autospec=True, return_value=handle):
                    self.assertFalse(merger._matching_kdk_already_merged("/KDK/fixture.kdk"))

        self.assertTrue(handle.closed)

    def test_sys_patch_sanity_reader_closes_mounted_system_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            version_plist = root / "System/Library/CoreServices/SystemVersion.plist"
            version_plist.parent.mkdir(parents=True)
            with version_plist.open("wb") as plist_file:
                plistlib.dump(
                    {"ProductVersion": "26.0", "ProductBuildVersion": "25A1"},
                    plist_file,
                )
            patcher = sys_patch.PatchSysVolume.__new__(sys_patch.PatchSysVolume)
            patcher.mount_location = str(root)
            patcher.constants = types.SimpleNamespace(
                detected_os_build="25A2",
                detected_os_version="26.0",
            )

            with mock.patch.object(sys_patch.plistlib, "load", wraps=plistlib.load) as plist_load:
                self.assertFalse(patcher._run_sanity_checks())

        self.assertEqual(plist_load.call_count, 1)
        self.assertTrue(plist_load.call_args.args[0].closed)

    def test_sys_patch_sanity_reader_closes_malformed_plist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            version_plist = root / "System/Library/CoreServices/SystemVersion.plist"
            version_plist.parent.mkdir(parents=True)
            version_plist.touch()
            patcher = sys_patch.PatchSysVolume.__new__(sys_patch.PatchSysVolume)
            patcher.mount_location = str(root)
            patcher.constants = types.SimpleNamespace(
                detected_os_build="25A1",
                detected_os_version="26.0",
            )
            handle = io.BytesIO(b"not a plist")

            with mock.patch.object(sys_patch.Path, "open", autospec=True, return_value=handle):
                self.assertFalse(patcher._run_sanity_checks())

        self.assertTrue(handle.closed)

    def test_auto_patch_rsr_plists_are_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kext = root / "Test.kext/Contents/Info.plist"
            kext.parent.mkdir(parents=True)
            with kext.open("wb") as plist_file:
                plistlib.dump({"GPUCompanionBundles": []}, plist_file)
            cryptex = root / "OS.dmg"
            cryptex.touch()
            rsr_plist = root / "rsr.plist"
            with rsr_plist.open("wb") as plist_file:
                plistlib.dump({"ProgramArguments": [], "WatchPaths": []}, plist_file)
            constants = types.SimpleNamespace(rsr_monitor_launch_daemon_path=rsr_plist)
            installer = auto_install.InstallAutomaticPatchingServices(constants)
            original_glob = Path.glob
            original_exists = Path.exists

            with mock.patch.object(auto_install.utilities, "get_preboot_uuid", return_value="fixture"), \
                 mock.patch.object(Path, "glob", autospec=True, side_effect=lambda path, pattern: [root / "Test.kext"] if str(path) == "/Library/Extensions" else original_glob(path, pattern)), \
                 mock.patch.object(Path, "exists", autospec=True, return_value=True), \
                 mock.patch.object(
                    auto_install.plistlib,
                    "load",
                    wraps=plistlib.load,
                 ) as plist_load, mock.patch.object(
                    auto_install.plistlib,
                    "dump",
                    wraps=plistlib.dump,
                 ) as plist_dump:
                self.assertTrue(installer._create_rsr_monitor_daemon())

            self.assertEqual(plist_load.call_count, 2)
            self.assertTrue(all(call.args[0].closed for call in plist_load.call_args_list))
            self.assertTrue(plist_dump.call_args.args[1].closed)


if __name__ == "__main__":
    unittest.main()
