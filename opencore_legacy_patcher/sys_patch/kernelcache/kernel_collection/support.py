"""
support.py: Kernel Cache support functions
"""

import logging
import plistlib

from pathlib  import Path
from ...patchsets import PatchType
from ...root_selection import SELECTABLE_ROOT_PATCHES

from ....datasets import os_data
from ....support  import subprocess_wrapper
from ...root_state import ROOT_PATCH_METADATA_PATH, ROOT_PATCH_METADATA_SCHEMA


class KernelCacheSupport:

    def __init__(self, mount_location_data: str, detected_os: int, skip_root_kmutil_requirement: bool) -> None:
        self.mount_location_data = mount_location_data
        self.detected_os = detected_os
        self.skip_root_kmutil_requirement = skip_root_kmutil_requirement


    def check_kexts_needs_authentication(self, kext_name: str) -> bool:
        """
        Verify whether the user needs to authenticate in System Preferences
        Sets 'needs_to_open_preferences' to True if the kext is not in the AuxKC

        Logic:
            Under 'private/var/db/KernelManagement/AuxKC/CurrentAuxKC/com.apple.kcgen.instructions.plist'
                ["kextsToBuild"][i]:
                ["bundlePathMainOS"] = /Library/Extensions/Test.kext
                ["cdHash"] =           Bundle's CDHash (random on ad-hoc signed, static on dev signed)
                ["teamID"] =           Team ID (blank on ad-hoc signed)
            To grab the CDHash of a kext, run 'codesign -dvvv <kext_path>'
        """
        if not kext_name.endswith(".kext"):
            return False

        try:
            aux_cache_path = Path(self.mount_location_data) / Path("/private/var/db/KernelExtensionManagement/AuxKC/CurrentAuxKC/com.apple.kcgen.instructions.plist")
            if aux_cache_path.exists():
                with aux_cache_path.open("rb") as aux_cache_file:
                    aux_cache_data = plistlib.load(aux_cache_file)
                for kext in aux_cache_data["kextsToBuild"]:
                    if "bundlePathMainOS" in aux_cache_data["kextsToBuild"][kext]:
                        if aux_cache_data["kextsToBuild"][kext]["bundlePathMainOS"] == f"/Library/Extensions/{kext_name}":
                            return False
        except PermissionError:
            pass

        logging.info(f"  - {kext_name} requires authentication in System Preferences")

        return True


    def add_auxkc_support(self, install_file: str, source_folder_path: str, install_patch_directory: str, destination_folder_path: str) -> str:
        """
        Patch provided Kext to support Auxiliary Kernel Collection

        Logic:
            In macOS Ventura, KDKs are required to build new Boot and System KCs
            However for some patch sets, we're able to use the Auxiliary KCs with '/Library/Extensions'

            kernelmanagerd determines which kext is installed by their 'OSBundleRequired' entry
            If a kext is labeled as 'OSBundleRequired: Root' or 'OSBundleRequired: Safe Boot',
            kernelmanagerd will require the kext to be installed in the Boot/SysKC

            Additionally, kexts starting with 'com.apple.' are not natively allowed to be installed
            in the AuxKC. So we need to explicitly set our 'OSBundleRequired' to 'Auxiliary'

        Parameters:
            install_file            (str): Kext file name
            source_folder_path      (str): Source folder path
            install_patch_directory (str): Patch directory
            destination_folder_path (str): Destination folder path

        Returns:
            str: Updated destination folder path
        """

        if self.skip_root_kmutil_requirement is False:
            return destination_folder_path
        if not install_file.endswith(".kext"):
            return destination_folder_path
        if install_patch_directory != "/System/Library/Extensions":
            return destination_folder_path
        if self.detected_os < os_data.os_data.ventura:
            return destination_folder_path

        updated_install_location = str(self.mount_location_data) + "/Library/Extensions"

        logging.info(f"  - Adding AuxKC support to {install_file}")
        plist_path = Path(Path(source_folder_path) / Path(install_file) / Path("Contents/Info.plist"))
        with plist_path.open("rb") as plist_file:
            plist_data = plistlib.load(plist_file)

        # Check if we need to update the 'OSBundleRequired' entry
        if not plist_data["CFBundleIdentifier"].startswith("com.apple."):
            return updated_install_location
        if "OSBundleRequired" in plist_data:
            if plist_data["OSBundleRequired"] == "Auxiliary":
                return updated_install_location

        plist_data["OSBundleRequired"] = "Auxiliary"
        with plist_path.open("wb") as plist_file:
            plistlib.dump(plist_data, plist_file)

        return updated_install_location


    def clean_auxiliary_kc(self, *, expected_project_identity: str = None) -> bool:
        """Remove only recorded Data-volume kexts owned by supported patch families.

        Unusable history is reported without guessing deletion targets. File age
        and System-volume entries do not establish ownership of Data-volume files.
        """
        if self.detected_os < os_data.os_data.big_sur:
            return True
        try:
            if ROOT_PATCH_METADATA_PATH.is_symlink():
                raise ValueError("patch history is a symlink")
            with ROOT_PATCH_METADATA_PATH.open("rb") as metadata_file:
                metadata = plistlib.load(metadata_file)
        except FileNotFoundError:
            return True
        except (OSError, plistlib.InvalidFileException, TypeError, ValueError) as error:
            logging.warning(f"- Auxiliary cleanup skipped: unusable patch history: {error}")
            return False

        supported = set().union(*(definition.patch_names for definition in SELECTABLE_ROOT_PATCHES))
        try:
            if not isinstance(metadata, dict) or metadata.get("Metadata Schema") != ROOT_PATCH_METADATA_SCHEMA:
                raise ValueError("unrecognized patch history")
            if not expected_project_identity or metadata.get("Project Identity") != expected_project_identity:
                raise ValueError("patch history belongs to an unknown project")
            installed = metadata.get("Installed Patches")
            if not isinstance(installed, list) or not all(isinstance(name, str) and name in supported for name in installed):
                raise ValueError("unsupported installed patch families")

            # Validate the complete deletion inventory before issuing any command.
            targets = set()
            for name in installed:
                operations = metadata.get(name)
                if not isinstance(operations, dict):
                    raise ValueError(f"invalid operations for {name}")
                for operation in (PatchType.OVERWRITE_DATA_VOLUME, PatchType.MERGE_DATA_VOLUME):
                    locations = operations.get(operation, {})
                    if not isinstance(locations, dict):
                        raise ValueError(f"invalid locations for {name}")
                    for location, files in locations.items():
                        if not isinstance(files, dict):
                            raise ValueError(f"invalid file inventory for {name}")
                        for filename, source in files.items():
                            if not isinstance(filename, str) or Path(filename).name != filename or not filename or not isinstance(source, str):
                                raise ValueError("invalid file ownership record")
                            if location == "/Library/Extensions" and filename.endswith(".kext"):
                                targets.add(Path(self.mount_location_data or "/") / "Library/Extensions" / filename)
            if any(target.parent.is_symlink() or target.is_symlink() for target in targets):
                raise ValueError("extension cleanup target is a symlink")
        except (OSError, ValueError) as error:
            logging.warning(f"- Auxiliary cleanup skipped: {error}")
            return False

        for target in sorted(targets):
            if target.exists():
                logging.info(f"  - Removing owned extension {target.name}")
                subprocess_wrapper.run_as_root_and_verify(["/bin/rm", "-Rf", str(target)])
        return True
