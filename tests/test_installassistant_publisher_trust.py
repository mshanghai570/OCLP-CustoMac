"""InstallAssistant extraction must use the staged Apple publisher boundary."""
import subprocess
import unittest
from pathlib import Path
from unittest import mock
from opencore_legacy_patcher.support import macos_installer_handler as installer


class InstallAssistantPublisherTests(unittest.TestCase):
    def test_untrusted_package_cannot_invoke_privileged_installer(self):
        with mock.patch.object(installer, 'install_verified_package', create=True, side_effect=installer.PackageTrustError('wrong publisher')) as install, \
             mock.patch.object(installer.subprocess_wrapper, 'run_as_root', return_value=subprocess.CompletedProcess([], 0)) as root:
            self.assertFalse(installer.InstallerCreation().install_macOS_installer('/temporary/download'))
            root.assert_not_called()
            install.assert_called_once()

    def test_trusted_apple_package_uses_staged_install_result(self):
        with mock.patch.object(installer, 'install_verified_package', create=True, return_value=subprocess.CompletedProcess([], 0)) as install, \
             mock.patch.object(installer.subprocess_wrapper, 'run_as_root', return_value=subprocess.CompletedProcess([], 0)) as root:
            self.assertTrue(installer.InstallerCreation().install_macOS_installer('/temporary/download'))
            root.assert_not_called()
            install.assert_called_once_with(Path('/temporary/download/InstallAssistant.pkg'), installer.Publisher.APPLE)

    def test_failure_from_staged_installer_is_reported(self):
        with mock.patch.object(installer, 'install_verified_package', create=True, return_value=subprocess.CompletedProcess([], 1, b'failure', b'failure')), \
             mock.patch.object(installer.subprocess_wrapper, 'run_as_root', return_value=subprocess.CompletedProcess([], 0)):
            self.assertFalse(installer.InstallerCreation().install_macOS_installer('/temporary/download'))


if __name__ == '__main__':
    unittest.main()
