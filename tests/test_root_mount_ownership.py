"""A root operation must verify its mount and leave pre-existing mounts alone."""

import plistlib
import subprocess
import unittest

from unittest import mock

from opencore_legacy_patcher.sys_patch.mount import mount


class RootMountOwnershipTests(unittest.TestCase):
    def mount_object(self):
        response = subprocess.CompletedProcess([], 0, plistlib.dumps({"DeviceIdentifier": "disk3s5s1", "APFSSnapshot": True}))
        with mock.patch.object(mount.subprocess, "run", return_value=response):
            return mount.RootVolumeMount(25)

    def info(self, disk, location):
        return subprocess.CompletedProcess([], 0, plistlib.dumps({
            "DeviceIdentifier": disk, "MountPoint": location, "Mounted": True,
        }))

    def test_preexisting_update_mount_is_rejected_and_never_unmounted(self):
        instance = self.mount_object()
        with mock.patch.object(mount.Path, "exists", return_value=True), \
             mock.patch.object(mount.subprocess_wrapper, "run_as_root") as run:
            self.assertIsNone(instance.mount())
            self.assertTrue(instance.unmount())
        run.assert_not_called()

    def test_mounted_nonroot_volume_without_system_version_is_rejected(self):
        instance = self.mount_object()
        with mock.patch.object(mount.Path, "exists", return_value=False), \
             mock.patch.object(mount.subprocess, "run", return_value=self.info("disk9s1", mount.ROOT_MOUNT_PATH)) , \
             mock.patch.object(mount.subprocess_wrapper, "run_as_root") as run:
            self.assertIsNone(instance.mount())
        run.assert_not_called()

    def test_successful_owned_mount_is_verified_and_released_only_once(self):
        instance = self.mount_object()
        with mock.patch.object(mount.Path, "exists", side_effect=[False, True]), \
             mock.patch.object(mount.subprocess, "run", side_effect=[self.info("disk3s5s1", "/"), self.info("disk3s5", mount.ROOT_MOUNT_PATH)]) as info, \
             mock.patch.object(mount.subprocess_wrapper, "run_as_root", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(instance.mount(), mount.ROOT_MOUNT_PATH)
            self.assertTrue(instance.unmount())
            self.assertTrue(instance.unmount())
        self.assertEqual(info.call_count, 2)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0], ["/sbin/umount", mount.ROOT_MOUNT_PATH])

    def test_wrong_source_after_mount_is_rejected(self):
        instance = self.mount_object()
        with mock.patch.object(mount.Path, "exists", return_value=False), \
             mock.patch.object(mount.subprocess, "run", side_effect=[self.info("disk3s5s1", "/"), self.info("disk9s1", mount.ROOT_MOUNT_PATH)]), \
             mock.patch.object(mount.subprocess_wrapper, "run_as_root", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertIsNone(instance.mount())
        self.assertEqual(run.call_args.args[0], ["/sbin/umount", mount.ROOT_MOUNT_PATH])

    def test_unreadable_or_malformed_mount_information_blocks_mounting(self):
        instance = self.mount_object()
        for response in (subprocess.CompletedProcess([], 1, b""),
                         subprocess.CompletedProcess([], 0, b"invalid"),
                         subprocess.CompletedProcess([], 0, plistlib.dumps({}))):
            with self.subTest(response=response), \
                 mock.patch.object(mount.Path, "exists", return_value=False), \
                 mock.patch.object(mount.subprocess, "run", return_value=response), \
                 mock.patch.object(mount.subprocess_wrapper, "run_as_root") as run:
                self.assertIsNone(instance.mount())
            run.assert_not_called()

    def test_failed_unmount_can_be_retried(self):
        instance = self.mount_object()
        instance.mount_path = mount.ROOT_MOUNT_PATH
        instance.owns_mount = True
        with mock.patch.object(mount.subprocess_wrapper, "run_as_root", side_effect=[subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]) as run:
            self.assertFalse(instance.unmount())
            self.assertTrue(instance.unmount())
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
