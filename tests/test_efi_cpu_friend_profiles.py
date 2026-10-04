"""Regression coverage for CPUFriend profile handling in EFI builds."""

import plistlib
import tempfile
import types
import unittest

from pathlib import Path
from unittest.mock import patch

from opencore_legacy_patcher import constants
from opencore_legacy_patcher.efi_builder import build, misc


class CPUFriendProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.profile_root = self.root / "PlatformPlugin"
        self.model = "MacBookAir5,1"
        self.constants = types.SimpleNamespace(
            allow_oc_everywhere=False,
            disallow_cpufriend=False,
            serial_settings="Moderate",
            platform_plugin_plist_path=self.profile_root,
            cpufriend_version="1.3.0",
            cpufriend_path=self.root / "CPUFriend.zip",
            pp_kext_folder=self.root / "CPUFriendDataProvider.kext",
            pp_contents_folder=self.root / "CPUFriendDataProvider.kext/Contents",
        )
        self.builder = misc.BuildMiscellaneous.__new__(misc.BuildMiscellaneous)
        self.builder.model = self.model
        self.builder.constants = self.constants
        self.builder.config = {}

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_missing_profile_fails_before_cpu_friend_kext_is_enabled(self) -> None:
        with patch.object(misc.support, "BuildSupport") as build_support:
            with self.assertRaisesRegex(FileNotFoundError, "MacBookAir5,1"):
                self.builder._cpu_friend_handling()

        build_support.assert_not_called()
        self.assertFalse(self.constants.pp_kext_folder.exists())

    def test_missing_profile_is_ignored_when_cpu_friend_is_not_required(self) -> None:
        cases = (
            ("allow everywhere", self.model, {"allow_oc_everywhere": True}),
            ("CPUFriend disabled", self.model, {"disallow_cpufriend": True}),
            ("serial spoofing disabled", self.model, {"serial_settings": "None"}),
            ("unsupported legacy model", "iMac7,1", {}),
        )
        for label, model, overrides in cases:
            with self.subTest(label=label):
                constants = types.SimpleNamespace(**vars(self.constants) | overrides)
                self.assertIsNone(misc.validate_cpu_friend_profile(model, constants))

    def test_missing_profile_is_checked_before_base_build_starts(self) -> None:
        builder = build.BuildOpenCore.__new__(build.BuildOpenCore)
        builder.model = self.model
        builder.constants = self.constants
        self.constants.custom_model = None

        with patch.object(build.utilities, "cls"), patch.object(builder, "_generate_base") as generate_base:
            with self.assertRaisesRegex(FileNotFoundError, "MacBookAir5,1"):
                builder._build_efi()

        generate_base.assert_not_called()

    def test_deleted_model_profiles_are_reported_missing_when_required(self) -> None:
        target_constants = constants.Constants()
        target_constants.serial_settings = "Moderate"
        affected_models = (
            "MacBookAir5,1",
            "MacBookPro11,3",
            "MacBookPro11,4",
            "MacBookPro15,4",
            "MacBookPro16,1",
            "iMac18,1",
            "iMac20,1",
        )

        for model in affected_models:
            with self.subTest(model=model):
                with self.assertRaisesRegex(FileNotFoundError, model):
                    misc.validate_cpu_friend_profile(model, target_constants)

    def test_primary_tahoe_target_profile_is_present_and_valid(self) -> None:
        target_constants = constants.Constants()
        target_constants.serial_settings = "Moderate"
        target_profile = misc.validate_cpu_friend_profile("MacBookAir7,2", target_constants)

        self.assertIsNotNone(target_profile)
        assert target_profile is not None
        with target_profile.open("rb") as profile_file:
            profile = plistlib.load(profile_file)

        self.assertEqual(
            profile["IOKitPersonalities"]["CPUFriendDataProvider"]["CFBundleIdentifier"],
            "com.apple.driver.AppleACPIPlatform",
        )

    def test_present_profile_is_copied_and_provider_is_enabled(self) -> None:
        profile = self.profile_root / self.model / "Info.plist"
        profile.parent.mkdir(parents=True)
        profile.write_bytes(b"cpu friend profile fixture")
        provider_entry = {"Enabled": False}
        support_instance = types.SimpleNamespace(
            enable_kext=lambda *_args: None,
            get_kext_by_bundle_path=lambda _path: provider_entry,
        )

        with patch.object(misc.support, "BuildSupport", return_value=support_instance) as build_support:
            self.builder._cpu_friend_handling()

        copied_profile = self.constants.pp_contents_folder / "Info.plist"
        self.assertEqual(copied_profile.read_bytes(), profile.read_bytes())
        self.assertTrue(provider_entry["Enabled"])
        build_support.assert_any_call(self.model, self.constants, self.builder.config)


if __name__ == "__main__":
    unittest.main()
