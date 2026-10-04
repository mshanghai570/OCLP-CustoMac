"""Marketing version to kernel major, which changed at Tahoe.

`os_conversion` mapped between the two by a fixed offset: macOS 11 through 15 are
Darwin 20 through 24, so marketing plus nine gave the kernel major and kernel
minus nine gave the marketing one. macOS 26 is Darwin 25, so the rule stopped
holding exactly where this fork lives:

- `os_to_kernel("26.0")` returned 35, and the local installer list compares that
  against the host's kernel major. A macOS 26 install assistant was therefore
  reported as requiring a *newer* macOS than the Tahoe host building it.
- `kernel_to_os(25)` returned "16", which the unsupported-model dialog prints, so
  on Tahoe it named macOS 16 instead of macOS 26.

Nothing tested this conversion before, and the callers that compare the result
against a real kernel major only happened to be right: 35 exceeds every `os_data`
threshold, so the Sequoia and Tahoe branches were selected by accident.
"""

import unittest

from opencore_legacy_patcher.datasets import os_data as os_data_module
from opencore_legacy_patcher.datasets.os_data import os_conversion, os_data


# The kernel-to-marketing direction answers with the major alone.
PRE_TAHOE: tuple[tuple[str, int], ...] = (
    ("10.13", 17),
    ("10.15", 19),
    ("11", 20),
    ("12", 21),
    ("13", 22),
    ("14", 23),
    ("15", 24),
)


class MarketingToKernelTests(unittest.TestCase):
    def test_pre_tahoe_versions_keep_the_historical_offset(self) -> None:
        for marketing, kernel in PRE_TAHOE:
            with self.subTest(marketing=marketing):
                self.assertEqual(os_conversion.os_to_kernel(marketing), kernel)

    def test_only_the_major_component_is_read(self) -> None:
        for marketing, kernel in (("10.15.7", 19), ("11.7.10", 20), ("15.6", 24)):
            with self.subTest(marketing=marketing):
                self.assertEqual(os_conversion.os_to_kernel(marketing), kernel)

    def test_macos_26_is_darwin_25(self) -> None:
        for marketing in ("26.0", "26.6.2"):
            with self.subTest(marketing=marketing):
                self.assertEqual(os_conversion.os_to_kernel(marketing), os_data.tahoe.value)

    def test_the_post_tahoe_era_counts_one_ahead(self) -> None:
        """Apple kept incrementing the marketing number for the next release."""

        for marketing in ("26", "27", "28"):
            with self.subTest(marketing=marketing):
                self.assertEqual(os_conversion.os_to_kernel(marketing), int(marketing) - 1)


class KernelToMarketingTests(unittest.TestCase):
    def test_pre_tahoe_kernels_keep_the_historical_offset(self) -> None:
        for marketing, kernel in PRE_TAHOE:
            with self.subTest(marketing=marketing):
                self.assertEqual(os_conversion.kernel_to_os(kernel), marketing)

    def test_darwin_25_is_macos_26(self) -> None:
        self.assertEqual(os_conversion.kernel_to_os(os_data.tahoe.value), "26")

    def test_round_trip_over_every_supported_release(self) -> None:
        for kernel in os_data_module.SUPPORTED_MAJOR_VERSIONS:
            with self.subTest(kernel=kernel):
                self.assertEqual(os_conversion.os_to_kernel(os_conversion.kernel_to_os(kernel)), kernel)

    def test_a_supported_kernel_resolves_to_its_release_name(self) -> None:
        for kernel in os_data_module.SUPPORTED_MAJOR_VERSIONS:
            with self.subTest(kernel=kernel):
                self.assertEqual(
                    os_conversion.convert_kernel_to_marketing_name(kernel),
                    os_data(kernel).name.replace("_", " ").title(),
                )


class ConsumerTests(unittest.TestCase):
    """Each symptom, expressed as the property its consumer relies on."""

    def test_a_macos_26_installer_is_buildable_on_a_tahoe_host(self) -> None:
        """`gui_macos_installer_flash` turns away a local installer whose minimum
        host OS exceeds the host's kernel major, so a macOS 26 install assistant
        reporting 26.x must not be refused by the Tahoe host building it."""

        minimum_host_os = os_conversion.os_to_kernel("26.0")
        self.assertLessEqual(minimum_host_os, os_data.tahoe.value)

    def test_the_installer_backup_table_has_a_row_for_macos_26(self) -> None:
        """`ci_tooling/installer_backups` stores `os_to_kernel(version)` in
        `installer['OS']` and indexes a table keyed by release with it, so the
        converted value has to name a release rather than only compare like one."""

        self.assertIn(
            os_conversion.os_to_kernel("26.0"),
            {member.value for member in os_data},
        )

    def test_the_minimum_host_os_label_names_tahoe(self) -> None:
        """The local installer list prints this for an installer it turns away."""

        self.assertEqual(
            os_conversion.convert_kernel_to_marketing_name(os_conversion.os_to_kernel("26.0")),
            "Tahoe",
        )

    def test_a_sequoia_installer_is_still_buildable_on_sequoia(self) -> None:
        """The same comparison for the release before Tahoe, as a control."""

        self.assertLessEqual(os_conversion.os_to_kernel("15.6"), os_data.sequoia.value)


if __name__ == "__main__":
    unittest.main()
