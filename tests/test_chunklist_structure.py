"""Chunklists are strict consistency manifests, not publisher authentication."""

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from opencore_legacy_patcher.support.integrity_verification import ChunklistStatus, ChunklistVerification


def manifest(chunks, *, version=1, method=1, signature_method=1, header_size=36,
             count=None, chunk_offset=36, signature_offset=None, reserved=0):
    records = b"".join(struct.pack("<I", len(chunk)) + hashlib.sha256(chunk).digest() for chunk in chunks)
    signature_offset = min(chunk_offset + len(records), 2**64-1) if signature_offset is None else signature_offset
    header = b"CNKL" + struct.pack("<I4BQQQ", header_size, version, method, signature_method, reserved,
                                  len(chunks) if count is None else count, chunk_offset, signature_offset)
    signature_size = {1: 256, 3: 808}.get(signature_method, 256)
    return header + records + bytes(signature_size)


class ChunklistStructureTests(unittest.TestCase):
    def test_invalid_header_fields_are_rejected(self):
        fields = ({"header_size": 0}, {"header_size": 35}, {"header_size": 37},
                  {"version": 0}, {"version": 2}, {"method": 0}, {"method": 2},
                  {"signature_method": 0}, {"signature_method": 2}, {"reserved": 1},
                  {"chunk_offset": 0}, {"chunk_offset": 35}, {"chunk_offset": 2**64-1},
                  {"count": 2}, {"count": 2**64-1}, {"signature_offset": 0},
                  {"signature_offset": 71}, {"signature_offset": 2**64-1})
        for fields in fields:
            with self.subTest(fields=fields):
                verifier = ChunklistVerification("unused", manifest([b"asset"], **fields))
                self.assertEqual(verifier.status, ChunklistStatus.FAILURE)
                self.assertTrue(verifier.error_msg)

    def test_incomplete_records_and_signature_trailers_are_rejected(self):
        valid = manifest([b"asset"])
        for data in (valid[:36], valid[:71], valid[:72], valid[:-1], valid + b"extra"):
            with self.subTest(length=len(data)):
                self.assertEqual(ChunklistVerification("unused", data).status, ChunklistStatus.FAILURE)

    def test_zero_chunks_and_zero_length_chunks_are_rejected(self):
        for data in (manifest([]), manifest([b""])):
            with self.subTest(data=data):
                self.assertEqual(ChunklistVerification("unused", data).status, ChunklistStatus.FAILURE)

    def test_full_coverage_and_hashes_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset"
            for payload in (b"", b"asse", b"asset appended", b"other"):
                with self.subTest(payload=payload):
                    path.write_bytes(payload)
                    verifier = ChunklistVerification(path, manifest([b"asset"]))
                    verifier._validate()
                    self.assertEqual(verifier.status, ChunklistStatus.FAILURE)
                    self.assertTrue(verifier.error_msg)

    def test_exact_length_is_checked_even_with_matching_short_read_hash(self):
        data = bytearray(manifest([b"asset"]))
        data[36:40] = struct.pack("<I", 6)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset"
            path.write_bytes(b"asset")
            verifier = ChunklistVerification(path, bytes(data))
            verifier._validate()
            self.assertEqual(verifier.status, ChunklistStatus.FAILURE)

    def test_both_signature_formats_allow_only_consistency_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset"
            path.write_bytes(b"asset")
            for method in (1, 3):
                with self.subTest(method=method):
                    verifier = ChunklistVerification(path, manifest([b"as", b"set"], signature_method=method))
                    verifier._validate()
                    self.assertEqual(verifier.status, ChunklistStatus.SUCCESS)
                    self.assertEqual(getattr(verifier, "publisher_authenticated", None), False)
                    self.assertIn("consistency", getattr(verifier, "verification_scope", "").lower())


if __name__ == "__main__":
    unittest.main()
