"""
network_handler.py: Library dedicated to Network Handling tasks including downloading files

Primarily based around the DownloadObject class, which provides a simple
object for libraries to query download progress and status
"""

import time
import requests
import threading
import logging
import enum
import hashlib
import atexit
import weakref
import os
import fcntl
import tempfile

from collections.abc import Mapping

from typing import Optional, Union
from pathlib import Path

from . import utilities

SESSION = requests.Session()
DEFAULT_REQUEST_TIMEOUT = (5, 30)


class DownloadStatus(enum.Enum):
    """
    Enum for download status
    """

    INACTIVE:    str = "Inactive"
    DOWNLOADING: str = "Downloading"
    ERROR:       str = "Error"
    COMPLETE:    str = "Complete"


class NetworkUtilities:
    """
    Utilities for network related tasks, primarily used for downloading files
    """

    def __init__(self, url: str = None) -> None:
        self.url: str = url

        if self.url is None:
            self.url = "https://github.com"


    def verify_network_connection(self) -> bool:
        """
        Verifies that the network is available

        Returns:
            bool: True if network is available, False otherwise
        """

        response = None
        try:
            response = requests.head(self.url, timeout=5, allow_redirects=True)
            return True
        except (
            requests.exceptions.Timeout,
            requests.exceptions.TooManyRedirects,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError
        ):
            return False
        finally:
            if response is not None:
                response.close()

    def validate_link(self) -> bool:
        """
        Check for error

        Returns:
            bool: True if link is valid, False otherwise
        """
        response = None
        try:
            response = SESSION.head(self.url, timeout=5, allow_redirects=True)
            response.raise_for_status()
            return True
        except (
            requests.exceptions.Timeout,
            requests.exceptions.TooManyRedirects,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError
        ):
            return False
        finally:
            if response is not None:
                response.close()


    def get(self, url: str, **kwargs) -> requests.Response:
        """
        Wrapper for requests's get method
        Implement additional error handling

        Parameters:
            url (str): URL to get
            **kwargs: Additional parameters for requests.get

        Returns:
            requests.Response: Response object from requests.get
        """

        result: requests.Response = None

        kwargs.setdefault("timeout", DEFAULT_REQUEST_TIMEOUT)
        try:
            result = SESSION.get(url, **kwargs)
        except (
            requests.exceptions.Timeout,
            requests.exceptions.TooManyRedirects,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError
        ) as error:
            logging.warn(f"Error calling requests.get: {error}")
            # Return empty response object
            return requests.Response()

        return result

    def post(self, url: str, **kwargs) -> requests.Response:
        """
        Wrapper for requests's post method
        Implement additional error handling

        Parameters:
            url (str): URL to post
            **kwargs: Additional parameters for requests.post

        Returns:
            requests.Response: Response object from requests.post
        """

        result: requests.Response = None

        kwargs.setdefault("timeout", DEFAULT_REQUEST_TIMEOUT)
        try:
            result = SESSION.post(url, **kwargs)
        except (
            requests.exceptions.Timeout,
            requests.exceptions.TooManyRedirects,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError
        ) as error:
            logging.warn(f"Error calling requests.post: {error}")
            # Return empty response object
            return requests.Response()

        return result


class DownloadObject:
    """
    Object for downloading files from the network

    Usage:
        >>> download_object = DownloadObject(url, path)
        >>> download_object.download(display_progress=True)

        >>> if download_object.is_active():
        >>>     print(download_object.get_percent())

        >>> if not download_object.download_complete:
        >>>     print("Download failed")

        >>> print("Download complete"")

    """

    def __init__(self, url: str, path: str, checksum_algo: Optional["hashlib._Hash"] = None) -> None:
        self.url:       str = url
        self.status:    str = DownloadStatus.INACTIVE
        self.error_msg: str = ""
        self.filename:  str = self._get_filename()

        self.filepath:  Path = Path(path)

        self.total_file_size:      float = 0.0
        self._expected_file_size: Optional[int] = None
        self.downloaded_file_size: float = 0.0
        self.start_time:           float = time.time()

        self.error:             bool = False
        self.should_stop:       bool = False
        self._stop_event = threading.Event()
        self.download_complete: bool = False
        self.has_network:       bool = NetworkUtilities(self.url).verify_network_connection()

        self.active_thread: threading.Thread = None

        self.checksum = None
        self._checksum_storage: Optional[hashlib._Hash] = checksum_algo

        if self.has_network:
            self._populate_file_size()


    def __del__(self) -> None:
        self.stop()


    def download(self, display_progress: bool = False, spawn_thread: bool = True) -> None:
        """
        Download the file

        Spawns a thread to download the file, so that the main thread can continue
        Note sleep is disabled while the download is active

        Parameters:
            display_progress (bool): Display progress in console
            spawn_thread (bool): Spawn a thread to download the file, otherwise download in the current thread
            verify_checksum (Optional[hashlib._Hash]): Checksum algorithm to use for verifying the download, optional

        """
        self.status = DownloadStatus.DOWNLOADING
        logging.info(f"Starting download: {self.filename}")
        if spawn_thread:
            if self.active_thread:
                logging.error("Download already in progress")
                return
            self.active_thread = threading.Thread(target=self._download, args=(display_progress,))
            self.active_thread.start()
            return

        self._download(display_progress)


    def download_simple(self, verify_checksum: bool = False) -> Union[str, bool]:
        """
        Alternative to download(), mimics  utilities.py's old download_file() function

        Parameters:
            verify_checksum (bool): Return checksum of downloaded file if True

        Returns:
            If verify_checksum is True, returns the checksum of the downloaded file
            Otherwise, returns True if download was successful, False otherwise
        """

        if verify_checksum:
            self._checksum_storage = hashlib.sha256()

        self.download(spawn_thread=False)

        if not self.download_complete:
            return False

        return self._checksum_storage.hexdigest() if self._checksum_storage else True


    def _get_filename(self) -> str:
        """
        Get the filename from the URL

        Returns:
            str: Filename
        """

        return Path(self.url).name


    def _populate_file_size(self) -> None:
        """
        Get the file size of the file to be downloaded

        If unable to get file size, set to zero
        """

        result = None
        try:
            result = SESSION.head(self.url, allow_redirects=True, timeout=5)
            result.raise_for_status()
            if 'Content-Length' in result.headers:
                length = result.headers['Content-Length']
                if not isinstance(length, str) or not length.isascii() or not length.isdecimal():
                    raise ValueError("Invalid Content-Length")
                self._expected_file_size = int(length)
                self.total_file_size = self._expected_file_size
            else:
                raise Exception("Content-Length missing from headers")
        except Exception as e:
            logging.error(f"Error determining file size {self.url}: {str(e)}")
            logging.error("Assuming file size is 0")
            self.total_file_size = 0.0
            self._expected_file_size = None
        finally:
            if result is not None:
                result.close()


    def _update_checksum(self, chunk: bytes) -> None:
        """
        Update checksum with new chunk

        Parameters:
            chunk (bytes): Chunk to update checksum with
        """
        if self._checksum_storage:
            self._checksum_storage.update(chunk)


    def _prepare_working_directory(self, path: Path) -> bool:
        """
        Ensure the destination directory has space without modifying cached files.

        Parameters:
            path (str): Path to the file

        Returns:
            bool: True if successful, False if not
        """

        try:
            if not Path(path).parent.exists():
                logging.info(f"Creating directory: {Path(path).parent}")
                Path(path).parent.mkdir(parents=True, exist_ok=True)

            available_space = utilities.get_free_space(Path(path).parent)
            if self.total_file_size > available_space:
                msg = f"Not enough free space to download {self.filename}, need {utilities.human_fmt(self.total_file_size)}, have {utilities.human_fmt(available_space)}"
                logging.error(msg)
                raise Exception(msg)

        except Exception as e:
            self.error = True
            self.error_msg = str(e)
            self.status = DownloadStatus.ERROR
            logging.error(f"Error preparing working directory {path}: {self.error_msg}")
            return False

        logging.info(f"- Directory ready: {path}")
        return True


    def _download(self, display_progress: bool = False) -> None:
        """
        Download the file

        Libraries should invoke download() instead of this method

        Parameters:
            display_progress (bool): Display progress in console
        """

        utilities.disable_sleep_while_running()

        reference = weakref.ref(self)
        def stop_at_exit():
            target = reference()
            if target is not None:
                target.stop()

        response = None
        staging_path = None
        staging_fd = None
        lock_fd = None
        download_succeeded = False
        try:
            if not self.has_network:
                raise Exception("No network connection")

            # Keep this sidecar inode in place: unlinking a lock after release would
            # allow waiting and new writers to lock different inodes. flock also
            # coordinates separate patcher processes, not only Python threads.
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.filepath.parent.resolve() / f".{self.filepath.name}.download.lock"
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            while True:
                if self.should_stop or self._stop_event.is_set():
                    raise Exception("Download stopped")
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self._stop_event.wait(0.1)

            if self._prepare_working_directory(self.filepath) is False:
                raise Exception(self.error_msg)

            response = NetworkUtilities().get(self.url, stream=True, timeout=10, headers={"Accept-Encoding": "identity"})

            try:
                response.raise_for_status()
                if response.status_code == 206:
                    raise ValueError("Unexpected partial HTTP response for complete artifact download")
                expected_size = self._expected_file_size
                if expected_size is None and self.total_file_size > 0:
                    expected_size = int(self.total_file_size)
                headers = response.headers if isinstance(response.headers, Mapping) else {}
                if headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise ValueError("Unexpected Content-Encoding for artifact download")
                if "Content-Length" in headers:
                    length = headers["Content-Length"]
                    if not isinstance(length, str) or not length.isascii() or not length.isdecimal():
                        raise ValueError("Invalid Content-Length")
                    response_size = int(length)
                    if expected_size is not None and response_size != expected_size:
                        raise ValueError("Content-Length changed between HEAD and GET")
                    expected_size = response_size
                    self.total_file_size = response_size
                    # GET may supply the size even when HEAD did not. Recheck the
                    # actual staging requirement while holding the writer lock.
                    if self._prepare_working_directory(self.filepath) is False:
                        raise Exception(self.error_msg)

                staging_fd, staging_name = tempfile.mkstemp(
                    prefix=f".{self.filepath.name}.", suffix=".partial", dir=self.filepath.parent
                )
                staging_path = Path(staging_name)
                with open(staging_fd, 'wb') as file:
                    staging_fd = None  # the file context now owns the descriptor
                    atexit.register(stop_at_exit)
                    for i, chunk in enumerate(response.iter_content(1024 * 1024 * 4)):
                        if self.should_stop or self._stop_event.is_set():
                            raise Exception("Download stopped")
                        if chunk:
                            if expected_size is not None and self.downloaded_file_size + len(chunk) > expected_size:
                                raise ValueError("Downloaded file exceeds expected size")
                            if file.write(chunk) != len(chunk):
                                raise OSError("Incomplete write to download staging file")
                            self.downloaded_file_size += len(chunk)
                            if self._checksum_storage:
                                self._update_checksum(chunk)
                            if display_progress and i % 100:
                                # Don't use logging here, as we'll be spamming the log file
                                if self.total_file_size == 0.0:
                                    print(f"Downloaded {utilities.human_fmt(self.downloaded_file_size)} of {self.filename}")
                                else:
                                    print(f"Downloaded {self.get_percent():.2f}% of {self.filename} ({utilities.human_fmt(self.get_speed())}/s) ({self.get_time_remaining():.2f} seconds remaining)")
                    if self.should_stop or self._stop_event.is_set():
                        raise Exception("Download stopped")
                    if expected_size is not None and self.downloaded_file_size != expected_size:
                        raise ValueError(f"Incomplete download: expected {expected_size} bytes, received {self.downloaded_file_size}")
                    file.flush()
                    if os.fstat(file.fileno()).st_size != self.downloaded_file_size:
                        raise OSError("Download staging file size does not match transferred bytes")
                    os.fsync(file.fileno())
            finally:
                response.close()
            if self.should_stop or self._stop_event.is_set():
                raise Exception("Download stopped")
            checksum = self._checksum_storage.hexdigest() if self._checksum_storage else None
            # Publication happens only after length validation and successful
            # response/file close and fsync. Readers retain the old cache until now.
            os.replace(staging_path, self.filepath)
            staging_path = None
            self.checksum = checksum
            self.download_complete = True
            download_succeeded = True
            elapsed = max(time.time() - self.start_time, 0.000001)
            logging.info(f"Download complete: {self.filename}")
            logging.info("Stats:")
            logging.info(f"- Downloaded size: {utilities.human_fmt(self.downloaded_file_size)}")
            logging.info(f"- Time elapsed: {elapsed:.2f} seconds")
            logging.info(f"- Speed: {utilities.human_fmt(self.downloaded_file_size / elapsed)}/s")
            logging.info(f"- Location: {self.filepath}")
            if self.checksum:
                logging.info(f"Checksum: {self.checksum}")
        except Exception as e:
            self.error = True
            self.error_msg = str(e)
            self.status = DownloadStatus.ERROR
            logging.error(f"Error downloading {self.url}: {self.error_msg}")
        finally:
            atexit.unregister(stop_at_exit)
            if staging_fd is not None:
                os.close(staging_fd)
            if staging_path is not None:
                try:
                    staging_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    logging.warning(f"Unable to remove incomplete download {staging_path}: {cleanup_error}")
            if lock_fd is not None:
                os.close(lock_fd)
            utilities.enable_sleep_after_running()

        if download_succeeded:
            self.status = DownloadStatus.COMPLETE


    def get_percent(self) -> float:
        """
        Query the download percent

        Returns:
            float: The download percent, or -1 if unknown
        """

        if self.total_file_size == 0.0:
            return -1
        return self.downloaded_file_size / self.total_file_size * 100


    def get_speed(self) -> float:
        """
        Query the download speed

        Returns:
            float: The download speed in bytes per second
        """

        return self.downloaded_file_size / (time.time() - self.start_time)


    def get_time_remaining(self) -> float:
        """
        Query the time remaining for the download

        Returns:
            float: The time remaining in seconds, or -1 if unknown
        """

        if self.total_file_size == 0.0:
            return -1
        speed = self.get_speed()
        if speed <= 0:
            return -1
        return (self.total_file_size - self.downloaded_file_size) / speed


    def get_file_size(self) -> float:
        """
        Query the file size of the file to be downloaded

        Returns:
            float: The file size in bytes, or 0.0 if unknown
        """

        return self.total_file_size


    def is_active(self) -> bool:
        """
        Query if the download is active

        Returns:
            boolean: True if active, False if completed, failed, stopped, or inactive
        """

        if self.status == DownloadStatus.DOWNLOADING:
            return True
        return False


    def stop(self) -> None:
        """
        Stop the download

        If the download is active, this function will hold the thread until stopped
        """

        self.should_stop = True
        if getattr(self, "_stop_event", None) is not None:
            self._stop_event.set()
        if self.active_thread and self.active_thread is not threading.current_thread():
            self.active_thread.join(timeout=12)
