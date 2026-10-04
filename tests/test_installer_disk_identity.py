"""Installer scripts quote arguments and recheck attachment identity before erase."""
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from opencore_legacy_patcher.support import macos_installer_handler as installer


BOOT_SESSION = '12345678-1234-5678-1234-567812345678'


def disk_info(**changes):
    return {'DeviceIdentifier': 'disk9', 'DeviceNode': '/dev/disk9', 'WholeDisk': True,
            'Internal': False, 'VirtualOrPhysical': 'Physical', 'TotalSize': 32 * 1024**3,
            'DeviceTreePath': 'IODeviceTree:/USB@1', 'IORegistryEntryName': 'USB Media',
            **changes}


def registry(entry_id=1234):
    return [{'BSD Name': 'disk9', 'Whole': True, 'IORegistryEntryID': entry_id,
             'IORegistryEntryName': 'USB Media'}]


class InstallerDiskIdentityTests(unittest.TestCase):
    def fixture(self, directory, name='Install macOS.app'):
        root = Path(directory)
        source = root / 'source' / name
        tool = source / 'Contents/Resources/createinstallmedia'
        tool.parent.mkdir(parents=True)
        tool.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARG_LOG"\n')
        tool.chmod(0o755)
        (source / 'Contents/Info.plist').write_bytes(plistlib.dumps({'DTPlatformVersion': '10.12'}))
        staging = root / 'stage'
        staging.mkdir()
        return root, source, staging

    def generate(self, root, source, staging, disk='disk9', info=None, entries=None, expected=None):
        info = disk_info() if info is None else info
        entries = registry() if entries is None else entries
        def run(args, **kwargs):
            if args[0] == '/usr/sbin/sysctl':
                return subprocess.CompletedProcess(args, 0, BOOT_SESSION.encode())
            if args[0] == '/usr/sbin/diskutil':
                return subprocess.CompletedProcess(args, 0, plistlib.dumps(info))
            if args[0] == '/usr/sbin/ioreg':
                return subprocess.CompletedProcess(args, 0, plistlib.dumps(entries))
            if args[0] == '/bin/cp':
                shutil.copytree(source, staging / source.name)
            return subprocess.CompletedProcess(args, 0)
        with mock.patch.object(installer, 'tmp_dir', SimpleNamespace(name=str(staging))), \
             mock.patch.object(installer, 'can_copy_on_write', return_value=True), \
             mock.patch.object(installer.subprocess, 'run', side_effect=run):
            return installer.InstallerCreation().generate_installer_creation_script(
                str(root), str(source), disk, **({'expected_identity': expected} if expected is not None else {}))

    def execute_fake(self, root, info=None, entries=None, fail_info=False, erase_result=0, boot_session=BOOT_SESSION):
        """Replace only trusted absolute tool paths in the generated test artifact."""
        (root / 'info.plist').write_bytes(plistlib.dumps(info if info is not None else disk_info()))
        (root / 'registry.plist').write_bytes(plistlib.dumps(entries if entries is not None else registry()))
        diskutil = root / 'fake-diskutil'
        diskutil.write_text('#!/bin/sh\nif [ "$1" = info ]; then\n' +
                            ('exit 1\n' if fail_info else f'/bin/cat {str(root / "info.plist")!r}\n') +
                            f'else\n/bin/echo "$*" >> {str(root / "erase.log")!r}\nexit {erase_result}\nfi\n')
        diskutil.chmod(0o755)
        ioreg = root / 'fake-ioreg'
        ioreg.write_text(f'#!/bin/sh\n/bin/cat {str(root / "registry.plist")!r}\n')
        ioreg.chmod(0o755)
        sysctl = root / 'fake-sysctl'
        sysctl.write_text(f'#!/bin/sh\n/bin/echo {boot_session!r}\n')
        sysctl.chmod(0o755)
        script = root / 'Installer.sh'
        script.write_text(script.read_text().replace('/usr/sbin/diskutil', str(diskutil)).replace('/usr/sbin/ioreg', str(ioreg)).replace('/usr/sbin/sysctl', str(sysctl)))
        return subprocess.run(['/bin/bash', str(script)], env={**os.environ, 'ARG_LOG': str(root / 'args.log')}, capture_output=True, text=True, cwd=root)

    def test_malicious_or_partition_disk_names_cannot_generate_script(self):
        for disk in ('disk9s1', 'disk9; touch /tmp/injected', 'disk9\n/bin/echo bad', '-disk9', 'disk', '/dev/rdisk9'):
            with self.subTest(disk=disk), tempfile.TemporaryDirectory() as directory:
                root, source, staging = self.fixture(directory)
                self.assertFalse(self.generate(root, source, staging, disk))
                self.assertFalse((root / 'Installer.sh').exists())

    def test_internal_virtual_or_nonwhole_devices_cannot_generate_script(self):
        for changes in ({'Internal': True}, {'WholeDisk': False}, {'VirtualOrPhysical': 'Virtual'},
                        {'DeviceIdentifier': 'disk10'}, {'DeviceNode': '/dev/disk10'}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                root, source, staging = self.fixture(directory)
                self.assertFalse(self.generate(root, source, staging, info=disk_info(**changes)))

    def test_missing_or_ambiguous_registry_identity_is_rejected(self):
        for entries in ([], [{'BSD Name': 'disk9', 'Whole': True}], registry() + registry(5678)):
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as directory:
                root, source, staging = self.fixture(directory)
                self.assertFalse(self.generate(root, source, staging, entries=entries))

    def test_tool_and_legacy_bundle_paths_survive_shell_metacharacters(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source, staging = self.fixture(directory, "Install ' $(touch INJECTED) `echo oops` macOS.app")
            self.assertTrue(self.generate(root, source, staging, '/dev/disk9'))
            result = self.execute_fake(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / 'erase.log').exists())
            arguments = (root / 'args.log').read_text().splitlines()
            self.assertEqual(arguments[-2:], ['--applicationpath', str(staging / source.name)])
            self.assertFalse((root / 'INJECTED').exists())

    def test_script_from_previous_boot_cannot_erase_even_if_registry_id_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source, staging = self.fixture(directory)
            self.assertTrue(self.generate(root, source, staging))
            result = self.execute_fake(root, boot_session='87654321-4321-8765-4321-876543218765')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / 'erase.log').exists())
            self.assertFalse((root / 'args.log').exists())

    def test_failed_erase_reports_failure_and_never_runs_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source, staging = self.fixture(directory)
            self.assertTrue(self.generate(root, source, staging))
            result = self.execute_fake(root, erase_result=5)
            self.assertEqual(result.returncode, 5)
            self.assertTrue((root / 'erase.log').exists())
            self.assertFalse((root / 'args.log').exists())

    def test_reassigned_or_missing_attachment_never_erases_or_runs_installer(self):
        for entries, changes, fail_info in ((registry(5678), {}, False), ([], {}, False),
                                             (registry(), {'Internal': True}, False),
                                             (registry(), {'WholeDisk': False}, False),
                                             (registry(), {'VirtualOrPhysical': 'Virtual'}, False),
                                             (registry(), {'TotalSize': 16 * 1024**3}, False),
                                             (registry(), {'DeviceTreePath': 'IODeviceTree:/different'}, False),
                                             (registry(), {}, True)):
            with self.subTest(entries=entries, changes=changes, fail_info=fail_info), tempfile.TemporaryDirectory() as directory:
                root, source, staging = self.fixture(directory)
                self.assertTrue(self.generate(root, source, staging))
                result = self.execute_fake(root, disk_info(**changes), entries, fail_info)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / 'erase.log').exists())
                self.assertFalse((root / 'args.log').exists())

    def test_disk_enumeration_exposes_only_verified_attachments_with_selection_identity(self):
        entries = registry() + [{'BSD Name': 'disk10', 'Whole': True, 'IORegistryEntryID': 5678,
                                 'IORegistryEntryName': 'USB Media'}]
        def run(arguments, **kwargs):
            if arguments[0] == '/usr/sbin/sysctl':
                return subprocess.CompletedProcess(arguments, 0, BOOT_SESSION.encode())
            if arguments[0] == '/usr/sbin/ioreg':
                output = entries
            elif arguments[1] == 'list':
                output = {'AllDisksAndPartitions': [{'DeviceIdentifier': 'disk9'}, {'DeviceIdentifier': 'disk10'}]}
            elif arguments[-1] == 'disk9':
                output = disk_info()
            else:
                output = disk_info(DeviceIdentifier='disk10', DeviceNode='/dev/disk10', WholeDisk=False)
            return subprocess.CompletedProcess(arguments, 0, plistlib.dumps(output))
        with mock.patch.object(installer.subprocess, 'run', side_effect=run):
            disks = installer.InstallerCreation().list_disk_to_format()
        self.assertEqual(set(disks), {'disk9'})
        self.assertEqual(disks['disk9']['identity']['registry_id'], 1234)
        self.assertEqual(disks['disk9']['identity']['device_identifier'], 'disk9')

    def test_identity_captured_at_selection_must_match_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source, staging = self.fixture(directory)
            self.assertFalse(self.generate(root, source, staging, expected={'registry_id': 9876}))


if __name__ == '__main__':
    unittest.main()
