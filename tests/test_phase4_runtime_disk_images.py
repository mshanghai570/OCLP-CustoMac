"""Phase 4 protected-resource runtime mount regressions."""

import subprocess
import tempfile
import unittest
import hashlib

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.support import disk_image, reroute_payloads
from opencore_legacy_patcher.sys_patch.utilities import dmg_mount


class ProtectedDiskImageTests(unittest.TestCase):
    def test_runtime_rejects_mismatched_support_image_before_mount(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(dmg_mount.disk_image, "attach_protected_disk_image") as attach:
            root = Path(directory)
            image = root / "Universal-Binaries.dmg"
            image.write_bytes(b"unexpected image")
            constants = SimpleNamespace(
                payload_local_binaries_root_path_dmg=image,
                payload_local_binaries_root_path=root / "Universal-Binaries",
                payload_path=root,
                patcher_support_pkg_sha256="0" * 64,
                overlay_psp_path_dmg=root / "DortaniaInternalResources.dmg",
                app_icon_path=root / "icon.icns",
            )

            self.assertFalse(dmg_mount.PatcherSupportPkgMount(constants).mount())
            attach.assert_not_called()

    def test_runtime_accepts_pinned_support_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(dmg_mount.disk_image, "attach_protected_disk_image") as attach:
            root = Path(directory)
            image = root / "Universal-Binaries.dmg"
            image.write_bytes(b"expected image")
            constants = SimpleNamespace(
                payload_local_binaries_root_path_dmg=image,
                payload_local_binaries_root_path=root / "Universal-Binaries",
                payload_path=root,
                patcher_support_pkg_sha256=hashlib.sha256(b"expected image").hexdigest(),
                overlay_psp_path_dmg=root / "DortaniaInternalResources.dmg",
                app_icon_path=root / "icon.icns",
            )
            attach.return_value = subprocess.CompletedProcess([], 0, b"")

            self.assertTrue(dmg_mount.PatcherSupportPkgMount(constants).mount())
            attach.assert_called_once()

    def test_both_runtime_images_use_stdin_passphrase(self) -> None:
        for image_name in ("payloads.dmg", "Universal-Binaries.dmg"):
            with self.subTest(image_name=image_name), \
                 mock.patch.object(disk_image.subprocess_wrapper, "run") as run:
                disk_image.attach_protected_disk_image(
                    image_path=Path("/app/Contents/Frameworks") / image_name,
                    mountpoint=Path("/tmp/mount"),
                    shadow_path=Path("/tmp/overlay"),
                )

                args, kwargs = run.call_args
                command = args[0]
                self.assertIn("-stdinpass", command)
                self.assertNotIn("-passphrase", command)
                self.assertNotIn("password", command)
                self.assertEqual(kwargs["input"], b"password")
                self.assertEqual(kwargs["stdout"], subprocess.PIPE)
                self.assertEqual(kwargs["stderr"], subprocess.STDOUT)

    def test_outer_payload_runtime_uses_shared_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(reroute_payloads.tempfile, "TemporaryDirectory") as temporary_directory, \
             mock.patch.object(reroute_payloads.RoutePayloadDiskImage, "_unmount_active_dmgs"), \
             mock.patch.object(reroute_payloads.disk_image, "attach_protected_disk_image") as attach:
            temporary_directory.return_value.name = directory
            attach.return_value = subprocess.CompletedProcess([], 0, b"")
            constants = SimpleNamespace(
                wxpython_variant=True,
                launcher_script=None,
                payload_path_dmg=Path("/app/Contents/Frameworks/payloads.dmg"),
                current_path=Path("/app/Contents/Frameworks"),
                payload_path=Path("/app/Contents/Frameworks/payloads"),
            )

            reroute_payloads.RoutePayloadDiskImage(constants)

            attach.assert_called_once_with(
                image_path=constants.payload_path_dmg,
                mountpoint=Path(directory) / "payloads",
                shadow_path=Path(directory) / "payloads_overlay",
            )
            self.assertEqual(constants.payload_path, Path(directory) / "payloads")

    def test_inner_apfs_image_uses_host_mountpoint_and_logical_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(Path, "is_mount", return_value=True), \
             mock.patch.object(dmg_mount.disk_image, "attach_protected_disk_image") as attach:
            attach.return_value = subprocess.CompletedProcess([], 0, b"")
            temporary_root = Path(directory)
            payload_path = temporary_root / "payloads"
            payload_path.mkdir()
            image_path = temporary_root / "Universal-Binaries.dmg"
            image_path.touch()
            constants = SimpleNamespace(
                payload_local_binaries_root_path_dmg=image_path,
                payload_local_binaries_root_path=payload_path / "Universal-Binaries",
                payload_path=payload_path,
                app_icon_path=Path("/app/icon.icns"),
            )

            result = dmg_mount.PatcherSupportPkgMount(constants)._mount_universal_binaries_dmg()

            self.assertTrue(result)
            physical_mountpoint = temporary_root / "Universal-Binaries"
            attach.assert_called_once_with(
                image_path=constants.payload_local_binaries_root_path_dmg,
                mountpoint=physical_mountpoint,
                shadow_path=temporary_root / "Universal-Binaries_overlay",
            )
            self.assertTrue(constants.payload_local_binaries_root_path.is_symlink())
            self.assertEqual(constants.payload_local_binaries_root_path.resolve(), physical_mountpoint.resolve())


if __name__ == "__main__":
    unittest.main()
