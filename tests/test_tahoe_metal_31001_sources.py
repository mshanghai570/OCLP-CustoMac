"""Metal 31001 source paths must exist for the selected macOS release."""

import unittest

from opencore_legacy_patcher.sys_patch.patchsets.base import PatchType
from opencore_legacy_patcher.sys_patch.patchsets.shared_patches.metal_31001 import (
    LegacyMetal31001,
)


class Metal31001SourceTests(unittest.TestCase):
    def test_tahoe_does_not_reference_unpublished_renderbox_25(self) -> None:
        patches = LegacyMetal31001(25, 6, "26.6.2").patches()
        self.assertNotIn("Metal 31001 Common", patches)

    def test_sequoia_keeps_existing_renderbox_override(self) -> None:
        patches = LegacyMetal31001(24, 6, "15.6").patches()
        resources = patches["Metal 31001 Common"][PatchType.OVERWRITE_SYSTEM_VOLUME][
            "/System/Library/PrivateFrameworks/RenderBox.framework/Versions/A/Resources"
        ]
        self.assertEqual(resources["default.metallib"], "RenderBox-24")


if __name__ == "__main__":
    unittest.main()
