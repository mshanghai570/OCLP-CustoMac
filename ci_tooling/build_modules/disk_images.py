"""
disk_images.py: Fetch and generate disk images (Universal-Binaries.dmg, payloads.dmg)
"""

import subprocess

from pathlib import Path

from opencore_legacy_patcher import constants
from opencore_legacy_patcher.support import subprocess_wrapper

from .payload_contract import PayloadContract, PayloadContractError
from .version_contract import VersionContractError, validate_source



class GenerateDiskImages:

    def __init__(self, reset_dmg_cache: bool = False) -> None:
        """
        Initialize
        """
        self.reset_dmg_cache = reset_dmg_cache
        self.payload_contract = PayloadContract()


    def _delete_extra_binaries(self):
        """
        Delete extra binaries from payloads directory
        """

        whitelist_folders = [
            "ACPI",
            "Config",
            "Drivers",
            "Icon",
            "Kexts",
            "OpenCore",
            "Tools",
            "Launch Services",
        ]

        whitelist_files = []

        print("Deleting extra binaries...")
        for file in Path("payloads").glob(pattern="*"):
            if file.is_dir():
                if file.name in whitelist_folders:
                    continue
                print(f"- Deleting {file.name}")
                subprocess_wrapper.run_and_verify(["/bin/rm", "-rf", file])
            else:
                if file.name in whitelist_files:
                    continue
                print(f"- Deleting {file.name}")
                subprocess_wrapper.run_and_verify(["/bin/rm", "-f", file])



    def _generate_payloads_dmg(self):
        """
        Generate disk image containing all payloads
        Disk image will be password protected due to issues with
        Apple's notarization system and inclusion of kernel extensions
        """

        payload_image = Path("./payloads.dmg")
        self.payload_contract.validate_payload_root(Path("./payloads"))

        if payload_image.exists():
            if self.reset_dmg_cache is False:
                try:
                    self.payload_contract.validate_payload_dmg(payload_image)
                except PayloadContractError as error:
                    print("- Existing payloads.dmg is incompatible with current source; regenerating")
                    print(error)
                else:
                    print("- payloads.dmg already exists and passed current payload contract")
                    return

            print("- Removing old payloads.dmg")
            subprocess_wrapper.run_and_verify(
                ["/bin/rm", "-rf", "./payloads.dmg"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

        print("Generating DMG...")
        subprocess_wrapper.run_and_verify([
            '/usr/bin/hdiutil', 'create', './payloads.dmg',
            '-megabytes', '32000',  # Overlays can only be as large as the disk image allows
            '-format', 'UDZO', '-ov',
            '-volname', 'OpenCore Patcher Resources (Base)',
            '-fs', 'APFS',
            '-layout', 'NONE',
            '-srcfolder', './payloads',
            '-passphrase', 'password', '-encryption'
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        self.payload_contract.validate_payload_dmg(payload_image)
        print("DMG generation and payload contract validation complete")


    def _download_resources(self):
        """
        Download required dependencies
        """

        patcher_support_pkg_version = constants.Constants().patcher_support_pkg_version
        required_resources = [
            "Universal-Binaries.dmg"
        ]

        print("Downloading required resources...")
        for resource in required_resources:
            if Path(f"./{resource}").exists():
                if self.reset_dmg_cache is True:
                    print(f"  - Removing old {resource}")
                    # Just to be safe
                    assert resource, "Resource cannot be empty"
                    assert resource not in ("/", "."), "Resource cannot be root"
                    subprocess_wrapper.run_and_verify(
                        ["/bin/rm", "-rf", f"./{resource}"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE
                    )
                else:
                    self.payload_contract.validate_universal_binaries_dmg(Path(resource))
                    print(f"- {resource} already exists and passed Tahoe patch resource checks")
                    continue

            print(f"- Downloading {resource}...")

            subprocess_wrapper.run_and_verify(
                [
                    "/usr/bin/curl", "--fail", "--location", "--output", resource,
                    f"https://github.com/kgp-macPro/PatcherSupportPkg-laobamac/releases/download/{patcher_support_pkg_version}/{resource}"
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

            if not Path(f"./{resource}").exists():
                print(f"- {resource} not found")
                raise Exception(f"{resource} not found")
            self.payload_contract.validate_universal_binaries_dmg(Path(resource))


    def generate(self) -> None:
        """
        Generate disk images
        """

        # Source first: this method deletes from the working tree and writes new
        # images, so a release-number defect stops the build before either.
        validate_source()
        self._delete_extra_binaries()
        self._generate_payloads_dmg()
        self._download_resources()
