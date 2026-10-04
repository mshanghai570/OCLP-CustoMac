"""Fail-closed coherence checks for the payloads used by application builds."""

import hashlib
import importlib
import pkgutil
import re
import stat
import subprocess
import tempfile
import zipfile

from collections.abc import Iterable, Iterator
from pathlib import Path

from opencore_legacy_patcher import constants
from opencore_legacy_patcher.support.disk_image import PROTECTED_DISK_IMAGE_PASSWORD
from opencore_legacy_patcher.sys_patch.patchsets.base import iter_patchset_sources
from opencore_legacy_patcher.sys_patch.patchsets.detect import HardwarePatchsetDetection
from opencore_legacy_patcher.sys_patch.patchsets.hardware.base import BaseHardware
from opencore_legacy_patcher.sys_patch.patchsets.shared_patches.base import BaseSharedPatchSet
from opencore_legacy_patcher.datasets.os_data import os_conversion, os_data


SHARED_PATCHES_PACKAGE: str = "opencore_legacy_patcher.sys_patch.patchsets.shared_patches"
# Derived, so this contract follows the datasets instead of restating the number.
TAHOE_MARKETING_VERSION: str = f"{os_conversion.kernel_to_os(os_data.tahoe)}.0"
TAHOE_BUILD: str = "25A5316i"


class PayloadContractError(RuntimeError):
    """The payload source or disk image is incompatible with current source."""


def shared_patchset_classes() -> list[type[BaseSharedPatchSet]]:
    """Return every ``BaseSharedPatchSet`` subclass in the shared-patches package.

    Discovered rather than listed, so a new shared patch module is covered by the
    payload checks as soon as it is added. These modules construct without any
    hardware, which is what lets the checks reach the dormant hardware families.
    """

    package = importlib.import_module(SHARED_PATCHES_PACKAGE)
    classes: set[type[BaseSharedPatchSet]] = set()
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name == "base":
            continue
        module = importlib.import_module(f"{SHARED_PATCHES_PACKAGE}.{module_info.name}")
        for value in vars(module).values():
            if not isinstance(value, type) or not issubclass(value, BaseSharedPatchSet):
                continue
            if value is BaseSharedPatchSet or value.__module__ != module.__name__:
                continue
            classes.add(value)
    return sorted(classes, key=lambda patchset: patchset.__name__)


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

    @classmethod
    def iter_root_patch_sources(cls, patches: dict) -> Iterator[tuple[str, str]]:
        """Yield ``(patchset, relative source path)`` for every bundled root patch.

        The payload-relative subset of ``iter_patchset_sources``, which is the one
        shared definition of what a patchset reads off disk; this used to walk the
        dictionaries itself and so could drift from the installer's own traversal.
        Sources taken from the booted root volume and runtime-computed
        ``DynamicPatchset`` entries are both excluded.
        """
        for patchset, relative_path, from_payload in iter_patchset_sources(patches):
            if from_payload:
                yield patchset, relative_path

    def validate_root_patch_sources(self, source_root: Path, patches: dict) -> None:
        """Check that each bundled root-patch source resolves inside the mounted PSP."""
        missing = {
            relative_path
            for _, relative_path in self.iter_root_patch_sources(patches)
            if not (source_root / relative_path).exists()
        }
        if missing:
            raise PayloadContractError(
                "PatcherSupportPkg is missing root-patch resources:\n- "
                + "\n- ".join(sorted(missing))
            )

    def probe_tahoe_patches(
        self,
        variants: Iterable[type[BaseHardware]],
        computer: object | None = None,
    ) -> tuple[dict[str, dict], dict[str, str]]:
        """Tahoe patch dictionaries per hardware family, plus families that cannot probe.

        Returns ``({family name: patches}, {variant name: failure})``. Families
        whose patch generation reads detected hardware raise unless ``computer``
        supplies it, and are reported rather than silently skipped.
        """
        self.constants.detected_os_version = TAHOE_MARKETING_VERSION
        if computer is not None:
            self.constants.computer = computer
        families: dict[str, dict] = {}
        unprobeable: dict[str, str] = {}
        for variant in variants:
            try:
                hardware = variant(
                    os_data.tahoe.value,
                    0,
                    TAHOE_BUILD,
                    self.constants,
                )
                if hardware.native_os():
                    continue
                patches = hardware.patches()
            except Exception as error:  # noqa: BLE001 - reported to the caller
                unprobeable[variant.__name__] = f"{type(error).__name__}: {error}"
                continue
            families[hardware.name()] = patches
        return families, unprobeable

    def probe_tahoe_shared_patches(
        self, marketing_version: str = TAHOE_MARKETING_VERSION
    ) -> tuple[dict[str, set[str]], dict[str, str]]:
        """Tahoe root-patch sources per shared patch module, plus modules that fail.

        Shared modules name the version directories that root patches copy from,
        and a dormant hardware family can use one that no probing family reaches,
        which is how a source naming an unpublished directory went unnoticed.
        """

        sources: dict[str, set[str]] = {}
        failures: dict[str, str] = {}
        for patchset in shared_patchset_classes():
            try:
                instance = patchset(os_data.tahoe.value, 0, marketing_version)
                patches: dict = {}
                for method_name in ("patches", "revert_patches"):
                    method = getattr(instance, method_name, None)
                    if not callable(method):
                        continue
                    try:
                        result = method()
                    except NotImplementedError:
                        continue
                    for name, operations in (result or {}).items():
                        patches.setdefault(name, {}).update(operations)
            except Exception as error:  # noqa: BLE001 - reported to the caller
                failures[patchset.__name__] = f"{type(error).__name__}: {error}"
                continue
            sources[patchset.__name__] = {
                relative_path for _, relative_path in self.iter_root_patch_sources(patches)
            }
        return sources, failures

    def validate_tahoe_root_patch_sources(self, source_root: Path) -> None:
        """Validate every Tahoe patch emitted by an enabled hardware family."""
        families, _ = self.probe_tahoe_patches(HardwarePatchsetDetection.hardware_variants())
        patches: dict = {}
        for patch_operations in families.values():
            for name, operations in patch_operations.items():
                if name in patches:
                    raise PayloadContractError(f"Duplicate Tahoe patch definition: {name}")
                patches[name] = operations
        self.validate_root_patch_sources(source_root, patches)

    def validate_universal_binaries_dmg(self, image_path: Path) -> None:
        """Mount the downloaded PSP read-only and verify its Tahoe patch sources."""
        if not image_path.is_file():
            raise PayloadContractError(f"PatcherSupportPkg disk image does not exist: {image_path}")

        actual_sha256 = self._sha256(image_path)
        if actual_sha256 != self.constants.patcher_support_pkg_sha256:
            raise PayloadContractError(
                f"PatcherSupportPkg disk image SHA-256 mismatch: {image_path} "
                f"(expected {self.constants.patcher_support_pkg_sha256}, got {actual_sha256})"
            )

        with tempfile.TemporaryDirectory(prefix="oclp-psp-contract-") as temporary_dir:
            mountpoint = Path(temporary_dir) / "Universal-Binaries"
            mountpoint.mkdir()
            try:
                attach = subprocess.run(
                    [
                        "/usr/bin/hdiutil", "attach", str(image_path.resolve()),
                        "-mountpoint", str(mountpoint),
                        "-nobrowse", "-readonly", "-noverify", "-stdinpass",
                    ],
                    input=PROTECTED_DISK_IMAGE_PASSWORD,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=600,
                )
            except subprocess.TimeoutExpired as error:
                raise PayloadContractError(
                    f"Timed out mounting PatcherSupportPkg disk image {image_path}"
                ) from error
            if attach.returncode != 0:
                raise PayloadContractError(
                    f"Unable to mount PatcherSupportPkg disk image {image_path}: "
                    f"{attach.stdout.decode('utf-8', errors='replace').strip()}"
                )

            validation_error: Exception | None = None
            try:
                self.validate_tahoe_root_patch_sources(mountpoint)
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
                    f"Unable to detach PatcherSupportPkg disk image {image_path}: "
                    f"{detach.stdout.decode('utf-8', errors='replace').strip()}"
                )
                if validation_error is not None:
                    raise detach_error from validation_error
                raise detach_error
            if validation_error is not None:
                raise validation_error

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
        self.validate_privilege_policy(application_path)
        self.validate_payload_dmg(
            application_path.resolve() / "Contents" / "Resources" / "payloads.dmg"
        )

    @staticmethod
    def validate_privilege_policy(root: Path) -> None:
        """Personal distributions must not contain a helper broker or setuid files."""
        for path in root.rglob("*"):
            if "PrivilegedHelperTools" in path.parts or path.name == "com.dortania.opencore-legacy-patcher.privileged-helper":
                raise PayloadContractError(f"Retired privileged helper included: {path}")
            if path.lstat().st_mode & (stat.S_ISUID | stat.S_ISGID):
                raise PayloadContractError(f"Setuid/setgid artifact is prohibited: {path}")
            if path.name == "postinstall" and path.is_file():
                script = path.read_text()
                if re.search(r"chmod[^\n]*(?:\+s|\b[2467][0-7]{3}\b)", script):
                    raise PayloadContractError(f"Package script enables setuid/setgid: {path}")

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

            self.validate_privilege_policy(expanded_path)
            applications = sorted(expanded_path.rglob("OpenCore-Patcher.app"))
            if len(applications) != 1:
                raise PayloadContractError(
                    f"Expected one packaged OpenCore-Patcher.app, found {len(applications)}"
                )
            self.validate_application(applications[0])
