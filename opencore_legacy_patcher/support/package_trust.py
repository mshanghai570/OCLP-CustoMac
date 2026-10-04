"""Publisher checks before executable packages cross the administrator boundary."""

import enum
import hashlib
import os
import re
import shlex
import stat
import subprocess
import tempfile
import shutil
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from . import network_handler, subprocess_wrapper


class Publisher(enum.Enum):
    APPLE = "Apple Software Update"
    PROJECT = "kgp-macPro/OCLP-CustoMac"


class PackageTrustError(RuntimeError):
    pass


# SHA-256 of DER certificates published at https://www.apple.com/certificateauthority/
# Names alone do not identify Apple when a custom trust root exists.
APPLE_UPDATE_CA = "1299e9bfe776a29ff452f8c4f5e55f3b4dfd2934349dd1850b8274f35c71745c"
APPLE_ROOT_CA = "b0b1730ecbc7ff4505142c49f1295e6eda6bcaed7e2c68c5be91b5a11001f024"


def _check_apple_signature(path):
    try:
        result = subprocess.run(["/usr/sbin/pkgutil", "--check-signature", str(path)], capture_output=True, timeout=60, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as error:
        raise PackageTrustError(f"Apple signature verification failed: {error}") from error
    output = result.stdout.decode("utf-8", errors="replace")
    certificates = re.findall(r"^\s*\d+\.\s+(.+?)\s*$", output, re.MULTILINE)
    fingerprints = [re.sub(r"[\s:]", "", value).lower() for value in re.findall(r"SHA256 Fingerprint:\s*([0-9A-Fa-f :\t\r\n]+)", output)]
    if (result.returncode or "Status: signed by a certificate trusted by macOS" not in output
            or certificates not in (["Software Update", "Apple Software Update Certification Authority", "Apple Root CA"],
                                   ["Apple Software Update", "Apple Software Update Certification Authority", "Apple Root CA"])
            or len(fingerprints) != 3 or not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in fingerprints)
            or fingerprints[1:] != [APPLE_UPDATE_CA, APPLE_ROOT_CA]):
        raise PackageTrustError("Package is not signed by the pinned Apple Software Update authority")


def _digest(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
                raise PackageTrustError("Package must be a nonempty regular file")
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        raise PackageTrustError(f"Cannot safely read package: {error}") from error


def _release_digest(source_url):
    """Accept only a digest fetched over HTTPS from this repository's release API."""
    parsed = urlsplit(source_url or "")
    match = re.fullmatch(r"/kgp-macPro/OCLP-CustoMac/releases/download/([^/]+)/([^/]+\.pkg)", parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment or not match:
        raise PackageTrustError("Package requires an official repository release with a published SHA-256 digest")
    tag, filename = (unquote(value) for value in match.groups())
    if '/' in tag or '/' in filename:
        raise PackageTrustError("Invalid release asset path")
    response = None
    try:
        response = network_handler.NetworkUtilities().get(
            f"https://api.github.com/repos/{Publisher.PROJECT.value}/releases/tags/{quote(tag, safe='')}"
        )
        response.raise_for_status()
        metadata = response.json()
        if not isinstance(metadata, dict) or metadata.get("tag_name") != tag:
            raise PackageTrustError("Release identity mismatch")
        assets = metadata.get("assets")
        if not isinstance(assets, list):
            raise PackageTrustError("Release assets missing")
        matching = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == filename and asset.get("browser_download_url") == source_url]
        if len(matching) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", matching[0].get("digest") or ""):
            raise PackageTrustError("Release has no authenticated SHA-256 digest for this package")
        return matching[0]["digest"][7:]
    except PackageTrustError:
        raise
    except Exception as error:
        raise PackageTrustError(f"Could not verify release metadata: {error}") from error
    finally:
        if response is not None:
            response.close()


def verify_installer_package(path, publisher, *, source_url=None):
    """Return the verified SHA-256; reject unsigned Apple or unpinned project packages."""
    path = Path(path).absolute()
    digest = _digest(path)
    if publisher == Publisher.APPLE:
        # Bind inspection to a private snapshot, rather than a path that can be
        # switched to signed bytes just while pkgutil opens it.
        with tempfile.TemporaryDirectory(prefix="oclp-package-verify-") as directory:
            snapshot = Path(directory) / "installer.pkg"
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as source, snapshot.open("xb") as target:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise PackageTrustError("Package must be a regular file")
                    shutil.copyfileobj(source, target, 1024 * 1024)
                snapshot.chmod(0o600)
            except OSError as error:
                raise PackageTrustError(f"Could not snapshot package: {error}") from error
            if _digest(snapshot) != digest:
                raise PackageTrustError("Package changed while taking verification snapshot")
            _check_apple_signature(snapshot)
            if _digest(snapshot) != digest:
                raise PackageTrustError("Verification snapshot changed")
    elif publisher == Publisher.PROJECT:
        if digest != _release_digest(source_url):
            raise PackageTrustError("Package SHA-256 does not match the official release")
    else:
        raise PackageTrustError("Unknown package publisher")
    if _digest(path) != digest:
        raise PackageTrustError("Package changed during verification")
    return digest


def install_verified_package(path, publisher, *, source_url=None):
    """Copy to an exclusive root directory and recheck verified bytes before execution."""
    digest = verify_installer_package(path, publisher, source_url=source_url)
    return _install_staged_package(path, digest, publisher)


def install_pinned_package(path, expected_sha256):
    """Install against a digest supplied by an existing trusted, pinned catalog."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or _digest(path) != expected_sha256:
        raise PackageTrustError("Package does not match its pinned SHA-256")
    return _install_staged_package(path, expected_sha256, None)


def _install_staged_package(path, digest, publisher):
    commands = [
        'set -eu', 'umask 077',
        'work=$(/usr/bin/mktemp -d /private/tmp/oclp-package.XXXXXXXX)',
        'trap \'/bin/rm -rf "$work"\' EXIT',
        'trap \'exit 1\' HUP INT TERM',
        f'/bin/cp {shlex.quote(str(Path(path).absolute()))} "$work/installer.pkg"',
        'actual=$(/usr/bin/shasum -a 256 "$work/installer.pkg")',
        f'[ "${{actual%% *}}" = {shlex.quote(digest)} ] || {{ echo "Package changed after verification" >&2; exit 1; }}',
    ]
    if publisher == Publisher.APPLE:
        commands.extend([
            'export LC_ALL=C',
            'signature=$(/usr/sbin/pkgutil --check-signature "$work/installer.pkg")',
            'printf "%s\\n" "$signature" | /usr/bin/grep -Eq "^[[:space:]]*Status: signed by a certificate trusted by macOS$"',
            'chain=$(printf "%s\\n" "$signature" | /usr/bin/awk \'/^[[:space:]]*[0-9]+\\. / {sub(/^[[:space:]]*[0-9]+\\. /, ""); sub(/[[:space:]]*$/, ""); print}\')',
            f'[ "$chain" = {shlex.quote("Software Update\nApple Software Update Certification Authority\nApple Root CA")} ] || [ "$chain" = {shlex.quote("Apple Software Update\nApple Software Update Certification Authority\nApple Root CA")} ]',
            'fingerprints=$(printf "%s\\n" "$signature" | /usr/bin/awk \'/SHA256 Fingerprint:/ {active=1; value=""; next} active {gsub(/[[:space:]:]/, ""); if ($0 !~ /^[0-9A-Fa-f]+$/) exit 1; value=value tolower($0); if (length(value)==64) {print value; active=0} else if (length(value)>64) exit 1} END {if (active) exit 1}\')',
            '[ "$(printf "%s\\n" "$fingerprints" | /usr/bin/wc -l | /usr/bin/tr -d " ")" = 3 ]',
            f'[ "$(printf "%s\\n" "$fingerprints" | /usr/bin/tail -n 2)" = {shlex.quote(APPLE_UPDATE_CA + chr(10) + APPLE_ROOT_CA)} ]',
        ])
    commands.append('/usr/sbin/installer -pkg "$work/installer.pkg" -target /')
    script = '\n'.join(commands)
    return subprocess_wrapper.run_as_root(["/bin/sh", "-c", script], capture_output=True)
