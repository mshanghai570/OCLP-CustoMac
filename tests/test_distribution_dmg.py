"""Distribution disk-image contract tests.

The DMG is the artifact a user downloads, and it is the one build output that
nothing in the test suite would otherwise cover: `hdiutil create` is not
checkable by reading source, and the failure mode that matters is an image
that mounts but carries the wrong bytes. These tests pin the command
contract, the fail-closed staging behavior, and the digest comparison that
makes a tampered or truncated image fail the build instead of shipping.
"""

import subprocess
import unittest

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from ci_tooling.build_modules import distribution_dmg


class DistributionDMGCommandTests(unittest.TestCase):
    def test_image_is_read_only_compressed_hfs_plus_with_versioned_volume_name(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            generator = distribution_dmg.GenerateDistributionDMG(
                package_root=Path(temporary_dir),
                output=Path(temporary_dir) / "out.dmg",
            )

            with mock.patch.object(distribution_dmg.subprocess_wrapper, "run_and_verify") as run_and_verify:
                generator._generate(Path(temporary_dir))

        run_and_verify.assert_called_once_with(
            [
                "/usr/bin/hdiutil", "create", str(Path(temporary_dir) / "out.dmg"),
                "-format", "UDZO",
                "-ov",
                "-volname", "OCLP-CustoMac 3.0.3",
                "-fs", "HFS+",
                "-layout", "NONE",
                "-srcfolder", temporary_dir,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_mount_is_readonly_and_detach_escalates_to_force(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            generator = distribution_dmg.GenerateDistributionDMG(
                package_root=Path(temporary_dir),
                output=Path(temporary_dir) / "out.dmg",
            )
            mountpoint = Path(temporary_dir) / "image"

            with mock.patch.object(distribution_dmg.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 1, b"busy", b"")
                generator._detach(mountpoint)

            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    ["/usr/bin/hdiutil", "detach", str(mountpoint)],
                    ["/usr/bin/hdiutil", "detach", str(mountpoint), "-force"],
                ],
            )


class DistributionDMGStagingTests(unittest.TestCase):
    def test_missing_installer_package_fails_before_any_image_is_written(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            generator = distribution_dmg.GenerateDistributionDMG(
                package_root=Path(temporary_dir),
                output=Path(temporary_dir) / "out.dmg",
            )

            with self.assertRaises(distribution_dmg.DistributionDMGError) as raised:
                generator.generate()

        self.assertIn("OpenCore-Patcher.pkg", str(raised.exception))

    def test_stage_hardlinks_packages_so_a_large_installer_is_not_copied(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            package_root = Path(temporary_dir) / "dist"
            staging_root = Path(temporary_dir) / "stage"
            package_root.mkdir()
            staging_root.mkdir()
            for name in distribution_dmg.DISTRIBUTION_PACKAGES:
                (package_root / name).write_bytes(f"payload:{name}".encode())

            generator = distribution_dmg.GenerateDistributionDMG(
                package_root=package_root,
                output=Path(temporary_dir) / "out.dmg",
            )
            staged = generator._stage(staging_root)

            self.assertEqual(
                sorted(staged),
                sorted([*distribution_dmg.DISTRIBUTION_PACKAGES, distribution_dmg.README_NAME]),
            )
            for name in distribution_dmg.DISTRIBUTION_PACKAGES:
                self.assertEqual(
                    staged[name].stat().st_ino,
                    (package_root / name).stat().st_ino,
                    f"{name} was copied rather than hardlinked",
                )

    def test_readme_states_the_installer_does_not_ship_the_application_bundle(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            generator = distribution_dmg.GenerateDistributionDMG(
                package_root=Path(temporary_dir),
                output=Path(temporary_dir) / "out.dmg",
            )
            readme = generator._readme()

        self.assertIn("It does not contain", readme)
        self.assertIn("'/Library/Application Support/Dortania'", readme)
        self.assertIn("'/Applications'", readme)


class DistributionDMGValidationTests(unittest.TestCase):
    def test_missing_image_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            generator = distribution_dmg.GenerateDistributionDMG(
                package_root=Path(temporary_dir),
                output=Path(temporary_dir) / "absent.dmg",
            )

            with self.assertRaises(distribution_dmg.DistributionDMGError):
                generator.validate({})

    def test_mount_failure_names_the_image_and_reports_hdiutil_output(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            output = Path(temporary_dir) / "out.dmg"
            output.write_bytes(b"not-an-image")
            generator = distribution_dmg.GenerateDistributionDMG(
                package_root=Path(temporary_dir),
                output=output,
            )

            with mock.patch.object(distribution_dmg.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 1, b"hdiutil: invalid image", b"")
                with self.assertRaises(distribution_dmg.DistributionDMGError) as raised:
                    generator.validate({})

        self.assertIn("hdiutil: invalid image", str(raised.exception))

    def test_member_digest_mismatch_fails_validation_and_detaches(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            output = Path(temporary_dir) / "out.dmg"
            output.write_bytes(b"image")
            generator = distribution_dmg.GenerateDistributionDMG(
                package_root=Path(temporary_dir),
                output=output,
            )

            def fake_attach(mountpoint):
                (mountpoint / "OpenCore-Patcher.pkg").write_bytes(b"substituted")
                return subprocess.CompletedProcess([], 0, b"attached", b"")

            with mock.patch.object(generator, "_mount", side_effect=fake_attach), \
                 mock.patch.object(generator, "_detach") as detach:
                detach.return_value = subprocess.CompletedProcess([], 0, b"", b"")
                with self.assertRaises(distribution_dmg.DistributionDMGError) as raised:
                    generator.validate({"OpenCore-Patcher.pkg": "0" * 64})

            detach.assert_called_once()

        self.assertIn("differs from source", str(raised.exception))

    def test_extra_or_missing_members_are_reported_rather_than_ignored(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            output = Path(temporary_dir) / "out.dmg"
            output.write_bytes(b"image")
            generator = distribution_dmg.GenerateDistributionDMG(
                package_root=Path(temporary_dir),
                output=output,
            )

            def fake_attach(mountpoint):
                (mountpoint / "Unexpected.pkg").write_bytes(b"extra")
                return subprocess.CompletedProcess([], 0, b"attached", b"")

            with mock.patch.object(generator, "_mount", side_effect=fake_attach), \
                 mock.patch.object(generator, "_detach") as detach:
                detach.return_value = subprocess.CompletedProcess([], 0, b"", b"")
                with self.assertRaises(distribution_dmg.DistributionDMGError) as raised:
                    generator.validate({"OpenCore-Patcher.pkg": "0" * 64})

        message = str(raised.exception)
        self.assertIn("missing=['OpenCore-Patcher.pkg']", message)
        self.assertIn("unexpected=['Unexpected.pkg']", message)

    def test_detach_failure_is_raised_even_when_validation_failed(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            output = Path(temporary_dir) / "out.dmg"
            output.write_bytes(b"image")
            generator = distribution_dmg.GenerateDistributionDMG(
                package_root=Path(temporary_dir),
                output=output,
            )

            def fake_attach(mountpoint):
                return subprocess.CompletedProcess([], 0, b"attached", b"")

            with mock.patch.object(generator, "_mount", side_effect=fake_attach), \
                 mock.patch.object(generator, "_detach") as detach:
                detach.return_value = subprocess.CompletedProcess([], 1, b"hdiutil: detach failed", b"")
                with self.assertRaises(distribution_dmg.DistributionDMGError) as raised:
                    generator.validate({"OpenCore-Patcher.pkg": "0" * 64})

        self.assertIn("Unable to detach", str(raised.exception))


if __name__ == "__main__":
    unittest.main()