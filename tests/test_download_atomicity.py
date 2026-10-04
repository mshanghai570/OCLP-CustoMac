"""Downloads publish complete files and preserve usable caches on failure."""

import builtins
import hashlib
import os
import fcntl
import select
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import requests

from opencore_legacy_patcher.support import network_handler as network


class AtomicDownloadTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "asset"
        self.path.write_bytes(b"cached")
        self.stack.enter_context(mock.patch.object(network.NetworkUtilities, "verify_network_connection", return_value=True))
        self.stack.enter_context(mock.patch.object(network.DownloadObject, "_populate_file_size"))
        self.stack.enter_context(mock.patch.object(network.utilities, "disable_sleep_while_running"))
        self.stack.enter_context(mock.patch.object(network.utilities, "enable_sleep_after_running"))
        self.stack.enter_context(mock.patch.object(network.utilities, "get_free_space", return_value=1024))

    def download(self, size=0):
        download = network.DownloadObject("https://example.invalid/asset", self.path, hashlib.sha256())
        download.total_file_size = size
        return download

    def response(self, chunks, headers=None):
        response = mock.Mock(headers=headers or {})
        response.iter_content.return_value = chunks
        return response

    def run_download(self, download, response):
        with mock.patch.object(network.NetworkUtilities, "get", return_value=response):
            download._download()

    def assert_failed_refresh(self, download):
        self.assertEqual(download.status, network.DownloadStatus.ERROR)
        self.assertFalse(download.download_complete)
        self.assertTrue(self.path.exists(), "failed refresh deleted the cached file")
        self.assertEqual(self.path.read_bytes(), b"cached")
        self.assertEqual(list(self.path.parent.glob("*.partial")), [])

    def test_http_failure_preserves_cached_file(self):
        response = self.response([])
        response.raise_for_status.side_effect = requests.HTTPError("404")
        download = self.download()
        self.run_download(download, response)
        self.assert_failed_refresh(download)
        response.close.assert_called_once()

    def test_network_stream_failure_preserves_cached_file(self):
        def stream():
            yield b"partial"
            raise requests.ConnectionError("disconnected")
        download = self.download()
        self.run_download(download, self.response(stream()))
        self.assert_failed_refresh(download)

    def test_disk_failure_preserves_cached_file(self):
        real_open = builtins.open
        def open_full_disk(path, mode="r", *args, **kwargs):
            handle = real_open(path, mode, *args, **kwargs)
            if mode == "wb":
                original_write = handle.write
                def fail_write(data):
                    original_write(data[:1])
                    raise OSError("disk full")
                handle.write = fail_write
            return handle
        download = self.download()
        with mock.patch("builtins.open", side_effect=open_full_disk):
            self.run_download(download, self.response([b"new"]))
        self.assert_failed_refresh(download)

    def test_existing_cache_does_not_bypass_capacity_check(self):
        download = self.download(2048)
        self.run_download(download, self.response([b"new"]))
        self.assert_failed_refresh(download)
        self.assertIn("free space", download.error_msg)

    def test_readers_see_cached_file_until_atomic_publication(self):
        def stream():
            yield b"new"
            self.assertEqual(self.path.read_bytes(), b"cached")
            stages = list(self.path.parent.glob("*.partial"))
            self.assertEqual(len(stages), 1)
            self.assertNotEqual(stages[0], self.path)
            yield b" data"
        download = self.download(8)
        self.run_download(download, self.response(stream()))
        self.assertTrue(download.download_complete)
        self.assertEqual(self.path.read_bytes(), b"new data")
        self.assertEqual(download.checksum, hashlib.sha256(b"new data").hexdigest())
        self.assertEqual(list(self.path.parent.glob("*.partial")), [])

    def test_short_and_oversized_transfers_preserve_cache(self):
        for payload in (b"ab", b"abcd"):
            with self.subTest(payload=payload):
                download = self.download(3)
                self.run_download(download, self.response([payload]))
                self.assert_failed_refresh(download)

    def test_get_content_length_is_checked_when_head_size_unknown(self):
        download = self.download()
        self.run_download(download, self.response([b"ab"], {"Content-Length": "3"}))
        self.assert_failed_refresh(download)

    def test_zero_and_malformed_get_lengths_do_not_publish_data(self):
        for length in ("0", "-1", "3.5", "invalid"):
            with self.subTest(length=length):
                download = self.download()
                self.run_download(download, self.response([b"abc"], {"Content-Length": length}))
                self.assert_failed_refresh(download)

    def test_failure_to_publish_preserves_cache_and_removes_stage(self):
        download = self.download()
        with mock.patch.object(os, "replace", side_effect=OSError("replace denied")):
            self.run_download(download, self.response([b"new"]))
        self.assert_failed_refresh(download)

    def test_response_close_failure_preserves_cache(self):
        download = self.download()
        response = self.response([b"new"])
        response.close.side_effect = OSError("response close failed")
        self.run_download(download, response)
        self.assert_failed_refresh(download)

    def test_short_disk_write_is_not_published(self):
        real_open = builtins.open
        def short_open(path, mode="r", *args, **kwargs):
            handle = real_open(path, mode, *args, **kwargs)
            if mode == "wb":
                original_write = handle.write
                handle.write = lambda data: original_write(data[:1])
            return handle
        download = self.download()
        with mock.patch("builtins.open", side_effect=short_open):
            self.run_download(download, self.response([b"new"]))
        self.assert_failed_refresh(download)

    def test_waiting_for_writer_lock_can_be_cancelled(self):
        lock_path = self.path.parent / f".{self.path.name}.download.lock"
        with lock_path.open("wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            download = self.download()
            with mock.patch.object(network.NetworkUtilities, "get", side_effect=AssertionError("cancelled waiter made a request")):
                thread = threading.Thread(target=download._download)
                thread.start()
                download.stop()
                thread.join(2)
                self.assertFalse(thread.is_alive())
            self.assert_failed_refresh(download)

    def test_get_size_rechecks_space_when_head_size_unknown(self):
        download = self.download()
        self.run_download(download, self.response([b"new"], {"Content-Length": "2048"}))
        self.assert_failed_refresh(download)
        self.assertIn("free space", download.error_msg)

    def test_encoded_response_is_not_checked_against_wire_length(self):
        download = self.download()
        self.run_download(download, self.response([b"new"], {"Content-Encoding": "gzip", "Content-Length": "3"}))
        self.assert_failed_refresh(download)

    def test_head_get_size_change_preserves_cache(self):
        download = self.download(3)
        self.run_download(download, self.response([b"new!"], {"Content-Length": "4"}))
        self.assert_failed_refresh(download)

    def test_cancellation_during_response_close_preserves_cache(self):
        download = self.download()
        response = self.response([b"new"])
        response.close.side_effect = download.stop
        self.run_download(download, response)
        self.assert_failed_refresh(download)

    def test_unsolicited_partial_http_response_preserves_cache(self):
        download = self.download()
        response = self.response([b"part"], {"Content-Length": "4"})
        response.status_code = 206
        self.run_download(download, response)
        self.assert_failed_refresh(download)

    def test_separate_process_writer_lock_is_respected(self):
        lock_path = self.path.parent / f".{self.path.name}.download.lock"
        script = "import fcntl, sys; lock = open(sys.argv[1], 'wb'); fcntl.flock(lock, fcntl.LOCK_EX); print('ready', flush=True); sys.stdin.readline()"
        process = subprocess.Popen([sys.executable, "-c", script, str(lock_path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        download = self.download()
        requested = threading.Event()
        def get(*args, **kwargs):
            requested.set()
            return self.response([b"new"])
        thread = threading.Thread(target=download._download)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 3)[0], "lock holder did not start")
            self.assertEqual(process.stdout.readline(), b"ready\n")
            with mock.patch.object(network.NetworkUtilities, "get", side_effect=get):
                thread.start()
                self.assertFalse(requested.wait(0.2))
                self.assertEqual(self.path.read_bytes(), b"cached")
                process.communicate(input=b"release\n", timeout=3)
                thread.join(3)
                self.assertFalse(thread.is_alive())
            self.assertTrue(download.download_complete)
            self.assertEqual(self.path.read_bytes(), b"new")
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)
            if thread.is_alive():
                download.stop()
                thread.join(3)

    def test_two_writers_to_one_destination_are_serialized(self):
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        def stream():
            first_started.set()
            if not release_first.wait(3):
                raise AssertionError("first download was not released")
            yield b"first"
        first = self.download()
        second = self.download()
        responses = {first: self.response(stream()), second: self.response([b"second"])}
        def get(url, **kwargs):
            if threading.current_thread().name == "second":
                second_started.set()
                return responses[second]
            return responses[first]
        with mock.patch.object(network.NetworkUtilities, "get", side_effect=get):
            thread1 = threading.Thread(target=first._download, name="first")
            thread2 = threading.Thread(target=second._download, name="second")
            try:
                thread1.start()
                self.assertTrue(first_started.wait(2))
                thread2.start()
                self.assertFalse(second_started.wait(0.2))
                self.assertEqual(self.path.read_bytes(), b"cached")
            finally:
                release_first.set()
                thread1.join(3)
                if thread2.ident is not None:
                    thread2.join(3)
            self.assertFalse(thread1.is_alive())
            self.assertFalse(thread2.is_alive())
        self.assertTrue(first.download_complete)
        self.assertTrue(second.download_complete)
        self.assertEqual(self.path.read_bytes(), b"second")


if __name__ == "__main__":
    unittest.main()
