"""
distribution_dmg.py: Generate the distributable disk image (OCLP-CustoMac.dmg)

The application cannot be delivered as a drag-to-Applications bundle: the
installer payload lives at /Library/Application Support/Dortania and the
package postinstall script publishes a symlink into /Applications, so the
unit that ships is the installer package. This module wraps the packages
built by ``package.GeneratePackage`` into one read-only disk image and
verifies the image by mounting it and comparing SHA-256 digests against the
sources, so a DMG that lost or substituted a package fails the build instead
of being published.
"""

import hashlib
import shutil
import subprocess
import tempfile

from pathlib import Path

from opencore_legacy_patcher import constants
from opencore_legacy_patcher.support import subprocess_wrapper


DISTRIBUTION_PACKAGES = (
    "OpenCore-Patcher.pkg",
    "OpenCore-Patcher-Uninstaller.pkg",
)

README_NAME = "README.txt"


class DistributionDMGError(RuntimeError):
    """The distribution disk image is missing, incomplete, or not reproducible."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class GenerateDistributionDMG:
    """Bundle the installer packages into a single distributable disk image."""

    def __init__(self, package_root: Path = Path("./dist"), output: Path = None) -> None:
        self._package_root = package_root
        self._constants = constants.Constants()
        self._output = output or Path("./dist") / f"{self._constants.patcher_name}-{self._constants.patcher_version}.dmg"
        self._volume_name = f"{self._constants.patcher_name} {self._constants.patcher_version}"

    @property
    def output_path(self) -> Path:
        """Path the generated disk image is written to."""
        return self._output

    def _source_packages(self) -> list[Path]:
        """Return the installer packages to ship, failing closed when one is absent."""
        packages = []
        for name in DISTRIBUTION_PACKAGES:
            path = self._package_root / name
            if not path.is_file():
                raise DistributionDMGError(f"Installer package does not exist: {path}")
            packages.append(path)
        return packages

    def _readme(self) -> str:
        """Describe the image contents for someone who mounts it without context."""
        lines = [
            f"{self._constants.patcher_name} {self._constants.patcher_version}",
            "",
            "This disk image contains the installer packages. It does not contain",
            "the application itself: the installer places the application in",
            "'/Library/Application Support/Dortania' and adds a shortcut to",
            "'/Applications'.",
            "",
            "Install:",
            "  1. Open 'OpenCore-Patcher.pkg' and follow the prompts.",
            "  2. Launch OpenCore-Patcher from '/Applications'.",
            "",
            "Remove an existing installation:",
            "  Open 'OpenCore-Patcher-Uninstaller.pkg'. This removes the",
            "  application and its privileged helper; it does not revert root",
            "  patches or an installed OpenCore configuration.",
            "",
            "Documentation: " + self._constants.guide_link,
            "Source: " + self._constants.repo_link,
            "",
        ]
        return "\n".join(lines)

    def _stage(self, staging_root: Path) -> dict[str, Path]:
        """Copy the packages and readme into a staging tree, returning staged paths.

        Packages are hardlinked when possible so staging a multi-gigabyte
        installer does not need a second copy of it on the volume.
        """
        staged = {}
        for source in self._source_packages():
            destination = staging_root / source.name
            try:
                destination.hardlink_to(source)
            except OSError:
                shutil.copy2(source, destination)
            staged[source.name] = destination

        readme = staging_root / README_NAME
        readme.write_text(self._readme(), encoding="utf-8")
        staged[README_NAME] = readme
        return staged

    def _expected_contents(self, staged: dict[str, Path]) -> dict[str, str]:
        """Digest every staged file, keyed by the name it must keep inside the image."""
        return {name: _sha256(path) for name, path in staged.items()}

    def _generate(self, staging_root: Path) -> None:
        if self._output.exists():
            print(f"- Removing old {self._output.name}")
            subprocess_wrapper.run_and_verify(
                ["/bin/rm", "-rf", str(self._output)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

        print(f"Generating {self._output.name}")
        subprocess_wrapper.run_and_verify([
            "/usr/bin/hdiutil", "create", str(self._output),
            "-format", "UDZO",
            "-ov",
            "-volname", self._volume_name,
            "-fs", "HFS+",
            "-layout", "NONE",
            "-srcfolder", str(staging_root),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _mount(self, mountpoint: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "/usr/bin/hdiutil", "attach", str(self._output.resolve()),
                "-mountpoint", str(mountpoint),
                "-nobrowse", "-readonly",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def _detach(self, mountpoint: Path) -> subprocess.CompletedProcess:
        detach = subprocess.run(
            ["/usr/bin/hdiutil", "detach", str(mountpoint)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if detach.returncode != 0:
            detach = subprocess.run(
                ["/usr/bin/hdiutil", "detach", str(mountpoint), "-force"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
        return detach

    def validate(self, expected: dict[str, str]) -> None:
        """Mount the image read-only and confirm it carries the staged bytes exactly."""
        if not self._output.is_file():
            raise DistributionDMGError(f"Distribution disk image does not exist: {self._output}")

        with tempfile.TemporaryDirectory(prefix="oclp-distribution-dmg-") as temporary_dir:
            mountpoint = Path(temporary_dir) / "image"
            mountpoint.mkdir()
            attach = self._mount(mountpoint)
            if attach.returncode != 0:
                raise DistributionDMGError(
                    f"Unable to mount distribution disk image {self._output}: "
                    f"{attach.stdout.decode('utf-8', errors='replace').strip()}"
                )

            validation_error: Exception | None = None
            try:
                mounted = {path.name for path in mountpoint.iterdir()}
                if mounted != set(expected):
                    missing = sorted(set(expected) - mounted)
                    unexpected = sorted(mounted - set(expected))
                    raise DistributionDMGError(
                        "Distribution disk image contents differ from the staged "
                        f"packages; missing={missing} unexpected={unexpected}"
                    )
                for name, digest in expected.items():
                    actual = _sha256(mountpoint / name)
                    if actual != digest:
                        raise DistributionDMGError(
                            f"Distribution disk image member differs from source: {name} "
                            f"({actual} != {digest})"
                        )
                print(f"{self._output.name} carries {len(expected)} verified files")
            except Exception as error:
                validation_error = error

            detach = self._detach(mountpoint)
            if detach.returncode != 0:
                detach_error = DistributionDMGError(
                    f"Unable to detach distribution disk image {self._output}: "
                    f"{detach.stdout.decode('utf-8', errors='replace').strip()}"
                )
                if validation_error is not None:
                    raise detach_error from validation_error
                raise detach_error
            if validation_error is not None:
                raise validation_error

    def generate(self) -> None:
        """Generate the distribution disk image and validate it before returning."""
        with tempfile.TemporaryDirectory(prefix="oclp-distribution-stage-") as temporary_dir:
            staged = self._stage(Path(temporary_dir))
            expected = self._expected_contents(staged)
            self._generate(Path(temporary_dir))

        self.validate(expected)
        print(f"Distribution disk image ready: {self._output}")