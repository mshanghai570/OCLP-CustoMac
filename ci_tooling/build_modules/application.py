import sys
import os
import stat
import struct
import tempfile
import plistlib
import subprocess

from pathlib import Path

from opencore_legacy_patcher.volume  import generate_copy_arguments
from opencore_legacy_patcher.support import subprocess_wrapper
from ci_tooling.build_metadata import SourceBuildMetadata
from ci_tooling import build_environment

from .payload_contract import PayloadContract


class GenerateApplication:
    """
    Generate OpenCore-Patcher.app
    """

    def __init__(self, reset_pyinstaller_cache: bool = False, git_branch: str = None, git_commit_url: str = None, git_commit_date: str = None, analytics_key: str = None, analytics_endpoint: str = None) -> None:
        """
        Initialize
        """
        self._pyinstaller = [sys.executable, "-m", "PyInstaller"]
        self._application_output = Path("./dist/OpenCore-Patcher.app")

        self._reset_pyinstaller_cache = reset_pyinstaller_cache

        self._git_branch = git_branch
        self._git_commit_url = git_commit_url
        self._git_commit_date = git_commit_date
        self._source_metadata: SourceBuildMetadata | None = None

        self._analytics_key = analytics_key
        self._analytics_endpoint = analytics_endpoint
        self._analytics_original_source: bytes | None = None


    def _generate_application(self) -> None:
        """
        Generate PyInstaller Application
        """
        build_environment.verify_pyinstaller_runtime()
        if self._application_output.exists():
            subprocess_wrapper.run_and_verify(["/bin/rm", "-rf", self._application_output], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        print("Generating OpenCore-Patcher.app")
        _args = self._pyinstaller + ["./OpenCore-Patcher-GUI.spec", "--noconfirm"]
        if self._reset_pyinstaller_cache:
            _args.append("--clean")

        subprocess_wrapper.run_and_verify(_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        build_environment.verify_packaged_python_runtime(self._application_output)
        PayloadContract().validate_application(self._application_output)


    def _embed_analytics_key(self) -> None:
        """
        Embed analytics key
        """
        _file = Path("./opencore_legacy_patcher/support/analytics_handler.py")

        if not all([self._analytics_key, self._analytics_endpoint]):
            print("Analytics key or endpoint not provided, skipping embedding")
            return

        print("Embedding analytics data")
        if not Path(_file).exists():
            raise FileNotFoundError("analytics_handler.py not found")

        self._analytics_original_source = _file.read_bytes()
        lines = []
        with open(_file, "r") as f:
            lines = f.readlines()

        for i, line in enumerate(lines):
            if line.startswith("SITE_KEY:         str = "):
                lines[i] = f"SITE_KEY:         str = {self._analytics_key!r}\n"
            elif line.startswith("ANALYTICS_SERVER: str = "):
                lines[i] = f"ANALYTICS_SERVER: str = {self._analytics_endpoint!r}\n"

        with open(_file, "w") as f:
            f.writelines(lines)


    def _remove_analytics_key(self) -> None:
        """
        Remove analytics key
        """
        _file = Path("./opencore_legacy_patcher/support/analytics_handler.py")

        if not all([self._analytics_key, self._analytics_endpoint]):
            return

        if self._analytics_original_source is not None:
            print("Restoring original analytics source")
            _file.write_bytes(self._analytics_original_source)
            self._analytics_original_source = None


    @staticmethod
    def _macho_minimum_versions(data: bytes) -> list[int]:
        """Read deployment targets without modifying executable bytes.

        Validate each thin/fat slice and every load-command boundary before
        accepting metadata. Unknown platforms and absent targets fail closed.
        """
        thin_magic = {b"\xce\xfa\xed\xfe": ("<", 28), b"\xcf\xfa\xed\xfe": ("<", 32),
                      b"\xfe\xed\xfa\xce": (">", 28), b"\xfe\xed\xfa\xcf": (">", 32)}
        fat_magic = {b"\xca\xfe\xba\xbe": (">", 20), b"\xbe\xba\xfe\xca": ("<", 20),
                     b"\xca\xfe\xba\xbf": (">", 32), b"\xbf\xba\xfe\xca": ("<", 32)}

        def thin(start: int, size: int) -> list[int]:
            end = start + size
            magic = data[start:start + 4]
            if magic not in thin_magic:
                raise ValueError("Invalid Mach-O slice magic")
            endian, header_size = thin_magic[magic]
            if size < header_size:
                raise ValueError("Truncated Mach-O header")
            ncmds, sizeofcmds = struct.unpack_from(endian + "II", data, start + 16)
            cursor = start + header_size
            command_end = cursor + sizeofcmds
            if command_end > end or ncmds > sizeofcmds // 8:
                raise ValueError("Mach-O commands exceed slice")
            versions = []
            for _ in range(ncmds):
                if cursor + 8 > command_end:
                    raise ValueError("Truncated Mach-O load command")
                command, length = struct.unpack_from(endian + "II", data, cursor)
                if length < 8 or length % 4 or cursor + length > command_end:
                    raise ValueError("Invalid Mach-O load-command size")
                if command == 0x24:  # LC_VERSION_MIN_MACOSX
                    if length != 16:
                        raise ValueError("Invalid LC_VERSION_MIN_MACOSX")
                    versions.append(struct.unpack_from(endian + "I", data, cursor + 8)[0])
                elif command == 0x32:  # LC_BUILD_VERSION
                    if length < 24:
                        raise ValueError("Truncated LC_BUILD_VERSION")
                    platform, minimum, sdk, tools = struct.unpack_from(endian + "IIII", data, cursor + 8)
                    if platform != 1 or length != 24 + 8 * tools:
                        raise ValueError("Unsupported Mach-O build platform or tools")
                    versions.append(minimum)
                cursor += length
            if cursor != command_end or len(versions) != 1 or not any(versions):
                raise ValueError("Missing or ambiguous macOS deployment target")
            return versions

        magic = data[:4]
        if magic in thin_magic:
            return thin(0, len(data))
        if magic not in fat_magic or len(data) < 8:
            raise ValueError("Invalid Mach-O file")
        endian, entry_size = fat_magic[magic]
        count = struct.unpack_from(endian + "I", data, 4)[0]
        table_end = 8 + count * entry_size
        if not count or table_end > len(data):
            raise ValueError("Invalid Mach-O fat architecture table")
        intervals = []
        versions = []
        for index in range(count):
            cursor = 8 + index * entry_size
            offset, size = struct.unpack_from(endian + ("QQ" if entry_size == 32 else "II"), data, cursor + 8)
            if not size or offset < table_end or offset + size > len(data):
                raise ValueError("Mach-O fat slice exceeds file")
            if any(offset < stop and offset + size > start for start, stop in intervals):
                raise ValueError("Overlapping Mach-O fat slices")
            intervals.append((offset, offset + size))
            versions.extend(thin(offset, size))
        return versions


    def _update_runtime_minimum(self) -> None:
        """Set the bundle floor from actual packaged native dependencies.

        A newer Python/wx build does not acquire older OS support by lowering
        its bootloader header. Keep all Mach-O targets and SDK fields intact.
        """
        contents = self._application_output / "Contents"
        executable = contents / "MacOS/OpenCore-Patcher"
        versions = self._macho_minimum_versions(executable.read_bytes())
        magic_numbers = {b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
                         b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
        for path in contents.rglob("*"):
            if not path.is_file() or path == executable:
                continue
            with path.open("rb") as source:
                if source.read(4) not in magic_numbers:
                    continue
                source.seek(0)
                versions.extend(self._macho_minimum_versions(source.read()))
        minimum = max(versions)
        plist_path = contents / "Info.plist"
        info = plistlib.loads(plist_path.read_bytes())
        existing = tuple(int(part) for part in info.get("LSMinimumSystemVersion", "0.0.0").split("."))
        actual = (minimum >> 16, (minimum >> 8) & 0xff, minimum & 0xff)
        floor = max(existing + (0,) * (3 - len(existing)), actual)
        info["LSMinimumSystemVersion"] = ".".join(str(part) for part in floor)
        mode = stat.S_IMODE(plist_path.stat().st_mode)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=plist_path.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(plistlib.dumps(info, sort_keys=True))
                temporary.flush()
                os.fsync(temporary.fileno())
                os.fchmod(temporary.fileno(), mode)
            os.replace(temporary_path, plist_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        print(f"Application runtime requires macOS {info['LSMinimumSystemVersion']} or newer")


    def _embed_git_data(self) -> None:
        """
        Embed git data
        """
        _file = self._application_output / "Contents" / "Info.plist"

        if self._source_metadata is None:
            raise RuntimeError("Source build metadata was not validated")

        print("Embedding git data")
        with _file.open("rb") as plist_file:
            _plist = plistlib.load(plist_file)
        _plist["Github"] = {
            "Branch": self._source_metadata.ref,
            "Commit SHA": self._source_metadata.commit_sha,
            "Commit URL": self._source_metadata.commit_url,
            "Commit Date": self._source_metadata.commit_date,
            "Repository": self._source_metadata.repository_url,
            "Project": "OCLP 3.0.0 Nightly - amfipassbeta Edition v2.0",
            "Version": _plist["CFBundleShortVersionString"],
        }
        with _file.open("wb") as plist_file:
            plistlib.dump(_plist, plist_file, sort_keys=True)


    def _embed_resources(self) -> None:
        """
        Embed resources
        """
        print("Embedding resources")
        for file in Path("payloads/Icon/AppIcons").glob("*.icns"):
            subprocess_wrapper.run_and_verify(
                generate_copy_arguments(str(file), self._application_output / "Contents" / "Resources/"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

        subprocess_wrapper.run_and_verify(
            generate_copy_arguments("payloads/Icon/AppIcons/Assets.car", self._application_output / "Contents/Resources/"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )


    def _refresh_ad_hoc_signature(self) -> None:
        """Seal post-PyInstaller mutations for unsigned local builds.

        PyInstaller ad-hoc signs the bundle before this module updates its
        Info.plist and resources. Refresh only the outer, timestamp-free
        ad-hoc seal. Configured release signing subsequently replaces it.
        """
        print("Refreshing local outer ad-hoc application signature")
        subprocess_wrapper.run_and_verify(
            [
                "/usr/bin/codesign", "--force", "--sign", "-",
                "--timestamp=none", self._application_output,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )


    def generate(self) -> None:
        """
        Generate OpenCore-Patcher.app
        """
        self._source_metadata = SourceBuildMetadata.from_repository(
            Path.cwd(),
            ref=self._git_branch,
            commit_url=self._git_commit_url,
            commit_date=self._git_commit_date,
        )
        try:
            self._embed_analytics_key()
            self._generate_application()
        finally:
            self._remove_analytics_key()

        self._update_runtime_minimum()
        self._embed_git_data()
        self._embed_resources()
        self._refresh_ad_hoc_signature()
