"""
generate_tahoe_metallib_pins.py: Regenerate the pinned macOS Tahoe metallib digests

The runtime resolver in `opencore_legacy_patcher/support/metallib_handler.py`
refuses any Tahoe (Darwin 25) metallib package whose SHA-256 is not listed in
`TAHOE_METALLIB_PINNED_SHA256`. That is deliberate: the package is downloaded
from the network and installed with root privileges, so its exact bytes must be
known before the download is trusted.

The consequence is that every new upstream build is unusable until its digest is
added. This tool is the intended way to do that, and it is the same code path
used to verify the table has not drifted.

Usage:
    python ci_tooling/generate_tahoe_metallib_pins.py --stdout
    python ci_tooling/generate_tahoe_metallib_pins.py
    python ci_tooling/generate_tahoe_metallib_pins.py --check

Run it from the project virtual environment, which provides `requests` and the
PyObjC modules the patcher package imports at load time. It is a manual
maintenance tool; it is not wired into CI because a new upstream build should
never fail an unrelated build or test run.
"""

import argparse
import ast
import re
import sys

from pathlib import Path

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from opencore_legacy_patcher.datasets.os_data import os_data
from opencore_legacy_patcher.support.metallib_handler import _darwin_build_sort_key


TARGET_FILE: Path = REPO_ROOT / "opencore_legacy_patcher/support/metallib_handler.py"
ASSIGNMENT_NAME: str = "TAHOE_METALLIB_PINNED_SHA256"
ASSIGNMENT_HEADER: str = f"{ASSIGNMENT_NAME}: dict[str, str] = {{"
RELEASES_API: str = "https://api.github.com/repos/pyquick/MetallibSupportPkg/releases"
DARWIN_MAJOR: int = int(os_data.tahoe)
BUILD_PATTERN = re.compile(rf"{DARWIN_MAJOR}[A-Za-z]\d+[A-Za-z]?")


def fetch_pins(session=None, per_page: int = 100, timeout: int = 30) -> dict[str, tuple[str, str]]:
    """
    Return `{build: (sha256, version)}` for every published Darwin 25 package

    The GitHub Releases API is the only consulted source. The static manifests
    used for Sequoia either carry no Darwin 25 entry or point at a different
    publisher shipping byte-different packages under identical version tags.
    """

    request = session if session is not None else requests

    pins: dict[str, tuple[str, str]] = {}
    page = 1
    while True:
        response = request.get(
            RELEASES_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "OCLP-metallib-pin-generator",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params={"per_page": per_page, "page": page},
            timeout=timeout,
        )
        try:
            response.raise_for_status()
            releases = response.json()
            if not releases:
                break

            for release in releases:
                tag = release.get("tag_name", "")
                if "-" not in tag:
                    continue
                version, build = tag.split("-", 1)
                if BUILD_PATTERN.fullmatch(build) is None:
                    continue

                for asset in release.get("assets", []):
                    if not asset.get("name", "").endswith(".pkg"):
                        continue
                    digest = (asset.get("digest") or "").removeprefix("sha256:")
                    if len(digest) != 64:
                        continue
                    pins[build] = (digest, version)

            if len(releases) < per_page:
                break
            page += 1
        finally:
            response.close()

    return pins


def render_pin_lines(pins: dict[str, tuple[str, str]]) -> list[str]:
    """Render the dictionary body in Darwin build order, one pinned build per line"""

    builds = sorted(pins, key=_darwin_build_sort_key)
    key_width = max(len(f'"{build}":') for build in builds)
    digest_width = max(len(f'"{pins[build][0]}",') for build in builds)

    lines = []
    for build in builds:
        digest, version = pins[build]
        key = f'"{build}":'
        value = f'"{digest}",'
        lines.append(f"    {key:<{key_width}} {value:<{digest_width}}  # {version}")
    return lines


def extract_pinned_digests(source: str) -> dict[str, str]:
    """Read the digest table out of `source` without importing the module"""

    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id != ASSIGNMENT_NAME:
            continue
        return dict(ast.literal_eval(node.value))

    raise LookupError(f"No module-level {ASSIGNMENT_NAME} assignment found")


def replace_pin_block(source: str, pins: dict[str, tuple[str, str]]) -> str:
    """Return `source` with the pinned digest block replaced by `pins`"""

    lines = source.splitlines(keepends=True)

    start = None
    for index, line in enumerate(lines):
        if line.strip() == ASSIGNMENT_HEADER:
            start = index
            break
    if start is None:
        raise LookupError(f"Could not find the {ASSIGNMENT_NAME} declaration")

    end = None
    for index in range(start + 1, len(lines)):
        if lines[index].rstrip("\n") == "}":
            end = index
            break
    if end is None:
        raise LookupError(f"Could not find the end of the {ASSIGNMENT_NAME} block")

    body = "".join(f"{line}\n" for line in render_pin_lines(pins))
    return "".join(lines[:start + 1]) + body + "".join(lines[end:])


def describe_changes(current: dict[str, str], pins: dict[str, tuple[str, str]]) -> list[str]:
    """Summarise how `current` differs from the freshly fetched `pins`"""

    changes = []
    for build in sorted(set(current) | set(pins), key=_darwin_build_sort_key):
        new = pins.get(build)
        old = current.get(build)
        if new is None:
            changes.append(f"  removed  {build} (was {old})")
        elif old is None:
            changes.append(f"  added    {build} -> {new[0]} ({new[1]})")
        elif old != new[0]:
            changes.append(f"  changed  {build}: {old} -> {new[0]}")
    return changes


def main(argv=None) -> int:

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--file", type=Path, default=TARGET_FILE, help="file holding the pinned digest table")
    parser.add_argument("--stdout", action="store_true", help="print the generated block instead of editing the file")
    parser.add_argument("--check", action="store_true", help="exit non-zero when the file's pinned table is out of date")
    arguments = parser.parse_args(argv)

    pins = fetch_pins()

    if arguments.stdout:
        print("\n".join(render_pin_lines(pins)))
        return 0

    source = arguments.file.read_text()
    current = extract_pinned_digests(source)

    if arguments.check:
        if replace_pin_block(source, pins) == source:
            print(f"Pinned Tahoe metallib table is current: {len(current)} Darwin {DARWIN_MAJOR} builds")
            return 0
        print(f"{arguments.file} is out of date:")
        for change in describe_changes(current, pins):
            print(change)
        return 1

    updated = replace_pin_block(source, pins)
    if updated == source:
        print(f"Pinned Tahoe metallib table is already current: {len(pins)} Darwin {DARWIN_MAJOR} builds")
        return 0

    arguments.file.write_text(updated)
    print(f"Updated {arguments.file} with {len(pins)} pinned Darwin {DARWIN_MAJOR} builds")
    for change in describe_changes(current, pins):
        print(change)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
