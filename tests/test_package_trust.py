import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opencore_legacy_patcher.support import package_trust as trust


def fingerprint(value):
    return " ".join(value[i:i+2].upper() for i in range(0, len(value), 2))


APPLE_SIGNATURE = f'''Package "Installer.pkg":
   Status: signed by a certificate trusted by macOS
   Certificate Chain:
    1. Software Update
       SHA256 Fingerprint:
           {fingerprint("a" * 64)}
       ------------------------------------------------------------------------
    2. Apple Software Update Certification Authority
       SHA256 Fingerprint:
           {fingerprint(trust.APPLE_UPDATE_CA)}
       ------------------------------------------------------------------------
    3. Apple Root CA
       SHA256 Fingerprint:
           {fingerprint(trust.APPLE_ROOT_CA)}
'''.encode()



class PackageTrustTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "installer.pkg"
        self.path.write_bytes(b"package bytes")

    def test_trusted_apple_signature_accepts_only_apple_publisher(self):
        signed = subprocess.CompletedProcess([], 0, APPLE_SIGNATURE, b"")
        with mock.patch.object(trust.subprocess, "run", return_value=signed):
            self.assertEqual(trust.verify_installer_package(self.path, trust.Publisher.APPLE), hashlib.sha256(b"package bytes").hexdigest())

    def test_unsigned_or_unrelated_valid_publisher_never_elevates(self):
        for output in (b"Status: no signature", APPLE_SIGNATURE.replace(b"Software Update\n", b"Developer ID Installer: Stranger (BAD)\n", 1)):
            with self.subTest(output=output), mock.patch.object(trust.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, b"")), mock.patch.object(trust.subprocess_wrapper, "run_as_root") as root:
                with self.assertRaises(trust.PackageTrustError):
                    trust.install_verified_package(self.path, trust.Publisher.APPLE)
                root.assert_not_called()

    def test_signature_check_failure_is_rejected(self):
        with mock.patch.object(trust.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, APPLE_SIGNATURE, b"invalid")):
            with self.assertRaises(trust.PackageTrustError):
                trust.verify_installer_package(self.path, trust.Publisher.APPLE)

    def test_matching_certificate_names_with_a_different_root_are_rejected(self):
        forged = APPLE_SIGNATURE.replace(fingerprint(trust.APPLE_ROOT_CA).encode(), fingerprint("b" * 64).encode())
        with mock.patch.object(trust.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, forged, b"")):
            with self.assertRaises(trust.PackageTrustError):
                trust.verify_installer_package(self.path, trust.Publisher.APPLE)

    def test_root_stage_shell_rejects_tampering_and_forged_publisher(self):
        signature_path = self.path.with_name("signature.txt")
        marker = self.path.with_name("installer-executed")
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        def run_unprivileged(argv, **kwargs):
            # Execute the real guard in a disposable directory, substituting
            # only platform signature inspection and the installer executable.
            script = argv[2].replace('/usr/sbin/pkgutil --check-signature "$work/installer.pkg"', f'/bin/cat {signature_path}')
            script = script.replace('/usr/sbin/installer -pkg "$work/installer.pkg" -target /', f'/usr/bin/touch {marker}')
            return subprocess.run(["/bin/sh", "-c", script], capture_output=True, timeout=10)
        with mock.patch.object(trust, "verify_installer_package", return_value=digest), mock.patch.object(trust.subprocess_wrapper, "run_as_root", side_effect=run_unprivileged):
            signature_path.write_bytes(APPLE_SIGNATURE)
            result = trust.install_verified_package(self.path, trust.Publisher.APPLE)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.exists())
            marker.unlink()
            signature_path.write_bytes(APPLE_SIGNATURE.replace(fingerprint(trust.APPLE_ROOT_CA).encode(), fingerprint("b" * 64).encode()))
            self.assertNotEqual(trust.install_verified_package(self.path, trust.Publisher.APPLE).returncode, 0)
            self.assertFalse(marker.exists())
            signature_path.write_bytes(APPLE_SIGNATURE)
            self.path.write_bytes(b"changed after verification")
            self.assertNotEqual(trust.install_verified_package(self.path, trust.Publisher.APPLE).returncode, 0)
            self.assertFalse(marker.exists())

    def test_changed_package_during_signature_check_is_rejected(self):
        def inspect(*args, **kwargs):
            self.path.write_bytes(b"modified package")
            return subprocess.CompletedProcess([], 0, APPLE_SIGNATURE, b"")
        with mock.patch.object(trust.subprocess, "run", side_effect=inspect):
            with self.assertRaises(trust.PackageTrustError):
                trust.verify_installer_package(self.path, trust.Publisher.APPLE)

    def test_path_swap_cannot_splice_trusted_signature_into_untrusted_digest(self):
        malicious = b"untrusted executable package"
        trusted = b"genuine Apple package"
        self.path.write_bytes(malicious)

        def inspect(argv, **kwargs):
            # The attacker changes the public path only while pkgutil opens it,
            # then restores the malicious package before the second hash check.
            self.path.write_bytes(trusted)
            try:
                inspected = Path(argv[-1]).read_bytes()
            finally:
                self.path.write_bytes(malicious)
            if inspected == trusted:
                return subprocess.CompletedProcess(argv, 0, APPLE_SIGNATURE, b"")
            return subprocess.CompletedProcess(argv, 1, b"Status: no signature", b"")

        with mock.patch.object(trust.subprocess, "run", side_effect=inspect):
            with self.assertRaises(trust.PackageTrustError):
                trust.verify_installer_package(self.path, trust.Publisher.APPLE)

    def test_symlink_is_rejected(self):
        link = self.path.with_name("link.pkg")
        link.symlink_to(self.path)
        with self.assertRaises(trust.PackageTrustError):
            trust.verify_installer_package(link, trust.Publisher.APPLE)

    def test_project_requires_matching_digest_from_exact_repository_release(self):
        digest = "sha256:" + hashlib.sha256(b"package bytes").hexdigest()
        url = "https://github.com/kgp-macPro/OCLP-CustoMac/releases/download/3.0.3/AutoPkg-Assets.pkg"
        response = mock.Mock()
        response.json.return_value = {"tag_name": "3.0.3", "assets": [{"browser_download_url": url, "digest": digest, "name": "AutoPkg-Assets.pkg"}]}
        with mock.patch.object(trust.network_handler.NetworkUtilities, "get", return_value=response):
            self.assertEqual(trust.verify_installer_package(self.path, trust.Publisher.PROJECT, source_url=url), digest[7:])
            response.close.assert_called()
            response.json.return_value["assets"][0]["digest"] = None
            with self.assertRaises(trust.PackageTrustError):
                trust.verify_installer_package(self.path, trust.Publisher.PROJECT, source_url=url)
        for bad_url in (url.replace("kgp-macPro", "attacker"), "https://nightly.link/build.zip", url.replace("https:", "http:")):
            with self.subTest(url=bad_url), self.assertRaises(trust.PackageTrustError):
                trust.verify_installer_package(self.path, trust.Publisher.PROJECT, source_url=bad_url)

    def test_root_stage_rechecks_digest_before_installer(self):
        with mock.patch.object(trust, "verify_installer_package", return_value="a" * 64), mock.patch.object(trust.subprocess_wrapper, "run_as_root", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as root:
            trust.install_verified_package(self.path, trust.Publisher.APPLE)
        shell = root.call_args.args[0][2]
        self.assertLess(shell.index("shasum"), shell.index("/usr/sbin/installer"))
        self.assertIn("a" * 64, shell)
        self.assertIn("mktemp -d /private/tmp/oclp-package.", shell)
