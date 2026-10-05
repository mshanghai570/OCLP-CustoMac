"""Stage, verify, and publish OpenCore while retaining a recovery copy on the ESP."""

import hashlib
import logging
import plistlib
import subprocess
import tempfile
import uuid

from pathlib import Path

from . import subprocess_wrapper, utilities


class EFIBootloaderTransaction:
    def __init__(self, source: Path, mount: Path, boot_efi: bool) -> None:
        self.source = Path(source)
        self.mount = Path(mount)
        self.boot_efi = boot_efi
        token = uuid.uuid4().hex
        self.stage = self.mount / f".OpenCore-Staging-{token}"
        self.backup = self.mount / f"OpenCore-Backup-{token}"
        self.paths = ["EFI/OC", "System", "boot.efi"] + (["EFI/BOOT"] if boot_efi else [])
        self.changed = []
        self.published = set()

    def _run(self, args) -> None:
        # The mounted FAT EFI belongs to the mounting user. Per-file escalation
        # creates separate prompts, including during rollback after cancellation.
        subprocess_wrapper.run_and_verify(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def _manifest(self, root: Path) -> dict:
        manifest = {}
        for relative in self.paths:
            path = root / relative
            if root.is_symlink() or path.is_symlink() or any((root / parent).is_symlink() for parent in Path(relative).parents if parent != Path(".")):
                raise ValueError(f"EFI transfer path is a symlink: {path}")
            if not path.exists():
                continue
            for entry in [path] + (list(path.rglob("*")) if path.is_dir() else []):
                key = str(entry.relative_to(root))
                if entry.is_symlink():
                    raise ValueError(f"EFI transfer entry is a symlink: {entry}")
                if entry.is_dir():
                    manifest[key] = {"Directory": True}
                elif entry.is_file():
                    digest = hashlib.sha256()
                    with entry.open("rb") as file:
                        for block in iter(lambda: file.read(1024 * 1024), b""):
                            digest.update(block)
                    manifest[key] = {"Size": entry.stat().st_size, "SHA256": digest.hexdigest()}
                else:
                    raise ValueError(f"Unsupported EFI transfer entry: {entry}")
        return manifest

    def _write_record(self, state: str, original: dict, expected: dict) -> None:
        payload = plistlib.dumps({"State": state, "Original": original, "Replacement": expected,
                                 "Managed Paths": self.paths}, sort_keys=True)
        with tempfile.NamedTemporaryFile(prefix="oclp-efi-journal-", suffix=".plist") as local:
            local.write(payload)
            local.flush()
            temporary = self.backup / ".Recovery.plist.tmp"
            self._run(["/bin/cp", local.name, str(temporary)])
            self._run(["/bin/mv", str(temporary), str(self.backup / "Recovery.plist")])
        self._run(["/bin/sync"])

    def _validate_config(self, config: dict) -> None:
        def required_path(prefix: str, relative: str, *, directory=False) -> Path:
            if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise ValueError("Unsafe or empty OpenCore configuration path")
            path = self.source / "EFI/OC" / prefix / relative
            valid = path.is_dir() if directory else path.is_file() and path.stat().st_size > 0
            if not valid:
                raise ValueError(f"OpenCore configuration references a missing file: {path}")
            return path

        for section, group, folder in (("ACPI", "Add", "ACPI"), ("UEFI", "Drivers", "Drivers"), ("Misc", "Tools", "Tools")):
            entries = config.get(section, {}).get(group, [])
            if not isinstance(entries, list):
                raise ValueError(f"Invalid OpenCore {section} configuration")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError(f"Invalid OpenCore {section} entry")
                if entry.get("Enabled", True):
                    required_path(folder, entry.get("Path"))
        entries = config.get("Kernel", {}).get("Add", [])
        if not isinstance(entries, list):
            raise ValueError("Invalid OpenCore Kernel configuration")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid OpenCore kext entry")
            if entry.get("Enabled", True):
                bundle = entry.get("BundlePath")
                required_path("Kexts", bundle, directory=True)
                plist_path = entry.get("PlistPath")
                if not isinstance(plist_path, str) or not plist_path:
                    raise ValueError("Enabled OpenCore kext has no Info.plist path")
                info_path = required_path("Kexts", f"{bundle}/{plist_path}")
                with info_path.open("rb") as file:
                    if not isinstance(plistlib.load(file), dict):
                        raise ValueError("OpenCore kext Info.plist must be a dictionary")
                if entry.get("ExecutablePath"):
                    required_path("Kexts", f"{bundle}/{entry['ExecutablePath']}")

    def _prepare(self) -> tuple[dict, dict]:
        if not self.mount.is_dir():
            raise ValueError("EFI mount is unavailable")
        for record in self.mount.glob("OpenCore-Backup-*/Recovery.plist"):
            with record.open("rb") as file:
                history = plistlib.load(file)
            if not isinstance(history, dict) or history.get("State") not in {"COMMITTED", "ROLLED_BACK"}:
                raise ValueError(f"An unfinished EFI installation requires recovery from {record.parent}")

        original = self._manifest(self.mount)
        expected = self._manifest(self.source)
        with (self.source / "EFI/OC/config.plist").open("rb") as config_file:
            config = plistlib.load(config_file)
        if not isinstance(config, dict):
            raise ValueError("OpenCore configuration must be a dictionary")
        self._validate_config(config)
        if expected.get("EFI/OC/OpenCore.efi", {}).get("Size", 0) == 0:
            raise ValueError("OpenCore executable is missing or empty")
        bootstrap = "EFI/BOOT/BOOTx64.efi" if self.boot_efi else "System/Library/CoreServices/boot.efi"
        convert = self.boot_efi and bootstrap not in expected
        if convert:
            bootstrap = "System/Library/CoreServices/boot.efi"
        if expected.get(bootstrap, {}).get("Size", 0) == 0:
            raise ValueError("OpenCore bootstrap is missing or empty")

        # Budget FAT allocation overhead and journal/backup directory entries.
        required = sum(((entry.get("Size", 0) + 65535) // 65536 or 1) * 65536 for entry in expected.values()) + 2 * 1024**2
        if utilities.get_free_space(str(self.mount)) < required:
            raise OSError("Insufficient free space to stage OpenCore on the EFI partition")

        self._run(["/bin/mkdir", str(self.stage)])
        self._run(["/bin/mkdir", "-p", str(self.stage / "EFI")])
        for relative in self.paths:
            if (self.source / relative).exists():
                self._run(["/bin/cp", "-R", str(self.source / relative), str(self.stage / relative)])
        if self._manifest(self.stage) != expected:
            raise ValueError("Staged OpenCore files do not match the source")
        if convert:
            self._run(["/bin/mkdir", "-p", str(self.stage / "EFI/BOOT")])
            self._run(["/bin/mv", str(self.stage / bootstrap), str(self.stage / "EFI/BOOT/BOOTx64.efi")])
            loader = expected[bootstrap]
            if "boot.efi" not in expected:
                self._run(["/bin/rm", "-rf", str(self.stage / "System")])
                expected = {key: value for key, value in expected.items() if key != "System" and not key.startswith("System/")}
            else:
                del expected[bootstrap]
            expected["EFI/BOOT"] = {"Directory": True}
            expected["EFI/BOOT/BOOTx64.efi"] = loader
        if self._manifest(self.stage) != expected:
            raise ValueError("Prepared bootstrap does not match the intended layout")
        self._run(["/bin/mkdir", str(self.backup)])
        self._run(["/bin/mkdir", "-p", str(self.backup / "EFI")])
        self._write_record("PREPARED", original, expected)
        return original, expected

    def install(self) -> bool:
        original = expected = None
        try:
            original, expected = self._prepare()
            self._run(["/bin/mkdir", "-p", str(self.mount / "EFI")])
            for relative in self.paths:
                self.changed.append(relative)
                active, previous, replacement = self.mount / relative, self.backup / relative, self.stage / relative
                if active.exists():
                    self._run(["/bin/mv", str(active), str(previous)])
                if replacement.exists():
                    self.published.add(relative)
                    self._run(["/bin/mv", str(replacement), str(active)])
            if self._manifest(self.mount) != expected or self._manifest(self.backup) != original:
                raise ValueError("Published EFI files or recovery backup failed verification")
            self._write_record("COMMITTED", original, expected)
            logging.info(f"OpenCore recovery backup retained at {self.backup}")
            return True
        except Exception as error:
            logging.error(f"OpenCore transfer failed: {error}")
            if self.changed:
                try:
                    for relative in reversed(self.changed):
                        active, previous = self.mount / relative, self.backup / relative
                        if relative in self.published and active.exists():
                            self._run(["/bin/rm", "-rf", str(active)])
                        if previous.exists():
                            self._run(["/bin/mv", str(previous), str(active)])
                    if self._manifest(self.mount) != original:
                        raise ValueError("Restored EFI files do not match their original contents")
                    self._write_record("ROLLED_BACK", original, expected)
                    logging.info("Previous boot files restored")
                except Exception as recovery_error:
                    logging.error(f"EFI rollback incomplete: {recovery_error}; recovery files remain at {self.backup}")
            return False
        finally:
            if self.stage.exists():
                try:
                    self._run(["/bin/rm", "-rf", str(self.stage)])
                except Exception as error:
                    logging.warning(f"Unable to remove EFI staging directory {self.stage}: {error}")
