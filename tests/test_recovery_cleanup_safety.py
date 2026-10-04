"""Recovery must tolerate bad history and preserve unrelated Data-volume files."""

import os
import plistlib
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.sys_patch import sys_patch
from opencore_legacy_patcher.sys_patch.kernelcache.kernel_collection import support
from opencore_legacy_patcher.sys_patch.patchsets import PatchType
from opencore_legacy_patcher.sys_patch.root_state import ROOT_PATCH_METADATA_SCHEMA


class RecoveryCleanupSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.metadata = self.root / "history.plist"
        self.extensions = self.root / "Library/Extensions"
        self.extensions.mkdir(parents=True)
        self.cleaner = support.KernelCacheSupport(str(self.root), 25, False)

    def history(self, operations):
        return {
            "Metadata Schema": ROOT_PATCH_METADATA_SCHEMA,
            "Project Identity": "test-project",
            "Installed Patches": ["Modern Audio"],
            "Modern Audio": operations,
        }

    def run_cleanup(self, payload):
        self.metadata.write_bytes(payload)
        with mock.patch.object(support, "ROOT_PATCH_METADATA_PATH", self.metadata), \
             mock.patch.object(support.subprocess_wrapper, "run_as_root") as run, \
             mock.patch.object(support.subprocess_wrapper, "run_as_root_and_verify") as verified:
            result = self.cleaner.clean_auxiliary_kc(expected_project_identity="test-project")
        return result, run, verified

    def test_malformed_or_unrecognized_history_never_deletes(self):
        invalid = [b"corrupt", plistlib.dumps([]), plistlib.dumps({}),
                   plistlib.dumps({"OCLP-R": "1.0"})]
        for operations in ("invalid", [], {PatchType.MERGE_DATA_VOLUME: []},
                           {PatchType.MERGE_DATA_VOLUME: {"/Library/Extensions": []}}):
            invalid.append(plistlib.dumps(self.history(operations)))
        for payload in invalid:
            with self.subTest(payload=payload):
                result, run, verified = self.run_cleanup(payload)
                self.assertFalse(result)
                run.assert_not_called()
                verified.assert_not_called()

    def test_old_user_kext_is_preserved_without_history(self):
        user_kext = self.extensions / "UserDriver.kext"
        user_kext.mkdir()
        os.utime(user_kext, (0, 0))
        with mock.patch.object(support, "ROOT_PATCH_METADATA_PATH", self.metadata), \
             mock.patch.object(support.subprocess_wrapper, "run_as_root") as run:
            self.cleaner.clean_auxiliary_kc()
        run.assert_not_called()
        self.assertTrue(user_kext.exists())

    def test_valid_cleanup_only_removes_declared_data_kext(self):
        owned = self.extensions / "Owned.kext"
        owned.mkdir()
        user = self.extensions / "User.kext"
        user.mkdir()
        metadata = self.history({
            PatchType.MERGE_DATA_VOLUME: {"/Library/Extensions": {"Owned.kext": "source"}},
            PatchType.MERGE_SYSTEM_VOLUME: {"/System/Library/Extensions": {"User.kext": "source"}},
        })
        self.metadata.write_bytes(plistlib.dumps(metadata))
        with mock.patch.object(support, "ROOT_PATCH_METADATA_PATH", self.metadata), \
             mock.patch.object(support.subprocess_wrapper, "run_as_root_and_verify") as run:
            self.assertTrue(self.cleaner.clean_auxiliary_kc(expected_project_identity="test-project"))
        self.assertEqual(run.call_args.args[0], ["/bin/rm", "-Rf", str(owned)])
        self.assertTrue(user.exists())

    def test_foreign_identity_and_traversal_are_rejected_before_deletion(self):
        for identity, filename in (("foreign", "Owned.kext"), ("test-project", "../Escape.kext")):
            self.metadata.write_bytes(plistlib.dumps(self.history({
                PatchType.MERGE_DATA_VOLUME: {"/Library/Extensions": {filename: "source"}},
            })))
            with self.subTest(identity=identity, filename=filename), \
                 mock.patch.object(support, "ROOT_PATCH_METADATA_PATH", self.metadata), \
                 mock.patch.object(support.subprocess_wrapper, "run_as_root_and_verify") as run:
                self.assertFalse(self.cleaner.clean_auxiliary_kc(expected_project_identity=identity))
                run.assert_not_called()

    def test_live_data_volume_cleanup_uses_an_absolute_path(self):
        self.cleaner.mount_location_data = ""
        self.metadata.write_bytes(plistlib.dumps(self.history({
            PatchType.MERGE_DATA_VOLUME: {"/Library/Extensions": {"Owned.kext": "source"}},
        })))
        with mock.patch.object(support, "ROOT_PATCH_METADATA_PATH", self.metadata), \
             mock.patch.object(support.Path, "exists", return_value=True), \
             mock.patch.object(support.subprocess_wrapper, "run_as_root_and_verify") as run:
            self.cleaner.clean_auxiliary_kc(expected_project_identity="test-project")
        self.assertEqual(run.call_args.args[0][-1], "/Library/Extensions/Owned.kext")

    def test_focused_preflight_preserves_plugins_and_display_settings(self):
        patcher = object.__new__(sys_patch.PatchSysVolume)
        patcher.constants = SimpleNamespace(detected_os=25, project_identity="test-project")
        patcher.mount_location_data = str(self.root)
        patcher.skip_root_kmutil_requirement = False
        patcher._clean_skylight_plugins = mock.Mock()
        patcher._delete_nonmetal_enforcement = mock.Mock()
        patcher._merge_kdk_with_root = mock.Mock()
        with mock.patch.object(sys_patch.kernelcache, "KernelCacheSupport"):
            patcher._preflight_checks({"Modern Wireless": {}, "Modern Audio": {}}, str(self.root))
        patcher._clean_skylight_plugins.assert_not_called()
        patcher._delete_nonmetal_enforcement.assert_not_called()


if __name__ == "__main__":
    unittest.main()
