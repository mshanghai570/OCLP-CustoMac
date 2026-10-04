"""
Release numbers must be numbers, and named once

The rules live in `ci_tooling/build_modules/version_contract.py`, because the
build gate runs them too; these tests pin each shape they recognise, the
legitimate forms they must stay quiet on, and the tree itself.

`GuardSelfTest` covers the scanner on code written for it, one shape at a time.
`VersionHandlingIsGuarded` covers this repository: a whole-tree scan, proof the
scan still reaches the files this fork had to repair, and a revert of each real
fix — rewritten in memory and on disk — that must trip the scanner.
"""

import unittest

from pathlib import Path
from unittest import mock

from ci_tooling.build_modules import disk_images, version_contract
from ci_tooling.build_modules.version_contract import (
    DECLARED_RELEASE_MODULES,
    FILES_WITH_REPAIRED_VERSION_HANDLING,
    PRAGMA,
    PRAGMA_MINIMUM_REASON,
    REPO_ROOT,
    VersionContractError,
    Violation,
    declared_release_numbers,
    iter_production_modules,
    mishandled_release_numbers,
    scan_path,
    scan_source,
    validate_source,
)


def mutate(source: str, old: str, new: str) -> str:
    """Textual replacement that refuses to quietly match nothing"""
    assert old in source, f"mutation target not found: {old!r}"
    return source.replace(old, new, 1)


class GuardSelfTest(unittest.TestCase):
    """The four shapes that were actually shipped, and the fixes that replaced them"""

    def assert_flagged(self, source: str, kind: str) -> Violation:
        violations, _ = scan_source(source)
        self.assertTrue(violations, f"scan missed:\n{source}")
        self.assertEqual(violations[0].kind, kind)
        return violations[0]

    def assert_clean(self, source: str) -> None:
        violations, _ = scan_source(source)
        self.assertEqual([str(violation) for violation in violations], [], f"scan false-positived on:\n{source}")

    def test_flags_component_compared_as_text(self):
        self.assert_flagged(
            """
for installer in products:
    if installer["Version"].split(".")[0] < supported_versions[-4].value:
        del installer
""",
            "version-component-ordering",
        )

    def test_flags_character_indexed_version(self):
        self.assert_flagged(
            """
def needs_flag(platform_version):
    if platform_version[0] == "10":
        return True
    return False
""",
            "character-indexed-version",
        )

    def test_flags_character_index_cast_to_int(self):
        self.assert_flagged(
            """
def needs_flag(platform_version):
    return int(platform_version[1]) < 13
""",
            "character-indexed-version",
        )

    def test_flags_version_field_ordering(self):
        self.assert_flagged(
            """
def newest(left, right):
    return left["Version"] > right["Version"]
""",
            "version-field-ordering",
        )

    def test_flags_sorting_by_version_text(self):
        self.assert_flagged(
            """
def order(products):
    products.sort(key=lambda product: product["Version"])
    return products
""",
            "version-text-sort",
        )

    def test_flags_sorting_by_a_version_field(self):
        """The shape the scan found in ``LocalInstallerCatalog``"""
        self.assert_flagged(
            """
def order(application_list):
    return {k: v for k, v in sorted(application_list.items(), key=lambda item: item[1]["Version"])}
""",
            "version-text-sort",
        )

    def test_flags_a_bare_release_number(self):
        self.assert_flagged(
            """
def is_tahoe(version):
    return version.major == 26
""",
            "bare-release-number",
        )

    def test_flags_a_bare_darwin_major(self):
        self.assert_flagged(
            """
def is_tahoe(build_match):
    return int(build_match.group(1)) == 25
""",
            "bare-release-number",
        )

    def test_flags_a_bare_os_minor(self):
        self.assert_flagged(
            """
def needs_new_compiler(constants):
    if constants.detected_os_minor < 4:
        return False
    return True
""",
            "bare-release-number",
        )

    def test_allows_a_named_release_constant(self):
        self.assert_clean(
            """
def is_tahoe(version):
    return version.major == _TAHOE_MARKETING_MAJOR


def needs_new_compiler(constants):
    return constants.detected_os_minor >= VENTURA_GPU_COMPILER_MINOR
"""
        )

    def test_allows_a_presence_sentinel(self):
        """``<= 0`` marks the .0 build, and ``== 0`` an unset field"""
        self.assert_clean(
            """
def native_os(self):
    if self._xnu_minor <= 0:
        return True
    if self._kernel == 0:
        return True
    return False
"""
        )

    def test_allows_a_build_identity(self):
        """A build identifier is not a release number"""
        self.assert_clean(
            """
def is_beta(self):
    return self._os_build != "21A5522h"
"""
        )

    def test_flags_a_duplicated_release_declaration(self):
        """The shape the KDK policy took before it derived its number"""
        self.assert_flagged(
            """
BLOCKED_ROOT_PATCH_KDK_DARWIN_MAJORS = frozenset({26})
""",
            "duplicated-release-declaration",
        )

    def test_flags_a_duplicated_marketing_version(self):
        self.assert_flagged(
            """
TAHOE_MARKETING_VERSION: str = "26.0"
""",
            "duplicated-release-declaration",
        )

    def test_flags_a_duplicated_darwin_major(self):
        self.assert_flagged(
            """
DARWIN_MAJOR: int = 25
""",
            "duplicated-release-declaration",
        )

    def test_allows_a_declaration_that_derives_its_number(self):
        self.assert_clean(
            """
BLOCKED_ROOT_PATCH_KDK_DARWIN_MAJORS = frozenset({TAHOE_DARWIN_MAJOR + 1})
TAHOE_MARKETING_VERSION: str = f"{os_conversion.kernel_to_os(os_data.tahoe)}.0"
DARWIN_MAJOR: int = int(os_data.tahoe)
"""
        )

    def test_allows_a_declaration_that_is_not_a_release(self):
        """A compression library, a GPU compiler and a build identifier"""
        self.assert_clean(
            """
self.apfs_zlib_version: str = "12.3.1"
BASE_VERSION = "31001"
TAHOE_BUILD: str = "25A5316i"
"""
        )

    def test_flags_a_release_number_in_a_message(self):
        self.assert_flagged(
            """
def report():
    logging.warning("Ignoring prohibited Darwin 26 KDK")
""",
            "release-number-in-message",
        )

    def test_flags_a_release_number_in_an_error_message(self):
        self.assert_flagged(
            """
def refuse():
    raise Exception("macOS 26 Kernel Debug Kits are prohibited for root patching")
""",
            "release-number-in-message",
        )

    def test_allows_a_message_that_derives_the_release(self):
        self.assert_clean(
            """
def report(label):
    logging.warning(f"Ignoring prohibited Darwin {label} KDK")
"""
        )

    def test_allows_a_docstring_that_names_a_release(self):
        """Documentation cannot derive anything, so it is not a message"""
        self.assert_clean(
            """
def patch():
    '''Supported on macOS 11.0 and newer, dropped in macOS 26.'''
    return True
"""
        )

    def test_allows_prose_about_something_other_than_a_release(self):
        """``30min+`` is a duration, not a release"""
        self.assert_clean(
            """
def label():
    return "Creating macOS installers can take 30min+ on slower USB drives."
"""
        )

    def test_the_datasets_are_the_source_of_the_declared_numbers(self):
        declared = declared_release_numbers()
        for release in (26, 25, 15, 11):
            self.assertIn(release, declared, f"{release} is a release the datasets declare")
        for not_a_release in (30, 60, 9):
            self.assertNotIn(not_a_release, declared)

    def test_allows_a_word_split_in_a_membership_test(self):
        """``boot_args.split()`` reads words, not version components"""
        self.assert_clean(
            """
def has_beta_flag(nvram):
    boot_args = nvram["boot-args"]
    if "-amfipassbeta" in boot_args.split():
        return True
    return False
"""
        )

    def test_pragma_suppresses_a_finding(self):
        """The allowlist has to actually allow, on the flagged line or above it"""
        violation = self.assert_flagged(
            """
def sort(a, b):
    return a["Version"] > b["Version"]
""",
            "version-field-ordering",
        )
        self.assertEqual(violation.lineno, 3)
        for pragma_line in (3, 2):
            lines = ["", "def sort(a, b):", '    return a["Version"] > b["Version"]']
            lines[pragma_line - 1] += f"  {PRAGMA} the caller already parsed both sides"
            with self.subTest(pragma_line=pragma_line):
                violations, pragmas = scan_source("\n".join(lines))
                self.assertEqual(len(pragmas), 1)
                self.assertEqual([str(found) for found in violations], [])
                unsuppressed, _ = scan_source("\n".join(lines), respect_pragmas=False)
                self.assertEqual(len(unsuppressed), 1)

    def test_a_string_is_not_a_pragma(self):
        """Only a comment suppresses a finding, not a string that quotes one"""
        violations, pragmas = scan_source(
            '''
def sort(a, b):
    """# version-text-ok: quoting the syntax does not excuse the next line"""
    return a["Version"] > b["Version"]
'''
        )
        self.assertEqual([violation.kind for violation in violations], ["version-field-ordering"])
        self.assertEqual(pragmas, [])

    def test_allows_numeric_version_comparison(self):
        self.assert_clean(
            """
def is_supported(installer, eol_floor):
    installer_version = _parse_numeric_version(installer["Version"])
    if installer_version is None:
        return False
    return installer_version < eol_floor
"""
        )

    def test_allows_component_of_a_parsed_version(self):
        self.assert_clean(
            """
def requires_applicationpath(platform_version):
    components = str(platform_version).split(".")
    if components[0] != "10":
        return False
    try:
        minor = int(components[1])
    except (IndexError, ValueError):
        return False
    return os_data.os_conversion.os_to_kernel(f"10.{minor}") < APPLICATIONPATH_DROPPED_AT
"""
        )

    def test_allows_kernel_major_arithmetic(self):
        self.assert_clean(
            """
def os_to_kernel(os):
    major = int(os.split(".")[0])
    if major > os_data.tahoe.value:
        return major - 1
    return major + 9
"""
        )

    def test_allows_sorting_by_a_key_that_parses(self):
        self.assert_clean(
            """
def order(products):
    return sorted(products, key=_version_sort_key)


def order_versions(versions):
    versions.sort(key=lambda v: (not v["Beta"], packaging.version.parse(v["RawVersion"])), reverse=True)
    return versions
"""
        )


class VersionHandlingIsGuarded(unittest.TestCase):
    """The tree itself, plus proof the scan still reaches the repaired files"""

    def test_no_production_code_mishandles_a_release_number(self):
        offenders = mishandled_release_numbers()
        self.assertEqual(
            offenders,
            [],
            "production code mishandles release numbers:\n" + "\n".join(offenders),
        )

    def test_scan_reaches_the_repaired_files(self):
        scanned = {str(path.relative_to(REPO_ROOT)) for path in iter_production_modules()}
        for expected in FILES_WITH_REPAIRED_VERSION_HANDLING:
            self.assertIn(expected, scanned)

    def test_guard_catches_the_reverted_floor(self):
        """Reversing the real fix in the real file must trip the scan"""
        path = REPO_ROOT / "opencore_legacy_patcher/sucatalog/products.py"
        source = path.read_text(encoding="utf-8")
        reverted = mutate(
            source,
            "                if installer_version < eol_floor:",
            '                if installer["Version"].split(".")[0] < supported_versions[0].value:',
        )
        violations, _ = scan_source(reverted, filename=str(path))
        self.assertEqual([violation.kind for violation in violations], ["version-component-ordering"])

    def test_guard_catches_the_reverted_character_index(self):
        """The dead ``--applicationpath`` branch, restored, must trip the scan"""
        path = REPO_ROOT / "opencore_legacy_patcher/support/macos_installer_handler.py"
        source = path.read_text(encoding="utf-8")
        reverted = mutate(
            source,
            '    return os_data.os_conversion.os_to_kernel(f"10.{minor}") < APPLICATIONPATH_DROPPED_AT',
            '    return int(platform_version[1]) < 13',
        )
        violations, _ = scan_source(reverted, filename=str(path))
        self.assertEqual([violation.kind for violation in violations], ["character-indexed-version"])

    def test_guard_catches_the_reverted_installer_sort(self):
        """The text sort the scan found in the local installer catalog"""
        path = REPO_ROOT / "opencore_legacy_patcher/support/macos_installer_handler.py"
        source = path.read_text(encoding="utf-8")
        reverted = mutate(
            source,
            '                key=lambda item: _version_order_key(item[1]["Version"]),',
            '                key=lambda item: item[1]["Version"],',
        )
        violations, _ = scan_source(reverted, filename=str(path))
        self.assertEqual([violation.kind for violation in violations], ["version-text-sort"])

    def test_guard_catches_the_reverted_tahoe_literal(self):
        """The marketing major, spelled out again in the real file, must trip the scan"""
        path = REPO_ROOT / "opencore_legacy_patcher/support/kdk_selection.py"
        source = path.read_text(encoding="utf-8")
        reverted = mutate(
            source,
            "            version.major == TAHOE_MARKETING_MAJOR",
            "            version.major == 26",
        )
        violations, _ = scan_source(reverted, filename=str(path))
        self.assertEqual([violation.kind for violation in violations], ["bare-release-number"])

    def test_guard_catches_the_reverted_filevault_message(self):
        """The release named in the FileVault 2 message, spelled out again"""
        path = REPO_ROOT / "opencore_legacy_patcher/efi_builder/firmware.py"
        source = path.read_text(encoding="utf-8")
        reverted = mutate(
            source,
            '        logging.info(f"- Enabling macOS {target_release} FileVault 2 support")',
            '        logging.info("- Enabling macOS 26 FileVault 2 support")',
        )
        violations, _ = scan_source(reverted, filename=str(path))
        self.assertEqual([violation.kind for violation in violations], ["release-number-in-message"])

    def test_guard_catches_the_reverted_marketing_version(self):
        """The Tahoe contract version, spelled out again, must trip the scan"""
        path = REPO_ROOT / "ci_tooling/build_modules/payload_contract.py"
        source = path.read_text(encoding="utf-8")
        reverted = mutate(
            source,
            'TAHOE_MARKETING_VERSION: str = f"{os_conversion.kernel_to_os(os_data.tahoe)}.0"',
            'TAHOE_MARKETING_VERSION: str = "26.0"',
        )
        violations, _ = scan_source(reverted, filename=str(path))
        self.assertEqual([violation.kind for violation in violations], ["duplicated-release-declaration"])

    def test_the_exempt_modules_are_exempt_and_still_scanned(self):
        """`os_data.tahoe = 25` is the declaration, not a copy of it"""
        for relative in DECLARED_RELEASE_MODULES + (version_contract.RULE_MODULE,):
            with self.subTest(module=relative):
                violations, _ = scan_path(REPO_ROOT / relative)
                self.assertEqual([str(violation) for violation in violations], [])
        scanned = {str(path.relative_to(REPO_ROOT)) for path in iter_production_modules()}
        for relative in DECLARED_RELEASE_MODULES:
            self.assertIn(relative, scanned)

    def test_pragmas_carry_a_reason_and_suppress_something(self):
        for path in iter_production_modules():
            source = path.read_text(encoding="utf-8")
            _, pragmas = scan_source(source, filename=str(path))
            unsuppressed, _ = scan_source(source, filename=str(path), respect_pragmas=False)
            for lineno, reason in pragmas:
                self.assertGreaterEqual(
                    len(reason), PRAGMA_MINIMUM_REASON, f"{path.name}:{lineno} pragma reason is too thin: {reason!r}"
                )
                flagged = [violation for violation in unsuppressed if violation.lineno in {lineno, lineno + 1}]
                self.assertTrue(flagged, f"{path.name}:{lineno} pragma suppresses nothing")


class TheBuildGateRunsTheSameContract(unittest.TestCase):
    """The build gate and the suite answer to one implementation"""

    def test_validate_source_passes_on_this_tree(self):
        validate_source()

    def test_validate_source_fails_closed_on_a_finding(self):
        finding = Violation("version-text-sort", 1, "")
        with mock.patch.object(version_contract, "scan_path", return_value=([finding], [])):
            with self.assertRaises(VersionContractError) as raised:
                validate_source()
        self.assertIn("version-text-sort", str(raised.exception))

    def test_the_build_stops_before_it_touches_the_working_tree(self):
        """`generate` deletes payloads and writes images, so the source gate is first"""
        generator = disk_images.GenerateDiskImages(reset_dmg_cache=False)
        calls: list[str] = []
        with mock.patch.object(disk_images, "validate_source", side_effect=lambda: calls.append("gate")), \
             mock.patch.object(generator, "_delete_extra_binaries", side_effect=lambda: calls.append("delete")), \
             mock.patch.object(generator, "_generate_payloads_dmg", side_effect=lambda: calls.append("dmg")), \
             mock.patch.object(generator, "_download_resources", side_effect=lambda: calls.append("download")):
            generator.generate()
        self.assertEqual(calls, ["gate", "delete", "dmg", "download"])


if __name__ == "__main__":
    unittest.main()
