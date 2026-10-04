"""Root state is rechecked under a process-wide kernel lock, released on exit."""

import importlib
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.sys_patch import sys_patch

MODULE = "opencore_legacy_patcher.support.operation_lock"
REAL_FSTAT = os.fstat


class RootOperationLockTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec(MODULE), "Root operations need an exclusive cross-process lock")
        return importlib.import_module(MODULE)

    def trusted_stat(self, fd):
        actual = REAL_FSTAT(fd)
        return SimpleNamespace(st_uid=0, st_mode=actual.st_mode, st_nlink=actual.st_nlink)

    def test_second_process_is_excluded_until_the_first_exits(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "operation.lock"
            path.touch(mode=0o444)
            child = subprocess.Popen([sys.executable, "-c",
                "import fcntl,sys; f=open(sys.argv[1]); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read()",
                str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "locked")
                with mock.patch.object(module.RootOperationLock, "_prepare"), \
                     mock.patch.object(module.os, "fstat", side_effect=self.trusted_stat):
                    lock = module.RootOperationLock(path=path)
                    self.assertFalse(lock.acquire())
                    child.terminate()
                    child.wait(timeout=10)
                    self.assertTrue(lock.acquire())
                    lock.release()
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=10)
                child.stdin.close()
                child.stdout.close()

    def test_untrusted_or_linked_lock_file_is_rejected(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.touch()
            link = Path(temporary) / "operation.lock"
            link.symlink_to(target)
            with mock.patch.object(module.RootOperationLock, "_prepare"):
                with self.assertRaises(OSError):
                    module.RootOperationLock(path=link).acquire()
            with mock.patch.object(module.RootOperationLock, "_prepare"):
                with self.assertRaises(ValueError):
                    module.RootOperationLock(path=target).acquire()

    def test_lock_is_released_when_operation_raises(self):
        module = self.module()
        lock = mock.Mock()
        lock.acquire.return_value = True
        with mock.patch.object(module, "RootOperationLock", return_value=lock):
            with self.assertRaisesRegex(RuntimeError, "operation failed"):
                module.execute_locked_root_operation(mock.Mock(side_effect=RuntimeError("operation failed")))
        lock.release.assert_called_once_with()

    def test_busy_lock_blocks_patch_state_check_and_root_mount(self):
        patcher = object.__new__(sys_patch.PatchSysVolume)
        patcher.constants = SimpleNamespace(detected_os=25)
        patcher.patch_selection = None
        patcher._mount_root_vol = mock.Mock()
        with mock.patch.object(sys_patch, "execute_locked_root_operation", return_value=False, create=True), \
             mock.patch.object(sys_patch, "HardwarePatchsetDetection") as detection, \
             mock.patch.object(sys_patch, "RootPatchStateEvaluator") as state:
            detection.return_value.patches = {}
            self.assertFalse(patcher.start_patch())
        state.assert_not_called()
        detection.assert_not_called()
        patcher._mount_root_vol.assert_not_called()

    def test_busy_lock_blocks_recovery_state_check_and_snapshot_switch(self):
        patcher = object.__new__(sys_patch.PatchSysVolume)
        patcher.constants = SimpleNamespace()
        patcher.patch_selection = None
        patcher._mount_root_vol = mock.Mock()
        with mock.patch.object(sys_patch, "execute_locked_root_operation", return_value=False, create=True), \
             mock.patch.object(sys_patch, "HardwarePatchsetDetection") as detection, \
             mock.patch.object(sys_patch, "RootPatchStateEvaluator") as state:
            state.return_value.evaluate.return_value.recovery_authorized = False
            self.assertFalse(patcher.start_unpatch())
        detection.assert_not_called()
        state.assert_not_called()
        patcher._mount_root_vol.assert_not_called()


if __name__ == "__main__":
    unittest.main()
