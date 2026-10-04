"""Malformed chunklists and validation I/O failures must terminate cleanly."""

import hashlib
import struct
import tempfile
import unittest

from pathlib import Path
from unittest import mock

from opencore_legacy_patcher.support.integrity_verification import ChunklistStatus, ChunklistVerification


def chunklist_for(data):
    header = b"CNKL" + struct.pack("<I", 36) + bytes((1, 1, 1, 0)) + struct.pack("<QQQ", 1, 36, 72)
    return header + struct.pack("<I", len(data)) + hashlib.sha256(data).digest() + bytes(256)


class ChunklistFailureTests(unittest.TestCase):
    def test_malformed_headers_report_failure_without_raising(self):
        for data in (b"", b"CNKL", b"bad!" + bytes(32)):
            with self.subTest(header=data):
                verifier = ChunklistVerification("unused", data)
                self.assertEqual(verifier.status, ChunklistStatus.FAILURE)
                self.assertTrue(verifier.error_msg)

    def test_unreadable_chunklist_reports_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            verifier = ChunklistVerification(Path(temporary) / "asset", Path(temporary) / "missing")
            self.assertEqual(verifier.status, ChunklistStatus.FAILURE)
            self.assertTrue(verifier.error_msg)

    def test_file_open_failure_ends_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "asset"
            path.write_bytes(b"asset")
            verifier = ChunklistVerification(path, chunklist_for(b"asset"))
            with mock.patch.object(Path, "open", side_effect=PermissionError("file unavailable")):
                verifier._validate()
            self.assertEqual(verifier.status, ChunklistStatus.FAILURE)
            self.assertIn("file unavailable", verifier.error_msg)

    def test_valid_file_is_still_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "asset"
            path.write_bytes(b"asset")
            verifier = ChunklistVerification(path, chunklist_for(b"asset"))
            verifier._validate()
            self.assertEqual(verifier.status, ChunklistStatus.SUCCESS)


if __name__ == "__main__":
    unittest.main()
