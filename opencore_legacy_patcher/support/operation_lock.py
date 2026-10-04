"""Serialize root mutations using a stable lock inode in a protected directory."""

import fcntl
import logging
import os
import stat
import subprocess

from pathlib import Path
from typing import Callable

from . import subprocess_wrapper

ROOT_OPERATION_LOCK_PATH = Path("/private/var/run/OCLP-CustoMac/root-operation.lock")


class RootOperationLock:
    def __init__(self, path: Path = ROOT_OPERATION_LOCK_PATH) -> None:
        self.path = path
        self.fd = None

    def _prepare(self) -> None:
        directory = self.path.parent
        if not directory.exists():
            subprocess_wrapper.run_as_root_and_verify(
                ["/usr/bin/install", "-d", "-m", "0755", str(directory)], capture_output=True,
            )
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Root-operation lock directory is not protected")
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            # touch preserves the same inode if another process creates it first.
            subprocess_wrapper.run_as_root_and_verify(["/usr/bin/touch", str(self.path)], capture_output=True)
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
                raise ValueError("Root-operation lock file is not trusted")
            subprocess_wrapper.run_as_root_and_verify(["/bin/chmod", "0444", str(self.path)], capture_output=True)

    def acquire(self) -> bool:
        self._prepare()
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
                raise ValueError("Root-operation lock file is not protected")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd
        return True

    def release(self) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


def execute_locked_root_operation(operation: Callable[[], object]) -> object:
    lock = RootOperationLock()
    try:
        if lock.acquire() is False:
            logging.error("Another installation or root-patch operation is running; wait for it to finish")
            return False
    except Exception as error:
        logging.error(f"Cannot establish an exclusive root-operation lock: {error}")
        return False
    try:
        return operation()
    finally:
        lock.release()
