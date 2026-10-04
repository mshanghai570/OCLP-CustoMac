"""
metallib_handler.py: Library for handling Metal libraries
"""

import hashlib
import logging
import re
import requests
import subprocess
import packaging.version

from typing  import cast
from pathlib import Path

from .  import network_handler, subprocess_wrapper
from .. import constants

from ..datasets import os_data


# Packages built from github.com/pyquick/MetallibSupportPkg install themselves under
# the "Pyquick" vendor folder (see that project's cli.py, build_pkg()). The
# legacy folder is retained so packages installed by older OCLP releases, which
# sourced from the Dortania-maintained project, are still recognised and are not
# needlessly re-downloaded.
METALLIB_INSTALL_PATH:         str   = "/Library/Application Support/Pyquick/MetallibSupportPkg"
METALLIB_INSTALL_PATH_LEGACY:  str   = "/Library/Application Support/Dortania/MetallibSupportPkg"
METALLIB_INSTALL_PATH_HACKDOC: str   = "/Library/Application Support/Hackdoc/MetallibSupportPkg"
METALLIB_INSTALL_PATHS:        tuple = (
    METALLIB_INSTALL_PATH,
    METALLIB_INSTALL_PATH_LEGACY,
    METALLIB_INSTALL_PATH_HACKDOC,
)

METALLIB_API_LINK:     str  = "https://dortania.github.io/MetallibSupportPkg/manifest.json"

# macOS Tahoe (Darwin 25) shader downgrades are not published in any static
# manifest. pyquick's own manifest.json currently carries a single Darwin 24
# entry, and the third-party manifests that do list Darwin 25 builds resolve to
# a different publisher (hackdoc/metal) shipping byte-different packages under
# identical version tags. The GitHub Releases API for the upstream project is
# therefore the only source consulted for Tahoe, and every candidate must match
# a digest pinned below.
TAHOE_METALLIB_RELEASES_API: str = "https://api.github.com/repos/pyquick/MetallibSupportPkg/releases"

# SHA-256 digests of pyquick/MetallibSupportPkg Darwin 25 packages, as published
# by the GitHub Releases API. A Tahoe build that is absent from this table is
# refused rather than fetched: an unpinned package would be installed with root
# privileges and could not be validated afterwards.
TAHOE_METALLIB_PINNED_SHA256: dict[str, str] = {
    "25A353":   "610f6e9c132c551b373c8f7337d6a7f26b8fa1975c95306a27b79a46e26e5f31",  # 26.0
    "25A354":   "b75b8e01ee275cbc7fe7f9d14b2c9f10902efe5a0a9e2f2d6feb4e7a79600351",  # 26.0
    "25A362":   "893dcf0748f4910ace182151a75a4c72b99205fc453eba3bf15f262418a061b7",  # 26.0.1
    "25A5279m": "0175e7a1f0287768efebdca4d3850cc21ddee67644efe3099b401dea52c56fb5",  # 26.0
    "25A5295e": "1cc279943339e215ae5a5d87fb1090142b0459f5cd998c3d383de8d04c9fdb9e",  # 26.0
    "25A5306g": "d4773f8bfadc2d83e88287b6e37ed440b6c85b2e823cdb3524ef4c6532f55fe1",  # 26.0
    "25A5316i": "229efc6a0d3d84507dc494b06afdcf4a06210705346e52a3fef5e0db8a77cbbb",  # 26.0
    "25A5327h": "d282acf9c02ee4cde99c55d6c20b998fb3105714868eba1fc2ea5c8f0afe1297",  # 26.0
    "25A5327m": "513c32345f1bd852e1211ffdd6c49cbccb5305a83f3c56865c8415f73592b7d5",  # 26.0
    "25A5338b": "571d6a84db5f44a012c86a78ff3a7900c02d18ecfb9acdf4c2b76caa4280b488",  # 26.0
    "25A5346a": "41422ce8878033c0f5ffc35667ee076119cd10899baecca99908f6344810b73e",  # 26.0
    "25A5349a": "aa952238bc2253f5a32bfa78de7af3167f22d173189775925a027391a2ff9a0f",  # 26.0
    "25A5351b": "83fe899147036266eebcbc9196dba1fd3c285b17b95afe2039b3a31c58e34882",  # 26.0
    "25B77":    "884ef235fdea1cb58ad87d9cc689d63d23cb410baf3156af09e98bf9abb6fbe4",  # 26.1
    "25B78":    "aeedfe3768996dad46b6268ced5cd1b94c0c4c69c8f955f4732cf1c567bc337f",  # 26.1
    "25B5042k": "bdd520db9f9681bb653f477653243ae3b13eba46eafe76ba762ad5ecadd00c8b",  # 26.1
    "25B5057f": "94de77f76c977e73b61e29c2279cdd46adba0738a00f841834c11c65263b435c",  # 26.1
    "25B5062e": "15be309031fe98c1add4d94959a88311897f531c4772efb340a0fe8fac779b12",  # 26.1
    "25B5072a": "e80acabfe805b5c8c9c3ac358b41717e0e2d8d83ae6284383394ef787f3c080e",  # 26.1
    "25C56":    "39dae8009c651c9ba2503e490a020e67831e9dda663f56c2b1217671a71347f7",  # 26.2
    "25C5031i": "7844285ee9188e2f6a51072ee1d873ce947c8411c4eefbc149ba049e668d7440",  # 26.2
    "25C5037j": "bec128698e84c7a2239f2f3177bcd5a6f1652baa24e26ea7bd8b5249200e98e1",  # 26.2
    "25C5048a": "305c7cba60a095c2deb82c7992600e33071d104dd95f12e23b686afdd30f6c83",  # 26.2
    "25D122":   "4086a26269b424b63514c9f50322b20b76b760ac11adc527f0385caf7eb47998",  # 26.3
    "25D125":   "b88ef4703bc400b1595f49338acb66d46448c3a60de64301ab13704d25f6e3bc",  # 26.3
    "25D2128":  "e906f30c09c902120b70e9ee97b0ebfb47fa0d9af7c499e4a93e3e459d86be2f",  # 26.3.1
    "25D2140":  "c973aa6bf532f2022015ab203c73a5eb9eca7b2cdea5c75bf1349f71ccce4ac7",  # 26.3.2
    "25D5087f": "b6e432a7c7ec9c7cc1269b833aa1e5b9e9ece9baf2bbc0e7cd2b0b6098fc39f3",  # 26.3
    "25D5101c": "ef522444595abbe7b3506043188158dc67a3d2dca5b1b7b4b1ea3e935c7e1492",  # 26.3
    "25D5112c": "a1484fe1aca71f679cfb67d6a46d9e1063b2c8afa6725ba23b911c4b30e47a70",  # 26.3
    "25E241":   "b84c35b05ed3ad3f03f26c28ec7218d1617afeaf2a5cbc1ac25afad82dcd5081",  # 26.4
    "25E243":   "104f70959a1587b6b340044436f54837a75875ded7efeebeef265e607ad2228e",  # 26.4
    "25E246":   "ace03dd76c58b5e3770511bf5b8c68cc179373b6f79e81abdb462927814d02fa",  # 26.4
    "25E253":   "05bdf3e1f219809f6812590e118375f38d90409fcc60b103e23ba9c14c1ae54b",  # 26.4.1
    "25E5207k": "25556d56e22038cce669bbad821493f58afdf70700a50cc3114b9de1861598f8",  # 26.4
    "25E5218f": "45b56912cee135240fb6afb353929e5b4e470cfd6cf56edf992572092d163065",  # 26.4
    "25E5223i": "a9569b0de6fb718b8d3f083c8ef3e3e31eccba2bab672f75f6a563eafaee33c3",  # 26.4
    "25E5233c": "8d3149b3defa7b2e25aaec47e551ef4fff9c9eb9fa266c293e7387972ec6832c",  # 26.4
    "25F71":    "9f15f7d4515ce60602ee4ead6c54de6c8f628e30ad2515781da126ba000077ab",  # 26.5
    "25F80":    "de00ae20492568f2fe0b779e3358f6abb1d6fdb31b7df229642f4d99cbbcaf22",  # 26.5.1
    "25F84":    "55844fc135307bae3ea898c563d50d3564886635d88362927db08561bd4801b2",  # 26.5.2
    "25F5042g": "922602cdc29910be119337c70472476fb4d78b33f4f81f9af3ed4f18a6c6f7cb",  # 26.5
    "25F5053d": "87b9274c71adead5c92ba497e311ae89a69ad43d6985b3cd36fd6a6a7a9a6280",  # 26.5
    "25F5058e": "e6d2d1bee8cc07337a4d3d2ebd5c9931079241899c3beead5a1907da1383cc8e",  # 26.5
    "25F5068a": "9214504387535d8324b9b262db656f273df22deaba9b5ba2625ae33318050d5b",  # 26.5
    "25G70":    "974117df62acbf35a616a4635d5a0b112a53dedfcbd2844ed2d7a9aa9af8320d",  # 26.6
    "25G72":    "184f3a8c60fdb7609ad95895f569687635844111861e90d4d3caff412b272968",  # 26.6
    "25G76":    "f13ba2e8d9df25ee8a402d0629054768fbc7c729ec6fa294f551189cc7a1a8c0",  # 26.6.1
    "25G82":    "602c66b6a558edf81fc71474441fff54a9cdc2f616a91d44b0557a8a12beaea3",  # 26.6.2
    "25G83":    "3578553873558f97c7aba27722fb16ec63ab838a8b4d823c07e129c6df9c5867",  # 26.6.2
    "25G5028f": "bce043425d1061d8dc08ce85dec45f3fb1e8474fed0b3385d2a7c2c34fa9a159",  # 26.6
    "25G5043d": "573fa85ffd3ddb049e04ebafb56a4518147e3f8100ac9267a8ffe1128d44c04c",  # 26.6
    "25G5052e": "5d950111dbec4060ea3648120d325d28a47f85c73c36510415ebad6ac74dd233",  # 26.6
    "25G5057c": "a1ceba77f32b2141efb4dbc39058a72760a2ac45960bfad0cd790d29bb83f9d0",  # 26.6
    "25G5065a": "1ef0e962a068c2a5fff8eba8a387179022f2ca57a30493e8c2ffeebe9fa5e70a",  # 26.6
}


METALLIB_ASSET_LIST:   list = None


def _darwin_build_sort_key(build: str) -> tuple[int, int, int, str]:
    """
    Order Apple build identifiers such as 25G83, 25G5052e and 25G5028f.

    Apple cycles the build letter through A-Z then a-z, so uppercase sorts ahead
    of lowercase within a cycle. Later seeds may carry a trailing letter, which
    is compared last so 25G5028f sorts after 25G5052.
    """
    match = re.fullmatch(r"(\d+)([A-Za-z])(\d*)([A-Za-z]?)", build.strip())
    if match is None:
        return (0, 0, 0, "")

    major, letter, number, suffix = match.groups()
    letter_index = ord(letter)
    if letter.islower():
        letter_index += 26

    return (int(major), letter_index, int(number or 0), suffix)


class MetalLibraryObject:

    def __init__(self, global_constants: constants.Constants,
                 host_build: str, host_version: str,
                 ignore_installed: bool = False, passive: bool = False
        ) -> None:

        self.constants: constants.Constants = global_constants

        self.host_build:   str = host_build    # ex. 20A5384c
        self.host_version: str = host_version  # ex. 11.0.1

        self.passive: bool = passive  # Don't perform actions requiring elevated privileges

        self.ignore_installed:      bool = ignore_installed   # If True, will ignore any installed MetallibSupportPkg PKGs and download the latest
        self.metallib_already_installed: bool = False

        self.metallib_installed_path: str = ""

        self.metallib_url:         str = ""
        self.metallib_url_build:   str = ""
        self.metallib_url_version: str = ""

        self.metallib_url_is_exactly_match: bool = False

        self.metallib_closest_match_url:         str = ""
        self.metallib_closest_match_url_build:   str = ""
        self.metallib_closest_match_url_version: str = ""

        self.metallib_expected_sha256: str = ""

        self.success: bool = False

        self.error_msg: str = ""

        self._get_latest_metallib()


    def _get_remote_metallibs(self) -> dict:
        """
        Get the MetallibSupportPkg list from the API
        """

        global METALLIB_ASSET_LIST

        logging.info("Pulling metallib list from MetallibSupportPkg API")
        if METALLIB_ASSET_LIST:
            return METALLIB_ASSET_LIST

        try:
            results = network_handler.NetworkUtilities().get(
                METALLIB_API_LINK,
                headers={
                    "User-Agent": f"OCLP/{self.constants.patcher_version}"
                },
                timeout=5
            )
        except (requests.exceptions.Timeout, requests.exceptions.TooManyRedirects, requests.exceptions.ConnectionError):
            logging.info("Could not contact MetallibSupportPkg API")
            return None

        try:
            if results.status_code != 200:
                logging.info("Could not fetch Metallib list")
                return None

            METALLIB_ASSET_LIST = results.json()

            return METALLIB_ASSET_LIST
        finally:
            results.close()


    def _get_latest_metallib(self) -> None:
        """
        Get the latest MetallibSupportPkg PKG
        """

        parsed_version = cast(packaging.version.Version, packaging.version.parse(self.host_version))

        if os_data.os_conversion.os_to_kernel(str(parsed_version.major)) < os_data.os_data.sequoia:
            self.error_msg = "MetallibSupportPkg is not required for macOS Sonoma or older"
            logging.warning(f"{self.error_msg}")
            return

        # Tahoe has no usable manifest, so resolve it from pinned upstream
        # releases instead before falling back to the manifest-based lookup.
        if os_data.os_conversion.os_to_kernel(str(parsed_version.major)) >= os_data.os_data.tahoe:
            if self._resolve_tahoe_metallib():
                return

        self.metallib_installed_path = self._local_metallib_installed()
        if self.metallib_installed_path:
            logging.info(f"metallib already installed ({Path(self.metallib_installed_path).name}), skipping")
            self.metallib_already_installed = True
            self.success = True
            return

        remote_metallib_version = self._get_remote_metallibs()

        if remote_metallib_version is None:
            logging.warning("Failed to fetch metallib list, falling back to local metallib matching")

            # First check if a metallib matching the current macOS version is installed
            # ex. 13.0.1 vs 13.0
            loose_version = f"{parsed_version.major}.{parsed_version.minor}"
            logging.info(f"Checking for metallibs loosely matching {loose_version}")
            self.metallib_installed_path = self._local_metallib_installed(match=loose_version, check_version=True)
            if self.metallib_installed_path:
                logging.info(f"Found matching metallib: {Path(self.metallib_installed_path).name}")
                self.metallib_already_installed = True
                self.success = True
                return

            older_version = f"{parsed_version.major}.{parsed_version.minor - 1 if parsed_version.minor > 0 else 0}"
            logging.info(f"Checking for metallibs matching {older_version}")
            self.metallib_installed_path = self._local_metallib_installed(match=older_version, check_version=True)
            if self.metallib_installed_path:
                logging.info(f"Found matching metallib: {Path(self.metallib_installed_path).name}")
                self.metallib_already_installed = True
                self.success = True
                return

            logging.warning(f"Couldn't find metallib matching {self.host_version} or {older_version}, please install one manually")

            self.error_msg = f"Could not contact MetallibSupportPkg API, and no metallib matching {self.host_version} ({self.host_build}) or {older_version} was installed.\nPlease ensure you have a network connection or manually install a metallib."

            return


        # First check exact match
        for metallib in remote_metallib_version:
            if (metallib["build"] != self.host_build):
                continue
            self.metallib_url = metallib["url"]
            self.metallib_url_build = metallib["build"]
            self.metallib_url_version = metallib["version"]
            self.metallib_url_is_exactly_match = True
            break

        # If no exact match, check for closest match
        if self.metallib_url == "":
            for metallib in remote_metallib_version:
                metallib_version = cast(packaging.version.Version, packaging.version.parse(metallib["version"]))
                if metallib_version > parsed_version:
                    continue
                if metallib_version.major != parsed_version.major:
                    continue
                if metallib_version.minor not in range(parsed_version.minor - 1, parsed_version.minor + 1):
                    continue

                # The metallib list is already sorted by version then date, so the first match is the closest
                self.metallib_closest_match_url = metallib["url"]
                self.metallib_closest_match_url_build = metallib["build"]
                self.metallib_closest_match_url_version = metallib["version"]
                self.metallib_url_is_exactly_match = False
                break

        if self.metallib_url == "":
            if self.metallib_closest_match_url == "":
                logging.warning(f"No metallibs found for {self.host_build} ({self.host_version})")
                self.error_msg = f"No metallibs found for {self.host_build} ({self.host_version})"
                return
            logging.info(f"No direct match found for {self.host_build}, falling back to closest match")
            logging.info(f"Closest Match: {self.metallib_closest_match_url_build} ({self.metallib_closest_match_url_version})")

            self.metallib_url = self.metallib_closest_match_url
            self.metallib_url_build = self.metallib_closest_match_url_build
            self.metallib_url_version = self.metallib_closest_match_url_version
        else:
            logging.info(f"Direct match found for {self.host_build} ({self.host_version})")


        # Check if this metallib is already installed
        self.metallib_installed_path = self._local_metallib_installed(match=self.metallib_url_build)
        if self.metallib_installed_path:
            logging.info(f"metallib already installed ({Path(self.metallib_installed_path).name}), skipping")
            self.metallib_already_installed = True
            self.success = True
            return

        logging.info("Following metallib is recommended:")
        logging.info(f"- metallib Build: {self.metallib_url_build}")
        logging.info(f"- metallib Version: {self.metallib_url_version}")
        logging.info(f"- metallib URL: {self.metallib_url}")

        self.success = True


    def _get_tahoe_releases(self) -> list[dict]:
        """
        Fetch pyquick/MetallibSupportPkg releases from the GitHub Releases API

        Returns a list of dicts with 'build', 'version', 'url' and 'sha256' keys,
        restricted to Darwin 25 assets whose digest matches a pinned entry.
        """

        logging.info("Pulling metallib list from pyquick MetallibSupportPkg releases API")
        try:
            results = network_handler.NetworkUtilities().get(
                TAHOE_METALLIB_RELEASES_API,
                headers={
                    "User-Agent": f"OCLP/{self.constants.patcher_version}",
                    "Accept": "application/vnd.github+json",
                },
                params={"per_page": 100},
                timeout=10
            )
        except (requests.exceptions.Timeout, requests.exceptions.TooManyRedirects, requests.exceptions.ConnectionError):
            logging.info("Could not contact pyquick MetallibSupportPkg releases API")
            return []

        if results is None:
            logging.info("Could not fetch pyquick MetallibSupportPkg releases")
            return []

        try:
            if results.status_code != 200:
                logging.info("Could not fetch pyquick MetallibSupportPkg releases")
                return []

            releases = []
            for release in results.json():
                tag = release.get("tag_name", "")
                if "-" not in tag:
                    continue
                version, build = tag.split("-", 1)

                for asset in release.get("assets", []):
                    if not asset.get("name", "").endswith(".pkg"):
                        continue

                    digest = (asset.get("digest") or "").removeprefix("sha256:")
                    pinned = TAHOE_METALLIB_PINNED_SHA256.get(build)
                    if pinned is None:
                        logging.warning(f"Ignoring unpinned metallib build {build}, refusing to install an unverified package")
                        continue
                    if digest != pinned:
                        logging.warning(f"Ignoring metallib build {build}: upstream digest does not match the pinned value")
                        continue

                    releases.append({
                        "build":   build,
                        "version": version,
                        "url":     asset.get("browser_download_url", ""),
                        "sha256":  pinned,
                    })

            return releases
        finally:
            results.close()


    def _resolve_tahoe_metallib(self) -> bool:
        """
        Resolve a pinned macOS Tahoe metallib package

        Returns True when a candidate was selected (and the installed-package
        check has already been handled by the caller), False to let the caller
        fall back to the manifest-based lookup.
        """

        releases = self._get_tahoe_releases()
        if not releases:
            return False

        for release in releases:
            if release["build"] != self.host_build:
                continue
            self._select_tahoe_release(release, exactly_match=True)
            return True

        # No exact build match: pick the closest release that is not newer than the
        # running system, so metallibs never downgrade past the host's own version.
        host_version = cast(packaging.version.Version, packaging.version.parse(self.host_version))
        eligible = []
        for release in releases:
            try:
                release_version = cast(packaging.version.Version, packaging.version.parse(release["version"]))
            except packaging.version.InvalidVersion:
                continue
            if release_version.major != host_version.major:
                continue
            if release_version > host_version:
                continue
            eligible.append((release_version, release))

        if not eligible:
            logging.warning(f"No pinned Tahoe metallib found for {self.host_build} ({self.host_version})")
            return False

        _, release = max(eligible, key=lambda item: (item[0], _darwin_build_sort_key(item[1]["build"])))
        logging.info(f"No direct match found for {self.host_build}, falling back to closest match")
        self._select_tahoe_release(release, exactly_match=False)
        return True


    def _select_tahoe_release(self, release: dict, exactly_match: bool) -> None:
        """
        Record a selected Tahoe release as the download candidate
        """

        self.metallib_url         = release["url"]
        self.metallib_url_build   = release["build"]
        self.metallib_url_version = release["version"]
        self.metallib_expected_sha256 = release["sha256"]
        self.metallib_url_is_exactly_match = exactly_match

        self.metallib_installed_path = self._local_metallib_installed(match=self.metallib_url_build)
        if self.metallib_installed_path:
            logging.info(f"metallib already installed ({Path(self.metallib_installed_path).name}), skipping")
            self.metallib_already_installed = True
            self.success = True
            return

        logging.info("Following metallib is recommended:")
        logging.info(f"- metallib Build: {self.metallib_url_build}")
        logging.info(f"- metallib Version: {self.metallib_url_version}")
        logging.info(f"- metallib URL: {self.metallib_url}")
        logging.info(f"- metallib SHA-256: {self.metallib_expected_sha256}")

        self.success = True


    def _local_metallib_installed(self, match: str = None, check_version: bool = False) -> str:
        """
        Check if a metallib is already installed
        """

        if self.ignore_installed:
            return None

        for install_path in METALLIB_INSTALL_PATHS:
            if not Path(install_path).exists():
                continue

            for metallib_folder in sorted(Path(install_path).iterdir(), reverse=True):
                if not metallib_folder.is_dir():
                    continue
                if check_version:
                    if match not in metallib_folder.name:
                        continue
                else:
                    if not metallib_folder.name.endswith(f"-{match}"):
                        continue

                return metallib_folder

        return None


    def verify_metallib(self, metallib_path: str = None) -> bool:
        """
        Verify a downloaded metallib package against its pinned SHA-256

        Refuses to proceed when no digest is pinned, so an unexpected package is
        never handed to installer.
        """

        path = Path(metallib_path if metallib_path else self.constants.metallib_download_path)

        if self.metallib_expected_sha256 == "":
            self.error_msg = "Refusing to install an unverified metallib package: no pinned SHA-256 for this build"
            logging.error(self.error_msg)
            self.success = False
            return False

        if not path.is_file():
            self.error_msg = f"Metallib package is missing: {path}"
            logging.error(self.error_msg)
            self.success = False
            return False

        digest = hashlib.sha256()
        try:
            with path.open("rb") as package:
                for chunk in iter(lambda: package.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as error:
            self.error_msg = f"Unable to read metallib package: {error}"
            logging.error(self.error_msg)
            self.success = False
            return False

        actual = digest.hexdigest()
        if actual != self.metallib_expected_sha256:
            self.error_msg = (
                f"Metallib package failed integrity check for build {self.metallib_url_build}\n"
                f"- Expected: {self.metallib_expected_sha256}\n"
                f"- Actual:   {actual}"
            )
            logging.error(self.error_msg)
            self.success = False
            return False

        logging.info(f"Metallib package verified against pinned SHA-256 ({actual})")
        return True


    def retrieve_download(self, override_path: str = "") -> network_handler.DownloadObject:
        """
        Retrieve MetallibSupportPkg PKG download object
        """

        self.success = False
        self.error_msg = ""

        if self.metallib_already_installed:
            logging.info("No download required, metallib already installed")
            self.success = True
            return None

        if self.metallib_url == "":
            self.error_msg = "Could not retrieve metallib catalog, no metallib to download"
            logging.error(self.error_msg)
            return None

        logging.info(f"Returning DownloadObject for metallib: {Path(self.metallib_url).name}")
        self.success = True

        metallib_download_path = self.constants.metallib_download_path if override_path == "" else Path(override_path)
        return network_handler.DownloadObject(self.metallib_url, metallib_download_path)


    def install_metallib(self, metallib: str = None) -> None:
        """
        Install MetallibSupportPkg PKG
        """

        if not self.success:
            logging.error("Cannot install metallib, no metallib was successfully retrieved")
            return False

        if self.metallib_already_installed:
            logging.info("No installation required, metallib already installed")
            return True

        # Verify integrity immediately before elevating, so a package that was
        # swapped after download is never installed with root privileges.
        if self.verify_metallib(metallib) is False:
            return False

        result = subprocess_wrapper.run_as_root([
            "/usr/sbin/installer", "-pkg", metallib if metallib else self.constants.metallib_download_path, "-target", "/"
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            subprocess_wrapper.log(result)
            return False

        return True