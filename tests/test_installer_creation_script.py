"""The bootable-installer script, and the createinstallmedia flag it passes.

`generate_installer_creation_script` writes the shell script that formats a disk
and runs the installer's own `createinstallmedia`. It decides whether to pass
`--applicationpath` from that installer's `DTPlatformVersion`, and the flag
belongs to one era: createinstallmedia in macOS 10.12 and earlier has to be told
where the installer bundle is, and 10.13 dropped the option. (Apple's
instructions still describe it as a Sierra-and-earlier affair:
https://support.apple.com/en-us/101578 .)

The decision read the version as *characters* after truncating it to its leading
component. The guard asked whether `"10"[0]` was `"10"`, which is false, so the
flag was never passed to anything — not even the era that needs it. Apple's own
instructions still append it on Sierra and earlier, so the branch was dead code
and flashing a 10.12-or-earlier installer through this path wrote a script whose
createinstallmedia call was missing a required option. macOS 11 and later never
wanted the flag, so Tahoe is unaffected.

Both halves are exercised here: the decision itself, and the script the real
`generate_installer_creation_script` writes for an installer bundle on disk, with
only the copy and the signature check mocked.
"""

import plistlib
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.datasets.os_data import os_data
from opencore_legacy_patcher.support import macos_installer_handler


def fake_installer(root: Path, name: str, platform_version: str | None) -> Path:
    """A minimal installer bundle: an Info.plist and a createinstallmedia stub."""

    bundle = root / name
    (bundle / "Contents" / "Resources").mkdir(parents=True)
    (bundle / "Contents" / "Resources" / "createinstallmedia").write_text("#!/bin/sh\n")

    plist = {} if platform_version is None else {"DTPlatformVersion": platform_version}
    (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps(plist))

    return bundle


class ApplicationPathDecisionTests(unittest.TestCase):
    """Which platform versions still need to be pointed at their bundle."""

    def test_the_sierra_era_is_asked_for_the_bundle(self) -> None:
        for platform_version in ("10.9.5", "10.11", "10.12", "10.12.6"):
            with self.subTest(platform_version=platform_version):
                self.assertTrue(macos_installer_handler._requires_applicationpath(platform_version))

    def test_high_sierra_and_later_are_not(self) -> None:
        for platform_version in ("10.13", "10.13.6", "10.14.6", "10.15.7", "11", "13.5", "15.6", "26.0"):
            with self.subTest(platform_version=platform_version):
                self.assertFalse(macos_installer_handler._requires_applicationpath(platform_version))

    def test_a_version_without_a_minor_is_not_guessed(self) -> None:
        for platform_version in ("10", "", "1"):
            with self.subTest(platform_version=platform_version):
                self.assertFalse(macos_installer_handler._requires_applicationpath(platform_version))

    def test_a_minor_component_that_is_not_a_number_is_not_guessed(self) -> None:
        self.assertFalse(macos_installer_handler._requires_applicationpath("10.beta"))

    def test_the_boundary_is_the_release_that_dropped_the_option(self) -> None:
        """10.13 is the release that dropped it, and the datasets name its kernel major"""

        self.assertEqual(
            macos_installer_handler.APPLICATIONPATH_DROPPED_AT,
            int(os_data.high_sierra),
        )
        self.assertTrue(macos_installer_handler._requires_applicationpath("10.12.6"))
        self.assertFalse(macos_installer_handler._requires_applicationpath("10.13"))
        self.assertFalse(macos_installer_handler._requires_applicationpath("10.13.6"))


class LocalInstallerOrderTests(unittest.TestCase):
    """The local installer list is ordered by version number, not by version text."""

    def _catalog(self, versions: dict[str, str]) -> dict:
        """Run the real catalog listing against a temporary /Applications"""

        with tempfile.TemporaryDirectory() as applications:
            for display_name, platform_version in versions.items():
                bundle = Path(applications) / f"Install macOS {display_name}.app"
                (bundle / "Contents" / "Resources").mkdir(parents=True)
                (bundle / "Contents" / "Resources" / "createinstallmedia").write_text("#!/bin/sh\n")
                (bundle / "Contents" / "Info.plist").write_bytes(
                    plistlib.dumps(
                        {
                            "DTPlatformVersion": platform_version,
                            "CFBundleDisplayName": display_name,
                            # Kernel 20 is Big Sur, above the High Sierra floor the
                            # listing applies, so every bundle here is reported.
                            "DTSDKBuild": "20G95",
                        }
                    )
                )

            with mock.patch.object(macos_installer_handler, "APPLICATION_SEARCH_PATH", applications):
                return macos_installer_handler.LocalInstallerCatalog().available_apps

    def test_a_single_digit_major_does_not_sort_after_a_two_digit_one(self) -> None:
        catalog = self._catalog({"Ancient": "9.2.2", "Tahoe": "26.0", "Sierra": "10.12"})
        self.assertEqual(
            [entry["Version"] for entry in catalog.values()],
            ["9.2.2", "10.12", "26.0"],
            "versions were ordered as text",
        )

    def test_a_version_without_a_number_sorts_after_the_others(self) -> None:
        catalog = self._catalog({"Unknown": "Unknown", "Tahoe": "26.0", "Big Sur": "11.0"})
        self.assertEqual([entry["Version"] for entry in catalog.values()], ["11.0", "26.0", "Unknown"])

    def test_the_order_key_keeps_the_release_it_was_built_from(self) -> None:
        """A tie on the numbers still orders deterministically, by text"""

        key = macos_installer_handler._version_order_key
        self.assertLess(key("10.12"), key("10.12.6"))
        self.assertLess(key("10.12"), key("10.15"))
        self.assertLess(key("11.0"), key("13.0"))
        self.assertLess(key("26.0"), key("26.0 Beta"))


class GeneratedScriptTests(unittest.TestCase):
    """The script the real generator writes, for each installer era."""

    def _script_for(self, platform_version: str | None) -> str:
        with tempfile.TemporaryDirectory() as installer_root, tempfile.TemporaryDirectory() as output:
            installer_path = fake_installer(Path(installer_root), "Install macOS.app", platform_version)

            with mock.patch.object(
                     macos_installer_handler, "tmp_dir", SimpleNamespace(name=installer_root)
                 ), \
                 mock.patch.object(macos_installer_handler.InstallerCreation, "_disk_identity", return_value=({}, {
                     "boot_session_uuid": "12345678-1234-5678-1234-567812345678",
                     "registry_id": 1234, "size": 32 * 1024**3,
                     "tree_path": "IODeviceTree:/USB@1", "registry_name": "USB Media",
                 })), \
                 mock.patch.object(macos_installer_handler, "can_copy_on_write", return_value=True), \
                 mock.patch.object(macos_installer_handler, "generate_copy_arguments", return_value=["/bin/cp"]), \
                 mock.patch.object(
                     macos_installer_handler.subprocess, "run", return_value=SimpleNamespace(returncode=0)
                 ):
                generated = macos_installer_handler.InstallerCreation().generate_installer_creation_script(
                    output, str(installer_path), "disk9"
                )

            self.assertTrue(generated, "the generator reports failure for a healthy installer")
            script = Path(output) / "Installer.sh"
            self.assertTrue(script.exists())
            return script.read_text()

    def test_the_script_always_names_the_tool_and_the_disk(self) -> None:
        script = self._script_for("15.6")
        self.assertIn("eraseDisk HFS+ OCLP-Installer disk9", script)
        self.assertIn("--volume /Volumes/OCLP-Installer --nointeraction", script)
        # The tool runs from the copy in the temporary directory, not /Applications
        self.assertIn("/Install macOS.app/Contents/Resources/createinstallmedia", script)

    def test_an_installer_from_the_sierra_era_gets_the_flag(self) -> None:
        for platform_version in ("10.11.6", "10.12"):
            with self.subTest(platform_version=platform_version):
                script = self._script_for(platform_version)
                self.assertIn(" --applicationpath '", script)
                self.assertIn("/Install macOS.app'", script)

    def test_a_newer_installer_does_not(self) -> None:
        """10.13+ createinstallmedia no longer takes the option, 11+ never did."""

        for platform_version in ("10.13", "10.13.6", "10.14.6", "10.15.7", "11", "15.6", "26.0", "10", None):
            with self.subTest(platform_version=platform_version):
                self.assertNotIn("--applicationpath", self._script_for(platform_version))


if __name__ == "__main__":
    unittest.main()
