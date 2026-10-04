"""
mount.py: Handling macOS root volume mounting and unmounting
"""

import logging
import plistlib
import re
import subprocess

from pathlib import Path

from .snapshot import APFSSnapshot

from ...datasets import os_data
from ...support  import subprocess_wrapper

ROOT_MOUNT_PATH = "/System/Volumes/Update/mnt1"


class RootVolumeMount:

    def __init__(self, xnu_major: int) -> None:
        self.xnu_major = xnu_major
        self.root_volume_identifier = self._fetch_root_volume_identifier()

        self.mount_path = None
        self.owns_mount = False


    def _fetch_root_volume_identifier(self) -> str:
        """
        Resolve path to disk identifier

        ex. / -> disk1s1
        """
        result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", "/"], capture_output=True)
        if result.returncode != 0:
            raise RuntimeError("Failed to query root volume with diskutil.")
        try:
            content = plistlib.loads(result.stdout)
        except plistlib.InvalidFileException:
            raise RuntimeError("Failed to parse diskutil output.")

        disk = content["DeviceIdentifier"]

        if "APFSSnapshot" in content and content["APFSSnapshot"] is True:
            match = re.fullmatch(r"(disk\d+s\d+)s\d+", disk)
            if match is None:
                raise RuntimeError(f"Invalid APFS snapshot disk identifier: {disk}")
            disk = match.group(1)

        return disk


    def _mount_root_volume(self) -> str:
        """
        Mount the root volume.

        Returns the path to the root volume.
        """
        # Root volume same as data volume
        if self.xnu_major < os_data.os_data.catalina.value:
            return "/"

        # Catalina implemented a read-only root volume
        if self.xnu_major == os_data.os_data.catalina.value:
            result = subprocess_wrapper.run_as_root(["/sbin/mount", "-uw", "/"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if result.returncode != 0:
                logging.error("Failed to mount root volume")
                subprocess_wrapper.log(result)
                return None
            return "/"

        # Big Sur and newer implemented APFS snapshots for the root volume
        if self.xnu_major >= os_data.os_data.big_sur.value:
            if Path(ROOT_MOUNT_PATH, "System/Library/CoreServices/SystemVersion.plist").exists():
                logging.error("Root mount is already in use; finish or cancel the existing operation first")
                return None
            existing = self._mount_info()
            if existing is None or existing.get("MountPoint") == ROOT_MOUNT_PATH:
                logging.error("Cannot establish that the root mount location is available")
                return None
            result = subprocess_wrapper.run_as_root(["/sbin/mount", "-o", "nobrowse", "-t", "apfs", f"/dev/{self.root_volume_identifier}", ROOT_MOUNT_PATH], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if result.returncode != 0:
                logging.error("Failed to mount root volume")
                subprocess_wrapper.log(result)
                return None
            self.mount_path = ROOT_MOUNT_PATH
            self.owns_mount = True
            mounted = self._mount_info()
            if mounted is None or mounted.get("DeviceIdentifier") != self.root_volume_identifier or mounted.get("MountPoint") != ROOT_MOUNT_PATH:
                logging.error("Mounted root source does not match the expected system volume")
                self._unmount_root_volume(ignore_errors=False)
                return None
            return ROOT_MOUNT_PATH

        return None


    def _mount_info(self) -> dict | None:
        result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", ROOT_MOUNT_PATH], capture_output=True)
        if result.returncode != 0:
            return None
        try:
            data = plistlib.loads(result.stdout)
            if not isinstance(data, dict) or not isinstance(data.get("DeviceIdentifier"), str) or not isinstance(data.get("MountPoint"), str):
                return None
            return data
        except (plistlib.InvalidFileException, TypeError, ValueError):
            return None


    def _unmount_root_volume(self, ignore_errors: bool = True) -> bool:
        """
        Unmount the root volume.
        """
        if self.xnu_major < os_data.os_data.catalina.value:
            return True
        if self.xnu_major >= os_data.os_data.big_sur.value and self.owns_mount is False:
            return True
        if self.mount_path is None:
            return True

        args = ["/sbin/umount"]

        if self.xnu_major == os_data.os_data.catalina.value:
            args += ["-uw", self.mount_path]

        if self.xnu_major >= os_data.os_data.big_sur.value:
            args += [self.mount_path]

        result = subprocess_wrapper.run_as_root(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode != 0:
            if ignore_errors is False:
                logging.error("Failed to unmount root volume")
                subprocess_wrapper.log(result)
            return False

        self.owns_mount = False
        self.mount_path = None
        return True


    def mount(self) -> str:
        """
        Mount the root volume.

        Returns the path to the root volume.

        If none, failed to mount.
        """
        result = self._mount_root_volume()
        if result is None:
            logging.error("Failed to mount root volume")
            return None
        if not Path(result).exists():
            logging.error(f"Attempted to mount root volume, but failed: {result}")
            self._unmount_root_volume(ignore_errors=False)
            return None

        self.mount_path = result

        return result


    def unmount(self, ignore_errors: bool = True) -> bool:
        """
        Unmount the root volume.

        Returns True if successful, False otherwise.

        Note for Big Sur and newer, a snapshot is created before unmounting.
        And that unmounting is not critical to the process.
        """
        return self._unmount_root_volume(ignore_errors=ignore_errors)


    def create_snapshot(self) -> bool:
        """
        Create APFS snapshot of the root volume.
        """
        return APFSSnapshot(self.xnu_major, self.mount_path).create_snapshot()


    def revert_snapshot(self) -> bool:
        """
        Revert APFS snapshot of the root volume.
        """
        return APFSSnapshot(self.xnu_major, self.mount_path).revert_snapshot()
