"""Resource cleanup tests for streamed downloads."""

import builtins
import requests
import tempfile
import unittest

from pathlib import Path
from unittest import mock

from opencore_legacy_patcher.support import network_handler


class NetworkResponseResourceTests(unittest.TestCase):
    def test_verify_network_connection_closes_head_response(self) -> None:
        response = mock.Mock()
        with mock.patch.object(network_handler.requests, "head", return_value=response):
            self.assertTrue(
                network_handler.NetworkUtilities("https://example.invalid").verify_network_connection()
            )
        response.close.assert_called_once_with()

    def test_validate_link_closes_head_response(self) -> None:
        response = mock.Mock()
        with mock.patch.object(network_handler.SESSION, "head", return_value=response):
            self.assertTrue(
                network_handler.NetworkUtilities("https://example.invalid").validate_link()
            )
        response.raise_for_status.assert_called_once_with()
        response.close.assert_called_once_with()

    def test_file_size_probe_closes_head_response(self) -> None:
        network_response = mock.Mock()
        size_response = mock.Mock(headers={"Content-Length": "123"})

        with mock.patch.object(network_handler.requests, "head", return_value=network_response):
            with mock.patch.object(network_handler.SESSION, "head", return_value=size_response):
                download = network_handler.DownloadObject(
                    "https://example.invalid/asset",
                    "/tmp/unused-asset",
                )

        self.assertEqual(download.total_file_size, 123.0)
        network_response.close.assert_called_once_with()
        size_response.close.assert_called_once_with()
        download.stop()


class DownloadResourceTests(unittest.TestCase):
    def test_http_errors_are_not_downloaded(self) -> None:
        for status_code in (403, 404, 500):
            with self.subTest(status=status_code), tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "asset"
                download = self._download(destination)
                response = requests.Response()
                response.status_code = status_code
                response._content = b"error page"
                response._content_consumed = True
                self._run_download(download, response, [])
                self.assertFalse(download.download_complete)
                self.assertTrue(download.error)
                self.assertFalse(destination.exists())

    def test_stream_failure_keeps_error_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            download = self._download(Path(temporary) / "asset")
            response = mock.Mock()
            response.iter_content.side_effect = OSError("stream interrupted")
            self._run_download(download, response, [])
            self.assertEqual(download.status, network_handler.DownloadStatus.ERROR)

    def test_successful_download_has_complete_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            download = self._download(Path(temporary) / "asset")
            response = mock.Mock()
            response.iter_content.return_value = [b"asset"]
            self._run_download(download, response, [])
            self.assertEqual(download.status, network_handler.DownloadStatus.COMPLETE)

    def _download(self, path: Path) -> network_handler.DownloadObject:
        with mock.patch.object(
            network_handler.NetworkUtilities,
            "verify_network_connection",
            return_value=True,
        ), mock.patch.object(network_handler.DownloadObject, "_populate_file_size"):
            download = network_handler.DownloadObject("https://example.invalid/asset", path)
        download.start_time = 1.0
        download._prepare_working_directory = mock.Mock(return_value=True)
        return download

    def _run_download(self, download, response, opened_files) -> None:
        real_open = builtins.open

        def capture_open(*args, **kwargs):
            handle = real_open(*args, **kwargs)
            opened_files.append(handle)
            return handle

        with mock.patch.object(network_handler.NetworkUtilities, "get", return_value=response), \
             mock.patch.object(network_handler.utilities, "disable_sleep_while_running"), \
             mock.patch.object(network_handler.utilities, "enable_sleep_after_running"), \
             mock.patch.object(network_handler.atexit, "register"), \
             mock.patch("builtins.open", side_effect=capture_open):
            download._download()

    def test_successful_download_closes_response_and_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "asset"
            download = self._download(destination)
            response = mock.Mock()
            response.iter_content.return_value = [b"asset-data"]
            opened_files = []

            self._run_download(download, response, opened_files)

            self.assertTrue(download.download_complete)
            self.assertEqual(destination.read_bytes(), b"asset-data")
            response.close.assert_called_once_with()
            self.assertEqual(len(opened_files), 1)
            self.assertTrue(opened_files[0].closed)

    def test_stream_failure_closes_response_and_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "asset"
            download = self._download(destination)
            response = mock.Mock()
            response.iter_content.side_effect = OSError("stream interrupted")
            opened_files = []

            self._run_download(download, response, opened_files)

            self.assertTrue(download.error)
            self.assertIn("stream interrupted", download.error_msg)
            self.assertFalse(download.download_complete)
            response.close.assert_called_once_with()
            self.assertEqual(len(opened_files), 1)
            self.assertTrue(opened_files[0].closed)
            self.assertFalse(destination.exists())

    def test_destination_open_failure_still_closes_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "asset"
            download = self._download(destination)
            response = mock.Mock()
            opened_files = []

            with mock.patch.object(network_handler.NetworkUtilities, "get", return_value=response):
                with mock.patch.object(network_handler.utilities, "disable_sleep_while_running"):
                    with mock.patch.object(network_handler.utilities, "enable_sleep_after_running"):
                        with mock.patch.object(network_handler.atexit, "register"):
                            with mock.patch(
                                "builtins.open",
                                side_effect=PermissionError("cannot write destination"),
                            ):
                                download._download()

            self.assertTrue(download.error)
            self.assertIn("cannot write destination", download.error_msg)
            response.close.assert_called_once_with()
            self.assertFalse(destination.exists())

    def test_prepare_failure_does_not_delete_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "existing-asset"
            destination.write_bytes(b"preexisting")
            download = self._download(destination)
            download._prepare_working_directory = mock.Mock(return_value=False)
            response = mock.Mock()
            opened_files = []

            self._run_download(download, response, opened_files)

            self.assertEqual(destination.read_bytes(), b"preexisting")
            response.close.assert_not_called()
            self.assertEqual(opened_files, [])


if __name__ == "__main__":
    unittest.main()
