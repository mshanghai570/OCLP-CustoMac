"""Private settings remain intact under concurrency and interrupted updates."""

import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.support import global_settings


class GlobalSettingsSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "private" / "settings.plist"
        self.addCleanup(self.temporary.cleanup)

    def settings(self, **kwargs):
        return global_settings.GlobalEnviromentSettings(settings_path=self.path, legacy_paths=(), **kwargs)

    def test_private_storage_preserves_gui_values(self):
        settings = self.settings()
        settings.write_property("GUI:oc_timeout", 8)
        settings.write_property("GUI:sip_status", False)
        restored = self.settings()
        self.assertEqual(restored.read_settings()["GUI:oc_timeout"], 8)
        self.assertEqual(restored.read_property("GUI:oc_timeout"), 8)
        self.assertIs(restored.read_property("GUI:sip_status"), False)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        restored.delete_property("GUI:oc_timeout")
        self.assertIsNone(restored.read_property("GUI:oc_timeout"))

    def test_default_path_uses_effective_account_home_not_environment(self):
        account_home = self.root / "root-home"
        with mock.patch.object(global_settings.os, "geteuid", return_value=os.geteuid()), \
             mock.patch.object(global_settings.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(account_home))), \
             mock.patch.dict(os.environ, {"HOME": str(self.root / "untrusted-home"), "SUDO_USER": "other"}):
            settings = global_settings.GlobalEnviromentSettings(legacy_paths=())
        self.assertTrue(Path(settings.global_settings_plist).is_relative_to(account_home))
        self.assertFalse((self.root / "untrusted-home").exists())

    def test_trusted_legacy_migration_is_read_only_and_runs_once(self):
        legacy = self.root / "legacy.plist"
        legacy.write_bytes(plistlib.dumps({"GUI:oc_timeout": 9}))
        legacy.chmod(0o600)
        before = legacy.read_bytes()
        settings = global_settings.GlobalEnviromentSettings(settings_path=self.path, legacy_paths=(legacy,))
        self.assertEqual(settings.read_property("GUI:oc_timeout"), 9)
        settings.write_property("GUI:oc_timeout", 12)
        restored = global_settings.GlobalEnviromentSettings(settings_path=self.path, legacy_paths=(legacy,))
        self.assertEqual(restored.read_property("GUI:oc_timeout"), 12)
        self.assertEqual(legacy.read_bytes(), before)

    def test_world_writable_legacy_and_symlinks_are_not_imported(self):
        legacy = self.root / "legacy.plist"
        legacy.write_bytes(plistlib.dumps({"GUI:oc_timeout": 99}))
        legacy.chmod(0o666)
        link = self.root / "legacy-link.plist"
        link.symlink_to(legacy)
        settings = global_settings.GlobalEnviromentSettings(settings_path=self.path, legacy_paths=(legacy, link))
        self.assertIsNone(settings.read_property("GUI:oc_timeout"))

    def test_settings_and_lock_symlinks_do_not_modify_targets(self):
        settings = self.settings()
        target = self.root / "target.plist"
        target.write_bytes(plistlib.dumps({"sentinel": True}))
        before = target.read_bytes()
        self.path.unlink()
        self.path.symlink_to(target)
        settings.write_property("bad", True)
        self.assertIsNone(settings.read_property("sentinel"))
        self.assertEqual(settings.read_settings(), {})
        self.assertEqual(target.read_bytes(), before)
        self.path.unlink()
        settings = self.settings()
        lock = self.path.with_name(self.path.name + ".lock")
        lock.unlink()
        lock.symlink_to(target)
        settings.write_property("bad", True)
        self.assertEqual(target.read_bytes(), before)
        self.assertNotIn("bad", plistlib.loads(self.path.read_bytes()))

    def test_parent_symlink_is_rejected(self):
        destination = self.root / "destination"
        destination.mkdir(mode=0o700)
        self.path.parent.symlink_to(destination)
        self.settings()
        self.assertEqual(list(destination.iterdir()), [])

    def test_serialization_failure_preserves_original_and_cleans_temp_files(self):
        settings = self.settings()
        settings.write_property("keep", True)
        before = self.path.read_bytes()
        settings.write_property("invalid", object())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["settings.plist", "settings.plist.lock"])

    def test_interrupted_replace_preserves_original_and_cleans_temp_files(self):
        settings = self.settings()
        settings.write_property("keep", True)
        before = self.path.read_bytes()
        with mock.patch.object(global_settings.os, "replace", side_effect=OSError("simulated interruption")):
            settings.write_property("new", True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["settings.plist", "settings.plist.lock"])

    def test_process_exit_before_replace_keeps_previous_valid_plist(self):
        settings = self.settings()
        settings.write_property("keep", True)
        before = self.path.read_bytes()
        program = """
import os, sys
from pathlib import Path
from opencore_legacy_patcher.support import global_settings
settings = global_settings.GlobalEnviromentSettings(settings_path=Path(sys.argv[1]), legacy_paths=())
global_settings.os.replace = lambda *args, **kwargs: os._exit(73)
settings.write_property('interrupted', True)
"""
        child = subprocess.run([sys.executable, "-c", program, str(self.path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(child.returncode, 73, child.stderr)
        self.assertEqual(self.path.read_bytes(), before)
        abandoned = list(self.path.parent.glob("*.tmp"))
        self.assertEqual(len(abandoned), 1)
        self.assertEqual(stat.S_IMODE(abandoned[0].stat().st_mode), 0o600)
        settings.write_property("after_restart", True)
        self.assertTrue(settings.read_property("after_restart"))
        self.assertIsNone(settings.read_property("interrupted"))

    def test_malformed_settings_do_not_crash_or_overwrite_file(self):
        settings = self.settings()
        invalid = b"<?xml version='1.0'?><plist><dict>"
        self.path.write_bytes(invalid)
        self.assertIsNone(settings.read_property("any"))
        settings.write_property("bad", True)
        self.settings()
        self.assertEqual(self.path.read_bytes(), invalid)

    def test_root_migration_rejects_other_account_owned_file(self):
        legacy = self.root / "legacy.plist"
        legacy.write_bytes(plistlib.dumps({"authority": True}))
        legacy.chmod(0o600)
        settings = self.settings()
        real_fstat = os.fstat
        def untrusted_file(fd):
            value = real_fstat(fd)
            if stat.S_ISREG(value.st_mode):
                return SimpleNamespace(st_uid=501, st_mode=value.st_mode, st_nlink=value.st_nlink)
            return SimpleNamespace(st_uid=0, st_mode=value.st_mode, st_nlink=value.st_nlink)
        settings._legacy_paths = (legacy,)
        with mock.patch.object(global_settings.os, "geteuid", return_value=0), \
             mock.patch.object(global_settings.os, "fstat", side_effect=untrusted_file):
            self.assertEqual(settings._legacy_settings(), {})

    def test_unprotected_or_hardlinked_storage_is_rejected(self):
        settings = self.settings()
        settings.write_property("keep", True)
        before = self.path.read_bytes()
        self.path.chmod(0o666)
        settings.write_property("bad", True)
        self.assertEqual(self.path.read_bytes(), before)
        self.path.chmod(0o600)
        os.link(self.path, self.root / "hardlink.plist")
        settings.write_property("bad", True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_cross_process_updates_do_not_lose_properties(self):
        settings = self.settings()
        program = """
import sys
from pathlib import Path
from opencore_legacy_patcher.support.global_settings import GlobalEnviromentSettings
settings = GlobalEnviromentSettings(settings_path=Path(sys.argv[1]), legacy_paths=())
print('ready', flush=True)
sys.stdin.readline()
for i in range(20):
    settings.write_property(sys.argv[2] + str(i), i)
"""
        children = [subprocess.Popen([sys.executable, "-c", program, str(self.path), prefix], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for prefix in ("first", "second", "third")]
        try:
            for child in children:
                self.assertEqual(child.stdout.readline().strip(), "ready")
            for child in children:
                child.stdin.write("go\n")
                child.stdin.flush()
            for child in children:
                child.wait(timeout=30)
                self.assertEqual(child.returncode, 0, child.stderr.read())
            actual = plistlib.loads(self.path.read_bytes())
            for prefix in ("first", "second", "third"):
                for i in range(20):
                    self.assertEqual(actual[prefix + str(i)], i)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=10)
                child.stdin.close()
                child.stdout.close()
                child.stderr.close()
