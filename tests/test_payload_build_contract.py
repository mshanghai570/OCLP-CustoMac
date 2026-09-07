"""Fail-closed payload composition tests for application and package builds."""

import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from pathlib import Path
from unittest import mock

from ci_tooling.build_modules import application, disk_images, package, payload_contract


class PayloadContractTests(unittest.TestCase):
    def test_current_tracked_payload_tree_matches_contract(self) -> None:
        payload_contract.PayloadContract().validate_payload_root(Path("payloads"))

    def test_release_paths_are_derived_from_current_constants(self) -> None:
        contract = payload_contract.PayloadContract()
        root = Path("/payload-contract")
        actual = {
            component: str(path.relative_to(root))
            for component, path in contract.expected_release_components(root).items()
        }
        self.assertEqual(
            actual,
            {
                "OpenCorePkg": "OpenCore/OpenCore-RELEASE.zip",
                "Lilu": "Kexts/Acidanthera/Lilu-v1.7.2-RELEASE.zip",
                "WhateverGreen": "Kexts/Acidanthera/WhateverGreen-v1.7.0-RELEASE.zip",
                "RestrictEvents": "Kexts/Acidanthera/RestrictEvents-v1.1.6-RELEASE.zip",
                "AirportBrcmFixup": "Kexts/Acidanthera/AirportBrcmFixup-v2.2.0-RELEASE.zip",
                "BlueToolFixup": "Kexts/Acidanthera/BlueToolFixup-v2.7.2-RELEASE.zip",
                "NVMeFix": "Kexts/Acidanthera/NVMeFix-v1.1.3-RELEASE.zip",
                "CPUFriend": "Kexts/Acidanthera/CPUFriend-v1.3.0-RELEASE.zip",
                "CryptexFixup": "Kexts/Acidanthera/CryptexFixup-v1.0.5-RELEASE.zip",
                "DebugEnhancer": "Kexts/Acidanthera/DebugEnhancer-v1.1.1-RELEASE.zip",
                "AppleALC": "Kexts/Acidanthera/AppleALC-v1.9.7-RELEASE.zip",
                "FeatureUnlock": "Kexts/Acidanthera/FeatureUnlock-v1.1.8-RELEASE.zip",
                "AMFIPass": "Kexts/Acidanthera/AMFIPass-v1.4.1-RELEASE.zip",
            },
        )

    @staticmethod
    def _create_contract_fixture(root: Path) -> None:
        contract = payload_contract.PayloadContract(source_payload_root=root)
        for path in contract.expected_release_components(root).values():
            path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("fixture", b"fixture")
        ocvalidate = root / "OpenCore" / "ocvalidate"
        ocvalidate.write_bytes(b"fixture")

    def test_opencore_identity_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            reference = temporary_root / "reference"
            payload = temporary_root / "payload"
            self._create_contract_fixture(reference)
            shutil.copytree(reference, payload)
            contract = payload_contract.PayloadContract(source_payload_root=reference)

            with mock.patch.object(contract, "_reported_opencore_version", return_value="1.0.5"):
                with self.assertRaisesRegex(
                    payload_contract.PayloadContractError,
                    "contained ocvalidate reports 1.0.5, Constants requires 1.0.7",
                ):
                    contract.validate_payload_root(payload)

    def test_missing_lilu_and_opencore_mismatch_are_both_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            reference = temporary_root / "reference"
            payload = temporary_root / "payload"
            self._create_contract_fixture(reference)
            shutil.copytree(reference, payload)
            contract = payload_contract.PayloadContract(source_payload_root=reference)
            lilu = contract.expected_release_components(payload)["Lilu"]
            lilu.unlink()

            with mock.patch.object(contract, "_reported_opencore_version", return_value="1.0.5"):
                with self.assertRaises(payload_contract.PayloadContractError) as raised:
                    contract.validate_payload_root(payload)

            details = str(raised.exception)
            self.assertIn("Missing required current component: Kexts/Acidanthera/Lilu-v1.7.2-RELEASE.zip", details)
            self.assertIn("contained ocvalidate reports 1.0.5, Constants requires 1.0.7", details)

    def test_valid_existing_payload_image_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            disk_images, "PayloadContract"
        ) as contract_type, mock.patch.object(
            disk_images.subprocess_wrapper, "run_and_verify"
        ) as run_and_verify:
            previous_directory = Path.cwd()
            os.chdir(directory)
            try:
                Path("payloads").mkdir()
                Path("payloads.dmg").touch()
                generator = disk_images.GenerateDiskImages(reset_dmg_cache=False)
                generator._generate_payloads_dmg()
            finally:
                os.chdir(previous_directory)

        contract_type.return_value.validate_payload_root.assert_called_once()
        contract_type.return_value.validate_payload_dmg.assert_called_once()
        run_and_verify.assert_not_called()

    def test_invalid_existing_payload_image_is_regenerated_and_revalidated(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            disk_images, "PayloadContract"
        ) as contract_type, mock.patch.object(
            disk_images.subprocess_wrapper, "run_and_verify"
        ) as run_and_verify:
            contract_type.return_value.validate_payload_dmg.side_effect = [
                payload_contract.PayloadContractError("stale payload"),
                None,
            ]
            previous_directory = Path.cwd()
            os.chdir(directory)
            try:
                Path("payloads").mkdir()
                Path("payloads.dmg").touch()
                generator = disk_images.GenerateDiskImages(reset_dmg_cache=False)
                generator._generate_payloads_dmg()
            finally:
                os.chdir(previous_directory)

        self.assertEqual(contract_type.return_value.validate_payload_dmg.call_count, 2)
        self.assertEqual(run_and_verify.call_count, 2)
        self.assertEqual(run_and_verify.call_args_list[0].args[0][:3], ["/bin/rm", "-rf", "./payloads.dmg"])
        self.assertEqual(run_and_verify.call_args_list[1].args[0][:3], ["/usr/bin/hdiutil", "create", "./payloads.dmg"])

    def test_application_build_validates_the_embedded_payload(self) -> None:
        with mock.patch.object(
            application.subprocess_wrapper, "run_and_verify"
        ), mock.patch.object(application, "PayloadContract") as contract_type:
            generator = application.GenerateApplication()
            generator._generate_application()

        contract_type.return_value.validate_application.assert_called_once_with(
            Path("dist/OpenCore-Patcher.app")
        )

    def test_package_validation_expands_without_installing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package_path = Path(directory) / "OpenCore-Patcher.pkg"
            package_path.touch()
            contract = payload_contract.PayloadContract()

            def expand(command, **_kwargs):
                expanded_path = Path(command[-1])
                application_path = expanded_path / "root" / "OpenCore-Patcher.app"
                (application_path / "Contents" / "Resources").mkdir(parents=True)
                (application_path / "Contents" / "Resources" / "payloads.dmg").touch()
                return subprocess.CompletedProcess(command, 0, b"")

            with mock.patch.object(payload_contract.subprocess, "run", side_effect=expand), \
                 mock.patch.object(contract, "validate_application") as validate_application:
                contract.validate_package(package_path)

            validate_application.assert_called_once()

    def test_payload_validation_force_detaches_only_after_normal_detach_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "payloads.dmg"
            image.touch()
            contract = payload_contract.PayloadContract()
            completed = [
                subprocess.CompletedProcess([], 0, b"attached"),
                subprocess.CompletedProcess([], 1, b"busy"),
                subprocess.CompletedProcess([], 0, b"detached"),
            ]
            with mock.patch.object(contract, "validate_payload_root"), \
                 mock.patch.object(payload_contract.subprocess, "run", side_effect=completed) as run:
                contract.validate_payload_dmg(image)

        self.assertNotIn("-force", run.call_args_list[1].args[0])
        self.assertIn("-force", run.call_args_list[2].args[0])

    def test_package_generation_gates_source_app_and_generated_package(self) -> None:
        with mock.patch.object(package, "PayloadContract") as contract_type, \
             mock.patch.object(package.macos_pkg_builder, "Packages") as packages:
            packages.return_value.build.return_value = True
            package.GeneratePackage().generate()

        contract_type.return_value.validate_application.assert_called_once_with(
            Path("dist/OpenCore-Patcher.app")
        )
        contract_type.return_value.validate_package.assert_called_once_with(
            Path("dist/OpenCore-Patcher.pkg")
        )


if __name__ == "__main__":
    unittest.main()
