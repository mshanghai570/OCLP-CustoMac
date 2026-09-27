"""Regression tests for the public Constants construction contract."""

import unittest

from opencore_legacy_patcher.constants import Constants


class ConstantsContractTests(unittest.TestCase):
    def test_constructor_exposes_core_runtime_settings(self) -> None:
        current = Constants()

        self.assertEqual(current.patcher_version, "3.0.3")
        self.assertEqual(current.patcher_name, "OCLP-CustoMac")
        self.assertEqual(current.opencore_version, "1.0.7")
        self.assertEqual(current.payload_path.name, "payloads")


if __name__ == "__main__":
    unittest.main()
