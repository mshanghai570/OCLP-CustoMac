"""Snapshot recovery records each boundary and always releases its root mount."""

import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.sys_patch import sys_patch
from opencore_legacy_patcher.sys_patch.lifecycle import RootPatchLifecycleStore
from opencore_legacy_patcher.sys_patch.root_state import RootPatchStateEvaluator, RootPatchState, RootStateEvidence
from opencore_legacy_patcher.wx_gui import gui_sys_patch_start


class RevertTransactionSafetyTests(unittest.TestCase):
    def setUp(self):
        lock = mock.patch("opencore_legacy_patcher.support.operation_lock.RootOperationLock")
        lock.start()
        self.addCleanup(lock.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.metadata_path = self.root / "missing-history.plist"
        self.patcher = object.__new__(sys_patch.PatchSysVolume)
        self.patcher.constants = SimpleNamespace(
            detected_os=25, project_identity="test-project", commit_info=None,
            root_patcher_succeeded=False, root_patcher_patch_pending=False,
            root_patcher_revert_pending=False, root_patcher_pending_metadata=None,
        )
        self.patcher.mount_location = str(self.root / "mounted")
        self.patcher.mount_location_data = str(self.root)
        self.patcher.skip_root_kmutil_requirement = False
        self.patcher._clean_skylight_plugins = mock.Mock()
        self.patcher._delete_nonmetal_enforcement = mock.Mock()
        self.store = RootPatchLifecycleStore(
            self.patcher.constants, path=self.root / "lifecycle.plist",
            boot_session_reader=lambda: "boot-a",
            writer=lambda path, payload: bool(path.write_bytes(payload)),
        )

    def test_missing_history_still_persists_revert_pending_across_reopen(self):
        with mock.patch.object(sys_patch, "ROOT_PATCH_METADATA_PATH", self.metadata_path), \
             mock.patch.object(sys_patch, "RootPatchLifecycleStore", return_value=self.store):
            self.patcher._record_revert_pending()
        reopened = SimpleNamespace(commit_info=None)
        evaluator = RootPatchStateEvaluator(
            reopened, metadata_path=self.metadata_path,
            lifecycle_store=self.store, evidence_reader=lambda: RootStateEvidence(True, "Broken"),
        )
        result = evaluator.evaluate({})
        self.assertEqual(result.state, RootPatchState.REVERT_PENDING)
        self.assertFalse(result.patch_allowed)
        self.assertFalse(result.recovery_authorized)

    def test_revert_is_durable_before_bless_and_before_cleanup(self):
        def bless():
            self.assertEqual(self.store.read().record.state.value, "REVERT_IN_PROGRESS")
            return True

        def cleanup(**kwargs):
            self.assertEqual(self.store.read().record.state.value, "REVERT_PENDING")
            raise RuntimeError("cleanup failed")

        with mock.patch.object(sys_patch, "ROOT_PATCH_METADATA_PATH", self.metadata_path), \
             mock.patch.object(sys_patch, "RootPatchLifecycleStore", return_value=self.store), \
             mock.patch.object(sys_patch, "APFSSnapshot") as snapshot, \
             mock.patch.object(sys_patch.kernelcache, "KernelCacheSupport") as cleaner:
            snapshot.return_value.revert_snapshot.side_effect = bless
            cleaner.return_value.clean_auxiliary_kc.side_effect = cleanup
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                self.patcher._unpatch_root_vol()
        self.assertTrue(self.patcher.constants.root_patcher_revert_pending)
        self.assertFalse(self.patcher.constants.root_patcher_succeeded)
        self.patcher._clean_skylight_plugins.assert_not_called()
        self.patcher._delete_nonmetal_enforcement.assert_not_called()

    def test_failed_evidence_write_prevents_snapshot_switch(self):
        store = mock.Mock()
        store.read.return_value = self.store.read()
        store.write.return_value = False
        with mock.patch.object(sys_patch, "ROOT_PATCH_METADATA_PATH", self.metadata_path), \
             mock.patch.object(sys_patch, "RootPatchLifecycleStore", return_value=store), \
             mock.patch.object(sys_patch, "APFSSnapshot") as snapshot:
            self.assertFalse(self.patcher._unpatch_root_vol())
        snapshot.assert_not_called()

    def test_skipped_cleanup_is_reported_without_losing_snapshot_evidence(self):
        with mock.patch.object(sys_patch, "ROOT_PATCH_METADATA_PATH", self.metadata_path), \
             mock.patch.object(sys_patch, "RootPatchLifecycleStore", return_value=self.store), \
             mock.patch.object(sys_patch, "APFSSnapshot") as snapshot, \
             mock.patch.object(sys_patch.kernelcache, "KernelCacheSupport") as cleaner:
            snapshot.return_value.revert_snapshot.return_value = True
            cleaner.return_value.clean_auxiliary_kc.return_value = False
            self.assertFalse(self.patcher._unpatch_root_vol())
        self.assertEqual(self.store.read().record.state.value, "REVERT_PENDING")
        self.assertTrue(self.patcher.constants.root_patcher_cleanup_incomplete)

    def test_unpatch_releases_mount_on_success_failure_and_exception(self):
        self.patcher._mount_root_vol = mock.Mock(return_value=True)
        self.patcher._unmount_root_vol = mock.Mock()
        detection = SimpleNamespace(patches={}, can_unpatch=True)
        for failure in (None, RuntimeError("snapshot failed")):
            with self.subTest(failure=failure):
                self.patcher._unmount_root_vol.reset_mock()
                self.patcher._unpatch_root_vol = mock.Mock(side_effect=failure, return_value=False)
                with mock.patch.object(sys_patch, "HardwarePatchsetDetection", return_value=detection), \
                     mock.patch.object(sys_patch, "RootPatchStateEvaluator") as evaluator:
                    evaluator.return_value.evaluate.return_value = SimpleNamespace(recovery_authorized=True)
                    if failure:
                        with self.assertRaisesRegex(RuntimeError, "snapshot failed"):
                            self.patcher.start_unpatch()
                    else:
                        self.patcher.start_unpatch()
                self.patcher._unmount_root_vol.assert_called_once_with()

    def test_incomplete_cleanup_prompts_reboot_without_claiming_full_success(self):
        frame = SimpleNamespace(
            constants=SimpleNamespace(root_patcher_succeeded=False,
                                      root_patcher_revert_pending=True,
                                      root_patcher_cleanup_incomplete=True),
            frame_modal=object(),
        )
        with mock.patch.object(gui_sys_patch_start.gui_support, "RestartHost") as restart:
            gui_sys_patch_start.SysPatchStartFrame._post_patch(frame)
        message = restart.return_value.restart.call_args.kwargs["message"]
        self.assertIn("restored", message)
        self.assertIn("cleanup", message)
        self.assertNotIn("finished successfully", message)


if __name__ == "__main__":
    unittest.main()
