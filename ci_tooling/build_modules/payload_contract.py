"""Fail-closed coherence checks for the payloads used by application builds."""

import hashlib
import re
import subprocess
import tempfile
import zipfile

from pathlib import Path

from opencore_legacy_patcher import constants
from opencore_legacy_patcher.support.disk_image import PROTECTED_DISK_IMAGE_PASSWORD


class PayloadContractError(RuntimeError):
    """The payload source or disk image is incompatible with current source."""


class PayloadContract:
    """Validate the EFI-builder payload contract against current ``Constants``."""

    _RELEASE_COMPONENT_PROPERTIES = (
        ("OpenCorePkg", "opencore_zip_source"),
        ("Lilu", "lilu_path"),
        ("WhateverGreen", "whatevergreen_path"),
        ("RestrictEvents", "restrictevents_path"),
        ("AirportBrcmFixup", "airportbcrmfixup_path"),
        ("BlueToolFixup", "bluetool_path"),
        ("NVMeFix", "nvmefix_path"),
        ("CPUFriend", "cpufriend_path"),
        ("CryptexFixup", "cryptexfixup_path"),
        ("DebugEnhancer", "debugenhancer_path"),
        ("AppleALC", "applealc_path"),
        ("FeatureUnlock", "featureunlock_path"),
        ("AMFIPass", "amfipass_path"),
    )
    _OCVALIDATE_VERSION_PATTERN = re.compile(
        r"compatible with OpenCore version ([0-9]+(?:\.[0-9]+)+)!"
    )

    def __init__(self, source_payload_root: Path = Path("payloads")) -> None:
        self.source_payload_root = source_payload_root.resolve()
        self.constants = constants.Constants()
        self.constants.opencore_debug = False
        self.constants.kext_variant = "RELEASE"

    def expected_release_components(self, payload_root: Path) -> dict[str, Path]:
        """Return required RELEASE paths, with versions derived from ``Constants``."""
        payload_root = payload_root.resolve()
        self.constants.payload_path = payload_root
        return {
            component: getattr(self.constants, property_name)
            for component, property_name in self._RELEASE_COMPONENT_PROPERTIES
        }

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file_handle:
            for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _validate_zip(path: Path) -> str | None:
        try:
            with zipfile.ZipFile(path) as archive:
                bad_member = archive.testzip()
        except (OSError, zipfile.BadZipFile) as error:
            return f"Invalid component archive {path}: {error}"
        if bad_member is not None:
            return f"Invalid component archive {path}: corrupt member {bad_member}"
        return None

    def _reported_opencore_version(self, ocvalidate_path: Path) -> str:
        try:
            result = subprocess.run(
                [str(ocvalidate_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
        except OSError as error:
            raise PayloadContractError(
                f"Unable to execute contained ocvalidate at {ocvalidate_path}: {error}"
            ) from error

        output = result.stdout.decode("utf-8", errors="replace")
        match = self._OCVALIDATE_VERSION_PATTERN.search(output)
        if match is None:
            raise PayloadContractError(
                f"Unable to determine actual OpenCore identity from {ocvalidate_path}: {output.strip()}"
            )
        return match.group(1)

    def validate_payload_root(self, payload_root: Path) -> None:
        """Validate a source or mounted payload directory and fail on any mismatch."""
        payload_root = payload_root.resolve()
        if not payload_root.is_dir():
            raise PayloadContractError(f"Payload root does not exist: {payload_root}")
        if not self.source_payload_root.is_dir():
            raise PayloadContractError(
                f"Tracked payload baseline does not exist: {self.source_payload_root}"
            )

        errors: list[str] = []
        expected = self.expected_release_components(payload_root)
        reference = self.expected_release_components(self.source_payload_root)

        for component, path in expected.items():
            relative_path = path.relative_to(payload_root)
            if not path.is_file():
                errors.append(f"Missing required current component: {relative_path}")
                continue

            archive_error = self._validate_zip(path)
            if archive_error is not None:
                errors.append(archive_error)

            reference_path = reference[component]
            if not reference_path.is_file():
                errors.append(f"Tracked payload baseline is missing: {relative_path}")
            elif path != reference_path and self._sha256(path) != self._sha256(reference_path):
                errors.append(
                    f"Component differs from tracked payload baseline: {relative_path}"
                )

        ocvalidate_path = payload_root / "OpenCore" / "ocvalidate"
        if not ocvalidate_path.is_file():
            errors.append("Missing required OpenCore identity tool: OpenCore/ocvalidate")
        else:
            try:
                reported_version = self._reported_opencore_version(ocvalidate_path)
            except PayloadContractError as error:
                errors.append(str(error))
            else:
                if reported_version != self.constants.opencore_version:
                    errors.append(
                        "OpenCore identity mismatch: contained ocvalidate reports "
                        f"{reported_version}, Constants requires {self.constants.opencore_version}"
                    )

            reference_ocvalidate = self.source_payload_root / "OpenCore" / "ocvalidate"
            if not reference_ocvalidate.is_file():
                errors.append("Tracked payload baseline is missing: OpenCore/ocvalidate")
            elif (
                ocvalidate_path != reference_ocvalidate
                and self._sha256(ocvalidate_path) != self._sha256(reference_ocvalidate)
            ):
                errors.append(
                    "OpenCore identity tool differs from tracked payload baseline: OpenCore/ocvalidate"
                )

        if errors:
            raise PayloadContractError(
                f"Payload contract validation failed for {payload_root}:\n- "
                + "\n- ".join(errors)
            )

    def validate_payload_dmg(self, image_path: Path) -> None:
        """Mount an encrypted payload image read-only, validate it, and detach it."""
        image_path = image_path.resolve()
        if not image_path.is_file():
            raise PayloadContractError(f"Payload disk image does not exist: {image_path}")

        with tempfile.TemporaryDirectory(prefix="oclp-payload-contract-") as temporary_dir:
            mountpoint = Path(temporary_dir) / "payloads"
            mountpoint.mkdir()
            attach = subprocess.run(
                [
                    "/usr/bin/hdiutil", "attach", str(image_path),
                    "-mountpoint", str(mountpoint),
                    "-nobrowse", "-readonly", "-stdinpass",
                ],
                input=PROTECTED_DISK_IMAGE_PASSWORD,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if attach.returncode != 0:
                raise PayloadContractError(
                    f"Unable to mount payload disk image {image_path}: "
                    f"{attach.stdout.decode('utf-8', errors='replace').strip()}"
                )

            validation_error: Exception | None = None
            try:
                self.validate_payload_root(mountpoint)
            except Exception as error:
                validation_error = error

            detach_command = ["/usr/bin/hdiutil", "detach", str(mountpoint)]
            detach = subprocess.run(
                detach_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if detach.returncode != 0:
                detach = subprocess.run(
                    detach_command + ["-force"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            if detach.returncode != 0:
                detach_error = PayloadContractError(
                    f"Unable to detach payload disk image {image_path}: "
                    f"{detach.stdout.decode('utf-8', errors='replace').strip()}"
                )
                if validation_error is not None:
                    raise detach_error from validation_error
                raise detach_error
            if validation_error is not None:
                raise validation_error

    def validate_application(self, application_path: Path) -> None:
        """Validate the exact payload image embedded in an application bundle."""
        self.validate_payload_dmg(
            application_path.resolve() / "Contents" / "Resources" / "payloads.dmg"
        )

    def validate_package(self, package_path: Path) -> None:
        """Expand a package without installing it and validate its embedded application."""
        package_path = package_path.resolve()
        if not package_path.is_file():
            raise PayloadContractError(f"Package does not exist: {package_path}")

        with tempfile.TemporaryDirectory(prefix="oclp-package-contract-") as temporary_dir:
            expanded_path = Path(temporary_dir) / "expanded"
            expand = subprocess.run(
                ["/usr/sbin/pkgutil", "--expand-full", str(package_path), str(expanded_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if expand.returncode != 0:
                raise PayloadContractError(
                    f"Unable to expand package {package_path}: "
                    f"{expand.stdout.decode('utf-8', errors='replace').strip()}"
                )

            applications = sorted(expanded_path.rglob("OpenCore-Patcher.app"))
            if len(applications) != 1:
                raise PayloadContractError(
                    f"Expected one packaged OpenCore-Patcher.app, found {len(applications)}"
                )
            self.validate_application(applications[0])
