"""Fail-closed published-digest verification for downloaded Kernel Debug Kits."""

import hashlib
import subprocess
import tempfile
import types
import unittest

from pathlib import Path
from unittest import mock

from opencore_legacy_patcher.support import kdk_handler
from opencore_legacy_patcher.support.kdk_selection import (
    KernelDebugKitCandidate,
    catalog_sha256,
)


DIGEST = hashlib.sha256(b"kdk-bytes").hexdigest()

ENTRY = {
    "version": "26.6.2",
    "build": "25G82",
    "url": "https://example/25G82.dmg",
    "fileSize": 9,
    "sha256sum": DIGEST,
}
ENTRY_NO_DIGEST = {
    "version": "26.6.2",
    "build": "25G82",
    "url": "https://example/25G82.dmg",
    "fileSize": 9,
}


def constants(download_path):
    return types.SimpleNamespace(
        detected_os=25,
        detected_os_build="25G82",
        detected_os_version="26.6.2",
        kdk_download_path=download_path,
        patcher_version="3.0.0",
    )


class CatalogDigestTests(unittest.TestCase):
    def test_published_digest_is_normalised_and_invalid_values_are_dropped(self):
        self.assertEqual(catalog_sha256(ENTRY), DIGEST)
        self.assertEqual(catalog_sha256({"sha256sum": DIGEST.upper()}), DIGEST)
        self.assertIsNone(catalog_sha256({"sha256sum": "not-a-digest"}))
        self.assertIsNone(catalog_sha256({"sha256sum": DIGEST[:-1]}))
        self.assertIsNone(catalog_sha256({"sha256sum": 12345}))
        self.assertIsNone(catalog_sha256({"sha256sum": None}))
        self.assertIsNone(catalog_sha256(ENTRY_NO_DIGEST))
        self.assertIsNone(catalog_sha256("not-a-dict"))

    def test_candidate_carries_the_published_digest(self):
        self.assertEqual(KernelDebugKitCandidate.from_catalog_entry(ENTRY).sha256, DIGEST)
        self.assertIsNone(KernelDebugKitCandidate.from_catalog_entry(ENTRY_NO_DIGEST).sha256)

    def test_resolution_paths_pin_the_published_digest(self):
        with mock.patch.object(kdk_handler.KernelDebugKitObject, "_get_remote_kdks", return_value=[ENTRY]), \
             mock.patch.object(kdk_handler.KernelDebugKitObject, "_local_kdk_installed", return_value=None):
            exact = kdk_handler.KernelDebugKitObject(constants(Path("/private/tmp/KDK.dmg")), "25G82", "26.6.2")
            closest = kdk_handler.KernelDebugKitObject(constants(Path("/private/tmp/KDK.dmg")), "25G99", "26.6.3")
            manual = kdk_handler.KernelDebugKitObject(
                constants(Path("/private/tmp/KDK.dmg")),
                "25G82",
                "26.6.2",
                selected_candidate=KernelDebugKitCandidate.from_catalog_entry(ENTRY),
            )

        self.assertTrue(exact.success)
        self.assertEqual(exact.kdk_url_expected_sha256, DIGEST)
        self.assertEqual(exact.resolved_candidate().sha256, DIGEST)

        self.assertFalse(closest.kdk_url_is_exactly_match)
        self.assertEqual(closest.kdk_closest_match_url_expected_sha256, DIGEST)
        self.assertEqual(closest.kdk_url_expected_sha256, DIGEST)

        self.assertTrue(manual.success)
        self.assertEqual(manual.kdk_url_expected_sha256, DIGEST)

    def test_a_catalog_without_digests_leaves_no_expected_digest(self):
        with mock.patch.object(kdk_handler.KernelDebugKitObject, "_get_remote_kdks", return_value=[ENTRY_NO_DIGEST]), \
             mock.patch.object(kdk_handler.KernelDebugKitObject, "_local_kdk_installed", return_value=None):
            resolver = kdk_handler.KernelDebugKitObject(constants(Path("/private/tmp/KDK.dmg")), "25G82", "26.6.2")
        self.assertIsNone(resolver.kdk_url_expected_sha256)


class ValidateChecksumDigestTests(unittest.TestCase):
    def _resolver(self, expected_sha256):
        resolver = object.__new__(kdk_handler.KernelDebugKitObject)
        resolver.constants = constants(Path("/private/tmp/KDK.dmg"))
        resolver.passive = False
        resolver.kdk_url_expected_sha256 = expected_sha256
        return resolver

    def test_matching_digest_passes_image_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            dmg = Path(temporary) / "kdk.dmg"
            dmg.write_bytes(b"kdk-bytes")
            resolver = self._resolver(DIGEST)
            with mock.patch.object(kdk_handler.subprocess, "run",
                                    return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertTrue(resolver.validate_kdk_checksum(dmg))
        self.assertTrue(resolver.success)
        self.assertEqual(Path(run.call_args.args[0][-1]), dmg)

    def test_mismatched_digest_fails_before_image_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            dmg = Path(temporary) / "kdk.dmg"
            dmg.write_bytes(b"kdk-bytes")
            resolver = self._resolver("0" * 64)
            with mock.patch.object(kdk_handler.subprocess, "run") as run:
                self.assertFalse(resolver.validate_kdk_checksum(dmg))
            run.assert_not_called()
        self.assertFalse(resolver.success)
        self.assertIn("checksum", resolver.error_msg.lower())

    def test_unreadable_asset_fails_closed_when_a_digest_is_expected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "not-a-file"
            directory.mkdir()
            resolver = self._resolver(DIGEST)
            with mock.patch.object(kdk_handler.subprocess, "run") as run:
                self.assertFalse(resolver.validate_kdk_checksum(directory))
            run.assert_not_called()
        self.assertFalse(resolver.success)

    def test_missing_published_digest_falls_back_to_image_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            dmg = Path(temporary) / "kdk.dmg"
            dmg.write_bytes(b"kdk-bytes")
            resolver = self._resolver(None)
            with mock.patch.object(kdk_handler.subprocess, "run",
                                    return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertTrue(resolver.validate_kdk_checksum(dmg))
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
