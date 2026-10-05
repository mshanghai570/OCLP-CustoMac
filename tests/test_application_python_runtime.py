"""Reject a PyInstaller runtime that differs from the locked interpreter."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_tooling.build_modules.application import GenerateApplication
from ci_tooling import build_environment


class ApplicationPythonRuntimeTests(unittest.TestCase):
    def test_flat_packaged_runtime_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Patcher.app"
            library = app / "Contents/Frameworks/Python"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"runtime")
            checked = []
            def otool(argv, **kwargs):
                path = Path(argv[-1])
                if "Contents" in path.parts:
                    self.assertEqual(path, library)
                checked.append(path)
                return mock.Mock(stdout="    uuid DD419C79-4CD3-3AA9-9C28-260B93B2D514\n")
            with mock.patch.object(build_environment.subprocess, "run", side_effect=otool):
                build_environment.verify_packaged_python_runtime(app)
            self.assertIn(library, checked)

    def test_wrong_runtime_is_rejected_before_pyinstaller_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / "Python"
            library.write_bytes(b"a different Python patch release")
            with mock.patch("PyInstaller.depend.bindepend.get_python_library_path", return_value=str(library)), \
                 mock.patch("ci_tooling.build_modules.application.subprocess_wrapper.run_and_verify") as run, \
                 mock.patch("ci_tooling.build_modules.application.PayloadContract"):
                with self.assertRaisesRegex(RuntimeError, "PyInstaller.*Python.*SHA-256"):
                    GenerateApplication()._generate_application()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
