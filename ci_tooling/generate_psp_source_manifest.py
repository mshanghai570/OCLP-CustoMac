"""
generate_psp_source_manifest.py: Record which Tahoe root-patch sources PatcherSupportPkg publishes

Every hardware family in `opencore_legacy_patcher/sys_patch/patchsets/hardware`
names the PatcherSupportPkg release directories its patches copy from. A family
whose sources are not all published cannot be enabled: the build payload
contract fails on the downloaded image, which is why the dormant families are
dormant.

This tool records the published subset of those paths in
`ci_tooling/psp_tahoe_source_manifest.py`, so the same fact can be checked
without the 664 MB image. The unpublished remainder is deliberately not
generated: it is written by hand, with a reason, in
`tests/test_tahoe_psp_source_manifest.py`.

Usage:
    python ci_tooling/generate_psp_source_manifest.py --stdout
    python ci_tooling/generate_psp_source_manifest.py
    python ci_tooling/generate_psp_source_manifest.py --check
    python ci_tooling/generate_psp_source_manifest.py --inventory /Volumes/Universal-Binaries

Run it from the project virtual environment, which provides the modules the
patcher package imports at load time. It is a manual maintenance tool; it is not
wired into CI because a new upstream release should never fail an unrelated
build or test run.
"""

import argparse
import ast
import sys

from collections.abc import Iterable
from pathlib import Path

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from opencore_legacy_patcher import constants
from ci_tooling import tahoe_probe_hardware
from ci_tooling.build_modules.payload_contract import PayloadContract


TARGET_FILE: Path = REPO_ROOT / "ci_tooling/psp_tahoe_source_manifest.py"
PUBLISHED_ASSIGNMENT: str = "PUBLISHED"
DIRECTORIES_ASSIGNMENT: str = "SOURCE_DIRECTORIES"
BLOCK_FOOTER: str = "})"


def block_header(name: str) -> str:
    """The declaration line that opens `name`'s frozenset block"""

    return f"{name}: frozenset[str] = frozenset({{"
RELEASES_REPO: str = "kgp-macPro/PatcherSupportPkg-laobamac"
IMAGE_ROOT_PREFIX: str = "Universal-Binaries/"
GITHUB_API: str = "https://api.github.com"
REQUEST_HEADERS: dict[str, str] = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "OCLP-psp-source-manifest-generator",
    "X-GitHub-Api-Version": "2022-11-28",
}


def fetch_inventory(version: str, session=None, timeout: int = 30) -> tuple[str, set[str]]:
    """
    Return `(commit_sha, image_paths)` for the PatcherSupportPkg release `version`

    The tag's Git tree is used rather than the release DMG. The two were compared
    directly for the currently pinned release: the mounted image and the tag tree
    hold the same 7,718 entries with no difference in either direction, and the
    tree costs two API calls instead of a 664 MB download. Pass a mounted image
    to `--inventory` when the authoritative bytes need to be consulted instead.
    """

    request = session if session is not None else requests

    commit = request.get(
        f"{GITHUB_API}/repos/{RELEASES_REPO}/commits/{version}",
        headers=REQUEST_HEADERS,
        timeout=timeout,
    )
    try:
        commit.raise_for_status()
        commit_sha = commit.json()["sha"]
    finally:
        commit.close()

    tree = request.get(
        f"{GITHUB_API}/repos/{RELEASES_REPO}/git/trees/{commit_sha}",
        headers=REQUEST_HEADERS,
        params={"recursive": "1"},
        timeout=timeout,
    )
    try:
        tree.raise_for_status()
        payload = tree.json()
        if payload.get("truncated"):
            raise RuntimeError(f"Truncated tree for {RELEASES_REPO}@{commit_sha}; refusing a partial inventory")

        return commit_sha, {
            entry["path"].removeprefix(IMAGE_ROOT_PREFIX)
            for entry in payload.get("tree", [])
            if entry["path"].startswith(IMAGE_ROOT_PREFIX)
        }
    finally:
        tree.close()


def parse_inventory(text: str) -> set[str]:
    """Read a newline-separated path listing, accepting either the tree or image root"""

    paths = set()
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        paths.add(entry.removeprefix(IMAGE_ROOT_PREFIX))
    return paths


def read_inventory(location: Path) -> set[str]:
    """Read a path listing from a text file, or walk a mounted disk image root"""

    if location.is_dir():
        return {
            str(path.relative_to(location))
            for path in location.rglob("*")
        }
    return parse_inventory(location.read_text())


def required_sources() -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, str]]:
    """Return `(family sources, shared-module sources, failures)` for Tahoe.

    Both groups are needed. Shared patch modules name version directories too, and
    a dormant hardware family can use one that no probing family reaches.
    """

    families, unprobeable = tahoe_probe_hardware.probe_tahoe_hardware()
    shared, shared_failures = PayloadContract().probe_tahoe_shared_patches()
    return families, shared, {**unprobeable, **shared_failures}


def published_sources(required: Iterable[str], inventory: set[str]) -> list[str]:
    """Return the sorted sources the release actually publishes"""

    return sorted({path for path in required if path in inventory})


def render_frozenset_lines(entries: Iterable[str]) -> list[str]:
    """Render a frozenset body in sorted order, one entry per line"""

    return [f'    "{entry}",' for entry in sorted(set(entries))]


def replace_scalar(source: str, name: str, value: str) -> str:
    """Return `source` with the module-level `name: str = "..."` value replaced"""

    prefix = f"{name}: str = "
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f'{prefix}"{value}"\n'
            return "".join(lines)

    raise LookupError(f"Could not find the {name} declaration")


def extract_frozenset(source: str, name: str) -> frozenset[str]:
    """Read the `name` frozenset out of `source` without importing the module"""

    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id != name:
            continue
        value = node.value
        if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "frozenset":
            value = value.args[0]
        return frozenset(ast.literal_eval(value))

    raise LookupError(f"No module-level {name} assignment found")


def replace_frozenset_block(source: str, name: str, entries: Iterable[str]) -> str:
    """Return `source` with the `name` frozenset block replaced by `entries`"""

    lines = source.splitlines(keepends=True)
    header = block_header(name)

    start = None
    for index, line in enumerate(lines):
        if line.strip() == header:
            start = index
            break
    if start is None:
        raise LookupError(f"Could not find the {name} declaration")

    end = None
    for index in range(start + 1, len(lines)):
        if lines[index].rstrip("\n") == BLOCK_FOOTER:
            end = index
            break
    if end is None:
        raise LookupError(f"Could not find the end of the {name} block")

    body = "".join(f"{line}\n" for line in render_frozenset_lines(entries))
    return "".join(lines[:start + 1]) + body + "".join(lines[end:])


def source_directories(inventory: Iterable[str]) -> list[str]:
    """Return the source directories the release provides, such as `12.5-24`"""

    return sorted({path.split("/", 1)[0] for path in inventory})


def render_manifest(
    source: str,
    version: str,
    published: Iterable[str],
    directories: Iterable[str],
    commit: str | None = None,
) -> str:
    """Bring the release version, commit and both generated sets up to date"""

    updated = replace_frozenset_block(source, PUBLISHED_ASSIGNMENT, published)
    updated = replace_frozenset_block(updated, DIRECTORIES_ASSIGNMENT, directories)
    updated = replace_scalar(updated, "PACKAGE_VERSION", version)
    if commit is not None:
        updated = replace_scalar(updated, "TAG_COMMIT", commit)
    return updated


def describe_changes(current: frozenset[str], published: Iterable[str]) -> list[str]:
    """Summarise how `current` differs from the freshly computed `published` set"""

    published = set(published)
    changes = []
    for path in sorted(current - published):
        changes.append(f"  removed  {path}")
    for path in sorted(published - current):
        changes.append(f"  added    {path}")
    return changes


def main(argv=None) -> int:

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--file", type=Path, default=TARGET_FILE, help="file holding the published-source set")
    parser.add_argument("--stdout", action="store_true", help="print the generated block instead of editing the file")
    parser.add_argument("--check", action="store_true", help="exit non-zero when the file's published set is out of date")
    parser.add_argument(
        "--inventory", type=Path, default=None,
        help="text listing or mounted image root to read instead of the GitHub tag tree",
    )
    arguments = parser.parse_args(argv)

    version = constants.Constants().patcher_support_pkg_version
    if arguments.inventory is not None:
        commit_sha = None
        inventory = read_inventory(arguments.inventory)
    else:
        commit_sha, inventory = fetch_inventory(version)

    families, shared, unprobeable = required_sources()
    required = {
        path
        for group in (families, shared)
        for paths in group.values()
        for path in paths
    }
    published = published_sources(required, inventory)
    directories = source_directories(inventory)

    print(f"PatcherSupportPkg {version}: {len(required)} Tahoe sources required")
    print(f"  {len(families)} hardware families, {len(shared)} shared patch modules")
    print(f"  published: {len(published)}   unpublished: {len(required) - len(published)}")
    print(f"  {len(directories)} source directories in the release")
    for name, failure in sorted(unprobeable.items()):
        print(f"  cannot probe {name}: {failure}")

    if arguments.stdout:
        print("\n".join(render_frozenset_lines(published)))
        return 0

    source = arguments.file.read_text()
    current = extract_frozenset(source, PUBLISHED_ASSIGNMENT)
    updated = render_manifest(source, version, published, directories, commit_sha)

    if arguments.check:
        if updated == source:
            print(f"{arguments.file} is current: {len(current)} published Tahoe sources")
            return 0
        print(f"{arguments.file} is out of date:")
        for change in describe_changes(current, published):
            print(change)
        return 1

    if updated == source:
        print(f"{arguments.file} is already current: {len(published)} published Tahoe sources")
        return 0

    arguments.file.write_text(updated)
    print(f"Updated {arguments.file} with {len(published)} published Tahoe sources")
    for change in describe_changes(current, published):
        print(change)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
