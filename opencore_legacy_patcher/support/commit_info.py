"""
commit_info.py: Parse Commit Info from binary's info.plist
"""

import plistlib

from pathlib import Path
from typing import Callable


class ParseCommitInfo:

    def __init__(self, binary_path: str, plist_loader: Callable[[Path], dict] | None = None) -> None:
        """
        Parameters:
            binary_path (str): Path to binary
        """

        self.binary_path = str(binary_path)
        self.plist_path = self._convert_binary_path_to_plist_path()
        self._plist_loader = plist_loader or self._load_plist


    @staticmethod
    def _load_plist(path: Path) -> dict:
        with path.open("rb") as plist_file:
            return plistlib.load(plist_file)


    def _convert_binary_path_to_plist_path(self) -> str:
        """
        Resolve Info.plist path from binary path
        """

        if Path(self.binary_path).exists():
            plist_path = self.binary_path.replace("MacOS/OpenCore-Patcher", "Info.plist")
            if Path(plist_path).exists() and plist_path.endswith(".plist"):
                return plist_path
        return None


    def generate_commit_info(self) -> tuple:
        """
        Generate commit info from Info.plist

        Returns:
            tuple: (Branch, Commit Date, Commit URL, Commit SHA, Repository, Project)
        """

        if self.plist_path:
            plist_info = self._plist_loader(Path(self.plist_path))
            if "Github" in plist_info:
                return (
                    plist_info["Github"]["Branch"],
                    plist_info["Github"]["Commit Date"],
                    plist_info["Github"]["Commit URL"],
                    plist_info["Github"]["Commit SHA"],
                    plist_info["Github"]["Repository"],
                    plist_info["Github"]["Project"],
                )
        return (
            "Running from source",
            "Not applicable",
            "",
            "",
            "",
            "Running from source",
        )
