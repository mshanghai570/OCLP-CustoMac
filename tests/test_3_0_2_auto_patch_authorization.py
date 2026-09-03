"""Deterministic Path 1 and Path 2 authorization regressions for CustoMac 3.0.2."""

from __future__ import annotations

import inspect
import threading
import types
import unittest

from unittest import mock

from opencore_legacy_patcher.sys_patch.auto_patcher import start as auto_patch_start
from opencore_legacy_patcher.sys_patch.patchsets import (
    HardwarePatchsetSettings,
    HardwarePatchsetValidation,
)
from opencore_legacy_patcher.wx_gui import (
    gui_cache_os_update,
    gui_sys_patch_display,
    gui_sys_patch_start,
)


class _ImmediateThread:
    """Run a worker inline so tests never depend on scheduling or real I/O."""

    def __init__(self, target, *args, **kwargs):
        self._target = target

    def start(self) -> None:
        self._target()

    def is_alive(self) -> bool:
        return False


class Path1AuthorizationTests(unittest.TestCase):
    @staticmethod
    def _authorization_frame():
        frame = gui_cache_os_update.OSUpdateFrame.__new__(gui_cache_os_update.OSUpdateFrame)
        frame.frame = mock.Mock()
        frame.constants = types.SimpleNamespace(patcher_name="OCLP-CustoMac")
        frame.os_data = ("26.7", "25H123")
        return frame

    def _dialog_result(self, result):
        frame = self._authorization_frame()
        dialog = mock.Mock()
        if isinstance(result, BaseException):
            dialog.ShowModal.side_effect = result
        else:
            dialog.ShowModal.return_value = result
        with mock.patch.object(
            gui_cache_os_update.wx,
            "MessageDialog",
            return_value=dialog,
        ) as message_dialog:
            authorized = frame._notifyUser()
        dialog.Destroy.assert_called_once_with()
        return authorized, dialog, message_dialog.call_args.kwargs["message"]

    def _run_update_frame(self, authorized: bool):
        parent = mock.Mock()
        download = types.SimpleNamespace(download_complete=True)
        kdk = mock.Mock(success=True)
        kdk.retrieve_download.return_value = download
        detection = mock.Mock()
        detection.device_properties = {
            HardwarePatchsetSettings.KERNEL_DEBUG_KIT_REQUIRED: True,
            HardwarePatchsetSettings.METALLIB_SUPPORT_PKG_REQUIRED: False,
        }

        with mock.patch.object(
            gui_cache_os_update.utilities,
            "fetch_staged_update",
            return_value=("26.7", "25H123"),
        ), mock.patch.object(
            gui_cache_os_update,
            "HardwarePatchsetDetection",
            return_value=detection,
        ), mock.patch.object(
            gui_cache_os_update.kdk_handler,
            "KernelDebugKitObject",
            return_value=kdk,
        ), mock.patch.object(
            gui_cache_os_update.threading,
            "Thread",
            _ImmediateThread,
        ), mock.patch.object(
            gui_cache_os_update.gui_support,
            "wait_for_thread",
        ), mock.patch.object(
            gui_cache_os_update.OSUpdateFrame,
            "_generate_ui",
        ), mock.patch.object(
            gui_cache_os_update.OSUpdateFrame,
            "_notifyUser",
            return_value=authorized,
        ), mock.patch.object(
            gui_cache_os_update.OSUpdateFrame,
            "_handle_kdk",
        ) as handle_kdk, mock.patch.object(
            gui_cache_os_update.OSUpdateFrame,
            "_exit",
        ) as exit_frame, mock.patch.object(
            gui_cache_os_update.gui_download,
            "DownloadFrame",
        ) as download_frame:
            gui_cache_os_update.OSUpdateFrame(
                parent=parent,
                title="OCLP-CustoMac",
                global_constants=types.SimpleNamespace(),
            )

        return download_frame, handle_kdk, exit_frame

    def test_dialog_waits_without_timeout_or_implicit_result(self) -> None:
        frame = self._authorization_frame()
        entered = threading.Event()
        release = threading.Event()
        results = []
        dialog = mock.Mock()

        def _blocking_modal():
            entered.set()
            release.wait()
            return gui_cache_os_update.wx.ID_NO

        dialog.ShowModal.side_effect = _blocking_modal
        with mock.patch.object(gui_cache_os_update.wx, "MessageDialog", return_value=dialog):
            worker = threading.Thread(target=lambda: results.append(frame._notifyUser()))
            worker.start()
            self.assertTrue(entered.wait(timeout=1))
            self.assertTrue(worker.is_alive())
            self.assertEqual(results, [])
            release.set()
            worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [False])

    def test_cancel_close_escape_unexpected_and_exception_fail_closed(self) -> None:
        for result in (
            gui_cache_os_update.wx.ID_NO,
            gui_cache_os_update.wx.ID_CANCEL,
            gui_cache_os_update.wx.ID_OK,
            -999,
            RuntimeError("dialog failed"),
        ):
            with self.subTest(result=result):
                authorized, _, _ = self._dialog_result(result)
                self.assertFalse(authorized)

    def test_only_exact_continue_is_authorized(self) -> None:
        authorized, dialog, _ = self._dialog_result(gui_cache_os_update.wx.ID_YES)
        self.assertTrue(authorized)
        dialog.SetYesNoLabels.assert_called_once_with("&Continue", "&Cancel")

    def test_warning_states_selection_and_kdk_limitations(self) -> None:
        _, _, message = self._dialog_result(gui_cache_os_update.wx.ID_NO)
        self.assertIn(
            "Warning: The automatic update workflow does not support selective Root Patching. "
            "On a clean system, Modern Wi-Fi and Modern Audio are both applied by default, "
            "using automatic KDK selection.",
            message,
        )
        self.assertIn(
            "Manual Wi-Fi/Audio selection and Manual KDK selection are not available.",
            message,
        )
        for required in (
            "Modern Wi-Fi",
            "Modern Audio",
            "automatic KDK selection",
            "Manual Wi-Fi/Audio selection",
            "Manual KDK selection",
        ):
            with self.subTest(required=required):
                self.assertIn(required, message)

    def test_cancel_stops_before_download_or_resource_handling(self) -> None:
        download_frame, handle_kdk, exit_frame = self._run_update_frame(False)
        download_frame.assert_not_called()
        handle_kdk.assert_not_called()
        exit_frame.assert_called_once_with()

    def test_continue_preserves_existing_download_and_kdk_handling(self) -> None:
        download_frame, handle_kdk, exit_frame = self._run_update_frame(True)
        download_frame.assert_called_once()
        handle_kdk.assert_called_once()
        exit_frame.assert_called_once_with()

    def test_authorization_boundary_precedes_download_frame(self) -> None:
        source = inspect.getsource(gui_cache_os_update.OSUpdateFrame.__init__)
        authorization = source.index("if self._notifyUser() is False:")
        download = source.index("gui_download.DownloadFrame(")
        self.assertLess(authorization, download)
        self.assertIn("self._exit()\n            return", source[authorization:download])

    def test_legacy_countdown_and_shared_result_state_are_removed(self) -> None:
        source = inspect.getsource(gui_cache_os_update.OSUpdateFrame)
        self.assertNotIn("did_cancel", source)
        self.assertNotIn("_notifyUserThread", source)
        self.assertNotIn("time.sleep", source)
        self.assertNotIn("range(0, 10)", source)
        self.assertNotIn("wx.CallLater", source)

    def test_cache_path_still_has_no_root_patch_executor(self) -> None:
        source = inspect.getsource(gui_cache_os_update)
        self.assertNotIn("PatchSysVolume", source)
        self.assertNotIn("start_patch(", source)


class Path2ReviewRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.constants = types.SimpleNamespace(
            wxpython_variant=True,
            patcher_name="OCLP-CustoMac",
            patcher_version="3.0.2",
            app_icon_path="/tmp/test-icon.icns",
            special_build=False,
        )
        self.patches = {
            "Networking: Modern Wireless": True,
            "Miscellaneous: Modern Audio": True,
            HardwarePatchsetValidation.PATCHING_NOT_POSSIBLE: False,
        }

    def _run_auto_patch(
        self,
        returncode: int,
        subprocess_side_effect=None,
        network_available: bool = True,
    ):
        detection = mock.Mock(device_properties=self.patches)
        entrypoint = mock.Mock()
        network = mock.Mock()
        network.verify_network_connection.return_value = network_available
        run_result = types.SimpleNamespace(returncode=returncode)

        with mock.patch.object(
            auto_patch_start.updates,
            "CheckBinaryUpdates",
        ) as update_checker, mock.patch.object(
            auto_patch_start.utilities,
            "check_seal",
            return_value=True,
        ), mock.patch.object(
            auto_patch_start,
            "HardwarePatchsetDetection",
            return_value=detection,
        ), mock.patch.object(
            auto_patch_start.network_handler,
            "NetworkUtilities",
            return_value=network,
        ), mock.patch.object(
            auto_patch_start.subprocess,
            "run",
            side_effect=subprocess_side_effect,
            return_value=run_result,
        ) as run, mock.patch.object(
            auto_patch_start.gui_entry,
            "EntryPoint",
            return_value=entrypoint,
        ) as entrypoint_type:
            update_checker.return_value.check_binary_updates.return_value = None
            auto_patch_start.StartAutomaticPatching(self.constants).start_auto_patch()

        return run, entrypoint_type, entrypoint

    def test_missing_patches_still_prompt_with_review_action(self) -> None:
        run, _, _ = self._run_auto_patch(returncode=1)
        run.assert_called_once()
        script = run.call_args.args[0][2]
        self.assertIn(
            "OCLP-CustoMac has detected that your system is currently running without Root Patches.\n\n"
            "Would you like to review the available Root Patches and select which ones to apply?",
            script,
        )
        self.assertIn('buttons {"Cancel", "Review Root Patches"}', script)
        self.assertIn('default button "Review Root Patches"', script)
        self.assertIn('cancel button "Cancel"', script)
        self.assertNotIn("macOS wipes all root patches", script)
        self.assertNotIn("Following Patches have been detected", script)
        self.assertNotIn("- Networking: Modern Wireless", script)
        self.assertNotIn("- Miscellaneous: Modern Audio", script)

    def test_no_network_safety_warning_remains_after_review_question(self) -> None:
        run, _, _ = self._run_auto_patch(returncode=1, network_available=False)
        script = run.call_args.args[0][2]
        question = "Would you like to review the available Root Patches and select which ones to apply?"
        warning = "WARNING: We're unable to verify whether there are any new releases of OCLP-CustoMac on GitHub."
        self.assertIn(warning, script)
        self.assertLess(script.index(question), script.index(warning))

    def test_cancel_exits_without_opening_patch_gui(self) -> None:
        _, entrypoint_type, _ = self._run_auto_patch(returncode=1)
        entrypoint_type.assert_not_called()

    def test_no_response_blocks_without_opening_patch_gui(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        result_holder = []

        def _blocking_run(*args, **kwargs):
            entered.set()
            release.wait()
            return types.SimpleNamespace(returncode=1)

        def _invoke():
            result_holder.append(self._run_auto_patch(1, subprocess_side_effect=_blocking_run))

        worker = threading.Thread(target=_invoke)
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        self.assertTrue(worker.is_alive())
        release.set()
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        _, entrypoint_type, _ = result_holder[0]
        entrypoint_type.assert_not_called()

    def test_review_opens_normal_selection_exactly_once(self) -> None:
        _, entrypoint_type, entrypoint = self._run_auto_patch(returncode=0)
        entrypoint_type.assert_called_once_with(self.constants)
        entrypoint.start.assert_called_once_with(entry=gui_sys_patch_display.SysPatchDisplayFrame)

    def test_review_has_no_automatic_start_handoff(self) -> None:
        source = inspect.getsource(auto_patch_start.StartAutomaticPatching.start_auto_patch)
        self.assertNotIn("start_patching=True", source)
        self.assertNotIn("SupportedEntryPoints.SYS_PATCH", source)

    def test_review_target_is_the_normal_custo_mac_selection_frame(self) -> None:
        self.assertIs(
            auto_patch_start.gui_sys_patch_display.SysPatchDisplayFrame,
            gui_sys_patch_display.SysPatchDisplayFrame,
        )
        display_source = inspect.getsource(gui_sys_patch_display.SysPatchDisplayFrame)
        self.assertIn('label="Start Root Patching"', display_source)
        self.assertIn("ManualKDKSelectionState", display_source)
        self.assertIn("RootPatchSelection.initialize", display_source)

    def test_closing_selection_without_start_has_no_patch_handoff(self) -> None:
        display = types.SimpleNamespace(frame_modal=mock.Mock())
        with mock.patch.object(
            gui_sys_patch_display.gui_sys_patch_start,
            "SysPatchStartFrame",
        ) as start_frame:
            gui_sys_patch_display.SysPatchDisplayFrame.on_return_dismiss(display)
        start_frame.assert_not_called()
        display.frame_modal.Hide.assert_called_once_with()
        display.frame_modal.Destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
