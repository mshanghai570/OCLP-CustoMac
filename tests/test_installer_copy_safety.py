"""Installer duplication must budget the bundle and reject partial copies."""

import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.support import macos_installer_handler as installer


class InstallerCopySafetyTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "source/Install macOS.app"
        (source / "Contents/Resources").mkdir(parents=True)
        (source / "Contents/Resources/createinstallmedia").write_bytes(b"tool")
        (source / "Contents/Info.plist").write_bytes(plistlib.dumps({"DTPlatformVersion": "26.0"}))
        temporary = root / "staging"
        temporary.mkdir()
        return source, temporary

    def test_no_cow_capacity_is_based_on_source_bundle_and_target_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            source, staging = self.fixture(Path(directory))
            (source / "Contents/large-payload").write_bytes(b"x" * 1024 * 1024)
            with mock.patch.object(installer, "tmp_dir", SimpleNamespace(name=str(staging))), \
                 mock.patch.object(installer, "can_copy_on_write", return_value=False), \
                 mock.patch.object(installer.utilities, "get_free_space", return_value=1024) as space, \
                 mock.patch.object(installer.subprocess, "run") as run:
                self.assertFalse(installer.InstallerCreation().generate_installer_creation_script(directory, str(source), "disk9"))
                run.assert_not_called()
                space.assert_called_with(str(staging))

    def test_failed_copy_cannot_proceed_to_codesign_or_script(self):
        with tempfile.TemporaryDirectory() as directory:
            source, staging = self.fixture(Path(directory))
            copied = staging / source.name
            def partial_copy(args, **kwargs):
                if args[0] == "/bin/cp":
                    copied.mkdir()
                    return subprocess.CompletedProcess(args, 1)
                return subprocess.CompletedProcess(args, 0)
            with mock.patch.object(installer, "tmp_dir", SimpleNamespace(name=str(staging))), \
                 mock.patch.object(installer, "can_copy_on_write", return_value=True), \
                 mock.patch.object(installer.subprocess, "run", side_effect=partial_copy) as run:
                self.assertFalse(installer.InstallerCreation().generate_installer_creation_script(directory, str(source), "disk9"))
                self.assertFalse(any(call.args[0][0] == "/usr/bin/codesign" for call in run.call_args_list))
                self.assertFalse((Path(directory) / "Installer.sh").exists())

    def test_successful_command_with_incomplete_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source, staging = self.fixture(Path(directory))
            (staging / source.name).mkdir()
            with mock.patch.object(installer, "tmp_dir", SimpleNamespace(name=str(staging))), \
                 mock.patch.object(installer, "can_copy_on_write", return_value=True), \
                 mock.patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                self.assertFalse(installer.InstallerCreation().generate_installer_creation_script(directory, str(source), "disk9"))
                self.assertFalse((Path(directory) / "Installer.sh").exists())


if __name__ == "__main__":
    unittest.main()
