"""Tahoe metallib resolution must use pinned pyquick packages, not a manifest."""

from __future__ import annotations

import hashlib
import types
import unittest

from pathlib import Path
from unittest import mock

from opencore_legacy_patcher.support import metallib_handler
from opencore_legacy_patcher.support.metallib_handler import MetalLibraryObject


TAHOE_BUILD = "25G83"
TAHOE_VERSION = "26.6.2"
TAHOE_SHA256 = metallib_handler.TAHOE_METALLIB_PINNED_SHA256[TAHOE_BUILD]


def _release(build: str, version: str, digest: str) -> dict:
    return {
        "tag_name": f"{version}-{build}",
        "assets": [
            {
                "name": f"MetallibSupportPkg-{version}-{build}.pkg",
                "browser_download_url": (
                    f"https://github.com/pyquick/MetallibSupportPkg/releases/download/"
                    f"{version}-{build}/MetallibSupportPkg-{version}-{build}.pkg"
                ),
                "digest": f"sha256:{digest}",
            }
        ],
    }


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.closed = False

    def json(self):
        return self._payload

    def close(self) -> None:
        self.closed = True


class TahoeMetallibResolverTests(unittest.TestCase):
    def _constants(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(patcher_version="3.0.3")

    def _object(self, build: str = TAHOE_BUILD, version: str = TAHOE_VERSION) -> MetalLibraryObject:
        """Build a MetalLibraryObject without letting __init__ resolve anything."""
        obj = MetalLibraryObject.__new__(MetalLibraryObject)
        obj.constants = self._constants()
        obj.host_build = build
        obj.host_version = version
        obj.passive = False
        obj.ignore_installed = False
        obj.metallib_already_installed = False
        obj.metallib_installed_path = ""
        obj.metallib_url = ""
        obj.metallib_url_build = ""
        obj.metallib_url_version = ""
        obj.metallib_url_is_exactly_match = False
        obj.metallib_closest_match_url = ""
        obj.metallib_closest_match_url_build = ""
        obj.metallib_closest_match_url_version = ""
        obj.metallib_expected_sha256 = ""
        obj.success = False
        obj.error_msg = ""
        return obj

    def test_darwin_build_sort_key_orders_letters_and_numbers(self) -> None:
        key = metallib_handler._darwin_build_sort_key
        self.assertLess(key("25G70"), key("25G76"))
        self.assertLess(key("25F84"), key("25G70"))
        self.assertLess(key("25A353"), key("25B77"))
        # Later seeds carry a trailing letter and must still order numerically.
        self.assertLess(key("25G76"), key("25G5028f"))
        self.assertLess(key("25G5028f"), key("25G5052e"))
        # Lowercase letters belong to the second cycle and sort after uppercase.
        self.assertLess(key("25Z1"), key("25a1"))
        self.assertEqual(key("not-a-build"), (0, 0, 0, ""))

    def test_exact_tahoe_build_is_selected_with_pinned_digest(self) -> None:
        obj = self._object()
        payload = [_release(TAHOE_BUILD, TAHOE_VERSION, TAHOE_SHA256)]

        with mock.patch.object(
            metallib_handler.network_handler.NetworkUtilities, "get",
            return_value=_FakeResponse(payload),
        ):
            self.assertTrue(obj._resolve_tahoe_metallib())

        self.assertEqual(obj.metallib_url_build, TAHOE_BUILD)
        self.assertEqual(obj.metallib_url_version, TAHOE_VERSION)
        self.assertEqual(obj.metallib_expected_sha256, TAHOE_SHA256)
        self.assertTrue(obj.metallib_url_is_exactly_match)

    def test_unpinned_build_is_refused(self) -> None:
        obj = self._object(build="25ZZZZ")
        obj.host_version = "26.9.9"
        payload = [_release("25ZZZZ", "26.9.9", "f" * 64)]

        with mock.patch.object(
            metallib_handler.network_handler.NetworkUtilities, "get",
            return_value=_FakeResponse(payload),
        ):
            releases = obj._get_tahoe_releases()

        self.assertEqual(releases, [])

    def test_build_whose_upstream_digest_disagrees_with_pin_is_refused(self) -> None:
        obj = self._object(build="25G9999")
        obj.host_version = "26.9.9"
        payload = [_release("25G9999", "26.9.9", "a" * 64)]

        with mock.patch.object(
            metallib_handler.network_handler.NetworkUtilities, "get",
            return_value=_FakeResponse(payload),
        ):
            self.assertFalse(obj._resolve_tahoe_metallib())

        self.assertEqual(obj.metallib_url, "")

    def test_closest_match_never_exceeds_host_version(self) -> None:
        obj = self._object(build="25G70", version="26.6")
        newer = metallib_handler.TAHOE_METALLIB_PINNED_SHA256["25G83"]
        payload = [
            _release("25G83", TAHOE_VERSION, newer),
            _release("25G72", "26.6", metallib_handler.TAHOE_METALLIB_PINNED_SHA256["25G72"]),
        ]

        with mock.patch.object(
            metallib_handler.network_handler.NetworkUtilities, "get",
            return_value=_FakeResponse(payload),
        ):
            self.assertTrue(obj._resolve_tahoe_metallib())

        # 26.6.2 is newer than the host's 26.6, so it must not be chosen.
        self.assertEqual(obj.metallib_url_build, "25G72")
        self.assertFalse(obj.metallib_url_is_exactly_match)

    def test_api_failure_falls_back_to_manifest_lookup(self) -> None:
        obj = self._object()

        with mock.patch.object(
            metallib_handler.network_handler.NetworkUtilities, "get",
            return_value=_FakeResponse([], status_code=403),
        ):
            self.assertFalse(obj._resolve_tahoe_metallib())

    def test_verify_metallib_accepts_matching_package(self) -> None:
        obj = self._object()
        payload = b"a genuine metallib package"
        obj.metallib_expected_sha256 = hashlib.sha256(payload).hexdigest()
        obj.metallib_url_build = TAHOE_BUILD

        with mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(Path, "open") as mocked_open:
            mocked_open.return_value.__enter__.return_value.read.side_effect = [payload, b""]
            self.assertTrue(obj.verify_metallib("/tmp/pkg.pkg"))

    def test_verify_metallib_rejects_tampered_package(self) -> None:
        obj = self._object()
        obj.metallib_expected_sha256 = TAHOE_SHA256
        obj.metallib_url_build = TAHOE_BUILD

        with mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(Path, "open") as mocked_open:
            mocked_open.return_value.__enter__.return_value.read.side_effect = [b"tampered", b""]
            self.assertFalse(obj.verify_metallib("/tmp/pkg.pkg"))

        self.assertFalse(obj.success)
        self.assertIn("failed integrity check", obj.error_msg)

    def test_verify_metallib_refuses_when_no_digest_pinned(self) -> None:
        obj = self._object()
        self.assertFalse(obj.verify_metallib("/tmp/pkg.pkg"))
        self.assertIn("unverified", obj.error_msg)

    def test_install_refuses_to_elevate_on_digest_mismatch(self) -> None:
        obj = self._object()
        obj.success = True
        obj.metallib_already_installed = False
        obj.metallib_url_build = TAHOE_BUILD
        obj.metallib_expected_sha256 = TAHOE_SHA256

        with mock.patch.object(
            metallib_handler.MetalLibraryObject, "verify_metallib", return_value=False
        ), mock.patch.object(metallib_handler.subprocess_wrapper, "run_as_root") as mocked_root:
            self.assertFalse(obj.install_metallib())

        mocked_root.assert_not_called()

    def test_install_path_includes_legacy_vendor_folders(self) -> None:
        self.assertIn("/Library/Application Support/Pyquick/MetallibSupportPkg",
                      metallib_handler.METALLIB_INSTALL_PATHS)
        self.assertIn("/Library/Application Support/Dortania/MetallibSupportPkg",
                      metallib_handler.METALLIB_INSTALL_PATHS)

    def test_pinned_table_covers_darwin25_builds_only(self) -> None:
        for build, digest in metallib_handler.TAHOE_METALLIB_PINNED_SHA256.items():
            self.assertTrue(build.startswith("25"), f"{build} is not a Darwin 25 build")
            self.assertEqual(len(digest), 64, f"{build} has a malformed digest")
            int(digest, 16)

    def test_pinned_digest_matches_documenting_audit(self) -> None:
        # The handoff records this digest for the inspected 26.6.2-25G83 package.
        self.assertEqual(
            TAHOE_SHA256,
            "3578553873558f97c7aba27722fb16ec63ab838a8b4d823c07e129c6df9c5867",
        )


if __name__ == "__main__":
    unittest.main()
