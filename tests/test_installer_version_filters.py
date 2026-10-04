"""Installer catalog filters, which compared version components as text.

`sucatalog` decides which macOS installers to offer. Two of those decisions were
made on the string form of a version:

- `CatalogProducts._list_latest_installers_only` dropped end-of-life installers
  with `installer["Version"].split(".")[0] < supported_versions[-4].value`. Every
  release major in today's window has two digits, so the floor "13" happened to
  order correctly against "26", "15", "14" — but a single-digit major does not:
  a catalog entry for classic Mac OS "9.2.2" is older than 13 yet compares as
  newer, so it would have been offered as a supported installer. Splitting the
  leading component collapses every 10.x release to "10", which is a *prefix* of
  a two-component floor like "10.12" and therefore compares as older than it, so
  against a Catalina-era floor the floor's own release was thrown away.
- `AppleDBProducts` capped its window at `os_data.sequoia` while the Software
  Update catalogue and the rest of the patcher had moved to Tahoe. That is the
  window the downloader GUI relies on (it constructs `AppleDBProducts` with the
  constants alone), so the app could not offer a macOS 26 installer at all.

These run without a network: `_list_latest_installers_only` is pure list
manipulation over product dicts, and the AppleDB window is decided by `max_ia`
alone, so both are driven directly.
"""

import inspect
import json
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.datasets.os_data import os_conversion, os_data
from opencore_legacy_patcher.sucatalog import AppleDBProducts, CatalogProducts
from opencore_legacy_patcher.sucatalog import products as products_module
from opencore_legacy_patcher.sucatalog.constants import CatalogVersion, SeedType
from opencore_legacy_patcher.support import network_handler


APPLEDB_SLICE: Path = Path(__file__).resolve().parent / "fixtures" / "appledb_macos_slice.json"


def catalog_product(version: str | None, catalog: SeedType = SeedType.PublicRelease) -> dict:
    """A product shaped like the ones `CatalogProducts.products` builds."""
    return {
        "ProductID": f"001-{version}",
        "PostDate": "2024-01-01T00:00:00Z",
        "Title": None,
        "Build": None,
        "Version": version,
        "Catalog": catalog,
    }


def appledb_product(major: int, raw_version: str, beta: bool = False) -> dict:
    """A product shaped like the ones `AppleDBProducts.products` builds."""
    return {
        "PostDate": "2024-01-01T00:00:00Z",
        "Title": f"macOS {os_data(major).name}",
        "Build": raw_version,
        "RawVersion": raw_version,
        "Version": raw_version,
        "Beta": beta,
        "InstallAssistant": {"XNUMajor": major},
    }


class EndOfLifeFloorTests(unittest.TestCase):
    """The `older than n-3` pass, which has to order numbers as numbers."""

    def _latest(self, versions: tuple, ceiling: CatalogVersion = CatalogVersion.TAHOE) -> list:
        catalog = CatalogProducts({}, max_install_assistant_version=ceiling)
        return catalog._list_latest_installers_only([catalog_product(version) for version in versions])

    def _latest_versions(self, versions: tuple, ceiling: CatalogVersion = CatalogVersion.TAHOE) -> list:
        return [product["Version"] for product in self._latest(versions, ceiling)]

    def test_installers_in_the_window_survive(self) -> None:
        versions = ("13.6.9", "14.7.10", "15.6.1", "26.0")
        self.assertEqual(set(self._latest_versions(versions)), set(versions))

    def test_installers_below_the_floor_are_dropped(self) -> None:
        versions = ("12.7.6", "13.6.9", "26.0")
        self.assertEqual(set(self._latest_versions(versions)), {"13.6.9", "26.0"})

    def test_a_single_digit_major_is_below_the_floor(self) -> None:
        """A classic Mac OS entry is older than 13 even though "9" > "13" as text."""

        self.assertEqual(self._latest_versions(("9.2.2", "13.6.9")), ["13.6.9"])

    def test_a_two_component_floor_keeps_its_own_release(self) -> None:
        """"10.12.6" is at the floor, not below it, although "10" < "10.12" as text."""

        kept = self._latest_versions(("10.11.6", "10.12.6", "10.13.6"), ceiling=CatalogVersion.CATALINA)
        self.assertEqual(set(kept), {"10.12.6", "10.13.6"})

    def test_unorderable_versions_are_left_alone(self) -> None:
        """Pre-release names and empty versions carry no number to compare."""

        kept = set(self._latest_versions((None, "", "mountainlion", "13.6.9")))
        self.assertEqual(kept, {"", "mountainlion", "13.6.9"})

    def test_a_floor_that_is_not_a_number_is_not_guessed(self) -> None:
        """A window that has run off the numbered releases must not raise.

        The El Capitan window reaches back to the last named release, so its
        floor carries no number and the pass has nothing to compare against.
        """

        kept = self._latest_versions(("10.11.6", "10.13.6"), ceiling=CatalogVersion.EL_CAPITAN)
        self.assertEqual(set(kept), {"10.11.6", "10.13.6"})


class ProductOrderingTests(unittest.TestCase):
    """The catalogue sort, which is also over version components."""

    def _sorted(self, versions: tuple) -> list:
        products = [catalog_product(version) for version in versions]
        return [product["Version"] for product in products_module._sort_products(products)]

    def test_a_single_digit_major_sorts_below_a_two_digit_one(self) -> None:
        self.assertEqual(self._sorted(("26.0", "9.2.2", "13.6.9")), ["9.2.2", "13.6.9", "26.0"])

    def test_shipped_releases_keep_the_order_text_gave_them(self) -> None:
        """Today's window is unchanged: all of its majors have two digits."""

        versions = ("26.0.1", "13.0", "15.6", "14.7.10", "26.0", "13.6.9", "14.0")
        self.assertEqual(self._sorted(versions), sorted(versions))


class AppleDBCatalogWindowTests(unittest.TestCase):
    """The AppleDB window that the installer downloader builds on."""

    def _window(self, majors: tuple, ceiling: os_data) -> set:
        instance = object.__new__(AppleDBProducts)  # __init__ fetches the API
        instance.max_ia = ceiling
        products = [appledb_product(major, f"{major}.0") for major in majors]
        return {product["InstallAssistant"]["XNUMajor"] for product in instance._list_latest_installers_only(products)}

    def test_the_window_spans_the_latest_four_kernel_majors(self) -> None:
        self.assertEqual(self._window((21, 22, 23, 24, 25), os_data.tahoe), {22, 23, 24, 25})

    def test_the_default_ceiling_is_the_release_the_patcher_ships(self) -> None:
        default = inspect.signature(AppleDBProducts.__init__).parameters["max_install_assistant_version"].default
        self.assertEqual(default, os_data.tahoe)

    def test_the_default_window_offers_tahoe(self) -> None:
        default = inspect.signature(AppleDBProducts.__init__).parameters["max_install_assistant_version"].default
        self.assertIn(int(os_data.tahoe), self._window((21, 22, 23, 24, 25), default))

    def test_both_installer_catalogues_share_one_ceiling(self) -> None:
        """The Software Update catalogue and AppleDB must cap at the same release."""

        catalog_ceiling = inspect.signature(CatalogProducts.__init__).parameters["max_install_assistant_version"].default
        appledb_ceiling = inspect.signature(AppleDBProducts.__init__).parameters["max_install_assistant_version"].default
        self.assertEqual(os_conversion.os_to_kernel(catalog_ceiling.value), int(appledb_ceiling))


class RecordedAppleDBSliceTests(unittest.TestCase):
    """The live AppleDB catalogue, recorded, driving the real code path

    Fetched 2026-09-29: 3,183 firmware entries, 33 of which passed the filters in
    the window Darwin 21-25. `fixtures/appledb_macos_slice.json` holds those
    releases trimmed to the fields the catalogue reads, so the ceiling that
    decided whether a macOS 26 installer could be offered at all is pinned
    against real Apple data rather than a hand-written dict. Only the API fetch
    and the per-link HEAD request are stood in for; everything between them is
    the shipped code.
    """

    def _catalogue(self, ceiling: os_data | None = None, payload: list | None = None) -> tuple[list, list]:
        """Drive the real catalogue over a recorded payload, links assumed live

        Returns ``(products, latest_products)``, both evaluated while the fetch and
        the per-link check are stubbed. `products` is a cached property that HEADs
        every link, so reading it after the patches came down would quietly make
        these tests need a network.
        """
        if payload is None:
            payload = json.loads(APPLEDB_SLICE.read_text(encoding="utf-8"))
        constants = SimpleNamespace(patcher_version="0.0.0-fixture")
        response = SimpleNamespace(json=lambda: payload, close=mock.Mock())
        with mock.patch.object(
                 network_handler.NetworkUtilities, "get", return_value=response
             ), \
             mock.patch.object(network_handler.NetworkUtilities, "validate_link", return_value=True):
            catalogue = (
                AppleDBProducts(constants)
                if ceiling is None
                else AppleDBProducts(constants, max_install_assistant_version=ceiling)
            )
            result = catalogue.products, catalogue.latest_products
        response.close.assert_called_once_with()
        return result

    def test_the_shipped_ceiling_offers_a_tahoe_installer(self) -> None:
        _, latest = self._catalogue()
        self.assertEqual([product["InstallAssistant"]["XNUMajor"] for product in latest], [22, 23, 24, 25])

        tahoe = latest[-1]
        self.assertEqual(tahoe["Version"], "26.7.1")
        self.assertEqual(tahoe["Build"], "25G241")
        self.assertFalse(tahoe["Beta"])
        self.assertEqual(tahoe["Title"], "macOS Tahoe")
        self.assertTrue(tahoe["InstallAssistant"]["URL"].startswith("https://swcdn.apple.com/"))

    def test_the_ceiling_from_before_the_repair_offers_no_tahoe(self) -> None:
        """`os_data.sequoia` was the default: macOS 26 could not be offered at all"""

        _, latest = self._catalogue(os_data.sequoia)
        majors = [product["InstallAssistant"]["XNUMajor"] for product in latest]
        self.assertEqual(majors, [21, 22, 23, 24])
        self.assertNotIn(int(os_data.tahoe), majors)

    def test_the_newest_release_wins_inside_a_major(self) -> None:
        """15.8 is recorded beside 15.8.1, and 26.7 beside 26.7.1"""

        _, latest = self._catalogue()
        by_major = {product["InstallAssistant"]["XNUMajor"]: product for product in latest}
        self.assertEqual((by_major[24]["Version"], by_major[24]["Build"]), ("15.8.1", "24H32"))
        self.assertEqual((by_major[25]["Version"], by_major[25]["Build"]), ("26.7.1", "25G241"))

    def test_the_oldest_major_inside_a_window_is_offered_too(self) -> None:
        """12.7.4 is recorded beside 12.7.6, and only 12.7.6 is offered"""

        _, latest = self._catalogue(os_data.sequoia)
        by_major = {product["InstallAssistant"]["XNUMajor"]: product for product in latest}
        self.assertEqual((by_major[21]["Version"], by_major[21]["Build"]), ("12.7.6", "21H1320"))

    def test_a_firmware_entry_without_a_beta_flag_is_not_a_beta(self) -> None:
        """The catalogue must not depend on AppleDB sending `beta` on every entry

        It does today, but resting on that made `Beta` None and the ordering pass
        compares the flags.
        """

        payload = [
            {
                "version": "26.7.1",
                "build": "25G241",
                "released": "2026-09-28",
                "deviceMap": ["MacPro7,1"],
                "sources": [
                    {
                        "type": "installassistant",
                        "deviceMap": ["MacPro7,1"],
                        "links": [{"url": "https://example.invalid/InstallAssistant.pkg", "active": True}],
                    }
                ],
            }
        ]
        products, _ = self._catalogue(payload=payload)
        self.assertEqual([product["Beta"] for product in products], [False])

    def test_an_rc_that_was_the_final_release_is_offered_once_as_a_release(self) -> None:
        """21H1320 is recorded twice: as 12.7.6 and as 12.7.6 RC 5"""

        products, _ = self._catalogue()
        offered = [product for product in products if product["Build"] == "21H1320"]
        self.assertEqual(len(offered), 1)
        self.assertFalse(offered[0]["Beta"])
        self.assertEqual(offered[0]["Version"], "12.7.6")


if __name__ == "__main__":
    unittest.main()
