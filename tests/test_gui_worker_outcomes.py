import logging
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.wx_gui import gui_build, gui_install_oc, gui_support, gui_update, gui_sys_patch_start


class GUIWorkerOutcomeTests(unittest.TestCase):
    def test_worker_result_and_exception_are_observed_by_waiter(self):
        self.assertTrue(hasattr(gui_support, "ResultThread"))
        with mock.patch.object(gui_support.wx, "Yield"):
            thread = gui_support.ResultThread(target=lambda: False)
            thread.start()
            self.assertIs(gui_support.wait_for_thread(thread), False)
            def fail():
                raise RuntimeError("worker failed")
            thread = gui_support.ResultThread(target=fail)
            thread.start()
            with self.assertRaisesRegex(RuntimeError, "worker failed"):
                gui_support.wait_for_thread(thread)

    def test_update_extraction_failure_stops_the_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            frame = SimpleNamespace(url="https://example.invalid/update.zip", pkg_download_path=Path(directory) / "OpenCore-Patcher.pkg", constants=SimpleNamespace(payload_path=Path(directory)), progress_bar_animation=mock.Mock(), progress_bar=mock.Mock())
            with mock.patch.object(gui_update.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"bad zip")), mock.patch.object(gui_update.wx, "CallAfter") as callback:
                with self.assertRaises(RuntimeError):
                    gui_update.UpdateFrame._extract_update(frame)
            callback.assert_not_called()

    def test_update_failure_never_opens_an_unverified_package(self):
        frame = SimpleNamespace(pkg_download_path=Path("/tmp/update.pkg"), url="https://example.invalid/update.pkg")
        with mock.patch.object(gui_update.package_trust, "install_verified_package", side_effect=RuntimeError("untrusted")), mock.patch.object(gui_update.subprocess, "run") as run:
            with self.assertRaises(RuntimeError):
                gui_update.UpdateFrame._install_update(frame)
        run.assert_not_called()

    def test_root_worker_removes_only_its_handler_even_on_exception(self):
        frame = SimpleNamespace(text_box=mock.Mock(), constants=SimpleNamespace(computer=SimpleNamespace(real_model="MacPro")), patch_selection={}, expected_patch_selection=None, manual_kdk_candidate=None)
        logger = logging.getLogger()
        original = list(logger.handlers)
        with mock.patch.object(gui_sys_patch_start.sys_patch, "PatchSysVolume") as patcher:
            patcher.return_value.start_patch.side_effect = RuntimeError("patch failed")
            with self.assertRaises(RuntimeError):
                gui_sys_patch_start.SysPatchStartFrame._start_root_patching(frame, {})
        self.assertEqual(logger.handlers, original)

    def test_build_and_install_workers_remove_only_their_handler(self):
        logger = logging.getLogger()
        preexisting = logging.NullHandler()
        logger.addHandler(preexisting)
        self.addCleanup(logger.removeHandler, preexisting)
        original = list(logger.handlers)

        build_frame = SimpleNamespace(constants=SimpleNamespace(custom_model=None, computer=SimpleNamespace(real_model="MacPro")),
                                      text_box=mock.Mock(), build_successful=False)
        with mock.patch.object(gui_support.wx, "CallAfter"), \
             mock.patch.object(gui_build.build, "BuildOpenCore", side_effect=RuntimeError("build failed")):
            gui_build.BuildFrame._build(build_frame)
        self.assertFalse(build_frame.build_successful)
        self.assertEqual(logger.handlers, original)

        install_frame = SimpleNamespace(constants=SimpleNamespace(), text_box=mock.Mock(), result=True)
        with mock.patch.object(gui_support.wx, "CallAfter"), \
             mock.patch.object(gui_install_oc.install, "tui_disk_installation", side_effect=RuntimeError("install failed")):
            gui_install_oc.InstallOCFrame._install_oc(install_frame, {"disk": "disk2"})
        self.assertIs(install_frame.result, False)
        self.assertEqual(logger.handlers, original)

    def test_queued_logging_ignores_closed_handler(self):
        box = mock.Mock()
        handler = gui_support.ThreadHandler(box)
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "text", (), None)
        with mock.patch.object(gui_support.wx, "CallAfter") as callback:
            handler.emit(record)
            handler.close()
            call = callback.call_args
            call.args[0](*call.args[1:])
        box.AppendText.assert_not_called()

