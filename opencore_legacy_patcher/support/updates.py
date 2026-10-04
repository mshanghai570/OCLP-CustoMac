"""
updates.py: Check for OpenCore Legacy Patcher binary updates

Call check_binary_updates() to determine if any updates are available
Returns dict with Link and Version of the latest binary update if available
"""

import logging
import requests

from typing import Optional, Union
from packaging import version

from . import network_handler

from .. import constants


REPO_LATEST_RELEASE_URL: str = "https://api.github.com/repos/kgp-macPro/OCLP-CustoMac/releases/latest"

CHANGELOG_FALLBACK: str = """## Unable to fetch changelog

Please check the Github page for more information about this release."""


def fetch_release_changelog(releases_url: str = REPO_LATEST_RELEASE_URL) -> str:
    """
    Return a GitHub release's notes, cut before the asset information section

    The response is released in every path that owns it, including when the
    body cannot be read.

    Parameters:
        releases_url (str): Release API endpoint to query

    Returns:
        str: Release notes, or the fallback text when they are unavailable
    """

    response = None
    try:
        response = requests.get(releases_url, timeout=network_handler.DEFAULT_REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        body = payload.get("body") if isinstance(payload, dict) else None
        if isinstance(body, str):
            return body.split("## Asset Information")[0]
        return CHANGELOG_FALLBACK
    except (requests.exceptions.RequestException, ValueError, TypeError):
        return CHANGELOG_FALLBACK
    finally:
        if response is not None:
            response.close()


class CheckBinaryUpdates:
    def __init__(self, global_constants: constants.Constants) -> None:
        self.constants: constants.Constants = global_constants
        try:
            self.binary_version = version.parse(self.constants.patcher_version)
        except version.InvalidVersion:
            assert self.constants.special_build is True, "Invalid version number for binary"
            # Special builds will not have a proper version number
            self.binary_version = version.parse("0.0.0")

        self.latest_details = None

    def check_if_newer(self, version: Union[str, version.Version]) -> bool:
        """
        Check if the provided version is newer than the local version

        Parameters:
            version (str): Version to compare against

        Returns:
            bool: True if the provided version is newer, False if not
        """
        if self.constants.special_build is True:
            return False

        return self._check_if_build_newer(version, self.binary_version)

    def _check_if_build_newer(self, first_version: Union[str, version.Version], second_version: Union[str, version.Version]) -> bool:
        """
        Check if the first version is newer than the second version

        Parameters:
            first_version_str (str): First version to compare against (generally local)
            second_version_str (str): Second version to compare against (generally remote)

        Returns:
            bool: True if first version is newer, False if not
        """

        if not isinstance(first_version, version.Version):
            try:
                first_version = version.parse(first_version)
            except version.InvalidVersion:
                # Special build > release build: assume special build is newer
                return True

        if not isinstance(second_version, version.Version):
            try:
                second_version = version.parse(second_version)
            except version.InvalidVersion:
                # Release build > special build: assume special build is newer
                return False

        return first_version > second_version


    def check_binary_updates(self) -> Optional[dict]:
        """
        Check if any updates are available for the OpenCore Legacy Patcher binary

        Returns:
            dict: Dictionary with Link and Version of the latest binary update if available
        """

        if self.constants.special_build is True:
            # Special builds do not get updates through the updater
            return None

        if self.latest_details:
            # We already checked
            return self.latest_details

        if not network_handler.NetworkUtilities(REPO_LATEST_RELEASE_URL).verify_network_connection():
            return None

        response = network_handler.NetworkUtilities().get(REPO_LATEST_RELEASE_URL)
        try:
            response.raise_for_status()
            data_set = response.json()

            if not isinstance(data_set, dict) or not isinstance(data_set.get("tag_name"), str):
                return None

            # The release marked as latest will always be stable, and thus, have a proper version number
            # But if not, let's not crash the program
            try:
                latest_remote_version = version.parse(data_set["tag_name"])
            except version.InvalidVersion:
                return None

            if not self._check_if_build_newer(latest_remote_version, self.binary_version):
                return None

            assets = data_set.get("assets")
            if not isinstance(assets, list):
                return None
            for asset in assets:
                if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
                    continue
                logging.info(f"Found asset: {asset['name']}")
                if asset["name"] == "OpenCore-Patcher.pkg" and isinstance(asset.get("browser_download_url"), str) and asset["browser_download_url"]:
                    self.latest_details = {
                        "Name": asset["name"],
                        "Version": latest_remote_version,
                        "Link": asset["browser_download_url"],
                        "Github Link": f"{self.constants.repo_link}/releases/tag/{latest_remote_version}",
                    }
                    return self.latest_details

            return None
        except (requests.exceptions.RequestException, ValueError, TypeError):
            return None
        finally:
            response.close()
