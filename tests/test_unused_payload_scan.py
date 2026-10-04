"""The PatcherSupportPkg dead-weight scan: exact, bounded, and actually reachable.

`_find_unused_files` answers which files in the 664 MB Universal-Binaries image no
patch set can reach, which is what keeps the payload from growing without bound.
It was unreachable -- `--validate` built the validator with `verify_unused_files`
left False and nothing ever passed True -- and too slow to run if it had been: it
recomputed `Path.relative_to` for every file against every named source, which is
49 seconds of path normalisation on a payload of the real size.

Its matching rule was also looser than its own description. "Is either path
anywhere inside the other" counts a file as used when its name merely *starts*
with a named path, so a kept-aside copy stayed invisible. The rule below is a
strict subset: a file is used when it is named, or when it lives beneath a named
directory, since root patches copy whole bundles.
"""

import tempfile
import types
import unittest

from pathlib import Path
from unittest import mock

from opencore_legacy_patcher import constants
from opencore_legacy_patcher.support import arguments, validation
from opencore_legacy_patcher.support.utilities import check_cli_args


NAMED_BUNDLE: str = "12.5-3802/System/Library/Extensions/AppleHDA.kext"


class UnusedPayloadScanTests(unittest.TestCase):
    def _validator(self, payload_root: Path, named: list[Path]) -> validation.PatcherValidation:
        validator = validation.PatcherValidation.__new__(validation.PatcherValidation)
        # The scan reads only this, and the real path is a read-only property
        validator.constants = types.SimpleNamespace(
            payload_local_binaries_root_path=payload_root
        )
        validator.active_patchset_files = [str(path) for path in named]
        return validator

    def _touch(self, root: Path, *relative_paths: str) -> None:
        for relative_path in relative_paths:
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

    def test_a_named_file_and_a_named_bundle_are_both_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._touch(
                root,
                f"{NAMED_BUNDLE}/Contents/Info.plist",
                f"{NAMED_BUNDLE}/Contents/MacOS/AppleHDA",
                "13.7.2-25/usr/libexec/wifip2pd",
            )
            validator = self._validator(
                root,
                [root / NAMED_BUNDLE, root / "13.7.2-25/usr/libexec/wifip2pd"],
            )
            self.assertEqual(validator._find_unused_files(), [])

    def test_a_sibling_with_a_coincidental_name_prefix_is_reported(self) -> None:
        """The old substring rule counted this copy as used and hid it."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._touch(
                root,
                f"{NAMED_BUNDLE}/Contents/MacOS/AppleHDA",
                f"{NAMED_BUNDLE}.backup/Contents/MacOS/AppleHDA",
            )
            validator = self._validator(root, [root / NAMED_BUNDLE])
            self.assertEqual(
                validator._find_unused_files(),
                [Path(f"{NAMED_BUNDLE}.backup/Contents/MacOS/AppleHDA")],
            )

    def test_an_unrelated_sibling_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._touch(
                root,
                f"{NAMED_BUNDLE}/Contents/MacOS/AppleHDA",
                "12.5-3802/System/Library/Extensions/AppleIntelHDGraphics.kext/Contents/Info.plist",
            )
            validator = self._validator(root, [root / NAMED_BUNDLE])
            self.assertEqual(
                validator._find_unused_files(),
                [
                    Path(
                        "12.5-3802/System/Library/Extensions/"
                        "AppleIntelHDGraphics.kext/Contents/Info.plist"
                    )
                ],
            )

    def test_image_metadata_is_never_reported_as_dead_weight(self) -> None:
        """These ship in every image and no patch set can name them."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._touch(
                root,
                ".DS_Store",
                ".signed",
                ".fseventsd/fseventsd-uuid",
                f"{NAMED_BUNDLE}/Contents/MacOS/AppleHDA",
                "12.5-3802/System/Library/Extensions/Dead.kext",
            )
            validator = self._validator(root, [root / NAMED_BUNDLE])
            self.assertEqual(
                validator._find_unused_files(),
                [Path("12.5-3802/System/Library/Extensions/Dead.kext")],
            )

    def test_named_sources_outside_the_payload_are_ignored(self) -> None:
        """`relative_to` raises on them, and they are not ours to report on."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._touch(root, "12.5-3802/System/Library/Extensions/Dead.kext")
            validator = self._validator(root, [Path("/System/Library/Extensions/Elsewhere")])
            self.assertEqual(
                validator._find_unused_files(),
                [Path("12.5-3802/System/Library/Extensions/Dead.kext")],
            )

    def test_nothing_is_scanned_without_named_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._touch(root, "12.5-3802/System/Library/Extensions/Dead.kext")
            validator = self._validator(root, [])
            with mock.patch.object(Path, "rglob") as scan:
                self.assertEqual(validator._find_unused_files(), [])
            scan.assert_not_called()

    def test_named_paths_are_normalised_once_not_once_per_pair(self) -> None:
        """`relative_to` per (file, named source) pair is what made it unusable."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._touch(
                root,
                *[
                    f"12.5-3802/System/Library/Extensions/Bundle{index}/Contents/Info.plist"
                    for index in range(40)
                ],
                *[
                    f"12.5-3802/System/Library/Extensions/Target{index}/Contents/Info.plist"
                    for index in range(30)
                ],
            )
            named = [
                root / f"12.5-3802/System/Library/Extensions/Target{index}"
                for index in range(30)
            ]
            validator = self._validator(root, named)

            calls = 0
            original_relative_to = Path.relative_to

            def counting_relative_to(path_self, *args, **kwargs):
                nonlocal calls
                calls += 1
                return original_relative_to(path_self, *args, **kwargs)

            with mock.patch.object(Path, "relative_to", counting_relative_to):
                unused = validator._find_unused_files()

            self.assertEqual(len(unused), 40)
            # Linear is ~70 calls; the quadratic rule needed ~2,100.
            self.assertLess(
                calls,
                200,
                f"{calls} path normalisations for 70 files and 30 named sources "
                "is per-pair work",
            )


class UnusedPayloadFlagTests(unittest.TestCase):
    """The scan has to be reachable, or none of the above matters."""

    def _parse(self, *argv: str):
        with mock.patch("sys.argv", ["OpenCore-Patcher", *argv]):
            return check_cli_args()

    def test_the_flag_requests_the_scan(self) -> None:
        self.assertTrue(self._parse("--validate_unused_payload").validate_unused_payload)

    def test_plain_validation_does_not(self) -> None:
        args = self._parse("--validate")
        self.assertTrue(args.validate)
        self.assertFalse(args.validate_unused_payload)

    def test_the_flag_alone_is_a_valid_invocation(self) -> None:
        """Every other entry point is absent, so it must not fall through to None."""
        self.assertIsNotNone(self._parse("--validate_unused_payload"))

    def _dispatch(self, **selected: bool) -> mock.Mock:
        """Run `arguments` with the given entry points selected, and return the validator."""
        flags = {
            "validate": False,
            "validate_unused_payload": False,
            "build": False,
            "patch_sys_vol": False,
            "unpatch_sys_vol": False,
            "prepare_for_update": False,
            "cache_os": False,
            "auto_patch": False,
        }
        flags.update(selected)
        with mock.patch.object(
            arguments.utilities, "check_cli_args"
        ) as parse, mock.patch.object(arguments.validation, "PatcherValidation") as run:
            parse.return_value = type("Args", (), flags)()
            arguments.arguments(constants.Constants())
        return run

    def test_the_handler_passes_the_request_through(self) -> None:
        for selected, expected in (
            ({"validate": True}, False),
            ({"validate_unused_payload": True}, True),
        ):
            with self.subTest(selected=selected):
                run = self._dispatch(**selected)
                run.assert_called_once_with(mock.ANY, verify_unused_files=expected)

    def test_the_flag_dispatches_validation_on_its_own(self) -> None:
        """Parsing the flag is not enough; nothing else would route it."""
        self._dispatch(validate_unused_payload=True).assert_called_once()


if __name__ == "__main__":
    unittest.main()
