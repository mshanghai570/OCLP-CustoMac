"""Root copy failures must stop patching before cache/snapshot creation."""

import subprocess
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.sys_patch.utilities import files, kdk_merge


class RootCopyFailureTests(unittest.TestCase):
    def test_framework_merge_failure_does_not_fix_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(files.subprocess_wrapper, "run_as_root", return_value=
                                   subprocess.CompletedProcess(["rsync"], 23, b"partial copy")), \
                 mock.patch.object(files, "fix_permissions") as permissions:
                with self.assertRaisesRegex(Exception, "exit code 23"):
                    files.install_new_file(temporary, temporary, "IO80211.framework",
                                           files.PatchType.MERGE_SYSTEM_VOLUME)
                permissions.assert_not_called()

    def test_required_install_destination_cannot_be_silently_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileNotFoundError):
                files.install_new_file(temporary, str(Path(temporary) / "missing"),
                                       "IO80211.framework", files.PatchType.MERGE_SYSTEM_VOLUME)

    def test_successful_framework_merge_fixes_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(files.subprocess_wrapper, "run_as_root", return_value=
                                   subprocess.CompletedProcess(["rsync"], 0)), \
                 mock.patch.object(files, "fix_permissions") as permissions:
                files.install_new_file(temporary, temporary, "IO80211.framework",
                                       files.PatchType.MERGE_SYSTEM_VOLUME)
                permissions.assert_called_once_with(temporary + "/IO80211.framework")

    def test_kdk_partial_merge_fails_even_with_preexisting_libkern(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            libkern = root / "System/Library/Extensions/System.kext/PlugIns/Libkern.kext/Libkern"
            libkern.parent.mkdir(parents=True)
            libkern.touch()
            merger = kdk_merge.KernelDebugKitMerge(SimpleNamespace(), str(root), False)
            with mock.patch.object(kdk_merge.subprocess_wrapper, "run_as_root", return_value=
                                   subprocess.CompletedProcess(["rsync"], 23, b"partial copy")):
                with self.assertRaisesRegex(Exception, "exit code 23"):
                    merger._merge_kdk("/unused/kdk")

    def test_hid_signature_copy_failures_stop_the_operation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            signature = root / "System/Library/Extensions/IOHIDFamily.kext/Contents/PlugIns/IOHIDEventDriver.kext/Contents/_CodeSignature"
            signature.mkdir(parents=True)
            backup = root / "IOHIDEventDriver_CodeSignature.bak"
            backup.mkdir()
            merger = kdk_merge.KernelDebugKitMerge(SimpleNamespace(payload_path=root), str(root), False)
            for method in (merger._backup_hid_cs, merger._restore_hid_cs):
                with self.subTest(method=method.__name__):
                    with mock.patch.object(kdk_merge, "generate_copy_arguments", return_value=["/bin/cp"]), \
                         mock.patch.object(kdk_merge.subprocess_wrapper, "run_as_root", return_value=
                                           subprocess.CompletedProcess(["cp"], 1, b"copy failed")):
                        with self.assertRaisesRegex(Exception, "exit code 1"):
                            method()


if __name__ == "__main__":
    unittest.main()
