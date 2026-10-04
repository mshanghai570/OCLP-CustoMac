"""Lifecycle writes must verify the exact state and use unique protected staging."""

import os
import plistlib
import subprocess
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.sys_patch import lifecycle

REAL_RUN = subprocess.run
REAL_LSTAT = Path.lstat


class LifecycleWriteIdentityTests(unittest.TestCase):
    def test_valid_but_different_state_is_not_accepted_as_the_requested_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lifecycle.plist"

            def wrong_writer(path, payload):
                data = plistlib.loads(payload)
                data["State"] = lifecycle.RootPatchLifecycleState.REVERT_PENDING.value
                path.write_bytes(plistlib.dumps(data))
                return True

            store = lifecycle.RootPatchLifecycleStore(
                SimpleNamespace(), path=path, boot_session_reader=lambda: "boot-a", writer=wrong_writer,
            )
            self.assertFalse(store.write(lifecycle.RootPatchLifecycleState.PATCH_IN_PROGRESS, {"Requested Patches": []}))

    def test_two_default_writes_use_different_root_owned_staging_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir()
            destination = root / "records/lifecycle.plist"
            copied = []

            def root_stat(path):
                data = list(REAL_LSTAT(path))
                data[4] = 0
                return os.stat_result(data)

            def run(args, **kwargs):
                args = [str(arg) for arg in args]
                if args == ["/bin/sync"]:
                    return subprocess.CompletedProcess(args, 0)
                for argument in args[1:]:
                    if argument.startswith("/"):
                        self.assertTrue(Path(argument).is_relative_to(root), "Test attempted a live mutation")
                if args[0] == "/bin/cp":
                    copied.append(Path(args[-1]))
                return REAL_RUN(args, capture_output=True)

            def verified(args, **kwargs):
                result = run(args, **kwargs)
                if result.returncode:
                    raise subprocess.CalledProcessError(result.returncode, args)

            store = lifecycle.RootPatchLifecycleStore(
                SimpleNamespace(payload_path=payload), path=destination, boot_session_reader=lambda: "boot-a",
            )
            with mock.patch.object(lifecycle.Path, "lstat", root_stat), \
                 mock.patch.object(lifecycle.subprocess_wrapper, "run_as_root", side_effect=run), \
                 mock.patch.object(lifecycle.subprocess_wrapper, "run_as_root_and_verify", side_effect=verified):
                self.assertTrue(store.write(lifecycle.RootPatchLifecycleState.PATCH_IN_PROGRESS, {"Requested Patches": []}))
                self.assertTrue(store.write(lifecycle.RootPatchLifecycleState.REVERT_PENDING, {"Requested Patches": []}))
            self.assertEqual(len(copied), 2)
            self.assertNotEqual(copied[0], copied[1])
            self.assertFalse(any(path.exists() for path in copied))


if __name__ == "__main__":
    unittest.main()
