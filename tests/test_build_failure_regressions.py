"""Build operations must run under optimization and restore temporary source edits."""

import ast
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

from pathlib import Path
from unittest import mock

from ci_tooling.build_modules import application


class BuildFailureTests(unittest.TestCase):
    def test_analytics_source_is_restored_after_build_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_cwd = Path.cwd()
            self.addCleanup(os.chdir, original_cwd)
            os.chdir(temporary)
            source = Path("opencore_legacy_patcher/support/analytics_handler.py")
            source.parent.mkdir(parents=True)
            original = 'SITE_KEY:         str = "original"\nANALYTICS_SERVER: str = "original-endpoint"\n# preserved\n'
            source.write_text(original)
            builder = application.GenerateApplication(analytics_key="new-key", analytics_endpoint="new-endpoint")
            with mock.patch.object(application.SourceBuildMetadata, "from_repository"), \
                 mock.patch.object(builder, "_generate_application", side_effect=RuntimeError("build failed")):
                with self.assertRaisesRegex(RuntimeError, "build failed"):
                    builder.generate()
            self.assertEqual(source.read_text(), original)

    def test_analytics_values_are_valid_python_literals(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_cwd = Path.cwd()
            self.addCleanup(os.chdir, original_cwd)
            os.chdir(temporary)
            source = Path("opencore_legacy_patcher/support/analytics_handler.py")
            source.parent.mkdir(parents=True)
            source.write_text('SITE_KEY:         str = ""\nANALYTICS_SERVER: str = ""\n')
            key = 'key"\nwith newline'
            endpoint = "endpoint'\\with slash"
            builder = application.GenerateApplication(analytics_key=key, analytics_endpoint=endpoint)
            builder._embed_analytics_key()
            namespace = {}
            exec(compile(ast.parse(source.read_text()), str(source), "exec"), namespace)
            self.assertEqual(namespace["SITE_KEY"], key)
            self.assertEqual(namespace["ANALYTICS_SERVER"], endpoint)

    def test_optimized_python_still_builds_all_packages(self):
        root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent('''
            from pathlib import Path
            from unittest.mock import patch
            from ci_tooling.build_modules import package

            class LocalPackageBuilder:
                def __init__(self, **kwargs):
                    self.output = Path(kwargs["pkg_output"])
                def build(self):
                    self.output.parent.mkdir(parents=True, exist_ok=True)
                    self.output.write_bytes(b"built package")
                    return True

            with patch.object(package.macos_pkg_builder, "Packages", LocalPackageBuilder), \
                 patch.object(package, "PayloadContract"):
                package.GeneratePackage().generate()
            outputs = ("OpenCore-Patcher.pkg", "OpenCore-Patcher-Uninstaller.pkg", "AutoPkg-Assets.pkg")
            if any(not (Path("dist") / name).is_file() for name in outputs):
                raise RuntimeError("Required package build was skipped")
        ''')
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, "-O", "-c", script], cwd=temporary,
                                    env={**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"},
                                    capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
