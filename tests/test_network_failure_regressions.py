"""Unavailable or malformed network metadata must leave usable caller state."""

import copy
import requests
import unittest

from types import SimpleNamespace
from unittest import mock

from opencore_legacy_patcher.sucatalog.products_appledb import AppleDBProducts
from opencore_legacy_patcher.support import network_handler, updates


def valid_release():
    return {
        "version": "26.0", "build": "25A1", "released": "2026-01-01",
        "deviceMap": ["MacPro7,1"], "sources": [{
            "type": "installassistant", "deviceMap": ["MacPro7,1"], "size": 100,
            "links": [{"active": True, "url": "https://example.invalid/InstallAssistant.pkg"}],
        }],
    }


class NetworkFailureTests(unittest.TestCase):
    def test_get_and_post_have_bounded_default_timeouts(self):
        for method in ("get", "post"):
            with self.subTest(method=method), mock.patch.object(network_handler.SESSION, method) as request:
                getattr(network_handler.NetworkUtilities(), method)("https://example.invalid")
                self.assertEqual(request.call_args.kwargs["timeout"], (5, 30))

    def test_explicit_timeout_is_preserved(self):
        with mock.patch.object(network_handler.SESSION, "get") as request:
            network_handler.NetworkUtilities().get("https://example.invalid", timeout=2)
        self.assertEqual(request.call_args.kwargs["timeout"], 2)

    def test_offline_changelog_returns_fallback(self):
        with mock.patch.object(updates.requests, "get", side_effect=requests.exceptions.ConnectionError("offline")):
            self.assertEqual(updates.fetch_release_changelog(), updates.CHANGELOG_FALLBACK)

    def test_malformed_update_releases_return_no_update(self):
        payloads = ([], None, {"tag_name": None}, {"tag_name": "3.0.4"},
                    {"tag_name": "3.0.4", "assets": None},
                    {"tag_name": "3.0.4", "assets": [None, {}, {"name": "OpenCore-Patcher.pkg"}]})
        constants = SimpleNamespace(patcher_version="3.0.3", special_build=False, repo_link="https://example.invalid")
        for payload in payloads:
            with self.subTest(payload=payload):
                response = mock.Mock()
                response.json.return_value = payload
                with mock.patch.object(network_handler.NetworkUtilities, "verify_network_connection", return_value=True), \
                     mock.patch.object(network_handler.NetworkUtilities, "get", return_value=response):
                    self.assertIsNone(updates.CheckBinaryUpdates(constants).check_binary_updates())
                response.close.assert_called_once_with()

    def test_invalid_update_json_returns_no_update(self):
        constants = SimpleNamespace(patcher_version="3.0.3", special_build=False)
        response = mock.Mock()
        response.json.side_effect = ValueError("invalid json")
        with mock.patch.object(network_handler.NetworkUtilities, "verify_network_connection", return_value=True), \
             mock.patch.object(network_handler.NetworkUtilities, "get", return_value=response):
            self.assertIsNone(updates.CheckBinaryUpdates(constants).check_binary_updates())
        response.close.assert_called_once_with()


class AppleDBFailureTests(unittest.TestCase):
    def catalog(self, response):
        with mock.patch.object(network_handler.NetworkUtilities, "get", return_value=response), \
             mock.patch.object(network_handler.NetworkUtilities, "validate_link", return_value=True):
            catalog = AppleDBProducts(SimpleNamespace(patcher_version="3.0.3"))
            products = catalog.latest_products
        return products

    def test_failed_fetch_leaves_usable_empty_catalog(self):
        response = mock.Mock()
        response.json.side_effect = ValueError("invalid json")
        self.assertEqual(self.catalog(response), [])

    def test_invalid_top_level_payload_is_an_empty_catalog(self):
        for payload in (None, {"error": "unavailable"}, "error"):
            with self.subTest(payload=payload):
                response = mock.Mock()
                response.json.return_value = payload
                self.assertEqual(self.catalog(response), [])

    def test_malformed_release_does_not_hide_following_valid_release(self):
        payloads = [None, {}, {"deviceMap": ["MacPro7,1"]}]
        for field, value in (("released", "bad date"), ("build", None), ("version", "invalid"),
                             ("deviceMap", 1), ("sources", None)):
            entry = valid_release()
            entry[field] = value
            payloads.append(entry)
        response = mock.Mock()
        response.json.return_value = payloads + [valid_release()]
        products = self.catalog(response)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["Build"], "25A1")

    def test_malformed_sources_do_not_hide_valid_source(self):
        entry = valid_release()
        entry["sources"] = [None, {}, {"type": "installassistant", "deviceMap": None},
                            {"type": "installassistant", "deviceMap": ["MacPro7,1"], "links": [None, {}]}] + entry["sources"]
        response = mock.Mock()
        response.json.return_value = [copy.deepcopy(entry)]
        self.assertEqual(len(self.catalog(response)), 1)


if __name__ == "__main__":
    unittest.main()
