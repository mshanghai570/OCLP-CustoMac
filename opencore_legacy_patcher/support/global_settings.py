"""Private settings for the effective account, including root automation.

GUI preferences belong to the user running the GUI. Elevated automation uses
root's own preferences and never selects another user's home through HOME or
SUDO_USER. Legacy preferences are imported once, read-only, when trustworthy;
world-writable /Users/Shared preferences are deliberately not authoritative.
"""

import fcntl
import logging
import os
import plistlib
import pwd
import stat
import uuid

from contextlib import contextmanager
from pathlib import Path
from xml.parsers.expat import ExpatError


class GlobalEnviromentSettings:
    """Query and atomically update preferences owned by the effective account."""

    def __init__(self, settings_path: str | Path | None = None,
                 legacy_paths: tuple[str | Path, ...] | None = None) -> None:
        self.file_name = ".com.dortania.opencore-legacy-patcher.plist"
        # passwd, rather than environment variables, defines the trust boundary.
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve()
        path = Path(settings_path) if settings_path is not None else (
            home / "Library/Application Support/OpenCore Legacy Patcher" / self.file_name
        )
        self.global_settings_folder = str(path.parent)
        self.global_settings_plist = str(path)
        self._legacy_paths = tuple(Path(item) for item in legacy_paths) if legacy_paths is not None else (
            Path("/Users/Shared") / self.file_name,
            home / "Library/Preferences/com.dortania.opencore-legacy-patcher.plist",
        )
        try:
            with self._locked_directory() as directory:
                try:
                    self._read_plist(path.name, directory=directory)
                except FileNotFoundError:
                    initial = {"Developed by Dortania": True}
                    initial.update(self._legacy_settings())
                    self._write_plist(initial, path.name, directory=directory)
        except (OSError, ValueError, TypeError) as error:
            logging.info("Unable to initialize private settings: %s", error)

    @staticmethod
    def _validate_directory(fd: int, *, private: bool = False) -> None:
        metadata = os.fstat(fd)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in (0, os.geteuid()):
            raise PermissionError("Untrusted settings directory owner")
        writable = metadata.st_mode & 0o022
        # Root-owned sticky ancestors (e.g. /tmp) permit safe private children.
        sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
        if writable and not sticky_root:
            raise PermissionError("Settings directory is writable by another account")
        if private and (metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077):
            raise PermissionError("Settings directory must be private to the effective account")

    @classmethod
    @contextmanager
    def _directory(cls, path: Path, *, create: bool = False, private: bool = False):
        """Walk via descriptors so parent symlinks cannot redirect operations."""
        path = Path(os.path.abspath(path))
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            cls._validate_directory(fd)
            parts = path.parts[1:]
            for index, part in enumerate(parts):
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
                cls._validate_directory(fd, private=private and index == len(parts) - 1)
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _validate_file(fd: int, *, legacy: bool = False) -> None:
        metadata = os.fstat(fd)
        owners = (0, os.geteuid()) if legacy else (os.geteuid(),)
        unsafe_mode = 0o022 if legacy else 0o077
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid not in owners
                or metadata.st_nlink != 1 or metadata.st_mode & unsafe_mode):
            raise PermissionError("Untrusted settings file ownership, mode, or links")

    @contextmanager
    def _locked_directory(self):
        path = Path(self.global_settings_plist)
        with self._directory(path.parent, create=True, private=True) as directory:
            fd = os.open(path.name + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
            try:
                self._validate_file(fd)
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield directory
            finally:
                os.close(fd)

    @classmethod
    def _read_plist(cls, path: str | Path, *, directory: int | None = None,
                    legacy: bool = False) -> dict:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as plist_file:
            cls._validate_file(plist_file.fileno(), legacy=legacy)
            try:
                result = plistlib.load(plist_file)
            except ExpatError as error:
                raise ValueError("Malformed settings plist") from error
        if not isinstance(result, dict):
            raise ValueError("Settings plist must contain a dictionary")
        return result

    @classmethod
    def _write_plist(cls, plist: dict, path: str | Path, *, directory: int) -> None:
        name = str(path)
        temporary = f".{name}.{uuid.uuid4().hex}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as plist_file:
                plistlib.dump(plist, plist_file)
                plist_file.flush()
                os.fsync(plist_file.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def _legacy_settings(self) -> dict:
        result = {}
        for path in self._legacy_paths:
            try:
                with self._directory(path.parent) as directory:
                    result.update(self._read_plist(path.name, directory=directory, legacy=True))
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError) as error:
                logging.info("Skipping untrusted or unreadable legacy settings %s: %s", path, error)
        return result

    def read_settings(self) -> dict:
        """Return a trusted snapshot for restoring GUI defaults."""
        try:
            with self._locked_directory() as directory:
                return self._read_plist(Path(self.global_settings_plist).name,
                                        directory=directory)
        except (OSError, ValueError, TypeError) as error:
            logging.info("Unable to read private settings: %s", error)
            return {}

    def read_property(self, property_name: str):
        """Read a preference, returning None when unavailable or untrusted."""
        return self.read_settings().get(property_name)

    def _update(self, change) -> None:
        try:
            with self._locked_directory() as directory:
                name = Path(self.global_settings_plist).name
                current = self._read_plist(name, directory=directory)
                change(current)
                self._write_plist(current, name, directory=directory)
        except (OSError, ValueError, TypeError, OverflowError) as error:
            logging.info("Unable to update private settings: %s", error)

    def write_property(self, property_name: str, property_value) -> None:
        """Serialize a complete read/modify/replace transaction under flock."""
        self._update(lambda current: current.update({property_name: property_value}))

    def delete_property(self, property_name: str) -> None:
        self._update(lambda current: current.pop(property_name, None))

    def _convert_defaults_to_global_settings(self) -> None:
        """Explicitly import trustworthy legacy values without deleting sources."""
        self._update(lambda current: current.update(self._legacy_settings()))
