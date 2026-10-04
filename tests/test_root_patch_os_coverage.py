"""Which releases the root-patch validation path actually covers.

`--validate` walks a list of kernel majors and checks every source each enabled
family names for that release against the mounted payload. The list stopped at
Sequoia (24) while the host-OS check already allowed Tahoe (25), so the fork's
only target was the one release whose sources no validation run ever checked.
That is the same shape as the missing `12.5-25` directory: a per-release list
that had quietly fewer entries than the product supports, which the offline
oracle found only because it is not restricted to the validated releases.

The supported range is now declared once, in `datasets/os_data.py`, and read by
both the host-OS bound and the validation walk. These tests pin the range, prove
the walk reaches Darwin 25 against a real payload tree, and prove that run is not
vacuous.
"""

import tempfile
import unittest

from pathlib import Path
from unittest import mock

from ci_tooling import psp_tahoe_source_manifest
from ci_tooling import tahoe_probe_hardware
from ci_tooling.build_modules.payload_contract import PayloadContract
from opencore_legacy_patcher import constants
from opencore_legacy_patcher.datasets import os_data as os_data_module
from opencore_legacy_patcher.datasets.os_data import os_data
from opencore_legacy_patcher.support import validation
from opencore_legacy_patcher.sys_patch.patchsets import PayloadSourceError
from opencore_legacy_patcher.sys_patch.patchsets.detect import HardwarePatchsetDetection


REPO_ROOT: Path = Path(__file__).resolve().parents[1]
PAYLOAD_ROOT_NAME: str = "Universal-Binaries"
TAHOE_BUILD: str = "25A5316i"


class SupportedReleaseRangeTests(unittest.TestCase):
    def test_range_is_ordered_without_duplicates(self) -> None:
        supported = os_data_module.SUPPORTED_MAJOR_VERSIONS
        self.assertEqual(list(supported), sorted(set(supported)), "oldest first, no repeats")

    def test_range_spans_big_sur_through_tahoe(self) -> None:
        supported = os_data_module.SUPPORTED_MAJOR_VERSIONS
        self.assertEqual(supported[0], os_data.big_sur.value)
        self.assertEqual(supported[-1], os_data.tahoe.value, "Tahoe is the release this fork ships")

    def test_range_omits_no_release_between_its_ends(self) -> None:
        """A release declared in `os_data` but left out of the range is both
        unsupported by the host check and unvalidated by the walk."""

        supported = set(os_data_module.SUPPORTED_MAJOR_VERSIONS)
        declared = {
            member.value
            for member in os_data
            if os_data_module.SUPPORTED_MAJOR_VERSIONS[0]
            <= member.value
            <= os_data_module.SUPPORTED_MAJOR_VERSIONS[-1]
        }
        self.assertEqual(supported, declared)

    def test_validate_walks_the_shared_declaration(self) -> None:
        """The walk's release list was written out by hand and fell behind."""

        source = (REPO_ROOT / "opencore_legacy_patcher/support/validation.py").read_text()
        block = source.split("def _validate_sys_patch")[1].split("# unmount the dmg")[0]
        self.assertIn("for supported_os in os_data.SUPPORTED_MAJOR_VERSIONS:", block)
        self.assertNotIn(
            "os_data.os_data.",
            block,
            "a hand-written release list is back in _validate_sys_patch",
        )

    def test_host_os_check_accepts_exactly_the_supported_range(self) -> None:
        detector = HardwarePatchsetDetection.__new__(HardwarePatchsetDetection)
        detector._dortania_internal_check = lambda: False

        supported = os_data_module.SUPPORTED_MAJOR_VERSIONS
        for major in supported:
            detector._xnu_major = major
            self.assertFalse(
                detector._validation_check_unsupported_host_os(),
                f"Darwin {major} is declared supported",
            )

        for major in (supported[0] - 1, supported[-1] + 1):
            detector._xnu_major = major
            self.assertTrue(
                detector._validation_check_unsupported_host_os(),
                f"Darwin {major} is outside the supported range",
            )


class TahoeRootPatchWalkTests(unittest.TestCase):
    """The walk itself, run for Darwin 25 against a payload built from the manifest."""

    @classmethod
    def setUpClass(cls) -> None:
        """This is about which sources are read, not about this host's state.

        Detection consults FileVault, SIP, the secure boot level and AMFI while it
        runs. Those answers cannot change the patch sources, and the subprocesses
        they spawn dominate the runtime of the walk below.
        """

        for name in (
            "_validation_check_filevault_is_enabled",
            "_validation_check_system_integrity_protection_enabled",
            "_validation_check_secure_boot_model_enabled",
            "_validation_check_amfi_enabled",
            "_dortania_internal_check",
        ):
            patcher = mock.patch.object(HardwarePatchsetDetection, name, return_value=False)
            patcher.start()
            cls.addClassCleanup(patcher.stop)

    def _payload(self, directory: str) -> constants.Constants:
        """Materialise every published Tahoe source, then point constants at it."""

        payload_root = Path(directory) / PAYLOAD_ROOT_NAME
        for published in psp_tahoe_source_manifest.PUBLISHED:
            target = payload_root / published
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()

        config = constants.Constants()
        config.payload_path = Path(directory)
        config.detected_os = os_data.tahoe.value
        config.detected_os_minor = 0
        config.detected_os_version = "26.0"
        # The two enabled families read the machine identifier, so a fixture keeps
        # the requested source set the same on any host.
        config.computer = tahoe_probe_hardware.TAHOE_PROBE_PROFILES[0].computer()
        return config

    @staticmethod
    def _validation(config: constants.Constants) -> validation.PatcherValidation:
        """A validator without the EFI build pass or the disk image mount."""

        instance = validation.PatcherValidation.__new__(validation.PatcherValidation)
        instance.constants = config
        instance.verify_unused_files = False
        instance.active_patchset_files = []
        return instance

    @staticmethod
    def _requested_sources(config: constants.Constants, minor: int) -> set[str]:
        detector = HardwarePatchsetDetection(
            config,
            xnu_major=os_data.tahoe.value,
            xnu_minor=minor,
            os_build=TAHOE_BUILD,
            validation=True,
        )
        return {
            path for _, path in PayloadContract().iter_root_patch_sources(detector.patches)
        }

    def test_the_walk_validates_every_darwin_25_minor(self) -> None:
        """`_validate_sys_patch` walks minors 0 through 9 for each release."""

        with tempfile.TemporaryDirectory() as directory:
            config = self._payload(directory)
            requested = {
                minor: self._requested_sources(config, minor) for minor in range(0, 10)
            }

            self.assertTrue(requested[0], "the enabled families must name sources on Tahoe")
            self.assertEqual(
                {tuple(sorted(paths)) for paths in requested.values()},
                {tuple(sorted(requested[0]))},
                "every Tahoe minor asks for the same sources",
            )
            self.assertEqual(
                set().union(*requested.values()) - set(psp_tahoe_source_manifest.PUBLISHED),
                set(),
                "the walk must not require a source the pinned release does not publish",
            )

            for minor in requested:
                with self.subTest(minor=minor):
                    self._validation(config)._validate_root_patch_files(
                        os_data.tahoe.value, minor
                    )

    def test_a_source_absent_from_the_payload_still_fails_the_walk(self) -> None:
        """Otherwise the run above would pass without checking anything."""

        with tempfile.TemporaryDirectory() as directory:
            config = self._payload(directory)
            missing = sorted(self._requested_sources(config, 0))[0]
            (Path(directory) / PAYLOAD_ROOT_NAME / missing).unlink()

            with self.assertRaises(PayloadSourceError) as raised:
                self._validation(config)._validate_root_patch_files(os_data.tahoe.value, 0)

        self.assertIn(missing, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
