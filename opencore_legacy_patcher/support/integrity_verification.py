"""
integrity_verification.py: Check file consistency against .chunklist and .integrityDataV1 manifests

Based off of chunklist.py:
- https://gist.github.com/dhinakg/cbe30edf31ddc153fd0b0c0570c9b041
"""

import enum
import hashlib
import logging
import binascii
import threading
import os

from typing import Union
from pathlib import Path

CHUNK_LENGTH = 4 + 32
HEADER_LENGTH = 36
SIGNATURE_LENGTHS = {1: 256, 3: 808}


class ChunklistStatus(enum.Enum):
    """
    Chunklist status
    """
    IN_PROGRESS = 0
    SUCCESS     = 1
    FAILURE     = 2


class ChunklistVerification:
    """
    Check file consistency against Apple's chunklist format.
    Supports both chunklist and integrityDataV1 files
    - Ref: https://github.com/apple-oss-distributions/xnu/blob/xnu-8020.101.4/bsd/kern/chunklist.h

    Trust policy: SUCCESS means every byte matches the supplied manifest. This
    class checks the signature trailer's size, but does not authenticate it
    against Apple public keys. Callers must obtain the manifest through trusted
    HTTPS metadata/transport, and independently verify the publisher before
    installing executable content. An untrusted file and manifest can agree.

    Parameters:
        file_path      (Path): Path to the file to validate
        chunklist_path (Path): Path to the chunklist file

    Usage:
        >>> chunk_obj = ChunklistVerification("InstallAssistant.pkg", "InstallAssistant.pkg.integrityDataV1")
        >>> chunk_obj.validate()
        >>> while chunk_obj.status == ChunklistStatus.IN_PROGRESS:
        ...     print(f"Validating {chunk_obj.current_chunk} of {chunk_obj.total_chunks}")

        >>> if chunk_obj.status == ChunklistStatus.FAILURE:
        ...     print(chunk_obj.error_msg)
    """

    def __init__(self, file_path: Path, chunklist_path: Union[Path, bytes]) -> None:
        if isinstance(chunklist_path, bytes):
            self.chunklist_path: bytes = chunklist_path
        else:
            self.chunklist_path: Path = Path(chunklist_path)
        self.file_path:          Path = Path(file_path)

        self.error_msg:     str = ""
        self.current_chunk: int = 0
        self.status: ChunklistStatus = ChunklistStatus.IN_PROGRESS
        self.publisher_authenticated: bool = False
        self.verification_scope: str = "File consistency with supplied chunklist; publisher not authenticated"
        self.chunks: list[dict] | None = None
        try:
            self.chunks = self._generate_chunks(self.chunklist_path)
        except (OSError, ValueError) as error:
            self.error_msg = f"Unable to read chunklist: {error}"
        if self.chunks is None:
            self.error_msg = self.error_msg or "Invalid or truncated chunklist header"
            self.status = ChunklistStatus.FAILURE
        self.total_chunks: int = len(self.chunks or [])


    def _generate_chunks(self, chunklist: Union[Path, bytes]) -> list[dict] | None:
        """
        Generate chunk records, or return None for an invalid header

        Parameters:
            chunklist (Path | bytes): Path to the chunklist file or the chunklist file itself
        """

        chunklist: bytes = chunklist if isinstance(chunklist, bytes) else chunklist.read_bytes()
        if len(chunklist) < HEADER_LENGTH:
            return None

        # Ref: https://github.com/apple-oss-distributions/xnu/blob/xnu-8020.101.4/bsd/kern/chunklist.h#L59-L69
        header: dict = {
            "magic":       chunklist[:4],
            "length":      int.from_bytes(chunklist[4:8], "little"),
            "fileVersion": chunklist[8],
            "chunkMethod": chunklist[9],
            "sigMethod":   chunklist[10],
            "reserved":    chunklist[11],
            "chunkCount":  int.from_bytes(chunklist[12:20], "little"),
            "chunkOffset": int.from_bytes(chunklist[20:28], "little"),
            "sigOffset":   int.from_bytes(chunklist[28:36], "little")
        }

        if (header["magic"] != b"CNKL" or header["length"] != HEADER_LENGTH
                or header["fileVersion"] != 1 or header["chunkMethod"] != 1
                or header["sigMethod"] not in SIGNATURE_LENGTHS or header["reserved"] != 0):
            return None

        chunks_end = header["chunkOffset"] + header["chunkCount"] * CHUNK_LENGTH
        signature_end = header["sigOffset"] + SIGNATURE_LENGTHS[header["sigMethod"]]
        if (header["chunkCount"] == 0 or header["chunkOffset"] < HEADER_LENGTH
                or chunks_end > len(chunklist) or header["sigOffset"] < chunks_end
                or signature_end != len(chunklist)):
            return None

        all_chunks = chunklist[header["chunkOffset"]:chunks_end]
        chunks = [{"length": int.from_bytes(all_chunks[i:i+4], "little"), "checksum": all_chunks[i+4:i+CHUNK_LENGTH]} for i in range(0, len(all_chunks), CHUNK_LENGTH)]
        if len(chunks) != header["chunkCount"] or any(chunk["length"] == 0 for chunk in chunks):
            return None

        return chunks


    def _validate(self) -> None:
        """Ensure worker I/O failures produce a terminal status for GUI callers."""
        try:
            self._validate_file()
        except (OSError, ValueError) as error:
            self.error_msg = f"Unable to validate {self.file_path}: {error}"
            self.status = ChunklistStatus.FAILURE
            logging.info(self.error_msg)


    def _validate_file(self) -> None:
        """
        Validates provided file against chunklist
        """

        if self.chunks is None:
            self.status = ChunklistStatus.FAILURE
            return

        if not Path(self.file_path).exists():
            self.error_msg = f"File {self.file_path} does not exist"
            self.status = ChunklistStatus.FAILURE
            logging.info(self.error_msg)
            return

        if not Path(self.file_path).is_file():
            self.error_msg = f"File {self.file_path} is not a file"
            self.status = ChunklistStatus.FAILURE
            logging.info(self.error_msg)
            return

        with self.file_path.open("rb") as f:
            covered_size = sum(chunk["length"] for chunk in self.chunks)
            if os.fstat(f.fileno()).st_size != covered_size:
                raise ValueError("Chunklist does not cover the complete file")
            self.current_chunk = 0
            for chunk in self.chunks:
                self.current_chunk += 1
                digest = hashlib.sha256()
                remaining = chunk["length"]
                while remaining:
                    data = f.read(min(remaining, 1024 * 1024))
                    if not data:
                        raise ValueError(f"Truncated file at chunk {self.current_chunk}")
                    digest.update(data)
                    remaining -= len(data)
                status = digest.digest()
                if status != chunk["checksum"]:
                    self.error_msg = f"Chunk {self.current_chunk} checksum status FAIL: chunk sum {binascii.hexlify(chunk['checksum']).decode()}, calculated sum {binascii.hexlify(status).decode()}"
                    self.status = ChunklistStatus.FAILURE
                    logging.info(self.error_msg)
                    return

            if f.read(1) or os.fstat(f.fileno()).st_size != covered_size:
                raise ValueError("Unexpected bytes after the final chunk")

        self.status = ChunklistStatus.SUCCESS
        logging.info(self.verification_scope)


    def validate(self) -> None:
        """
        Spawns _validate() thread
        """
        threading.Thread(target=self._validate).start()
