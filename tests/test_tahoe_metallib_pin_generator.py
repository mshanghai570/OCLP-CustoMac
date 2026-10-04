"""
Regression tests for the pinned Tahoe metallib digest table and its generator.

The runtime resolver refuses any Tahoe metallib package that is not pinned by
SHA-256, so the table in `metallib_handler.py` is a security boundary rather
than a convenience cache. These tests keep it parseable, keep the generator's
canonical form matching what is checked in, and prove the generator cannot
rewrite anything except the pinned block.
"""

import hashlib
import importlib.util
import re
import tempfile
import unittest

from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "generate_tahoe_metallib_pins",
    REPO_ROOT / "ci_tooling" / "generate_tahoe_metallib_pins.py",
)
pin_generator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pin_generator)


PIN_LINE = re.compile(
    r'    "(?P<build>[^"]+)":\s+"(?P<digest>[0-9a-f]{64})",\s+# (?P<version>\S+)'
)

# Recorded in docs/TAHOE-INTEL-REPAIR-HANDOFF.md from the downloaded release asset.
DOCUMENTED_25G83_SHA256 = "3578553873558f97c7aba27722fb16ec63ab838a8b4d823c07e129c6df9c5867"


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _release(version: str, build: str, digest: str, asset_name: str = "MetallibSupportPkg.pkg") -> dict:
    return {
        "tag_name": f"{version}-{build}",
        "assets": [
            {
                "name": asset_name,
                "digest": f"sha256:{digest}",
                "browser_download_url": f"https://example.invalid/{version}-{build}/{asset_name}",
            }
        ],
    }


def _repository_pins() -> dict[str, tuple[str, str]]:
    """Recover `{build: (digest, version)}` from the checked-in source file"""

    pins = {}
    for line in pin_generator.TARGET_FILE.read_text().splitlines():
        match = PIN_LINE.fullmatch(line)
        if match is not None:
            pins[match.group("build")] = (match.group("digest"), match.group("version"))
    return pins


class _FakeResponse:

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.closed = False

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeSession:

    def __init__(self, pages: list) -> None:
        self.pages = pages
        self.calls: list = []
        self.responses: list[_FakeResponse] = []

    def get(self, url: str, **kwargs) -> _FakeResponse:
        self.calls.append((url, kwargs))
        page = int(kwargs["params"]["page"])
        payload = self.pages[page - 1] if page <= len(self.pages) else []
        response = _FakeResponse(payload)
        self.responses.append(response)
        return response


class TahoeMetallibPinGeneratorTests(unittest.TestCase):

    def test_repository_table_is_present_and_darwin25_only(self):
        pins = _repository_pins()

        self.assertEqual(len(pins), 55, "the pinned table should cover all 55 published Darwin 25 builds")
        for build in pins:
            self.assertRegex(build, r"^25[A-Za-z]\d+[A-Za-z]?$")
        for digest, _ in pins.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_repository_table_pins_the_documented_26_6_2_build(self):
        pins = _repository_pins()

        self.assertIn("25G83", pins)
        self.assertEqual(pins["25G83"][0], DOCUMENTED_25G83_SHA256)

    def test_fetch_pins_excludes_non_darwin25_and_unusable_assets(self):
        mixed_assets = {
            "tag_name": "26.1-25C74",
            "assets": [
                {"name": "MetallibSupportPkg.zip", "digest": f"sha256:{_digest('zip')}"},
                {"name": "MetallibSupportPkg.pkg", "digest": f"sha256:{_digest('pkg')}"},
            ],
        }
        pages = [[
            _release("26.0", "25A353", _digest("a")),
            _release("15.0", "24A335", _digest("b")),
            _release("26.0", "25A354", _digest("c"), asset_name="MetallibSupportPkg.zip"),
            _release("26.0", "25A362", _digest("d"), asset_name="Other.pkg"),
            mixed_assets,
            {
                "tag_name": "26.0-25A5295e",
                "assets": [{"name": "MetallibSupportPkg.pkg", "digest": "sha512:deadbeef"}],
            },
            {"tag_name": "notag", "assets": [{"name": "MetallibSupportPkg.pkg", "digest": f"sha256:{_digest('e')}"}]},
        ]]
        session = _FakeSession(pages)

        pins = pin_generator.fetch_pins(session=session, per_page=100)

        self.assertEqual(set(pins), {"25A353", "25A362", "25C74"})
        self.assertEqual(pins["25A353"], (_digest("a"), "26.0"))
        # A release carrying both a .zip and a .pkg is pinned from the .pkg only.
        self.assertEqual(pins["25C74"], (_digest("pkg"), "26.1"))
        # The runtime resolver pins any asset ending in .pkg regardless of its
        # base name, so the generator must not be stricter than the resolver.
        self.assertEqual(pins["25A362"], (_digest("d"), "26.0"))

    def test_fetch_pins_follows_pagination_until_a_short_page(self):
        first_page = [_release("26.0", f"25A{index}", _digest(str(index))) for index in range(100)]
        second_page = [_release("26.1", "25B1", _digest("last"))]
        session = _FakeSession([first_page, second_page])

        pins = pin_generator.fetch_pins(session=session, per_page=100)

        self.assertEqual(len(session.calls), 2)
        self.assertEqual(len(pins), 101)
        self.assertIn("25B1", pins)
        self.assertTrue(all(response.closed for response in session.responses))

    def test_fetch_pins_closes_response_when_http_status_fails(self):
        response = mock.Mock()
        response.raise_for_status.side_effect = RuntimeError("HTTP 500")
        session = mock.Mock()
        session.get.return_value = response

        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            pin_generator.fetch_pins(session=session)

        response.close.assert_called_once_with()

    def test_fetch_pins_closes_response_when_json_parsing_fails(self):
        response = mock.Mock()
        response.json.side_effect = ValueError("not JSON")
        session = mock.Mock()
        session.get.return_value = response

        with self.assertRaisesRegex(ValueError, "not JSON"):
            pin_generator.fetch_pins(session=session)

        response.close.assert_called_once_with()

    def test_render_pin_lines_uses_darwin_build_order(self):
        pins = {
            "25G5052e": (_digest("g"), "26.6"),
            "25A353":   (_digest("a"), "26.0"),
            "25C74":    (_digest("c"), "26.1"),
            "25A5279m": (_digest("am"), "26.0"),
        }

        lines = pin_generator.render_pin_lines(pins)

        builds = [line.split('"')[1] for line in lines]
        self.assertEqual(builds, ["25A353", "25A5279m", "25C74", "25G5052e"])

    def test_render_pin_lines_aligns_digests_and_records_version(self):
        pins = {
            "25A353":   (_digest("a"), "26.0"),
            "25A5279m": (_digest("b"), "26.0.1"),
        }

        lines = pin_generator.render_pin_lines(pins)

        first, second = lines
        self.assertIn("# 26.0", first)
        self.assertIn("# 26.0.1", second)

        # The key field is padded so every digest value starts in the same column.
        ordered = sorted(pins, key=pin_generator._darwin_build_sort_key)
        value_columns = [line.index(f'"{pins[build][0]}"') for line, build in zip(lines, ordered)]
        self.assertEqual(value_columns, [value_columns[0]] * len(lines), "digest values should be column aligned")

    def test_replace_pin_block_preserves_surrounding_source(self):
        source = (
            "before = 1\n"
            "\n"
            f"{pin_generator.ASSIGNMENT_HEADER}\n"
            '    "25A353": "deadbeef",\n'
            "}\n"
            "\n"
            "after = 2\n"
        )

        updated = pin_generator.replace_pin_block(source, {"25A353": (_digest("a"), "26.0")})

        self.assertTrue(updated.startswith("before = 1\n"))
        self.assertTrue(updated.endswith("after = 2\n"))
        self.assertIn(_digest("a"), updated)
        self.assertNotIn("deadbeef", updated)

    def test_replace_pin_block_requires_the_pinned_assignment(self):
        source = "before = 1\n"

        with self.assertRaises(LookupError):
            pin_generator.replace_pin_block(source, {"25A353": (_digest("a"), "26.0")})

    def test_describe_changes_reports_added_removed_and_changed(self):
        current = {"25A353": _digest("old"), "25C74": _digest("gone")}
        pins = {"25A353": (_digest("new"), "26.0"), "25G83": (_digest("added"), "26.6.2")}

        changes = pin_generator.describe_changes(current, pins)

        self.assertEqual(len(changes), 3)
        self.assertTrue(any("added" in change and "25G83" in change for change in changes))
        self.assertTrue(any("removed" in change and "25C74" in change for change in changes))
        self.assertTrue(any("changed" in change and "25A353" in change for change in changes))

    def test_check_reports_the_repository_table_as_current(self):
        with mock.patch.object(pin_generator, "fetch_pins", return_value=_repository_pins()):
            exit_code = pin_generator.main(["--check"])

        self.assertEqual(exit_code, 0, "the checked-in table must already be in the generator's canonical form")

    def test_check_returns_one_and_reports_drift(self):
        pins = _repository_pins()
        pins["25G83"] = (_digest("tampered"), pins["25G83"][1])

        with tempfile.TemporaryDirectory() as scratch:
            target = Path(scratch) / "metallib_handler.py"
            target.write_text(pin_generator.TARGET_FILE.read_text())
            with mock.patch.object(pin_generator, "fetch_pins", return_value=pins):
                exit_code = pin_generator.main(["--check", "--file", str(target)])

        self.assertEqual(exit_code, 1)

    def test_default_mode_rewrites_only_the_pinned_block(self):
        pins = _repository_pins()
        pins["25G83"] = (_digest("rotated"), pins["25G83"][1])
        original = pin_generator.TARGET_FILE.read_text()

        with tempfile.TemporaryDirectory() as scratch:
            target = Path(scratch) / "metallib_handler.py"
            target.write_text(original)
            with mock.patch.object(pin_generator, "fetch_pins", return_value=pins):
                exit_code = pin_generator.main(["--file", str(target)])
            updated = target.read_text()

        self.assertEqual(exit_code, 0)
        self.assertIn(_digest("rotated"), updated)

        def outside(text: str) -> list:
            return [line for line in text.splitlines() if not PIN_LINE.fullmatch(line)]

        # Every line outside the pinned block is untouched.
        self.assertEqual(outside(original), outside(updated))

    def test_stdout_mode_does_not_modify_the_file(self):
        before = pin_generator.TARGET_FILE.read_text()

        with mock.patch.object(pin_generator, "fetch_pins", return_value=_repository_pins()):
            exit_code = pin_generator.main(["--stdout"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(pin_generator.TARGET_FILE.read_text(), before)


if __name__ == "__main__":
    unittest.main()
