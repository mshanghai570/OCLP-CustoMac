"""Overlapping operations must retain their own sleep inhibition lifetime."""

import unittest
import gc
import tempfile
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.support import utilities, network_handler
from opencore_legacy_patcher.wx_gui import gui_macos_installer_flash


class SleepOperationOwnershipTests(unittest.TestCase):
    def test_finishing_one_of_two_operations_keeps_sleep_inhibited(self):
        with mock.patch.object(utilities, "sleep_process", None), \
             mock.patch.object(utilities, "_sleep_users", 0, create=True), \
             mock.patch.object(utilities.subprocess, "Popen") as process, \
             mock.patch.object(utilities.atexit, "register") as register, \
             mock.patch.object(utilities.atexit, "unregister"):
            utilities.disable_sleep_while_running()
            utilities.disable_sleep_while_running()
            utilities.enable_sleep_after_running()
            process.return_value.kill.assert_not_called()
            utilities.enable_sleep_after_running()
            process.assert_called_once()
            process.return_value.kill.assert_called_once()
            register.assert_called_once()

    def test_flashing_releases_sleep_even_when_worker_raises(self):
        frame = SimpleNamespace(_flash_installer_awake=mock.Mock(side_effect=RuntimeError("flash failed")))
        with mock.patch.object(utilities, "disable_sleep_while_running") as acquire, \
             mock.patch.object(utilities, "enable_sleep_after_running") as release:
            with self.assertRaises(RuntimeError):
                gui_macos_installer_flash.macOSInstallerFlashFrame._flash_installer(frame, {})
        acquire.assert_called_once()
        release.assert_called_once()

    def test_download_cancellation_joins_with_a_bound(self):
        download = network_handler.DownloadObject.__new__(network_handler.DownloadObject)
        download.active_thread = mock.Mock()
        download.active_thread.is_alive.side_effect = [True, False]
        download.should_stop = False
        download.stop()
        self.assertTrue(download.should_stop)
        download.active_thread.join.assert_called_once_with(timeout=12)
        download.active_thread = None

    def test_completed_downloads_are_not_retained_by_exit_callbacks(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(network_handler.NetworkUtilities, "verify_network_connection", return_value=True), \
             mock.patch.object(network_handler.DownloadObject, "_populate_file_size"), \
             mock.patch.object(network_handler.NetworkUtilities, "get") as get, \
             mock.patch.object(utilities, "disable_sleep_while_running"), \
             mock.patch.object(utilities, "enable_sleep_after_running"):
            get.return_value.iter_content.return_value = [b"asset"]
            references = []
            for index in range(3):
                download = network_handler.DownloadObject("https://example.invalid/asset", Path(directory) / str(index))
                download._download()
                self.assertTrue(download.download_complete)
                references.append(weakref.ref(download))
                del download
            gc.collect()
            self.assertTrue(all(reference() is None for reference in references))


if __name__ == "__main__":
    unittest.main()
