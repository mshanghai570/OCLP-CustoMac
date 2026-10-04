"""
base.py: Base class for all patch sets
"""

from collections.abc import Iterable, Iterator
from enum import StrEnum
from pathlib import Path


class PatchType(StrEnum):
    """
    Type of patch
    """
    OVERWRITE_SYSTEM_VOLUME = "Overwrite System Volume"
    OVERWRITE_DATA_VOLUME   = "Overwrite Data Volume"
    MERGE_SYSTEM_VOLUME     = "Merge System Volume"
    MERGE_DATA_VOLUME       = "Merge Data Volume"
    REMOVE_SYSTEM_VOLUME    = "Remove System Volume"
    REMOVE_DATA_VOLUME      = "Remove Data Volume"
    EXECUTE                 = "Execute"


class DynamicPatchset(StrEnum):
    MetallibSupportPkg = "MetallibSupportPkg"


# The patch types that copy a file into a patched volume. `REMOVE_*` delete and
# `EXECUTE` runs a process, so neither names a source to copy.
COPY_OPERATIONS: tuple[PatchType, ...] = (
    PatchType.OVERWRITE_SYSTEM_VOLUME,
    PatchType.OVERWRITE_DATA_VOLUME,
    PatchType.MERGE_SYSTEM_VOLUME,
    PatchType.MERGE_DATA_VOLUME,
)

_DYNAMIC_VALUES: frozenset[str] = frozenset(member.value for member in DynamicPatchset)


def is_runtime_sourced(source: object) -> bool:
    """Whether `source` is a `DynamicPatchset`, by member or by value.

    Accepting the bare value as well as the member matches the `in
    DynamicPatchset` test this replaced, which resolved both, and unlike that
    test it cannot raise on an unhashable entry.
    """

    if isinstance(source, DynamicPatchset):
        return True
    try:
        return source in _DYNAMIC_VALUES
    except TypeError:
        return False


def compose_source_path(source_version: str, destination: str, filename: str = "") -> str:
    """The path a patch set entry names: `source_version/destination/filename`.

    Relative to PatcherSupportPkg, unless `source_version` is itself absolute, in
    which case the result is absolute too. Without a `filename` this is the
    directory an entry's files are copied from, which the installer passes to
    `install_new_file` separately from the name.
    """

    return str(Path(source_version) / destination.lstrip("/") / filename)


def resolve_source_path(source_root: str | Path, source: str, from_payload: bool) -> Path:
    """Where `source` lives on disk: beneath `source_root`, or at its own path."""

    return Path(source_root) / source if from_payload else Path(source)


def source_entry_path(
    source_root: str | Path, source_version: str, destination: str, filename: str = ""
) -> Path:
    """Absolute path of one patch set entry's source, the way the installer reads it."""

    return resolve_source_path(
        source_root,
        compose_source_path(source_version, destination, filename),
        not str(source_version).startswith("/"),
    )


def iter_patchset_sources(patches: dict) -> Iterator[tuple[str, str, bool]]:
    """Yield `(patchset, source, from_payload)` for every file a patchset copies.

    This is the single definition of what a patchset reads off disk, shared by
    the installer, the GUI validator and the release build contract; they used to
    compose the path independently, so a change to one could silently disagree
    with the others.

    `source` is `source_version / destination / filename`. `from_payload` is False
    when the source is read from the booted root volume rather than from
    PatcherSupportPkg, in which case `source` is absolute. Entries whose source is
    computed at runtime (`DynamicPatchset`) name no path yet and are skipped.
    """

    for patchset, operations in patches.items():
        for operation in COPY_OPERATIONS:
            for destination, files in (operations.get(operation) or {}).items():
                for filename, source_version in files.items():
                    if is_runtime_sourced(source_version):
                        continue
                    yield (
                        patchset,
                        compose_source_path(source_version, destination, filename),
                        not str(source_version).startswith("/"),
                    )


class PayloadSourceError(RuntimeError):
    """A patchset names a source that is not present on disk.

    Raised by the installer and the GUI validator, which both used to abort on
    the first absent file with a bare `Exception`, hiding any others until the
    user had rebooted and tried again.
    """


def format_missing_sources(missing: Iterable[tuple[str, str]]) -> str:
    """Render `(patchset, source)` pairs as one message naming every absent file."""

    return "PatcherSupportPkg is missing root-patch resources:\n- " + "\n- ".join(
        f"{patchset}: {source}" for patchset, source in sorted(missing)
    )


class BasePatchset:

    def __init__(self) -> None:
        # XNU Kernel versions
        self.macOS_12_0_B7: float = 21.1
        self.macOS_12_4:    float = 21.5
        self.macOS_12_5:    float = 21.6
        self.macOS_13_3:    float = 22.4
        self.macOS_14_1:    float = 23.1
        self.macOS_14_2:    float = 23.2
        self.macOS_14_4:    float = 23.4
        self.macOS_15_2:    float = 24.2
        self.macOS_15_3:    float = 24.3