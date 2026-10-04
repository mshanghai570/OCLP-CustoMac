"""The bundle's runtime floor must describe actual Mach-O dependencies."""
import os
import plistlib
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from ci_tooling.build_modules.application import GenerateApplication


def thin(minimum=0x000D0000, sdk=0x000C0100, endian='<', build=True, payload=b''):
    command = struct.pack(endian + ('IIIIII' if build else 'IIII'),
                          *([0x32, 24, 1, minimum, sdk, 0] if build else [0x24, 16, minimum, sdk]))
    return struct.pack(endian + 'IIIIIIII', 0xFEEDFACF, 0x01000007, 3, 2, 1, len(command), 0, 0) + command + payload


def fat(slices, endian='>', wide=False):
    width = 32 if wide else 20
    offset = 8 + width * len(slices)
    entries = []
    body = b''
    for item in slices:
        entries.append(struct.pack(endian + ('IIQQII' if wide else 'IIIII'),
                                   *([0x01000007, 3, offset, len(item), 0, 0] if wide else [0x01000007, 3, offset, len(item), 0])))
        offset += len(item)
        body += item
    return struct.pack(endian+'II', 0xCAFEBABF if wide else 0xCAFEBABE, len(slices)) + b''.join(entries) + body


class ApplicationMetadataTests(unittest.TestCase):
    def fixture(self, root, binary):
        builder = GenerateApplication()
        builder._application_output = root / 'Patcher.app'
        contents = builder._application_output / 'Contents'
        executable = contents / 'MacOS/OpenCore-Patcher'
        executable.parent.mkdir(parents=True)
        executable.write_bytes(binary)
        executable.chmod(0o755)
        info = contents / 'Info.plist'
        info.write_bytes(plistlib.dumps({'LSMinimumSystemVersion': '10.10.0', 'Keep': 'unchanged'}))
        return builder, executable, info

    def test_load_command_update_preserves_binary_and_uses_packaged_dependency_floor(self):
        binary = thin(0x000A0D00, build=False, payload=b'\x00\x0d\x0a\x00')
        with tempfile.TemporaryDirectory() as directory:
            builder, executable, info = self.fixture(Path(directory), binary)
            dependency = info.parent / 'Frameworks/Python'
            dependency.parent.mkdir()
            dependency.write_bytes(thin(0x000A0F00))
            builder._update_runtime_minimum()
            self.assertEqual(executable.read_bytes(), binary)
            self.assertEqual(plistlib.loads(info.read_bytes())['LSMinimumSystemVersion'], '10.15.0')

    def test_all_fat_slices_and_endianness_contribute_to_runtime_floor(self):
        for wide in (False, True):
            for endian in ('<', '>'):
                with self.subTest(wide=wide, endian=endian), tempfile.TemporaryDirectory() as directory:
                    binary = fat([thin(0x000B0000), thin(0x000E0401, endian='>')], endian, wide)
                    builder, executable, info = self.fixture(Path(directory), binary)
                    builder._update_runtime_minimum()
                    self.assertEqual(executable.read_bytes(), binary)
                    self.assertEqual(plistlib.loads(info.read_bytes())['LSMinimumSystemVersion'], '14.4.1')

    def test_32_bit_macho_commands_are_read_without_rewriting_binary(self):
        command = struct.pack('<IIII', 0x24, 16, 0x000A0C00, 0x000C0100)
        binary = struct.pack('<IIIIIII', 0xFEEDFACE, 7, 3, 2, 1, len(command), 0) + command
        with tempfile.TemporaryDirectory() as directory:
            builder, executable, info = self.fixture(Path(directory), binary)
            builder._update_runtime_minimum()
            self.assertEqual(executable.read_bytes(), binary)
            self.assertEqual(plistlib.loads(info.read_bytes())['LSMinimumSystemVersion'], '10.12.0')

    def test_sdk_override_does_not_rewrite_matching_payload_bytes(self):
        binary = thin(payload=b'\x00\x01\x0c\x00')
        with tempfile.TemporaryDirectory() as directory:
            builder, executable, _ = self.fixture(Path(directory), binary)
            builder._update_runtime_minimum()
            self.assertEqual(executable.read_bytes(), binary)

    def test_malformed_headers_and_load_commands_preserve_original_files(self):
        good = thin()
        malformed = [b'not MachO', good[:25], good[:32] + struct.pack('<II', 0x32, 4096),
                     good[:32] + struct.pack('<IIIIII', 0x32, 8, 1, 0, 0, 0),
                     fat([good])[:20], fat([good])[:-1]]
        for binary in malformed:
            with self.subTest(binary=binary[:12]), tempfile.TemporaryDirectory() as directory:
                builder, executable, info = self.fixture(Path(directory), binary)
                original = info.read_bytes()
                with self.assertRaises(ValueError):
                    builder._update_runtime_minimum()
                self.assertEqual(executable.read_bytes(), binary)
                self.assertEqual(info.read_bytes(), original)

    def test_atomic_metadata_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            builder, executable, info = self.fixture(Path(directory), thin())
            original = info.read_bytes()
            with mock.patch('os.replace', side_effect=OSError('replace failed'), create=True):
                with self.assertRaises(OSError):
                    builder._update_runtime_minimum()
            self.assertEqual(info.read_bytes(), original)
            self.assertEqual(executable.stat().st_mode & 0o777, 0o755)


if __name__ == '__main__':
    unittest.main()
