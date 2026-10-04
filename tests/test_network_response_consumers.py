"""Caller-owned HTTP responses are closed after their payloads are consumed."""

import ast
import plistlib
import tempfile
import types
import unittest

from collections import Counter
from pathlib import Path
from unittest import mock

from opencore_legacy_patcher.sucatalog import products as catalog_products
from opencore_legacy_patcher.sucatalog import products_appledb, url as catalog_url
from opencore_legacy_patcher.support import analytics_handler, kdk_handler, metallib_handler, updates
from opencore_legacy_patcher.wx_gui import gui_settings


class GetResponseConsumerTests(unittest.TestCase):
    def test_appledb_catalog_closes_response_after_parsing(self) -> None:
        response = mock.Mock()
        response.json.return_value = [{"version": "26.7.1"}]
        constants = types.SimpleNamespace(patcher_version="3.0.2")

        with mock.patch.object(products_appledb.network_handler.NetworkUtilities, "get", return_value=response):
            catalogue = products_appledb.AppleDBProducts(constants)

        self.assertEqual(catalogue.data, [{"version": "26.7.1"}])
        response.close.assert_called_once_with()

    def test_appledb_catalog_closes_response_when_json_parsing_fails(self) -> None:
        response = mock.Mock()
        response.json.side_effect = ValueError("not json")
        constants = types.SimpleNamespace(patcher_version="3.0.2")

        with mock.patch.object(products_appledb.network_handler.NetworkUtilities, "get", return_value=response):
            catalogue = products_appledb.AppleDBProducts(constants)

        self.assertEqual(catalogue.data, [])
        response.close.assert_called_once_with()

    def test_nightly_branch_lookup_closes_response_when_json_parsing_fails(self) -> None:
        response = mock.Mock()
        response.json.side_effect = ValueError("not json")
        settings = gui_settings.SettingsFrame.__new__(gui_settings.SettingsFrame)
        settings.constants = types.SimpleNamespace(commit_info=("Built from source",))

        with mock.patch.object(gui_settings.network_handler.NetworkUtilities, "get", return_value=response):
            with self.assertRaises(ValueError):
                settings.on_nightly(None)

        response.close.assert_called_once_with()

    def test_software_update_catalog_closes_response_after_parsing(self) -> None:
        response = mock.Mock(content=plistlib.dumps({"Products": {}}))
        catalog = catalog_url.CatalogURL()

        with mock.patch.object(catalog_url.network_handler.NetworkUtilities, "get", return_value=response):
            self.assertEqual(catalog.url_contents, {"Products": {}})

        response.close.assert_called_once_with()

    def test_software_update_catalog_closes_response_when_parsing_fails(self) -> None:
        response = mock.Mock(content=b"invalid plist")
        catalog = catalog_url.CatalogURL()

        with mock.patch.object(catalog_url.network_handler.NetworkUtilities, "get", return_value=response):
            self.assertIsNone(catalog.url_contents)

        response.close.assert_called_once_with()

    def test_update_check_closes_response_after_json_parse(self) -> None:
        response = mock.Mock()
        response.json.return_value = {"tag_name": "v3.0.3", "assets": []}
        constants = types.SimpleNamespace(
            patcher_version="3.0.2",
            special_build=False,
            repo_link="https://github.com/kgp-macPro/OCLP-CustoMac",
        )
        checker = updates.CheckBinaryUpdates(constants)

        with mock.patch.object(
            updates.network_handler.NetworkUtilities,
            "verify_network_connection",
            return_value=True,
        ):
            with mock.patch.object(updates.network_handler.NetworkUtilities, "get", return_value=response):
                self.assertIsNone(checker.check_binary_updates())

        response.close.assert_called_once_with()

    def test_kdk_catalog_closes_response_after_json_parse(self) -> None:
        response = mock.Mock(status_code=200)
        response.json.return_value = [{"build": "25G82"}]
        resolver = kdk_handler.KernelDebugKitObject.__new__(kdk_handler.KernelDebugKitObject)
        resolver.constants = types.SimpleNamespace(patcher_version="3.0.2")

        with mock.patch.object(kdk_handler, "KDK_ASSET_LIST", None):
            with mock.patch.object(kdk_handler.network_handler.NetworkUtilities, "get", return_value=response):
                self.assertEqual(resolver._get_remote_kdks(), [{"build": "25G82"}])

        response.close.assert_called_once_with()

    def test_kdk_catalog_closes_response_on_non_success_status(self) -> None:
        response = mock.Mock(status_code=503)
        resolver = kdk_handler.KernelDebugKitObject.__new__(kdk_handler.KernelDebugKitObject)
        resolver.constants = types.SimpleNamespace(patcher_version="3.0.2")

        with mock.patch.object(kdk_handler, "KDK_ASSET_LIST", None):
            with mock.patch.object(kdk_handler.network_handler.NetworkUtilities, "get", return_value=response):
                self.assertIsNone(resolver._get_remote_kdks())

        response.json.assert_not_called()
        response.close.assert_called_once_with()

    def test_metallib_catalog_closes_response_after_json_parse(self) -> None:
        response = mock.Mock(status_code=200)
        response.json.return_value = [{"version": "15.6"}]
        resolver = metallib_handler.MetalLibraryObject.__new__(metallib_handler.MetalLibraryObject)
        resolver.constants = types.SimpleNamespace(patcher_version="3.0.2")

        with mock.patch.object(metallib_handler, "METALLIB_ASSET_LIST", None):
            with mock.patch.object(metallib_handler.network_handler.NetworkUtilities, "get", return_value=response):
                self.assertEqual(resolver._get_remote_metallibs(), [{"version": "15.6"}])

        response.close.assert_called_once_with()

    def test_metallib_catalog_preserves_parsed_payload_and_closes_response(self) -> None:
        payload = [{"version": "15.6", "assets": [{"name": "Metallib.pkg"}]}]
        response = mock.Mock(status_code=200)
        response.json.return_value = payload
        resolver = metallib_handler.MetalLibraryObject.__new__(metallib_handler.MetalLibraryObject)
        resolver.constants = types.SimpleNamespace(patcher_version="3.0.2")

        with mock.patch.object(metallib_handler, "METALLIB_ASSET_LIST", None):
            with mock.patch.object(metallib_handler.network_handler.NetworkUtilities, "get", return_value=response):
                self.assertEqual(resolver._get_remote_metallibs(), payload)

        response.close.assert_called_once_with()

    def test_tahoe_metallib_releases_closes_response_on_non_success(self) -> None:
        response = mock.Mock(status_code=503)
        resolver = metallib_handler.MetalLibraryObject.__new__(metallib_handler.MetalLibraryObject)
        resolver.constants = types.SimpleNamespace(patcher_version="3.0.2")

        with mock.patch.object(metallib_handler.network_handler.NetworkUtilities, "get", return_value=response):
            self.assertEqual(resolver._get_tahoe_releases(), [])

        response.json.assert_not_called()
        response.close.assert_called_once_with()

    def test_catalog_product_package_responses_close_after_parsing(self) -> None:
        plist_response = mock.Mock(content=plistlib.dumps({"ProductVersion": "15.6"}))
        distribution_response = mock.Mock(content=b"<installer-gui-script/>\n")
        metadata_response = mock.Mock(content=plistlib.dumps({"CFBundleShortVersionString": "15.6"}))
        product = {
            "PostDate": "2026-01-01T00:00:00Z",
            "Packages": [
                {
                    "URL": "https://example.invalid/InstallAssistant.pkg",
                    "Size": 1,
                    "IntegrityDataURL": "https://example.invalid/integrity",
                    "IntegrityDataSize": 1,
                },
                {"URL": "https://example.invalid/Info.plist", "Size": 1},
            ],
            "Distributions": {"English": "https://example.invalid/English.dist"},
            "ServerMetadataURL": "https://example.invalid/metadata.plist",
        }
        responses = [plist_response, distribution_response, metadata_response]
        catalog = catalog_products.CatalogProducts(
            {"Products": {"fixture": product}},
            install_assistants_only=False,
        )

        with mock.patch.object(
            catalog_products.network_handler.NetworkUtilities,
            "get",
            side_effect=responses,
        ):
            self.assertEqual(len(catalog.products), 1)

        for response in responses:
            response.close.assert_called_once_with()

    def test_tahoe_metallib_releases_closes_response_after_parsing(self) -> None:
        response = mock.Mock(status_code=200)
        response.json.return_value = []
        resolver = metallib_handler.MetalLibraryObject.__new__(metallib_handler.MetalLibraryObject)
        resolver.constants = types.SimpleNamespace(patcher_version="3.0.2")

        with mock.patch.object(metallib_handler.network_handler.NetworkUtilities, "get", return_value=response):
            self.assertEqual(resolver._get_tahoe_releases(), [])

        response.close.assert_called_once_with()


class PostResponseConsumerTests(unittest.TestCase):
    def test_analytics_payload_post_closes_response(self) -> None:
        response = mock.Mock()
        analytics = analytics_handler.Analytics.__new__(analytics_handler.Analytics)
        analytics.data = "{}"

        with mock.patch.object(analytics_handler, "ANALYTICS_SERVER", "https://example.invalid/analytics"), \
             mock.patch.object(analytics_handler, "SITE_KEY", "site-key"), \
             mock.patch.object(analytics_handler.network_handler.NetworkUtilities, "post", return_value=response) as post:
            analytics._post_analytics_data()

        post.assert_called_once_with("https://example.invalid/analytics", json = "{}")
        response.close.assert_called_once_with()

    def test_crash_report_post_closes_response(self) -> None:
        response = mock.Mock()
        analytics = analytics_handler.Analytics.__new__(analytics_handler.Analytics)
        analytics.version = "3.0.3"
        analytics.os = "15.7.9"
        analytics.model = "MacBookAir9,1"
        analytics.date = "2026-10-01 00-00-00"
        analytics.constants = types.SimpleNamespace(commit_info=("refs/heads/main", "2026-10-01T00:00:00Z", "abc123"))

        with tempfile.TemporaryDirectory() as directory:
            log_file = Path(directory) / "crash.log"
            log_file.write_text("panic")

            with mock.patch.object(analytics_handler, "ANALYTICS_SERVER", "https://example.invalid"), \
                 mock.patch.object(analytics_handler, "SITE_KEY", "site-key"), \
                 mock.patch.object(analytics_handler.global_settings, "GlobalEnviromentSettings") as settings, \
                 mock.patch.object(analytics_handler.network_handler.NetworkUtilities, "post", return_value=response) as post:
                settings.return_value.read_property.return_value = False
                analytics.send_crash_report(log_file)

        post.assert_called_once()
        response.close.assert_called_once_with()


class DirectHttpCallInventoryTests(unittest.TestCase):
    """Every direct HTTP client call needs explicit ownership review."""

    def test_all_direct_http_calls_are_in_the_audited_inventory(self) -> None:
        root = Path(__file__).resolve().parents[1]
        allowed_calls = Counter({
            ("opencore_legacy_patcher/support/network_handler.py", "verify_network_connection", "requests", "head"): 1,
            ("opencore_legacy_patcher/support/network_handler.py", "validate_link", "SESSION", "head"): 1,
            ("opencore_legacy_patcher/support/network_handler.py", "get", "SESSION", "get"): 1,
            ("opencore_legacy_patcher/support/network_handler.py", "post", "SESSION", "post"): 1,
            ("opencore_legacy_patcher/support/network_handler.py", "_populate_file_size", "SESSION", "head"): 1,
            ("opencore_legacy_patcher/support/updates.py", "fetch_release_changelog", "requests", "get"): 1,
            ("ci_tooling/generate_psp_source_manifest.py", "fetch_inventory", "request", "get"): 2,
            ("ci_tooling/generate_tahoe_metallib_pins.py", "fetch_pins", "request", "get"): 1,
        })
        found_calls: Counter = Counter()

        class HttpCallVisitor(ast.NodeVisitor):
            def __init__(self, relative_path: str) -> None:
                self.relative_path = relative_path
                self.function_stack: list[str] = []
                self.calls: Counter = Counter()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.function_stack.append(node.name)
                self.generic_visit(node)
                self.function_stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                function = node.func
                if (
                    isinstance(function, ast.Attribute)
                    and function.attr in {"get", "post", "head", "put", "patch", "delete", "options", "request"}
                    and isinstance(function.value, ast.Name)
                    and function.value.id in {"requests", "SESSION", "request"}
                ):
                    owner = self.function_stack[-1] if self.function_stack else "<module>"
                    self.calls[(self.relative_path, owner, function.value.id, function.attr)] += 1
                self.generic_visit(node)

        for source_root in (root / "opencore_legacy_patcher", root / "ci_tooling"):
            for path in source_root.rglob("*.py"):
                relative_path = path.relative_to(root).as_posix()
                visitor = HttpCallVisitor(relative_path)
                visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
                found_calls.update(visitor.calls)

        self.assertEqual(
            found_calls,
            allowed_calls,
            "Direct HTTP call sites changed. Audit response ownership and closure, "
            "then update this inventory deliberately.",
        )

    def test_all_response_returning_network_utilities_calls_are_in_the_audited_inventory(self) -> None:
        root = Path(__file__).resolve().parents[1]
        allowed_calls = Counter({
            ("opencore_legacy_patcher/sucatalog/products_appledb.py", "__init__", "get"): 1,
            ("opencore_legacy_patcher/sucatalog/url.py", "url_contents", "get"): 1,
            ("opencore_legacy_patcher/sucatalog/products.py", "products", "get"): 3,
            ("opencore_legacy_patcher/support/network_handler.py", "_download", "get"): 1,
            ("opencore_legacy_patcher/support/updates.py", "check_binary_updates", "get"): 1,
            ("opencore_legacy_patcher/support/kdk_handler.py", "_get_remote_kdks", "get"): 1,
            ("opencore_legacy_patcher/support/analytics_handler.py", "send_crash_report", "post"): 1,
            ("opencore_legacy_patcher/support/analytics_handler.py", "_post_analytics_data", "post"): 1,
            ("opencore_legacy_patcher/support/metallib_handler.py", "_get_remote_metallibs", "get"): 1,
            ("opencore_legacy_patcher/support/metallib_handler.py", "_get_tahoe_releases", "get"): 1,
            ("opencore_legacy_patcher/wx_gui/gui_settings.py", "on_nightly", "get"): 1,
        })
        found_calls: Counter = Counter()

        class NetworkUtilitiesCallVisitor(ast.NodeVisitor):
            def __init__(self, relative_path: str) -> None:
                self.relative_path = relative_path
                self.function_stack: list[str] = []
                self.calls: Counter = Counter()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.function_stack.append(node.name)
                self.generic_visit(node)
                self.function_stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                function = node.func
                if (
                    isinstance(function, ast.Attribute)
                    and function.attr in {"get", "post"}
                    and isinstance(function.value, ast.Call)
                    and (
                        isinstance(function.value.func, ast.Name)
                        and function.value.func.id == "NetworkUtilities"
                        or isinstance(function.value.func, ast.Attribute)
                        and function.value.func.attr == "NetworkUtilities"
                    )
                ):
                    owner = self.function_stack[-1] if self.function_stack else "<module>"
                    self.calls[(self.relative_path, owner, function.attr)] += 1
                self.generic_visit(node)

        for path in (root / "opencore_legacy_patcher").rglob("*.py"):
            relative_path = path.relative_to(root).as_posix()
            visitor = NetworkUtilitiesCallVisitor(relative_path)
            visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
            found_calls.update(visitor.calls)

        self.assertEqual(
            found_calls,
            allowed_calls,
            "NetworkUtilities GET/POST call sites changed. Audit response ownership "
            "and closure, then update this inventory deliberately.",
        )


class ReleaseChangelogConsumerTests(unittest.TestCase):
    def test_changelog_closes_response_after_parsing(self) -> None:
        response = mock.Mock()
        response.json.return_value = {"body": "## Changes\n\nFixed\n## Asset Information\nignored"}

        with mock.patch.object(updates.requests, "get", return_value=response):
            self.assertEqual(updates.fetch_release_changelog(), "## Changes\n\nFixed\n")

        response.close.assert_called_once_with()

    def test_changelog_closes_response_without_a_body(self) -> None:
        response = mock.Mock()
        response.json.return_value = {"message": "API rate limit exceeded"}

        with mock.patch.object(updates.requests, "get", return_value=response):
            self.assertEqual(updates.fetch_release_changelog(), updates.CHANGELOG_FALLBACK)

        response.close.assert_called_once_with()

    def test_changelog_closes_response_when_json_parsing_fails(self) -> None:
        response = mock.Mock()
        response.json.side_effect = ValueError("not json")

        with mock.patch.object(updates.requests, "get", return_value=response):
            with self.assertRaises(ValueError):
                updates.fetch_release_changelog()

        response.close.assert_called_once_with()

    def test_gui_call_sites_fetch_through_the_shared_helper(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "opencore_legacy_patcher/wx_gui/gui_main_menu.py",
            "opencore_legacy_patcher/sys_patch/auto_patcher/start.py",
        ):
            source = (root / relative).read_text()
            self.assertNotIn("requests.get(", source, relative)
            self.assertIn("updates.fetch_release_changelog(", source, relative)


if __name__ == "__main__":
    unittest.main()
