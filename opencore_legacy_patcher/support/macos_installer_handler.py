"""
macos_installer_handler.py: Handler for local macOS installers
"""

import logging
import plistlib
import tempfile
import subprocess
import re
import stat
import shlex

from pathlib import Path

from ..datasets import os_data
from .package_trust import PackageTrustError, Publisher, install_verified_package

from . import (
    utilities,
    subprocess_wrapper
)

from ..volume import (
    can_copy_on_write,
    generate_copy_arguments
)


APPLICATION_SEARCH_PATH:  str = "/Applications"

# createinstallmedia has to be told where the installer bundle is on the macOS
# 10.12 and earlier era, and 10.13 — High Sierra — dropped the option. The
# boundary is that release's kernel major, taken from the datasets, rather than a
# bare 13 that also reads as Ventura's marketing major.
APPLICATIONPATH_DROPPED_AT: int = int(os_data.os_data.high_sierra)

tmp_dir = tempfile.TemporaryDirectory()


def _version_order_key(version: str) -> tuple:
    """
    Ordering key for one local installer's version string

    The catalog used to sort these as text, where "9.2.2" lands after "26.0"
    and "13.7.2" lands before "9.2.2". The numbers are what the installer list
    is ordered by. A version with no number in it ("Unknown") sorts after the
    numbered ones, by its text, rather than being dropped.
    """
    text = version or ""
    try:
        numbers = tuple(int(component) for component in text.split("."))
    except ValueError:
        return (1, (), text)
    return (0, numbers, text)


def _requires_applicationpath(platform_version: str) -> bool:
    """
    Whether createinstallmedia must be pointed at its own installer bundle

    The previous test indexed `platform_version` as characters after truncating
    it to its leading component, so it asked whether the first character of "10"
    was "10" — never true — and the flag was never passed to an installer that
    needs it.
    """
    components = str(platform_version).split(".")
    if components[0] != "10":
        return False
    try:
        minor = int(components[1])
    except (IndexError, ValueError):
        return False
    # `os_to_kernel` owns the 10.x conversion, so the comparison is between two
    # kernel majors rather than between a minor and a number that could be read
    # as a release of its own.
    return os_data.os_conversion.os_to_kernel(f"10.{minor}") < APPLICATIONPATH_DROPPED_AT


class InstallerCreation():

    def __init__(self) -> None:
        pass

    @staticmethod
    def _bundle_inventory(bundle: Path) -> dict:
        inventory = {}
        if not bundle.is_dir() or bundle.is_symlink():
            raise ValueError("Installer bundle is not a directory")
        for path in bundle.rglob("*"):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                entry = ("link", path.readlink().as_posix())
            elif stat.S_ISDIR(info.st_mode):
                entry = ("directory", 0)
            elif stat.S_ISREG(info.st_mode):
                entry = ("file", info.st_size)
            else:
                raise ValueError(f"Unsupported installer entry: {path}")
            inventory[path.relative_to(bundle).as_posix()] = entry
        for required in ("Contents/Info.plist", "Contents/Resources/createinstallmedia"):
            if inventory.get(required, (None, 0))[0] != "file" or inventory[required][1] == 0:
                raise ValueError(f"Installer is missing {required}")
        return inventory


    def install_macOS_installer(self, download_path: str) -> bool:
        """
        Installs InstallAssistant.pkg

        Parameters:
            download_path (str): Path to InstallAssistant.pkg

        Returns:
            bool: True if successful, False otherwise
        """

        logging.info("Extracting macOS installer from InstallAssistant.pkg")
        try:
            result = install_verified_package(Path(download_path) / "InstallAssistant.pkg", Publisher.APPLE)
        except PackageTrustError as error:
            logging.error(f"Cannot install untrusted InstallAssistant package: {error}")
            return False
        if result.returncode != 0:
            logging.info("Failed to install InstallAssistant")
            subprocess_wrapper.log(result)
            return False

        logging.info("InstallAssistant installed")
        return True


    @staticmethod
    def _normalize_disk_identifier(disk: str) -> str:
        if not isinstance(disk, str) or re.fullmatch(r"(?:/dev/)?disk[0-9]+", disk) is None:
            raise ValueError("Expected a whole diskN device identifier")
        return disk.removeprefix("/dev/")


    @staticmethod
    def _read_device_plist(arguments: list[str]):
        result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise ValueError("Device information command failed")
        return plistlib.loads(result.stdout)


    @classmethod
    def _disk_identity(cls, disk: str) -> tuple[dict, dict]:
        """Pin a physical attachment, even when whole-disk DiskUUID is absent.

        IORegistryEntryID identifies this IOMedia attachment during the current
        boot. A removal/replacement receives a different ID, including a cloned
        USB with the same partition UUID. Never infer identity from diskN alone.
        """
        disk = cls._normalize_disk_identifier(disk)
        info = cls._read_device_plist(["/usr/sbin/diskutil", "info", "-plist", disk])
        if not isinstance(info, dict) or info.get("DeviceIdentifier") != disk or info.get("DeviceNode") != f"/dev/{disk}":
            raise ValueError("Disk identifier changed")
        if info.get("WholeDisk") is not True or info.get("Internal") is not False or info.get("VirtualOrPhysical") != "Physical":
            raise ValueError("Disk is not an external whole physical device")
        if type(info.get("TotalSize")) is not int or info["TotalSize"] <= 15032385536:
            raise ValueError("Installer disk is too small or has unknown capacity")
        for field in ("DeviceTreePath", "IORegistryEntryName"):
            if not isinstance(info.get(field), str) or not info[field]:
                raise ValueError("Disk has no trustworthy device fingerprint")
        entries = cls._read_device_plist(["/usr/sbin/ioreg", "-a", "-r", "-c", "IOMedia"])
        if not isinstance(entries, list):
            raise ValueError("Missing IOMedia registry")
        matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("BSD Name") == disk]
        if len(matches) != 1:
            raise ValueError("Missing or ambiguous disk attachment")
        entry = matches[0]
        if entry.get("Whole") is not True or type(entry.get("IORegistryEntryID")) is not int or entry["IORegistryEntryID"] <= 0:
            raise ValueError("Disk attachment has no valid registry identity")
        if entry.get("IORegistryEntryName") != info["IORegistryEntryName"]:
            raise ValueError("Disk registry fingerprint changed")
        boot = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if boot.returncode != 0:
            raise ValueError("Cannot establish disk attachment boot session")
        boot_session = boot.stdout.decode("ascii").strip()
        if re.fullmatch(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", boot_session) is None:
            raise ValueError("Missing or invalid boot session UUID")
        identity = {"boot_session_uuid": boot_session, "registry_id": entry["IORegistryEntryID"], "device_identifier": disk,
                    "size": info["TotalSize"], "tree_path": info["DeviceTreePath"],
                    "registry_name": info["IORegistryEntryName"]}
        return info, identity


    @staticmethod
    def _disk_guard_script(disk: str, identity: dict) -> str:
        quote = shlex.quote
        checks = {"DeviceIdentifier": disk, "DeviceNode": f"/dev/{disk}", "WholeDisk": "true",
                  "Internal": "false", "VirtualOrPhysical": "Physical", "TotalSize": str(identity["size"]),
                  "DeviceTreePath": identity["tree_path"], "IORegistryEntryName": identity["registry_name"]}
        script = """#!/bin/bash
set -e
fail_disk() { /bin/echo "Installer disk identity changed or cannot be verified" >&2; exit 1; }
check_dir=$(/usr/bin/mktemp -d /private/tmp/oclp-installer.XXXXXX) || fail_disk
trap '/bin/rm -rf "$check_dir"' EXIT
/usr/sbin/diskutil info -plist """ + quote(disk) + """ > "$check_dir/disk.plist" || fail_disk
field() { /usr/libexec/PlistBuddy -c "Print :$1" "$check_dir/disk.plist" 2>/dev/null; }
"""
        for field, value in checks.items():
            script += f'[ "$(field {quote(field)})" = {quote(value)} ] || fail_disk\n'
        script += """/usr/sbin/ioreg -a -r -c IOMedia > "$check_dir/registry.plist" || fail_disk
registry_field() { /usr/libexec/PlistBuddy -c "Print :$1:'$2'" "$check_dir/registry.plist" 2>/dev/null; }
index=0
matches=0
while /usr/libexec/PlistBuddy -c "Print :$index" "$check_dir/registry.plist" >/dev/null 2>&1; do
    if [ "$(registry_field "$index" 'BSD Name')" = """ + quote(disk) + """ ]; then
        matches=$((matches + 1))
        [ "$(registry_field "$index" Whole)" = true ] || fail_disk
        [ "$(registry_field "$index" IORegistryEntryID)" = """ + quote(str(identity["registry_id"])) + """ ] || fail_disk
        [ "$(registry_field "$index" IORegistryEntryName)" = """ + quote(identity["registry_name"]) + """ ] || fail_disk
    fi
    index=$((index + 1))
done
[ "$matches" -eq 1 ] || fail_disk
"""
        script += 'current_boot=$(/usr/sbin/sysctl -n kern.bootsessionuuid) || fail_disk\n'
        script += f'[ "$current_boot" = {quote(identity["boot_session_uuid"])} ] || fail_disk\n'
        return script


    def generate_installer_creation_script(self, tmp_location: str, installer_path: str, disk: str, expected_identity: dict = None) -> bool:
        """
        Creates installer.sh to be piped to OCLP-Helper and run as admin

        Script includes:
        - Format provided disk as HFS+ GPT
        - Run createinstallmedia on provided disk

        Implementing this into a single installer.sh script allows us to only call
        OCLP-Helper once to avoid nagging the user about permissions

        Parameters:
            tmp_location (str): Path to temporary directory
            installer_path (str): Path to InstallAssistant.pkg
            disk (str): Disk to install to

        Returns:
            bool: True if successful, False otherwise
        """

        try:
            disk = self._normalize_disk_identifier(disk)
        except ValueError as error:
            logging.error(str(error))
            return False
        additional_args = ""
        script_location = Path(tmp_location) / Path("Installer.sh")

        # Due to a bug in createinstallmedia, running from '/Applications' may sometimes error:
        #   'Failed to extract AssetData/boot/Firmware/Manifests/InstallerBoot/*'
        # This affects native Macs as well even when manually invoking createinstallmedia

        # To resolve, we'll copy into our temp directory and run from there

        # Create a new tmp directory
        # Our current one is a disk image, thus CoW will not work
        global tmp_dir
        ia_tmp = tmp_dir.name

        try:
            source_inventory = self._bundle_inventory(Path(installer_path))
        except (OSError, ValueError) as error:
            logging.error(f"Cannot inspect installer bundle: {error}")
            return False

        logging.info(f"Creating temporary directory at {ia_tmp}")
        # Delete all files in tmp_dir
        for file in Path(ia_tmp).glob("*"):
            if subprocess.run(["/bin/rm", "-rf", str(file)]).returncode != 0:
                logging.error("Failed to clear installer staging directory")
                return False

        # Copy installer to tmp
        if can_copy_on_write(installer_path, ia_tmp) is False:
            # Ensure we have enough space for the duplication when CoW is not supported
            space_available = utilities.get_free_space(str(ia_tmp))
            space_needed = sum((size + 4095) // 4096 * 4096 for kind, size in source_inventory.values() if kind == "file") + 128 * 1024**2
            if space_available < space_needed:
                logging.info("Not enough free space to create installer.sh")
                logging.info(f"{utilities.human_fmt(space_available)} available, {utilities.human_fmt(space_needed)} required")
                return False

        if subprocess.run(generate_copy_arguments(installer_path, ia_tmp)).returncode != 0:
            logging.error("Failed to copy macOS installer; script generation cancelled")
            return False

        # Adjust installer_path to point to the copied installer
        installer_path = Path(ia_tmp) / Path(Path(installer_path).name)
        try:
            copied_inventory = self._bundle_inventory(installer_path)
        except (OSError, ValueError) as error:
            logging.error(f"Copied installer is incomplete: {error}")
            return False
        if copied_inventory != source_inventory:
            logging.error("Copied installer does not match the source bundle inventory")
            return False

        # Verify code signature before executing
        createinstallmedia_path = str(Path(installer_path) / Path("Contents/Resources/createinstallmedia"))
        if subprocess.run(["/usr/bin/codesign", "-v", "-R=anchor apple", createinstallmedia_path]).returncode != 0:
            logging.info(f"Installer has broken code signature")
            return False

        plist_path = str(Path(installer_path) / Path("Contents/Info.plist"))
        if Path(plist_path).exists():
            with Path(plist_path).open("rb") as plist_file:
                plist = plistlib.load(plist_file)
            if "DTPlatformVersion" in plist:
                if _requires_applicationpath(plist["DTPlatformVersion"]):
                    additional_args = f" --applicationpath {shlex.quote(str(installer_path))}"

        try:
            _, identity = self._disk_identity(disk)
            if expected_identity is not None and identity != expected_identity:
                raise ValueError("Selected disk attachment changed before preparation")
        except (OSError, ValueError, TypeError, plistlib.InvalidFileException) as error:
            logging.error(f"Cannot verify installer disk: {error}")
            return False

        script = self._disk_guard_script(disk, identity)
        script += f"/usr/sbin/diskutil eraseDisk HFS+ OCLP-Installer {shlex.quote(disk)}\n"
        script += f"{shlex.quote(createinstallmedia_path)} --volume /Volumes/OCLP-Installer --nointeraction{additional_args}\n"
        script_location.write_text(script)
        return script_location.is_file()


    def list_disk_to_format(self) -> dict:
        """
        List applicable disks for macOS installer creation
        Only lists disks that are:
        - 14GB or larger
        - External

        Current limitations:
        - Does not support PCIe based SD cards readers

        Returns:
            dict: Dictionary of disks
        """

        list_disks = {}
        try:
            try:
                disks = self._read_device_plist(["/usr/sbin/diskutil", "list", "-plist", "physical"])
            except (ValueError, plistlib.InvalidFileException):
                disks = self._read_device_plist(["/usr/sbin/diskutil", "list", "-plist"])
            for disk in disks["AllDisksAndPartitions"]:
                try:
                    info, identity = self._disk_identity(disk["DeviceIdentifier"])
                except (OSError, ValueError, TypeError, KeyError, plistlib.InvalidFileException):
                    continue
                list_disks[info["DeviceIdentifier"]] = {
                    "identifier": info["DeviceNode"], "name": info.get("MediaName", "Disk"),
                    "size": info["TotalSize"], "identity": identity,
                }
        except (OSError, ValueError, TypeError, KeyError, plistlib.InvalidFileException) as error:
            logging.error(f"Cannot enumerate installer disks safely: {error}")
        return list_disks



class LocalInstallerCatalog:
    """
    Finds all macOS installers on the local machine.
    """

    def __init__(self) -> None:
        self.available_apps: dict = self._list_local_macOS_installers()


    def _list_local_macOS_installers(self) -> dict:
        """
        Searches for macOS installers in /Applications

        Returns:
            dict: A dictionary of macOS installers found on the local machine.

            Example:
                "Install macOS Big Sur Beta.app": {
                    "Short Name": "Big Sur Beta",
                    "Version": "11.0",
                    "Build": "20A5343i",
                    "Path": "/Applications/Install macOS Big Sur Beta.app",
                },
                etc...
        """

        application_list: dict = {}

        for application in Path(APPLICATION_SEARCH_PATH).iterdir():
            # Certain Microsoft Applications have strange permissions disabling us from reading them
            try:
                if not (Path(APPLICATION_SEARCH_PATH) / Path(application) / Path("Contents/Resources/createinstallmedia")).exists():
                    continue

                if not (Path(APPLICATION_SEARCH_PATH) / Path(application) / Path("Contents/Info.plist")).exists():
                    continue
            except PermissionError:
                continue

            try:
                with (Path(APPLICATION_SEARCH_PATH) / Path(application) / Path("Contents/Info.plist")).open("rb") as info_plist:
                    application_info_plist = plistlib.load(info_plist)
            except (PermissionError, TypeError, plistlib.InvalidFileException):
                continue

            if "DTPlatformVersion" not in application_info_plist:
                continue
            if "CFBundleDisplayName" not in application_info_plist:
                continue

            app_version:  str = application_info_plist["DTPlatformVersion"]
            clean_name:   str = application_info_plist["CFBundleDisplayName"]
            app_sdk:      str = application_info_plist["DTSDKBuild"] if "DTSDKBuild" in application_info_plist else "Unknown"
            min_required: str = application_info_plist["LSMinimumSystemVersion"] if "LSMinimumSystemVersion" in application_info_plist else "Unknown"

            kernel:       int = 0
            try:
                kernel = int(app_sdk[:2])
            except ValueError:
                pass

            min_required = os_data.os_conversion.os_to_kernel(min_required) if min_required != "Unknown" else 0

            if min_required == os_data.os_data.sierra and kernel == os_data.os_data.ventura:
                # Ventura's installer requires El Capitan minimum
                # Ref: https://github.com/dortania/OpenCore-Legacy-Patcher/discussions/1038
                min_required = os_data.os_data.el_capitan

            # app_version can sometimes report GM instead of the actual version
            # This is a workaround to get the actual version
            if app_version.startswith("GM"):
                if kernel == 0:
                    app_version = "Unknown"
                else:
                    app_version = os_data.os_conversion.kernel_to_os(kernel)

            # Check if App Version is High Sierra or newer
            if kernel < os_data.os_data.high_sierra:
                continue

            results = self._parse_sharedsupport_version(Path(APPLICATION_SEARCH_PATH) / Path(application)/ Path("Contents/SharedSupport/SharedSupport.dmg"))
            if results[0] is not None:
                app_sdk = results[0]
            if results[1] is not None:
                app_version = results[1]

            application_list.update({
                application: {
                    "Short Name": clean_name,
                    "Version": app_version,
                    "Build": app_sdk,
                    "Path": application,
                    "Minimum Host OS": min_required,
                    "OS": kernel
                }
            })

        # Sort Applications by version
        application_list = {
            k: v for k, v in sorted(
                application_list.items(),
                key=lambda item: _version_order_key(item[1]["Version"]),
            )
        }
        return application_list


    def _parse_sharedsupport_version(self, sharedsupport_path: Path) -> tuple:
        """
        Determine true version of macOS installer by parsing SharedSupport.dmg
        This is required due to Info.plist reporting the application version, not the OS version

        Parameters:
            sharedsupport_path (Path): Path to SharedSupport.dmg

        Returns:
            tuple: Tuple containing the build and OS version
        """

        detected_build: str = None
        detected_os:    str = None

        if not sharedsupport_path.exists():
            return (detected_build, detected_os)

        if not sharedsupport_path.name.endswith(".dmg"):
            return (detected_build, detected_os)


        # Create temporary directory to extract SharedSupport.dmg to
        with tempfile.TemporaryDirectory() as tmpdir:

            output = subprocess.run(
                [
                    "/usr/bin/hdiutil", "attach", "-noverify", sharedsupport_path,
                    "-mountpoint", tmpdir,
                    "-nobrowse",
                ],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )

            if output.returncode != 0:
                return (detected_build, detected_os)


            ss_info_files = [
                Path("SFR/com_apple_MobileAsset_SFRSoftwareUpdate/com_apple_MobileAsset_SFRSoftwareUpdate.xml"),
                Path("com_apple_MobileAsset_MacSoftwareUpdate/com_apple_MobileAsset_MacSoftwareUpdate.xml")
            ]

            for ss_info in ss_info_files:
                if not Path(tmpdir / ss_info).exists():
                    continue
                with (tmpdir / ss_info).open("rb") as ss_plist:
                    plist = plistlib.load(ss_plist)
                if "Assets" in plist:
                    if "Build" in plist["Assets"][0]:
                        detected_build = plist["Assets"][0]["Build"]
                    if "OSVersion" in plist["Assets"][0]:
                        detected_os = plist["Assets"][0]["OSVersion"]

            # Unmount SharedSupport.dmg
            subprocess.run(["/usr/bin/hdiutil", "detach", tmpdir], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        return (detected_build, detected_os)
