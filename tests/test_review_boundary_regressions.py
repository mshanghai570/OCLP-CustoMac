"""Regression cases for reviewed system-boundary correctness failures."""

import plistlib
import subprocess
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.support import kdk_handler, subprocess_wrapper
from opencore_legacy_patcher.sys_patch.mount.mount import RootVolumeMount


class SubprocessLoggingTests(unittest.TestCase):
    def test_text_output_can_be_logged(self):
        output = subprocess_wrapper.generate_log(subprocess.CompletedProcess(["tool"], 1, "failure", "detail"))
        self.assertIn("failure", output)
        self.assertIn("detail", output)
        self.assertIn("Return Code: 1", output)

    def test_non_utf8_output_can_be_logged(self):
        output = subprocess_wrapper.generate_log(subprocess.CompletedProcess(["tool"], 1, b"failure\xff", b"detail\xff"))
        self.assertIn("failure\ufffd", output)
        self.assertIn("detail\ufffd", output)


class SnapshotIdentifierTests(unittest.TestCase):
    def test_snapshot_suffix_is_removed_in_full(self):
        for identifier in ("disk1s5s1", "disk1s5s12", "disk12s3s101"):
            with self.subTest(identifier=identifier):
                response = subprocess.CompletedProcess([], 0, plistlib.dumps({
                    "DeviceIdentifier": identifier, "APFSSnapshot": True,
                }))
                with mock.patch("opencore_legacy_patcher.sys_patch.mount.mount.subprocess.run", return_value=response):
                    self.assertEqual(RootVolumeMount(25).root_volume_identifier, identifier.rsplit("s", 1)[0])

    def test_nonsnapshot_identifier_is_preserved(self):
        response = subprocess.CompletedProcess([], 0, plistlib.dumps({"DeviceIdentifier": "disk12s3"}))
        with mock.patch("opencore_legacy_patcher.sys_patch.mount.mount.subprocess.run", return_value=response):
            self.assertEqual(RootVolumeMount(25).root_volume_identifier, "disk12s3")

    def test_failed_diskutil_is_not_parsed_as_success(self):
        response = subprocess.CompletedProcess([], 1, plistlib.dumps({"DeviceIdentifier": "disk1s5"}))
        with mock.patch("opencore_legacy_patcher.sys_patch.mount.mount.subprocess.run", return_value=response):
            with self.assertRaises(RuntimeError):
                RootVolumeMount(25)

    def test_invalid_snapshot_identifier_is_rejected(self):
        response = subprocess.CompletedProcess([], 0, plistlib.dumps({"DeviceIdentifier": "disk1s5", "APFSSnapshot": True}))
        with mock.patch("opencore_legacy_patcher.sys_patch.mount.mount.subprocess.run", return_value=response):
            with self.assertRaises(RuntimeError):
                RootVolumeMount(25)


class KDKValidationPathTests(unittest.TestCase):
    def test_checksum_validation_does_not_prune_existing_recovery_kdks(self):
        with tempfile.TemporaryDirectory() as temporary:
            selected = Path(temporary) / "selected.dmg"
            selected.touch()
            resolver = object.__new__(kdk_handler.KernelDebugKitObject)
            resolver.constants = SimpleNamespace(kdk_download_path=selected, should_nuke_kdks=True)
            resolver.passive = False
            with mock.patch.object(kdk_handler.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                 mock.patch.object(resolver, "_remove_unused_kdks") as prune:
                self.assertTrue(resolver.validate_kdk_checksum())
            prune.assert_not_called()

    def test_explicit_download_is_the_image_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / "alternate.dmg"
            selected.touch()
            resolver = object.__new__(kdk_handler.KernelDebugKitObject)
            resolver.constants = SimpleNamespace(kdk_download_path=root / "default.dmg", should_nuke_kdks=False)
            resolver.passive = True
            with mock.patch.object(kdk_handler.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertTrue(resolver.validate_kdk_checksum(selected))
            self.assertEqual(Path(run.call_args.args[0][-1]), selected)


if __name__ == "__main__":
    unittest.main()
