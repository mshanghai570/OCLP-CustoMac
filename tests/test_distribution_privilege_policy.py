import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ci_tooling.build_modules.payload_contract import PayloadContract, PayloadContractError
from ci_tooling.build_modules import package_scripts
from ci_tooling.source_whitespace import violations


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_DIRECTORY = REPO_ROOT / "ci_tooling" / "privileged_helper_tool"
HELPER_SOURCE = HELPER_DIRECTORY / "main.m"
HELPER_BINARY = HELPER_DIRECTORY / "com.dortania.opencore-legacy-patcher.privileged-helper"


class DistributionPrivilegePolicyTests(unittest.TestCase):
    def test_actual_files_and_modes_reject_retired_broker_and_setuid_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "executable"
            binary.write_text("executable fixture")
            binary.chmod(0o4755)
            with self.assertRaisesRegex(PayloadContractError, "Setuid"):
                PayloadContract.validate_privilege_policy(root)
            binary.chmod(0o755)
            PayloadContract.validate_privilege_policy(root)
            helper = root / "Library/PrivilegedHelperTools/helper"
            helper.parent.mkdir(parents=True)
            helper.write_bytes(b"retired helper")
            with self.assertRaisesRegex(PayloadContractError, "Retired"):
                PayloadContract.validate_privilege_policy(root)

    def test_package_postinstall_must_not_reenable_setuid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "postinstall"
            script.write_text('#!/bin/sh\n/bin/chmod +s "$helper"\n')
            with self.assertRaisesRegex(PayloadContractError, "enables"):
                PayloadContract.validate_privilege_policy(root)

    def test_clean_git_checkout_whitespace_is_still_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True)
            (root / "fixture.py").write_text("value = 1 \n")
            subprocess.run(["git", "add", "fixture.py"], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"], cwd=root, check=True)
            result = subprocess.run(["git", "diff", "--check"], cwd=root, capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(list(violations(root)), ["fixture.py:1: trailing whitespace"])


class RetiredHelperContractTests(unittest.TestCase):
    """The broker is retired: source, artifact, and build all refuse root work."""

    def test_helper_source_has_no_authentication_or_command_execution(self):
        source = HELPER_SOURCE.read_text(encoding="utf-8")
        for removed in ("SecCodeCopySigningInformation", "SecStaticCodeCreateWithPath",
                        "Security/Security.h", "VALID_CLIENT_TEAM_ID", "isSBitSet",
                        "NSTask", "system("):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, source)
        self.assertIn("return 170", source)

    def test_bundled_helper_refuses_every_request_in_both_slices(self):
        self.assertTrue(HELPER_BINARY.is_file())
        archs = subprocess.run(["/usr/bin/lipo", "-archs", str(HELPER_BINARY)],
                               capture_output=True, check=True, text=True).stdout.split()
        self.assertEqual(sorted(archs), ["arm64", "x86_64"])
        libraries = subprocess.run(["/usr/bin/otool", "-L", str(HELPER_BINARY)],
                                   capture_output=True, check=True, text=True).stdout
        self.assertNotIn("Security.framework", libraries)
        for arguments in ([], ["--version"], ["/usr/bin/true", "--keep-suid"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run([str(HELPER_BINARY), *arguments],
                                        capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 170, result.stderr)
                self.assertIn(b"retired", result.stderr)
                self.assertNotIn(b"uid=0", result.stdout)

    @unittest.skipUnless(shutil.which("clang"), "clang is required to verify the helper source build")
    def test_helper_source_build_is_also_a_refusing_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            built = Path(directory) / "helper"
            subprocess.run(["clang", "-mmacosx-version-min=10.9", "-o", str(built),
                            str(HELPER_SOURCE)], check=True, capture_output=True, timeout=120)
            result = subprocess.run([str(built), "/usr/bin/true"], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 170, result.stderr)
        self.assertIn(b"retired", result.stderr)

    def test_helper_installer_refuses_to_deploy_a_setuid_broker(self):
        result = subprocess.run(["/bin/zsh", str(HELPER_DIRECTORY / "install.sh")],
                                capture_output=True, timeout=30, cwd=str(HELPER_DIRECTORY))
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(b"chmod", result.stdout + result.stderr)
        self.assertIn(b"retired", result.stderr)

    def test_build_and_packaging_never_reference_the_helper_broker(self):
        for relative in ("Build-Project.command", "ci_tooling/build_modules/package.py"):
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(file=relative):
                self.assertNotIn("PrivilegedHelperTools", source)
                self.assertNotIn("privileged-helper", source)

    def test_package_scripts_only_remove_a_previously_installed_helper(self):
        source = (REPO_ROOT / "ci_tooling/build_modules/package_scripts.py").read_text(encoding="utf-8")
        self.assertNotIn("generate_set_suid_bit", source)
        scripts = package_scripts.GenerateScripts()
        helper = "Library/PrivilegedHelperTools/com.dortania.opencore-legacy-patcher.privileged-helper"
        self.assertIn(helper, scripts.preinstall_pkg())
        self.assertIn(helper, scripts.uninstall())
        self.assertNotIn(helper, scripts.postinstall_pkg())
        self.assertNotIn(helper, scripts.postinstall_autopkg())

