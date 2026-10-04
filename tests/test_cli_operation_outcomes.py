"""Failed and exceptional root work must be visible to package automation."""

import unittest
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.support import arguments
from ci_tooling.build_modules.package_scripts import ZSHFunctions


class CLIOperationOutcomeTests(unittest.TestCase):
    def handler(self, sandbox=False):
        handler = arguments.arguments.__new__(arguments.arguments)
        handler.constants = SimpleNamespace(
            custom_model=None, computer=SimpleNamespace(real_model="Test"),
            payload_path=Path("/Library/InstallerSandboxes/test" if sandbox else "/tmp/payload"),
            root_patcher_succeeded=False,
        )
        return handler

    def test_failed_patch_exits_nonzero_including_installer_sandbox(self):
        for sandbox in (False, True):
            with self.subTest(sandbox=sandbox):
                handler = self.handler(sandbox)
                with mock.patch.object(arguments.sys_patch, "PatchSysVolume") as patcher, \
                     mock.patch.object(arguments.utilities, "block_os_updaters"):
                    patcher.return_value.start_patch.return_value = False
                    with self.assertRaises(SystemExit) as failure:
                        handler._sys_patch_handler()
                    self.assertEqual(failure.exception.code, 1)

    def test_worker_exception_exits_nonzero_including_installer_sandbox(self):
        for sandbox in (False, True):
            with self.subTest(sandbox=sandbox):
                with mock.patch.object(arguments.sys_patch, "PatchSysVolume") as patcher, \
                     mock.patch.object(arguments.utilities, "block_os_updaters"):
                    patcher.return_value.start_patch.side_effect = RuntimeError("cache rebuild failed")
                    with self.assertRaises(SystemExit) as failure:
                        self.handler(sandbox)._sys_patch_handler()
                    self.assertEqual(failure.exception.code, 1)

    def test_successful_patch_returns_normally(self):
        with mock.patch.object(arguments.sys_patch, "PatchSysVolume") as patcher:
            patcher.return_value.start_patch.return_value = True
            self.handler()._sys_patch_handler()

    def test_failed_unpatch_exits_nonzero(self):
        with mock.patch.object(arguments.sys_patch, "PatchSysVolume") as patcher:
            patcher.return_value.start_unpatch.return_value = False
            with self.assertRaises(SystemExit) as failure:
                self.handler()._sys_unpatch_handler()
            self.assertEqual(failure.exception.code, 1)

    def test_autopkg_returns_before_reboot_when_patching_fails(self):
        script = ZSHFunctions().generate_postinstall_main(is_autopkg=True)
        harmless_functions = '\n'.join((
            '_setSUIDBit() { :; }', '_createAlias() { :; }', '_prewarmGatekeeper() { :; }',
            '_fixSettingsFilePermission() { :; }', '_reboot() { echo REBOOT_REACHED; }',
        ))
        for patch_status in (0, 1):
            with self.subTest(patch_status=patch_status):
                program = harmless_functions + f'\n_startPatching() {{ return {patch_status}; }}\n' + script + '\n_main\n'
                result = subprocess.run(['/bin/zsh', '--no-rcs', '-c', program], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, patch_status, result.stderr)
                self.assertEqual('REBOOT_REACHED' in result.stdout, patch_status == 0)


if __name__ == "__main__":
    unittest.main()
