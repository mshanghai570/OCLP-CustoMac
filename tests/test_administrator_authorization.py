"""Personal builds must ask macOS for authorization and never invoke a setuid broker."""

import base64
import io
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from opencore_legacy_patcher.support import subprocess_wrapper as wrapper
from ci_tooling.build_modules import package, package_scripts


def result_envelope(status, out=b"", err=b""):
    return b"OCLP_AUTH_V1:" + str(status).encode() + b"\n" + base64.b64encode(out) + b"\nOCLP_AUTH_STDERR\n" + base64.b64encode(err) + b"\n"


class AdministratorAuthorizationTests(unittest.TestCase):
    def test_nonroot_command_uses_apple_authorization_and_preserves_binary_output(self):
        result = subprocess.CompletedProcess([], 0, result_envelope(23, b"out\xff\n", b"err\x00\n"), b"")
        with mock.patch.object(os, "geteuid", return_value=501), mock.patch.object(wrapper.subprocess, "run", return_value=result) as run:
            actual = wrapper.run_as_root(["/bin/echo", "literal $() `x` \"quoted\""], capture_output=True)
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/osascript")
        self.assertEqual(actual.returncode, 23)
        self.assertEqual(actual.stdout, b"out\xff\n")
        self.assertEqual(actual.stderr, b"err\x00\n")

    def test_already_privileged_automation_executes_directly(self):
        with mock.patch.object(os, "geteuid", return_value=0), mock.patch.object(wrapper.subprocess, "run") as run:
            wrapper.run_as_root(["/bin/echo", "value"], capture_output=True)
        self.assertEqual(run.call_args.args[0], ["/bin/echo", "value"])

    def test_cancelled_authorization_does_not_run_a_helper(self):
        cancelled = subprocess.CompletedProcess([], 1, b"", b"User canceled (-128)")
        with mock.patch.object(os, "geteuid", return_value=501), mock.patch.object(wrapper.subprocess, "run", return_value=cancelled) as run:
            result = wrapper.run_as_root(["/bin/echo", "value"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/osascript")

    def test_cancelled_authorization_preserves_combined_diagnostic(self):
        cancelled = subprocess.CompletedProcess([], 1, b"", b"User canceled (-128)")
        with mock.patch.object(os, "geteuid", return_value=501), mock.patch.object(wrapper.subprocess, "run", return_value=cancelled):
            result = wrapper.run_as_root(["/bin/echo", "value"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"User canceled (-128)")
        self.assertIsNone(result.stderr)

    def test_malformed_authorization_preserves_combined_diagnostic(self):
        for frame in (b"malformed", result_envelope(-1), result_envelope(256),
                      b"OCLP_AUTH_V1:0\n!!!!\nOCLP_AUTH_STDERR\n\n",
                      result_envelope(0) + b"\nOCLP_AUTH_STDERR\n"):
            with self.subTest(frame=frame):
                process = subprocess.CompletedProcess([], 0, frame, b"")
                with mock.patch.object(os, "geteuid", return_value=501), mock.patch.object(wrapper.subprocess, "run", return_value=process):
                    result = wrapper.run_as_root(["/bin/echo", "value"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 1)
                self.assertIn(b"Invalid authorization response", result.stdout)
                self.assertIsNone(result.stderr)

    def test_windowed_application_can_inherit_output_without_console_streams(self):
        for text in (False, True):
            with self.subTest(text=text):
                process = subprocess.CompletedProcess([], 0, result_envelope(7, b"output\n", b"error\n"), b"")
                with mock.patch.object(os, "geteuid", return_value=501), \
                     mock.patch.object(wrapper.subprocess, "run", return_value=process), \
                     mock.patch.object(wrapper.sys, "stdout", None), \
                     mock.patch.object(wrapper.sys, "stderr", None):
                    try:
                        result = wrapper.run_as_root(["/bin/echo", "value"], text=text)
                    except AttributeError as error:
                        self.fail(f"Windowed application lost the command result: {error}")
                self.assertEqual(result.returncode, 7)
                self.assertIsNone(result.stdout)
                self.assertIsNone(result.stderr)

    def test_inherited_binary_output_supports_text_only_streams(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        process = subprocess.CompletedProcess([], 0, result_envelope(0, b"out\xff\n", b"err\xff\n"), b"")
        with mock.patch.object(os, "geteuid", return_value=501), \
             mock.patch.object(wrapper.subprocess, "run", return_value=process), \
             mock.patch.object(wrapper.sys, "stdout", stdout), \
             mock.patch.object(wrapper.sys, "stderr", stderr):
            try:
                result = wrapper.run_as_root(["/bin/echo", "value"])
            except AttributeError as error:
                self.fail(f"Text stream lost the command result: {error}")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(stdout.getvalue(), "out\ufffd\n")
        self.assertEqual(stderr.getvalue(), "err\ufffd\n")

    def test_inherited_binary_stream_preserves_bytes(self):
        output = io.BytesIO()
        process = subprocess.CompletedProcess([], 0, result_envelope(0, b"out\xff\n"), b"")
        stream = io.TextIOWrapper(output, encoding="utf-8")
        with mock.patch.object(os, "geteuid", return_value=501), \
             mock.patch.object(wrapper.subprocess, "run", return_value=process), \
             mock.patch.object(wrapper.sys, "stdout", stream):
            result = wrapper.run_as_root(["/bin/echo", "value"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output.getvalue(), b"out\xff\n")
        stream.close()

    def test_authorization_shell_quotes_arguments_and_preserves_failure(self):
        self.assertTrue(hasattr(wrapper, "_authorization_shell"), "Need a reviewed argument-quoting authorization boundary")
        value = "literal ' \" $() `not-a-command` \\ newline\nvalue"
        script = wrapper._authorization_shell(["/usr/bin/printf", "%s", value], combined=False)
        result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, result_envelope(0, value.encode()))

    def test_installer_does_not_distribute_or_enable_a_setuid_helper(self):
        builder = package.GeneratePackage()
        for files in (builder._files, builder._autopkg_files):
            self.assertFalse(any("PrivilegedHelperTools" in path for path in files.values()))
        for autopkg in (False, True):
            script = package_scripts.GenerateScripts()._generate_postinstall_script(is_autopkg=autopkg)
            self.assertNotIn("_setSUIDBit", script)
            self.assertNotIn("chmod -R +s", script)


if __name__ == "__main__":
    unittest.main()
