"""Regression tests for generated config existence validation."""

import plistlib
import tempfile
import types
import unittest

from pathlib import Path
from unittest.mock import patch

from opencore_legacy_patcher.efi_builder import support
from opencore_legacy_patcher.efi_builder.support import BuildSupport


class GeneratedConfigExistsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.release = Path(self.temporary_directory.name)
        for directory in ("EFI/OC/ACPI", "EFI/OC/Kexts", "EFI/OC/Tools", "EFI/OC/Drivers"):
            (self.release / directory).mkdir(parents=True, exist_ok=True)
        self.constants = types.SimpleNamespace(opencore_release_folder=self.release)
        self.support = BuildSupport("KGP-Tahoe-Fixture", self.constants, {})

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_absent_config_uses_missing_config_branch(self) -> None:
        with self.assertRaisesRegex(Exception, "OpenCore config file missing"):
            self.support.validate_pathing()

    def test_existing_minimal_config_uses_validation_branch_and_closes_plist(self) -> None:
        config = {
            "ACPI": {"Add": []},
            "Kernel": {"Add": []},
            "Misc": {"Tools": []},
            "UEFI": {"Drivers": []},
        }
        config_path = self.release / "EFI/OC/config.plist"
        with config_path.open("wb") as config_file:
            plistlib.dump(config, config_file)

        with patch(
            "opencore_legacy_patcher.efi_builder.support.plistlib.load",
            wraps=plistlib.load,
        ) as plist_load:
            self.support.validate_pathing()

        self.assertEqual(plist_load.call_count, 1)
        self.assertTrue(plist_load.call_args.args[0].closed)

    def test_shared_plist_helpers_close_files_on_read_and_write(self) -> None:
        plist_path = self.release / "fixture.plist"
        with patch(
            "opencore_legacy_patcher.efi_builder.support.plistlib.load",
            wraps=plistlib.load,
        ) as plist_load:
            with patch(
                "opencore_legacy_patcher.efi_builder.support.plistlib.dump",
                wraps=plistlib.dump,
            ) as plist_dump:
                support.dump_plist({"fixture": True}, plist_path)
                loaded = support.load_plist(plist_path)

        self.assertEqual(loaded, {"fixture": True})
        self.assertTrue(plist_dump.call_args.args[1].closed)
        self.assertTrue(plist_load.call_args.args[0].closed)

    def test_malformed_kext_validation_closes_info_plist(self) -> None:
        kext = self.release / "EFI/OC/Kexts/Test.kext"
        info_path = kext / "Contents/Info.plist"
        info_path.parent.mkdir(parents=True)
        with info_path.open("wb") as info_file:
            plistlib.dump({}, info_file)

        with patch(
            "opencore_legacy_patcher.efi_builder.support.plistlib.load",
            wraps=plistlib.load,
        ) as plist_load:
            self.support._validate_malformed_kexts(self.release / "EFI/OC/Kexts")

        self.assertEqual(plist_load.call_count, 1)
        self.assertTrue(plist_load.call_args.args[0].closed)


if __name__ == "__main__":
    unittest.main()
