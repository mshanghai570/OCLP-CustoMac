"""Exercise EFI transfers on disposable directories, never on a real partition."""

import plistlib
import shutil
import subprocess
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.support import install, operation_lock

REAL_RUN = subprocess.run


class EFIInstallTransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.esp = self.root / "esp"
        self.source.mkdir()
        self.esp.mkdir()
        self.mounted = False
        self.commands = []
        self.fail_command = None
        self.fail_copy = False
        self.corrupt_copy = False
        for relative, content in {
            "EFI/OC/OpenCore.efi": b"new OpenCore",
            "System/Library/CoreServices/boot.efi": b"new bootstrap",
        }.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.source / "EFI/OC/config.plist").write_bytes(plistlib.dumps({
            "ACPI": {"Add": []}, "Kernel": {"Add": []},
            "UEFI": {"Drivers": []}, "Misc": {"Tools": []},
        }))
        icon = self.root / ".VolumeIcon.icns"
        icon.write_bytes(b"icon")
        self.constants = SimpleNamespace(
            opencore_release_folder=self.source, boot_efi=False, recovery_status=False,
            icon_path_sd=icon, icon_path_ssd=icon, icon_path_external=icon, icon_path_internal=icon,
        )
        self.installer = install.tui_disk_installation(self.constants)
        self.original = {
            "EFI/OC/OpenCore.efi": b"old OpenCore", "EFI/OC/config.plist": b"old configuration",
            "System/Library/CoreServices/boot.efi": b"old bootstrap",
            "boot.efi": b"old root bootstrap", "EFI/BOOT/BOOTx64.efi": b"old fallback",
            "EFI/Microsoft/bootmgfw.efi": b"unrelated Windows loader",
        }
        self.restore_original()

    def restore_original(self):
        for child in self.esp.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        for relative, content in self.original.items():
            path = self.esp / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def run_command(self, args, **kwargs):
        args = [str(arg) for arg in args]
        if args[0] == "/usr/sbin/diskutil":
            if args[1] == "info":
                data = ({"DeviceIdentifier": "disk9s1", "ParentWholeDisk": "disk9",
                         "Content": "EFI", "FilesystemType": "msdos", "BusProtocol": "USB",
                         "Mounted": self.mounted, "VolumeUUID": "volume-test"}
                        if args[-1] == "disk9s1" else {"MediaName": "Test Drive", "SolidState": False})
                if self.mounted:
                    data["MountPoint"] = str(self.esp)
                return subprocess.CompletedProcess(args, 0, plistlib.dumps(data), b"")
            if args[1] in ("mount", "umount", "unmount"):
                self.mounted = args[1] == "mount"
                self.commands.append(args)
                return subprocess.CompletedProcess(args, 0, b"", b"")
        self.commands.append(args)
        if self.fail_command == len(self.commands) or (self.fail_copy and args[0] == "/bin/cp"):
            return subprocess.CompletedProcess(args, 1, b"injected failure", b"")
        if args[0] == "/bin/sync":
            return subprocess.CompletedProcess(args, 0, b"", b"")
        self.assertIn(args[0], ("/bin/cp", "/bin/mv", "/bin/rm", "/bin/mkdir"))
        for position, argument in enumerate(args[1:], 1):
            if argument.startswith("/"):
                if args[0] == "/bin/cp" and position == 1 and Path(argument).name.startswith("oclp-efi-journal-"):
                    continue  # Read-only source created by this transaction's NamedTemporaryFile.
                self.assertTrue(Path(argument).is_relative_to(self.root), f"Unsafe test mutation: {argument}")
        result = REAL_RUN(args, capture_output=True)
        if self.corrupt_copy and args[0] == "/bin/cp" and (self.source / "EFI/OC") in map(Path, args[1:]):
            (Path(args[-1]) / "OpenCore.efi").write_bytes(b"corrupt")
        return result

    def verified_command(self, args, **kwargs):
        result = self.run_command(args, **kwargs)
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, args, result.stdout)

    def perform_install(self, free_space=1024**3):
        with mock.patch.object(operation_lock, "RootOperationLock"), \
             mock.patch.object(install.subprocess, "run", side_effect=self.run_command), \
             mock.patch.object(install.subprocess_wrapper, "run_as_root", side_effect=self.run_command), \
             mock.patch.object(install.subprocess_wrapper, "run_as_root_and_verify", side_effect=self.verified_command), \
             mock.patch.object(install.utilities, "get_free_space", return_value=free_space):
            return self.installer.install_opencore("disk9s1")

    def assert_original_active(self):
        for relative, expected in self.original.items():
            self.assertEqual((self.esp / relative).read_bytes(), expected, relative)

    def test_failed_copy_preserves_existing_bootloader(self):
        self.fail_copy = True
        self.assertFalse(self.perform_install())
        self.assert_original_active()
        self.assertFalse(self.mounted)

    def test_insufficient_space_preserves_existing_bootloader(self):
        self.assertFalse(self.perform_install(free_space=0))
        self.assert_original_active()

    def test_missing_enabled_driver_is_rejected_before_publication(self):
        path = self.source / "EFI/OC/config.plist"
        config = plistlib.loads(path.read_bytes())
        config["UEFI"]["Drivers"] = [{"Path": "Missing.efi", "Enabled": True}]
        path.write_bytes(plistlib.dumps(config))
        self.assertFalse(self.perform_install())
        self.assert_original_active()

    def test_unfinished_previous_install_blocks_another_transfer(self):
        previous = self.esp / "OpenCore-Backup-interrupted"
        previous.mkdir()
        (previous / "Recovery.plist").write_bytes(plistlib.dumps({"State": "PREPARED"}))
        self.assertFalse(self.perform_install())
        self.assert_original_active()

    def test_enabled_kext_requires_a_real_info_plist(self):
        (self.source / "EFI/OC/Kexts/Test.kext").mkdir(parents=True)
        path = self.source / "EFI/OC/config.plist"
        config = plistlib.loads(path.read_bytes())
        config["Kernel"]["Add"] = [{"Enabled": True, "BundlePath": "Test.kext", "ExecutablePath": ""}]
        path.write_bytes(plistlib.dumps(config))
        self.assertFalse(self.perform_install())
        self.assert_original_active()

    def test_preexisting_mount_is_left_mounted(self):
        self.mounted = True
        self.assertTrue(self.perform_install())
        self.assertTrue(self.mounted)

    def test_bootstrap_already_in_fallback_layout_is_preserved(self):
        self.constants.boot_efi = True
        (self.source / "EFI/BOOT").mkdir()
        (self.source / "EFI/BOOT/BOOTx64.efi").write_bytes(b"source fallback")
        self.assertTrue(self.perform_install())
        self.assertEqual((self.esp / "EFI/BOOT/BOOTx64.efi").read_bytes(), b"source fallback")

    def test_corrupt_staged_copy_is_rejected_before_publication(self):
        self.corrupt_copy = True
        self.assertFalse(self.perform_install())
        self.assert_original_active()

    def test_success_retains_recovery_backup_and_unrelated_bootloader(self):
        self.assertTrue(self.perform_install())
        self.assertEqual((self.esp / "EFI/OC/OpenCore.efi").read_bytes(), b"new OpenCore")
        self.assertEqual((self.esp / "EFI/Microsoft/bootmgfw.efi").read_bytes(), self.original["EFI/Microsoft/bootmgfw.efi"])
        backups = list(self.esp.glob("OpenCore-Backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "EFI/OC/OpenCore.efi").read_bytes(), b"old OpenCore")
        self.assertTrue((backups[0] / "Recovery.plist").is_file())
        self.assertFalse(self.mounted)

    def test_fallback_bootstrap_is_converted_before_publication(self):
        self.constants.boot_efi = True
        self.assertTrue(self.perform_install())
        self.assertEqual((self.esp / "EFI/BOOT/BOOTx64.efi").read_bytes(), b"new bootstrap")
        self.assertFalse((self.esp / "System").exists())
        backups = list(self.esp.glob("OpenCore-Backup-*"))
        self.assertEqual((backups[0] / "EFI/BOOT/BOOTx64.efi").read_bytes(), b"old fallback")

    def test_failure_at_each_publication_rename_restores_original_files(self):
        self.assertTrue(self.perform_install())
        publications = [index for index, args in enumerate(self.commands, 1)
                        if args[0] == "/bin/mv"]
        self.assertTrue(publications, "Publication must use checked renames")
        for index in publications:
            with self.subTest(command=index):
                self.restore_original()
                self.commands = []
                self.fail_command = index
                self.assertFalse(self.perform_install())
                self.assert_original_active()
                self.assertFalse(self.mounted)


if __name__ == "__main__":
    unittest.main()
