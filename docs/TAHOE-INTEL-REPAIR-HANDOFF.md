# Tahoe Intel Repair Handoff

## Goal

Make the patcher dependable for macOS Tahoe on Intel Macs, with the primary
target being the 2015 MacBook Air class machine using an Intel i5-5350U,
Intel HD 6000 (Broadwell), 8 GB RAM, and a 128 GB SSD.

## Current Repository State

- Branch: `main`, core repair committed at `a695743` (`fix: restore Tahoe core
  patch behavior`).
- Optimization commit under review: `3b4f2f9`.
- The ModernAudio import and unconditional AppleHDA mapping are committed in
  `a695743`; there is no pending ModernAudio working-tree change.
- Untracked `.agent-orchestration/`, `.tmp_config/`, and `data/` are outside
  this repair scope and must not be modified or committed.
- Seventeen tracked files are **deleted in the working tree**, none of them by
  this repair: `docs/images/ventura_uc2.png` (0.5 MB, directory mtime 2026-09-29
  19:03, after this session's last write) and sixteen board-id plists under
  `payloads/Kexts/Plists/PlatformPlugin/` (MacBookAir5,1; MacBookPro11,3; 11,4;
  15,4; 16,1; iMac17,1; 18,1; 20,1 — directory mtime 2026-09-27 08:45, before
  this session). The user directed that they remain deleted for now; this records
  the requested handling, not the deletion's provenance or rationale. Do not
  restore unless the user changes that instruction. The missing `Info.plist`
  files affect CPUFriend builds for seven models — MacBookAir5,1, MacBookPro11,3,
  MacBookPro11,4, MacBookPro15,4, MacBookPro16,1, iMac18,1 and iMac20,1 — when
  CPUFriend is enabled (non-`None` serial settings, unless explicitly disabled).
  The two deleted iMac17,1 board-id variants are not the `Info.plist` consumed by
  this  code path; the model's `Info.plist` remains. CPUFriend was previously

  enabled/copied before the missing-profile check, which could leave a partially
  modified build tree. The guard now checks the source profile first and raises
  `FileNotFoundError` before enabling/copying anything, and the EFI builder runs
  this preflight before creating or cleaning the build tree. Tests pin both the
  requirement conditions and this ordering. Other profile files and builds with
  CPUFriend disabled are not affected by this code path. The deleted
  `docs/images/ventura_uc2.png` remains referenced in `docs/UNIVERSALCONTROL.md`.
- `PayloadContract` validates release archives and Tahoe root-patch resources,
  not optional per-model CPUFriend profiles. No suite-wide payload check claims
  those deleted profiles are safe; the focused test now pins fail-before-mutation
  behavior when a selected model's profile is absent.
- `docs/TAHOE-INTEL-DEVICE-VALIDATION.md` is the on-device acceptance checklist.
  No Tahoe installation, flash, or hardware test was performed in this work.
- `tests/test_tahoe_broadwell_graphics_detection.py` is untracked. It used to
  assert that Broadwell detection is enabled, which contradicts the published
  scope; it now pins dormancy and passes. See "Published scope" below.
- Uncommitted work as of 2026-09-29, in ten separable pieces: (1) the
  `RenderBox-25` fix in `shared_patches/metal_31001.py`, the payload-source
  guards in `ci_tooling/build_modules`, and the source-manifest oracle in
  `ci_tooling`; (2) the Tahoe metallib resolver and digest pinning in
  `support/metallib_handler.py` with its pin generator; (3) the unified
  patchset-source traversal in `patchsets/base.py` and its three consumers;
  (4) the `12.5-25` Metal driver resolver in `patchsets/hardware/base.py`;
  (5) the `_find_unused_files` rewrite with its `--validate_unused_payload`
  flag; (6) `SUPPORTED_MAJOR_VERSIONS` in `datasets/os_data.py` with its two
  consumers, which is what makes `--validate` reach Tahoe; (7) the same file's
  `os_to_kernel`/`kernel_to_os` Tahoe correction; (8) the numeric installer
  version filters in `sucatalog/products.py` with the AppleDB Tahoe ceiling in
  `sucatalog/products_appledb.py`; (9) the `--applicationpath` decision in
  `support/macos_installer_handler.py`; and (10) the suite-wide release-number
  guard, `tests/test_version_text_ordering_guard.py`, with the two instances of
  the family it found and the repairs they drove — the ordering fix in
  `support/macos_installer_handler.py` (`_version_order_key`, plus the two
  `Info.plist` handles its tests freed) and the single declaration of the KDK
  policy numbers in `support/kdk_selection.py` that `support/kdk_handler.py`
  and `sys_patch/utilities/kdk_merge.py` now read, alongside the named GPU
  compiler thresholds in `sys_patch/sys_patch_helpers.py`, the derived FileVault 2
  message in `efi_builder/firmware.py`, and the derived `TAHOE_MARKETING_VERSION`
  and `DARWIN_MAJOR` in `ci_tooling`, alongside the `--applicationpath` bound
  re-expressed as High Sierra's kernel major so that no bare `13` remains, the
  recorded AppleDB slice in `tests/fixtures/appledb_macos_slice.json` with the
  `Beta` normalisation it exposed, and the contract module itself,
  `ci_tooling/build_modules/version_contract.py`, now wired into the build ahead
  of the destructive payload step. The new tests are
  `test_tahoe_broadwell_graphics_detection`, `test_tahoe_metal_31001_sources`,
  `test_tahoe_metal_driver_resolver`, `test_tahoe_metallib_pin_generator`,
  `test_tahoe_metallib_resolver`, `test_tahoe_psp_source_manifest`,
  `test_patchset_source_traversal`, `test_unused_payload_scan`,
  `test_patchset_execution`, `test_root_patch_os_coverage`,
  `test_os_version_conversion`, `test_installer_version_filters`,
  `test_installer_creation_script`, `test_version_text_ordering_guard` and
  `test_no_network_access`.
  Nothing from the 2026-09-29 session is committed.
- `payloads/Tools/OpenCore-Patcher.app/Contents/MacOS/OpenCore-Patcher` shows as
  modified in `git status` and was already so before this session; it is not
  part of this repair and must not be staged with it.
- GitHub Actions run `36324675531` builds an earlier commit and must not be
  used for release validation of the current repair.

### Local baseline (2026-09-29)

`.venv-test/bin/python -m unittest discover -s tests` runs **463 tests and they
all pass**, including `tests/test_no_network_access.py`, which puts every
non-loopback address out of reach for the whole run — so the suite needs no
network. Two runs late in the session failed with `OSError: [Errno 28] No space
left on device` — 78 tests, then 113 — because the data volume had fallen to
117 MiB free. That was the machine, not the code: the same sources are green
again once the volume had room, and the failing tests were the ones that create a
temporary directory. `compileall` over `opencore_legacy_patcher`, `ci_tooling` and
`tests` exits 0, `git diff --check` is clean, and
`python -m ci_tooling.generate_psp_source_manifest --check` reports the manifest
current (203 published / 15 unpublished). The 263-, 268-, 296-, 317-, 331-,
347-, 359-, 366-, 373-, 385-, 397-, 404-, 425-, 433-, 440-, 447-, 448- and
452-test counts recorded further down are earlier checkpoints; this section is the
current baseline. On 2026-10-01, the full suite was rerun in the shared tree:
511 tests passed. This includes new EFI CPUFriend profile coverage: the profile
is now preflighted before build-tree generation/cleanup and before kext mutation;
a missing file raises a direct `FileNotFoundError`. Tests cover preflight
ordering, CPUFriend requirement conditions, fail-before-mutation and the
success/copy path. The MacBookAir7,2 primary-target profile is parsed as a real
plist and its provider identity is checked; the seven deleted model profiles are
asserted missing while CPUFriend is required, without restoring them. The
missing-profile impact is limited to seven models under the CPUFriend conditions
above. `BuildSupport` closes parsed plist files, and all remaining direct EFI
builder plist reads/writes now go through shared context-managed helpers,
including config, USB-map, CPUFriend, AGPM, AGDP and AppleMuxControl plists.
Regression coverage asserts the helper handles are closed. The same cleanup now
covers KDK SystemVersion/backup metadata and the kernel-collection AuxKC plist
read/modify/write path; tests assert handles close both when a write occurs and
when a non-Apple plist returns without writing. Package generation closes
temporary script handles and deletes all five script files on success or failure.
Five further regressions cover shared settings reads/writes and migration, GUI
settings, analytics preferences, embedded commit metadata, and the RSR monitor's
kext/config plist path. An adjacent service-file checksum read now hashes in
bounded chunks and closes each descriptor rather than reading two whole files
into memory. Direct `plistlib.load/dump(... .open(...))` expressions have been
removed from production sources; the audit was limited to the identified
resource-handling sites, not all file I/O. In addition to settings, analytics,
defaults, commit metadata, installer-flash, and auto-patcher handling, a final
five-site pass converted OS build detection, staged-update reads,
legacy-extension cleanup, KDK-merge metadata checks, and mounted-root sanity
checks to context-managed plist handles. The focused resource-handle module has
16 passing tests, including five malformed-plist regressions confirming closure
when parsing fails at each of those sites. The complete suite has 501 passing
tests. `compileall`, `git diff --check`, PSP
manifest `--check` (203 published / 15 unpublished), and `validate_source()`
all pass on this state. The wider source scan found two remaining unscoped
plist handles in `ci_tooling/build_modules/application.py` while embedding Git
metadata; they are now context-managed, with tests asserting closure on normal
read/write and on a simulated write failure. The focused payload-build contract
module passes 21 tests. A repository-wide scan finds no direct
`plistlib.load/dump(...open(...))` expressions left in production or CI tooling.
This scan does not constitute a complete audit of every possible file-I/O
pattern. The release gates still require CI for this exact revision, package
inspection, and actual Tahoe hardware validation; none was performed locally.
A subsequent file-I/O pass found that streamed downloads closed their local file
but did not explicitly close the HTTP response, including when the destination
could not be opened or streaming failed. `DownloadObject._download()` now closes
the response in a `finally` block; deterministic tests cover successful
streaming, interrupted streaming, and destination-open failure, asserting local
and response resources are released. The new `test_network_download_resources`
module now passes 4 tests. Incomplete destinations are unlinked on errors after
opening the destination, while failures before opening preserve pre-existing
files. Tests cover interrupted streams, destination-open errors, and failure
before destination preparation. The complete suite now passes 501 tests, with
the same compile, diff, manifest, and version-contract gates green. File-I/O
review is incremental rather than exhaustive; other response consumers and
semantic failure handling still require review. The next pass covered the
three remaining internally consumed HEAD responses: connectivity verification,
installer-link validation, and download-size probing. They now close in `finally`
blocks, and focused mock-based tests assert closure. `test_network_download_resources`
now passes 7 tests; the full suite passes 501 tests with all established static
gates green. Response-returning GET/POST methods intentionally return ownership
to their callers, so their call sites still need consumer-specific lifecycle
review rather than unconditional closure in the wrapper. The consumer pass has
now closed GET responses in software-update catalog parsing, binary update
checks, KDK and metallib API lookups, and three installer-product metadata paths.
The Tahoe metallib API releases close on both successful parsing and non-200
responses; catalog parsing also closes if plist decoding fails. A new
`test_network_response_consumers` suite verifies these lifetimes, and existing
Tahoe metallib fakes were extended to model `Response.close()`. The focused
consumer suite has 11 passing tests; the complete suite now passes 511 tests.
The consumer audit has since closed analytics and crash-report POST responses,
the updater changelog GET, the nightly-branch UI GET, and the AppleDB catalogue
GET; cleanup runs in `finally` paths, and AppleDB preserves its existing fallback
on fetch/JSON errors. The changelog now uses one shared fetch helper from both
GUI call sites. Regression coverage also verifies closure on parse failures and
confirms the real recorded AppleDB catalogue fixture closes its API response.
`test_network_response_consumers` passes 19 tests, and the focused consumer
and installer-window modules pass 36 tests together. A subsequent audit of the
manual CI tooling found that both source-manifest and metallib-pin generators
consumed raw `requests` responses without closing them. Both now close each
response after materializing its JSON and also on HTTP, parsing, and truncated
inventory failures; mock tests pin those paths. The generator-focused modules
pass 44 tests together. A static AST inventory now pins all direct HTTP-client
calls across production and CI tooling to their audited sites; adding a direct
`requests`/session call fails with an instruction to review ownership and update
the inventory intentionally. The response-consumer plus generator modules pass
64 focused tests. Full suite: **526 tests**, passing. `compileall`,
`git diff --check`, PSP source manifest `--check` (203 published / 15
unpublished), and `validate_source()` also pass. The inventory also covers all response-returning `NetworkUtilities().get/post`
call sites separately, including the three package metadata reads and the
streamed downloader, and pins them to their audited callers; a newly added
consumer must therefore be reviewed and added explicitly. The two structural
inventory tests and existing consumer/lifecycle tests pass. Full suite is now
**527 tests**, passing. `compileall`, `git diff --check`, PSP manifest `--check`
(203 published / 15 unpublished), and `validate_source()` pass. The AST checks
cover direct requests/session calls and `NetworkUtilities().get/post`, but do not
identify dynamically wrapped transports or every network-adjacent path. Generic
GET/POST wrappers still return response ownership to callers; streamed downloads
keep their specialized lifecycle. On 2026-10-04, the 527-test suite was rerun
successfully (41.059 seconds); `compileall`, `git diff --check`, PSP source
manifest `--check` (203 published / 15 unpublished), and `validate_source()`
were also rerun successfully. There was no `dist/` directory, so no built
release package was available for inspection; package generation was not run.



The earlier baselines carried five failures in the untracked Broadwell
detection test and described them as deliberate. They were not awaiting
hardware: those five assertions required Broadwell to be registered, which
`tests/test_publication_contract.py` forbids. The test now pins dormancy, so the
suite is green without changing product scope.

## Completed Checkpoint

The core repair restores the pre-optimization eager `Constants` class, keeps
the corrected ModernAudio `Constants` import, and makes the Tahoe AppleHDA
mapping unconditional. It adds regression tests for immediate constants
initialization and for AppleHDA selection when the host reports AppleHDA.

The following checks passed in the project-local test environment:

- `tests.test_constants_contract` and `tests.test_phase3b_kdk_selection`
  (8 tests).
- `tests.test_payload_build_contract` (10 tests).
- `compileall` for production, CI tooling, and tests.
- `git diff --check` before the commit.

The project-local `.venv-test` environment ran the complete suite on
2026-09-27: 263 tests, with five failures, all in the untracked Broadwell
detection test that requires currently disabled graphics support. The other
258 tests passed. CI remains a release gate for any repair commit after it is
pushed.

## Findings

### 1. `LazyConstants` is broken and does not save memory

`opencore_legacy_patcher/constants.py` renamed `Constants` to `LazyConstants`.
Its constructor sets only `_initialized` and `_cached_values`; no method calls
`_ensure_initialized()`. As a result, ordinary use such as
`Constants().patcher_version` raises `AttributeError`.

`_initialize_constants()` duplicates the former complete `Constants.__init__`
body. Calling it would allocate all values at once, so it is not lazy and
cannot provide the claimed memory reduction.

**Repair:** restore the pre-`3b4f2f9` `Constants` implementation as the
production class. Do not retain a fake lazy wrapper. A future memory project
must first profile real memory use and introduce individual lazy properties
only for expensive, infrequently used values.

### 2. ModernAudio must not inspect the running system

The current optimized ModernAudio patch invokes `kextstat -l` and omits
`AppleHDA.kext` when the running OS reports AppleHDA or AppleALC. Root patch
selection must describe the target system volume, not the host used to build
or apply it. For example, a patched Tahoe installation could make this check
pass while a later root-patch operation still needs the payload copied.

The subprocess also creates a timeout/error path without producing a reliable
optimization. The original fixed payload mapping is deterministic.

**Repair:** retain the corrected `Constants` import, then remove the
`kextstat` cache and conditional mapping. Restore the original unconditional
`AppleHDA.kext: "26.0 Beta 1"` mapping for non-native Tahoe builds.

### 3. Broadwell graphics is excluded from root-patch detection

The i5-5350U target corresponds to `MacBookAir7,2`: the repository identifies
it as a Broadwell machine with an Intel HD 6000 GPU in
`opencore_legacy_patcher/datasets/smbios_data.py`.

`HardwarePatchsetDetection._hardware_variants` currently comments out
`intel_broadwell.IntelBroadwell` and registers only Modern Wireless and Modern
Audio. Therefore no graphics acceleration patch is selected for the primary
target on Tahoe. The existing Broadwell patch class already describes its
required graphics, Metal, OpenCL, and GVA payloads.

**Repair:** add a focused Broadwell/Tahoe detection test first. Re-enable
`IntelBroadwell` only after verifying its payload versions exist in the
PatcherSupportPkg used by the build and that the generated patch set passes
the Tahoe root-patch and KDK selection checks. Do not re-enable unrelated
legacy graphics families in the same change.

### 4. Existing relevant EFI support

`efi_builder/misc.py` already enables the SPI top-case workaround for
Broadwell-through-Kaby-Lake MacBooks. Preserve it and add a regression test
for the target model if the EFI builder test fixtures support it.

## Implementation Order

1. Add regression tests for constants initialization and the deterministic
   ModernAudio payload mapping.
2. Restore `Constants` and repair ModernAudio; run the focused tests, full
   test suite, compilation check, and source whitespace check.
3. Add a fixture representing `MacBookAir7,2` / Broadwell / Tahoe. Test that
   root-patch detection selects Intel Broadwell plus the expected shared
   patches, and that a KDK is required when appropriate.
4. Verify the PatcherSupportPkg contains every Broadwell payload version named
   by the patch set. Enable the Broadwell registry entry only if that check
   passes.
5. Build a fresh package from the repair commit in GitHub Actions. Download
   and inspect `OpenCore-Patcher.pkg`; validate package structure, app
   signatures, and presence of the intended root-patch resources.
6. On the target Mac, first boot from a recovery path with a known-good EFI,
   then test graphics acceleration, internal display brightness, keyboard and
   trackpad, sleep/wake, Wi-Fi, Bluetooth, and audio. Keep a bootable rollback
   USB available throughout the device test.

## Validation Constraints

The system `python3` lacks PyObjC (`ModuleNotFoundError: objc`). Use the
project-local `.venv-test/bin/python`, which can import `objc`, for local
regressions. The CI workflow creates its own locked macOS/x86_64 Python
environment and remains the authoritative build environment.

## Release Gates

- No `LazyConstants` wrapper remains unless independently profiled and tested.
- ModernAudio always selects the required AppleHDA payload for supported
  non-native Tahoe builds.
- No Tahoe graphics patch set names a support-package source that no pinned
  release publishes.
- A Tahoe metallib package is installed only after its SHA-256 matches a pinned
  entry; an unpinned or digest-mismatched build is refused without elevating.
- The registered scope is exactly Modern Wireless and Modern Audio, matching
  `README.md`, `REPORTS/PREPUBLICATION_PATCH_SCOPE_AND_OS_SUPPORT_AUDIT.md` and
  the registry assertion in `tests/test_publication_contract.py`.
- `--validate` walks every release the host gate authorizes, Tahoe included, so
  the fork's own target is not the one release whose sources go unchecked.
- Marketing and kernel majors convert correctly at Tahoe: `os_to_kernel("26.0")`
  is 25 and `kernel_to_os(25)` is "26", so no consumer compares 35 against a
  kernel major or prints macOS 16 on a Tahoe host.
- No hardware family, dormant ones included, names a Tahoe root-patch source
  that the pinned PatcherSupportPkg release does not publish.
- A new CI run is green for the exact repair commit.
- The downloaded package passes its workflow package checks.
- On-device validation completes the checklist in
  `docs/TAHOE-INTEL-DEVICE-VALIDATION.md` for the exact target hardware/build,
  with reviewable evidence and a verified rollback route.

## Tahoe Payload Verification and Source Oracle (2026-09-29)

Everything in this section was rerun against the real release, not a mock.

- The pinned `patcher_support_pkg_sha256` in `constants.py` is correct. The
  `2.0.0-tahoe-restored.1` `Universal-Binaries.dmg` was downloaded: 664,348,160
  bytes with SHA-256
  `3659ae0ebadc1062252bbeeb7fe75dce292b5b9d599681c6dfa3dc4430bbc6a4`, exactly
  the constant. A wrong pin would have failed every build and every install.
- The full build gate passes end to end on that image: digest check, read-only
  `hdiutil attach`, validation of every source emitted by the enabled families,
  and a clean detach.
- The tag's Git tree and the mounted image are path-identical: both hold 7,718
  `Universal-Binaries/` entries with no difference in either direction. The
  earlier "7,733 entries" figure was the whole repository tree.
- With Broadwell enabled in the probe's registry the image fails closed on
  exactly one source,
  `12.5-25/System/Library/Extensions/AppleIntelBDWGraphicsMTLDriver.bundle`.
  The second blocker recorded in the older audit below, `RenderBox-25`, is gone:
  the `shared_patches/metal_31001.py` gate added earlier in this session removed
  it. One blocker remains, not two.

Probing all 25 families in `HardwarePatchsetDetection.all_hardware_variants()`
shows Broadwell is not uniquely blocked. Five families reference eight sources
the pinned release does not publish, and the shared patch modules used by the
dormant families add seven more. All fifteen are recorded in `UNPUBLISHED` in
`tests/test_tahoe_psp_source_manifest.py`:

| Whose | Unpublished source |
| --- | --- |
| Graphics: Intel Ivy Bridge, Graphics: Intel Haswell, Graphics: Nvidia Kepler | `13.2.1-25/System/Library/Frameworks/Metal.framework` |
| Networking: Legacy Wireless | six paths under `12.7.2-25/` |
| Miscellaneous: T1 Security Chip | `13.7.1-25/.../LocalAuthentication.framework/Support/SharedUtils.framework` |
| the Non-Metal shared modules | seven `10.13.6-25`, `10.14.4-25`, `10.14.6-25` and `10.15.7-25` paths |

### The absent `12.5-25` directory

Five graphics families — Broadwell, Skylake, Polaris, Vega and Navi — are blocked
by one absent directory. `12.5-25` is not in the release, yet each of them wanted
a `12.5-25` copy of its Metal driver, because each built that path as
`f"12.5-{xnu_major}"`.

Those five copies were replaced with one resolver,
`BaseHardware._metal_driver_patch()`, which answers from a single table of
generations the release publishes. It returns the generation's directory where
one exists and omits the entry where none does, so the code can no longer name a
directory that does not exist:

- Ventura, Sonoma and Sequoia resolve to `12.5-22`, `12.5-23` and `12.5-24`
  exactly as before, and those directories are published.
- Tahoe omits the entry instead of naming `12.5-25`.

Deliberately not an option is falling back to a Sequoia driver: a copied Sequoia
bundle is not evidence of a working Tahoe one.

Omitting the entry makes the payload checks pass, which is why the incompleteness
is asserted directly in `tests/test_tahoe_metal_driver_resolver.py`: those five
families lose exactly their Metal driver on Tahoe and nothing else, they must
stay unregistered, and no patchset may interpolate its own generation into a
`12.5-` path again. The same file checks every resolver table value against
`SOURCE_DIRECTORIES`, the 97 directories the pinned release provides, so an entry
naming a directory that does not exist fails the suite rather than shipping.

Publishing one `12.5-25` directory would unblock all five.

### Published scope

The enabled registry is exactly Modern Wireless and Modern Audio, deliberately.
`README.md` advertises "Focused Modern Wi-Fi and AppleHDA root patching for
macOS", `REPORTS/PREPUBLICATION_PATCH_SCOPE_AND_OS_SUPPORT_AUDIT.md` requires
"Preserve `_hardware_variants` with exactly `ModernWireless` and `ModernAudio`"
and "Do not uncomment or add any dormant detector", and
`tests/test_publication_contract.py` pins that exact tuple.

Enabling Broadwell therefore needs all three of: a published `12.5-25` payload,
real device validation, and a revision of that audit report plus its registry
assertion. It is not a one-line uncomment.

### Offline source oracle

`ci_tooling/generate_psp_source_manifest.py` records which Tahoe root-patch
sources the pinned release publishes, in
`ci_tooling/psp_tahoe_source_manifest.py` (203 of the 218 required paths), along
with `SOURCE_DIRECTORIES`, the 97 source directories the release provides.
`UNPUBLISHED` in `tests/test_tahoe_psp_source_manifest.py` holds the remaining
fifteen with a reason. Together they make the image-backed contract reachable
without the 664 MB image, so a dormant family naming an unpublished source now
fails at test time.

Coverage comes from two probes:

- All 25 non-native hardware families, contributing 194 sources.
- All 14 shared patch modules in `patchsets/shared_patches`, probed directly and
discovered dynamically, so a new module is covered the moment it is added. They
contribute 63 sources, 24 of which no probing family reaches.

The shared modules are where the `RenderBox-25` bug actually lived, and the AMD
families consume them, so probing the modules directly covered paths that family
enumeration could not reach at the time. Verified by mutation twice:
reintroducing `RenderBox-25` makes the oracle name
`Graphics: Intel Broadwell -> RenderBox-25/...`, and changing `AMDOpenCL`'s
`12.5 non-AVX2.0` to the nonexistent `12.5-25`, reachable only through a dormant
AMD family, makes it name `12.5-25/System/Library/Frameworks/OpenCL.framework`.

The manifest is deliberately fail-closed: adding a root-patch source requires
either regenerating the manifest or recording why the source cannot ship.

### Fixture hardware for the dormant families

`ci_tooling/tahoe_probe_hardware.py` supplies four representative machines,
because `AMDLegacyGCN`, `AMDPolaris`, `AMDVega`, `AMDNavi` and `LegacyAudio` read
the detected GPUs and the model identifier to build their patch dictionaries and
so could not be enumerated on this host at all. A family's sources are the union
over the profiles, so a source it emits only under one hardware description is
still checked, and `present()` is deliberately not consulted: the question is
what the family requests on the hardware it targets, not what this machine has.

The profiles establish which payload paths a family can ask for, not that any
real Mac works, and they are not a substitute for the on-device test matrix.
Making these families probeable paid for itself immediately by exposing three
more `12.5-25` Metal driver gaps, for Polaris, Vega and Navi, which are now
handled by the resolver described above.

Enumerating them also required `AMDNavi` to appear in `all_hardware_variants()`.
That list is for tooling only and does not register anything:
`tests/test_publication_contract.py` still pins `hardware_variants()` to exactly
Modern Wireless and Modern Audio, and Navi stays dormant.

### One traversal for patchset sources (2026-09-29)

Finding those `12.5-25` gaps depended on the oracle and the installer agreeing
about which paths a patchset reads. They did not share any code that guaranteed
it. Four places composed a source's path independently:

| Site | Composition |
| --- | --- |
| `sys_patch._preflight_checks` | string concatenation, `startswith("/")` test |
| `sys_patch._execute_patchset` | the same expression again, at copy time |
| `support/validation._validate_root_patch_files` | concatenation against the payload root |
| `payload_contract.iter_root_patch_sources` | `Path` joins |

So a source could be validated at one path and copied from another. Two of the
four also wrapped `x in DynamicPatchset` in `try/except TypeError: pass`, a crash
guard rather than logic. All 343 copy entries in the Tahoe patch universe were
snapshotted under all five resulting path forms first and found to agree, which
is what made it safe to replace them with one implementation.

`patchsets/base.py` now defines that implementation: `compose_source_path`
(version/destination/filename), `resolve_source_path` (beneath the payload root,
or absolute), `source_entry_path` (the install-time path), `iter_patchset_sources`
(the walk, returning a `from_payload` flag) and `COPY_OPERATIONS`, the four patch
types that copy a file. The installer, the validator and the build contract are
all consumers; `tests/test_patchset_source_traversal.py` fails if any of them
composes a path itself or re-enumerates the four operations.

The refactor is behaviour-preserving and was checked against the snapshot: the
same 343 entries, resolving to the same bytes, with the manifest unchanged at 203
published and 15 unpublished sources. Two deliberate changes accompany it:

- The installer now reports **every** absent source in one message instead of
  raising on the first, so an incomplete payload does not have to be discovered
  one reboot at a time. That error is the new `PayloadSourceError`.
- The validator skips sources read from the booted root volume, because they sit
  outside the payload root and `_find_unused_files` resolves
  `active_patchset_files` with `.relative_to(payload_root)`. The installer still
  checks such a source at its own absolute path, which is what it did before.

### Payload dead-weight scan (2026-09-29)

`_find_unused_files` answers which of the ~7,700 files in the 664 MB
Universal-Binaries image no patch set can reach, which is what keeps the payload
from growing without bound. It had two problems beyond reach: it was dead code,
and it was quadratic.

**It could not be run.** `--validate` built the validator with
`verify_unused_files` left at False and nothing ever passed True, so the only way
to run it was to edit the source. `--validate_unused_payload` is now a validation
flag, and `_parse_arguments` branches on it as well as on `--validate` — without
that second half the flag parses and then falls through, doing nothing.

**It was too slow.** It recomputed `Path.relative_to` for every file against
every named source. On a synthetic payload of 8,000 files with 200 named sources
that is 297 seconds; the two set lookups that replace it run in 5.8, and the
remainder is the walk itself (`rglob` 0.74 s, `relative_to` 1.80 s).
`tests/test_unused_payload_scan.py` pins the shape of that with a call count
rather than a wall-clock time, since a timing test would be flaky.

**Its rule was looser than its own words.** "Is either path anywhere inside the
other" is a superset of "is named, or lives beneath a named directory", so it
could only ever hide dead weight: a file whose name merely begins with a named
path counted as used, which is how a kept-aside copy such as
`AppleHDA.kext.backup` stayed invisible. The subset rule is asserted on real
shaped data, not just claimed.

What it does not do is decide anything. It reports; removing payload files stays
a human decision, because a file no *currently enabled* patch set reaches is not
necessarily one no future release will want.

### Executing a patchset (2026-09-29)

The traversal above settled *which* path a source is read from, but only the
preflight half was ever exercised. `_preflight_checks` had direct tests; the copy
loop in `_execute_patchset` was covered indirectly, by a snapshot that compared
composed path strings. A divergence introduced at copy time would have kept the
suite green while an install aborted.

`tests/test_patchset_execution.py` drives the real `_execute_patchset` against a
real payload tree on disk, with only the volume operations mocked. The payload is
populated *only* where the patchset says a source lives, so the copy loop has to
compose the same path the preflight validated or the test fails.

**The file was proved to bite by mutation**, four substitutions that each failed
exactly the test claiming to guard the behaviour: appending the filename to the
source folder, swapping the system-volume test to the data volume, dropping the
pop that moves a rewritten entry out of its old directory, and turning the
missing-source abort into a log line. All four were reverted, and
`git diff --stat` for `sys_patch.py` is identical before and after the exercise.

Three properties that had no direct coverage are now pinned:

- Volume routing, including `/Library/Extensions` setting
  `needs_kmutil_exemptions`, the flag that makes the later rebuild bypass
  `kmutil`.
- A patch's removals run before its installs, and against their own volume.
- The AuxKC relocation rewrite. `add_auxkc_support` may hand back a different
directory, on which the loop moves the patchset entry, records it under the new
directory and copies the file exactly once. That single-copy rule holds only
  because the outer directory loop iterates a snapshot of the keys; the test
  asserts both the destination and the rewritten dictionary, so a change that
  re-sorted or re-entered the loop would be caught.

`EXECUTE` entries are covered too: the boolean value picks the elevated path, and
only the non-elevated one runs the string through a shell.

### The validation walk never reached Tahoe (2026-09-29)

`PatcherValidation._validate_sys_patch` walks a list of kernel majors and, for
each, checks every source the enabled families name against the mounted payload
and generates the patchset plist. That list was written out by hand as Big Sur
through Sequoia, so Darwin 25 — the only release this fork ships for — was the
one release whose sources no validation run had ever checked. The host gate in
`detect.py` already allowed Darwin 20 through 25, the range the tracked
`REPORTS/PREPUBLICATION_PATCH_SCOPE_AND_OS_SUPPORT_AUDIT.md` also records, so the
two disagreed about which releases the product supports.

That is the same shape as the missing `12.5-25` directory: a per-release list
with quietly fewer entries than the product supports. It survived because the
build-time payload contract validates Tahoe separately, and because the offline
oracle enumerates Tahoe whatever `--validate` happens to walk.

The range is now declared once, as `SUPPORTED_MAJOR_VERSIONS` in
`datasets/os_data.py`, and read by both the host bound and the walk, so the two
cannot drift apart again.

What the walk finds at Tahoe, measured offline against a payload materialised
from the published manifest: the enabled families name four sources at every
minor from 25.0 to 25.9 — `13.7.2-25` for `IO80211.framework`,
`WiFiPeerToPeer.framework` and `wifip2pd`, and `26.0 Beta 1` for
`AppleHDA.kext` — and all four are published. Every minor passes, and removing
any one of them still raises `PayloadSourceError` naming it, so the run above is
not vacuous.

`tests/test_root_patch_os_coverage.py` (7 tests) pins the range, proves the walk
reads the shared declaration rather than a literal list, drives
`_validate_root_patch_files` for 25.0 through 25.9 against a real payload tree,
and keeps the fails-without-a-source control. Three mutations confirmed it bites
— restoring the hand-written list, dropping Tahoe from the declaration, and
skipping the source-existence check — and all three were reverted. Detection
consults FileVault, SIP, the secure boot level and AMFI while it runs, so the
test patches those four answers out: they cannot change the source set, and the
subprocesses they spawn took it from 0.6 s to 18 s.

### Marketing versions stopped matching Darwin at Tahoe (2026-09-29)

`os_conversion` mapped marketing versions and kernel majors by a fixed offset.
macOS 11 through 15 are Darwin 20 through 24, so marketing plus nine gave the
kernel major and kernel minus nine gave the marketing one. macOS 26 is Darwin 25,
so the rule broke exactly where this fork lives, and nothing tested it:
`os_to_kernel("26.0")` returned **35**, and `kernel_to_os(25)` returned **"16"**.

Two consequences, one certain and one conditional:

- `ci_tooling/installer_backups/macOS_Installer_Backup.command` stores
  `os_to_kernel(version)` as each installer's `OS` and indexes a table keyed by
  release with it. For a macOS 26 entry that lookup was `_os_table[35]`, a
  `KeyError`; it now lands on the `os_data.tahoe` row. The same value also reaches
  `gui_macos_installer_flash`'s `installer['OS'] >= os_data.big_sur` size
  estimate, where 35 was only accidentally right. `gui_settings` prints
  `kernel_to_os(detected_os)` in the unsupported-model warning, so on a Tahoe
  host that dialog named macOS 16.
- `gui_macos_installer_flash` compares a local installer's
  `LSMinimumSystemVersion`, converted with `os_to_kernel`, against the host's
  kernel major. Whether that refuses a Tahoe installer depends on the value
  Apple writes into that plist, which cannot be checked offline. What is certain
  is that the comparison was only meaningful for marketing values below 26.

Two comparisons in `metallib_handler` and `kdk_handler` were correct by accident
for the same reason: 35 exceeds every `os_data` threshold, so the Sequoia and
Tahoe branches were selected for the wrong reason. After the fix they are
selected because 25 is Tahoe.

`tests/test_os_version_conversion.py` (12 tests) pins both directions over every
supported release, the round trip, the point-release forms, the era rule past
Tahoe, and each consumer property above. It was written first and failed
(`35 not less than or equal to 25`, `'16' != '26'`, `'26' != 'Tahoe'`), and four
of its own expectations were wrong on that first run, which is why the pre-Tahoe
table is now written out explicitly and the kernel-to-marketing direction is
tested with the major alone.

A sweep of the other per-release enumerations found no further drift, and the
result is recorded so the next release does not have to re-derive it:
`sucatalog.CatalogVersion` lists newest first and the installer list takes a
window of four from its head, so adding a release above it without updating
`max_install_assistant_version` would silently drop the newest installers from
that list (see "Installer Catalog Version Filters" below — the AppleDB window
had drifted a release behind the Software Update one, and both ceilings are now
pinned to agree); `CatalogVersion` and `Constants.icons_path` already include Tahoe; and
`legacy_accel_support` excludes it **on purpose**, because the non-Metal stack
cannot work on Tahoe (the unpublished `10.13.6-25`, `10.14.6-25` and `10.15.7-25`
directory gaps above block it).

## Installer Catalog Version Filters (2026-09-29)

`sucatalog` decides which installers the app offers, and two of those decisions
were made by comparing the *text* of a version string.

`CatalogProducts._list_latest_installers_only` dropped end-of-life installers
with `installer["Version"].split(".")[0] < supported_versions[-4].value`. Every
release in today's window (13 through 26) has a two-digit major, so the string
comparison happened to agree with numeric order, and the hardcoded `[-4]`
happened to name the oldest entry of the inverted four-wide window. Two cases
were wrong on the spot:

- A single-digit major. A classic Mac OS "9.2.2" entry is older than the "13"
  floor, but "9" > "13" as text, so it was offered as a supported installer.
- A two-component floor. Splitting the leading component off collapses every
  10.x release to "10", which is a *prefix* of a floor like "10.12" and
  therefore compares as smaller: against a Catalina-era ceiling the floor's own
  release was thrown away. The same pass raised `AttributeError` on a product
  with no version at all.

The floor is now parsed numerically, read as the oldest member of the window
rather than by a hardcoded offset, and a version that carries no number — the
pre-release names `CatalogVersion` still lists, or a missing one — is left alone
rather than ordered against a real release. The catalogue sort is numeric for
the same reason: as text, "9.2.2" sorts after "26.0".

`AppleDBProducts` capped its window at `os_data.sequoia`. That is the catalogue
`gui_macos_installer_download` builds, and it builds it with the constants alone,
so the default decides; its window is n-3 to n, so the app could not offer a
macOS 26 installer at all. The ceiling is now Tahoe, matching `CatalogProducts`,
and a test asserts the two ceilings name the same release.

`tests/test_installer_version_filters.py` (12 tests) was written first and failed
nine of twelve: the classic Mac OS entry survived, the two-component floor ate
its own release, a `None` version raised, and the three AppleDB ceiling
assertions failed. Four mutations — restoring the string floor, the text sort,
the Sequoia ceiling, and reading an unnumbered version as zero — are each
caught. Reverting the sort initially survived, because the test called the sort
*key* rather than an ordering seam; the ordering now lives in `_sort_products`,
which the `products` property calls and the test drives.

The live catalogue has now been fetched and recorded. On 2026-09-29, with the
Machine's own network, `AppleDBProducts` pulled 3,183 firmware entries from
`api.appledb.dev`, of which 28 passed every filter including the per-link HEAD
check. Driving `_list_latest_installers_only` — the seam the defect lived in —
over that data:

| ceiling | window (Darwin) | offered |
| --- | --- | --- |
| `os_data.tahoe` (shipped) | 22–25 | 13.7.8, 14.8.9, 15.8.1, **26.7.1 (25G241) — macOS Tahoe** |
| `os_data.sequoia` (before the repair) | 21–24 | 12.7.6, 13.7.8, 14.8.9, 15.8.1 — no macOS 26 |

So the defect is reproduced against real Apple data, and the repair is proven
against it: the shipped ceiling offers a Tahoe installer whose title resolves
through the datasets ("macOS Tahoe", not "macOS 26"), and the old ceiling could
not offer one at all. A trimmed slice of that live response — the 14 window
releases that the old ceiling and the new one disagree about, with the fields the
catalogue reads — is recorded in `tests/fixtures/appledb_macos_slice.json`, and
six tests drive the real `products`/`latest_products` over it with only the API
fetch and the per-link HEAD stood in for. The ceiling, the within-major
newest-wins rule, the RC-that-was-the-final-release dedup (21H1320 is recorded
both as 12.7.6 and as 12.7.6 RC 5) and the label are all pinned offline.

Building that fixture found a live fragility: AppleDB sends `beta` on every
release today, but the catalogue read it as `firmware.get("beta") or
firmware.get("rc")`, so an entry with neither key made `Beta` None — and
`sorted(..., key=lambda x: x["Beta"])` raises on `None < None` and on
`None < True`. It is now `bool(...)`, which is the invariant the search
formats assume, and a test feeds an entry with no `beta` key. An RC still ranks
as a beta either way.

The fixture's first version had a subtler fault worth recording: `products` is a
cached property that HEADs every link, and reading it *after* the patch context
exited made six "offline" tests quietly depend on the network — they passed
because this machine is online. The helper now evaluates both properties inside
the stubs, and the file runs in 0.07s. A test that passes for the wrong reason is
worse than one that fails.

So the network is now out of reach in the test process.
`tests/test_no_network_access.py` refuses every non-loopback address at
`socket.create_connection` and `socket.socket.connect` on import — the point where
`http.client`, `urllib3` and therefore `requests` all arrive. The suite runs with
it installed (**463 tests**), so no test was relying on connectivity, and putting
the helper's `return` back outside the stubs fails the catalogue tests with the
address they tried (`207.44.0.48`, swcdn.apple.com) and why. Loopback stays open,
because a local server is not the internet, and `allow_network()` is there for a
test that wants the real thing to say so out loud. A child process started by a
test has its own sockets; nothing in the suite shells out to something that
fetches.

Not covered: no installer was downloaded through the GUI, and no Tahoe installer
was booted. The window, the label, the dedup and the ordering are covered by the
recorded data; the download itself is unchanged.

## Bootable Installer Script (2026-09-29)

`support/macos_installer_handler.generate_installer_creation_script` writes the
script that formats a disk and runs the installer's own `createinstallmedia`, and
decides whether to append `--applicationpath`. That option belongs to the
macOS 10.12-and-earlier era; 10.13 dropped it, and Apple's instructions still
describe appending it on Sierra or earlier.

That decision read the platform version as *characters*. It truncated
`DTPlatformVersion` to its leading component — "10.12" became "10" — and then
asked whether `"10"[0]` was `"10"` (it is `"1"`) and whether `int("10"[1])` was
below 13 (it is 0). The outer condition was therefore never true and the flag was
never passed to anything. Flashing a 10.12-or-earlier installer through this path
produced a `createinstallmedia` call missing an option that era requires; macOS
11 and later never wanted the flag, so Tahoe is unaffected.

`tests/test_installer_creation_script.py` was written expecting the flag to be
passed *too often*, and its first run showed the Sierra-era cases with no flag at
all instead — which is why the test was written before the change. The decision
now lives in `_requires_applicationpath`, which parses the minor component as a
number, converts the 10.x release to its kernel major through the datasets, and
refuses to guess when there is nothing to parse (a missing key, a bare "10", a
non-numeric minor). Four mutations — the character guard, an off-by-one
bound, never asking, and asking on every 10.x release — are each caught. The
tests drive the real generator against an installer bundle on disk with only the
copy and the signature check mocked, so the assertions are on the script that
would be piped to OCLP-Helper.

Running them also surfaced a leaked file handle on the `Info.plist` read; it is
now closed with a context manager, as the script write beside it always was.

Not covered: nothing was flashed to a real disk, and no pre-10.13 installer was
available to run the generated script against.

## Release Numbers as Text and as Literals Are Guarded (2026-09-29)

The rules live in `ci_tooling/build_modules/version_contract.py`, not in the test
file, because they are a build contract as well as a test subject. `validate_source()`
is the first statement of `GenerateDiskImages.generate()` — ahead of the step that
deletes extra binaries from the working tree and the step that writes the images —
and `tests/test_version_text_ordering_guard.py` consumes the same scanner, so the
gate and the suite cannot drift apart. Run against this tree the gate reports zero
offenders; with the FileVault message put back by hand it refuses the build, names
the file, line, kind and text, and exits non-zero, and the file was restored
byte-identically afterwards. A test asserts both the refusal and the *order*, so
the gate cannot quietly move behind the destructive step.

Moving the scanner into `ci_tooling` means it scans itself, which immediately
found a hole in it: pragmas were detected by substring on the raw line, so the
module's own docstring — which documents the syntax — read as a pragma that
suppressed the next line. Pragmas are now collected from `tokenize` comments, so
only a real comment suppresses a finding, and a test quotes the syntax inside a
string to pin it.

Four defects in this fork were the same mistake reached from four directions, and
every one of them was found by reading code rather than by a failing test: a
string EOL floor in `sucatalog/products.py`, a text sort of product versions, an
AppleDB window stuck at Sequoia, and a character-indexed `DTPlatformVersion` in
`support/macos_installer_handler.py`. Fixing them one at a time leaves the next
one to a reader, so `tests/test_version_text_ordering_guard.py` parses the
production sources with `ast` and fails the suite when one of those shapes comes
back. A release number is a number.

The scan covers `opencore_legacy_patcher` and `ci_tooling` and recognises four
shapes: a version component compared as text (`x.split(".")[0] < "13"`), a
version-field ordering comparison (`a["Version"] > b["Version"]`), a character
index into a version variable, and `sorted`/`min`/`max`/`list.sort` whose `key`
reads version text without parsing it. Version text read *inside* a parser
(`packaging.version.parse`, `os_to_kernel`, `int`) is exactly the fix, so the
walk stops there — which is why the repaired code in all four files scans clean.

A second family shares the cause. The same files spelled release numbers out as
*literals*: the bare `13` in the `--applicationpath` test, a hardcoded `[-4]`
window offset, `os_data.sequoia` as the ceiling of a fork whose target is Tahoe.
So the scan also fails on a release number written inline in a comparison where
`os_data` and `CatalogVersion` already name it: a literal compared against an
expression whose name carries a version, kernel, Darwin major, build or SDK —
`version.major == 26`. Comparisons against `0` and `1` are exempt, because those
ask about presence (`<= 0` is the `.0` build) rather than about a release.

The copies also hid in *prose*: six messages across `kdk_handler` and
`kdk_merge` said "Darwin 26" while the policy that decided it was declared
elsewhere, so a fork that moved that policy would have kept printing the old
number. A string that names a release the datasets declare — the `os_data`
members and the `CatalogVersion` entries, read from their declarations rather
than imported — has to derive it. `RELEASE_NUMBER_TOKEN` requires the number to
stand alone, so a build identifier ("25G83") and a version ("26.6.2") are not
release numbers, and the string also has to talk about an OS, so "Creating macOS
installers can take 30min+ on slower USB drives" is prose about a duration.
Docstrings are exempt: a patchset docstring that says "Supported on macOS 11.0
and newer" is documentation, and documentation cannot derive anything.

The fourth rule looks at the other end — a declaration that restates a number the
datasets own. Its name test includes kernel and XNU, so a
`TAHOE_KERNEL_MAJOR = 25` would be caught too. `frozenset({26})` was the shape, and it took a reader to notice it
because the earlier rules were about uses. A declaration whose name names a
release (`RELEASE_DECLARATION_NAME`: Darwin, macOS, major, minor, release, or one
of the release code names) may not carry a literal that the datasets already
declare, in an int, a version string, or a shallow container such as
`frozenset({26})` or a dict keyed by release. `apfs_zlib_version = "12.3.1"` is
not a release and `TAHOE_BUILD = "25A5316i"` is a build identifier, so the rule
stays quiet on both. The two modules that declare which releases exist are
exempt, because restating a number there *is* the declaration, and a test pins
both that exemption and the fact that they are still scanned for the other
three rules.

That rule found three more copies. `ci_tooling/build_modules/payload_contract.py`
held `TAHOE_MARKETING_VERSION = "26.0"` (now derived through `kernel_to_os`) and
`ci_tooling/generate_tahoe_metallib_pins.py` held `DARWIN_MAJOR = 25` (now
`os_data.tahoe`); both files already imported the patcher, so neither needed the
literal. The third was not a copy but an ambiguity, and it is the reason the rule
is worth having: `APPLICATIONPATH_DROPPED_IN_MINOR = 13` is the *minor* of macOS
10.13, and 13 is also Ventura's marketing major. It was excused with a pragma
first, and then removed properly — the bound is now the kernel major of the
release that dropped the option, `int(os_data.os_data.high_sierra)`, and
`_requires_applicationpath` compares two kernel majors through `os_to_kernel`,
which owns the 10.x conversion. No bare `13` remains, so the last allowance
went with it.

Allowances are inline: `# version-text-ok: <reason>` or
`# release-number-ok: <reason>` on the flagged line or the line above suppresses
it. The reason has to be more than a placeholder, and a pragma that suppresses
nothing fails the suite, so an allowance cannot outlive the code it excuses. The
mechanism was used once and then retired, and the tree carries none today.

Thirty-nine tests: twenty-nine self-tests pinning each shape (and the legitimate
forms that must stay quiet), a whole-tree scan, a check that the scan still
reaches the four repaired files, seven "revert the real fix" tests that rewrite a
shipped file in memory and assert the guard trips, the pragma check, an integrity
check that the declared-number set really is the datasets', and the exemption
check on the two declaration modules. Reverting `_version_order_key`
in `support/macos_installer_handler.py`, `TAHOE_MARKETING_MAJOR` in
`support/kdk_selection.py`, the FileVault 2 message in `efi_builder/firmware.py`
and `TAHOE_MARKETING_VERSION` in `ci_tooling/build_modules/payload_contract.py`,
on disk — not in a string — makes the guard fail, and all four files were
restored byte-identically. Every revert is a mistake this fork actually shipped,
re-made.

Restoring the `--applicationpath` defect itself is the strongest of them: putting
`int(platform_version[1]) < 13` back, on disk, fails **eleven** tests — the
guard's tree scan and its revert test, plus nine in
`test_installer_creation_script.py` that assert on the script piped to
OCLP-Helper. The dead branch is caught in the syntax and in the behaviour, which
is what a guard is for.

The guard immediately found a fifth instance of the family, in the code path the
previous section repaired: `LocalInstallerCatalog._list_local_macOS_installers`
ordered the local installer list with `key=lambda item: item[1]["Version"]`. As
text, "9.2.2" lands after "26.0" and "13.7.2" before "9.2.2", so the installer
dropdown was in the wrong order. The key is now `_version_order_key`, which
orders the version's numeric components and keeps a version with no number
("Unknown") last by its text instead of dropping it. Three tests in
`test_installer_creation_script.py` drive the real catalog against a temporary
`/Applications`: a single-digit major no longer sorts after a two-digit one, an
unnumbered version sorts last, and the key still breaks ties on equal numbers.
Their `ResourceWarning`s exposed two leaked `Info.plist` handles in the same
file (the catalog listing and the SharedSupport reader); both are now closed
with context managers.

One false positive was worth narrowing rather than excusing: `boot_args.split()`
in `efi_builder/build.py` is a word split in a membership test, not a version.
`_is_split_call` now requires the separator to be spelled out as `"."`, and a
self-test keeps the AMFIPass shape quiet. `self._xnu_minor <= 0` in
`nvidia_kepler.py`, which names the `.0` build either side of a beta boundary,
is covered by the sentinel exemption rather than a pragma.

The bare-literal rule found five sites, and the first was the interesting one:
`kdk_handler` asked `kdk_darwin_major(build) == 26` to choose between "prohibited
Darwin 26 KDK" and "no valid ProductBuildVersion" — a second copy of the policy
`kdk_selection` already declared as `frozenset({26})`. Watching that literal is
what exposed the duplication: the block, the Darwin major, the marketing major
and six log messages were each spelled out separately, so bumping the fork's
target would have left them behind. `support/kdk_selection.py` now derives all of
them from the datasets — `TAHOE_DARWIN_MAJOR` from `os_data.tahoe`,
`TAHOE_MARKETING_MAJOR` through `kernel_to_os`, the block as the next major, and
the message text from the block — and `kdk_handler.py` and `kdk_merge.py` read
that one declaration. The remaining three sites were minor thresholds in
`sys_patch_helpers.patch_gpu_compiler_libraries` (`< 4  # 13.3`, `< 2  # 14.2
Beta 2`), now `GPU_COMPILER_BASE_FROM_VENTURA_MINOR` and
`GPU_COMPILER_BASE_FROM_SONOMA_MINOR`; that change is a rename and no behaviour
moved. `test_darwin26_kdk_policy.py` gained a test asserting the numbers derive
from `os_data.tahoe` and `CatalogVersion.TAHOE`, so a fork that moves its target
has one place to move.

The prose rule found one more live copy: `efi_builder/firmware.py` logged
"Enabling macOS 26 FileVault 2 support" against the same number, and now derives
it through `kernel_to_os`. Removing the copies is what makes the numbers
checkable — while six of them existed, none of them was wrong enough to notice.

Not covered: the guard reasons about syntax, so a version compared as text
through a helper of another name — or read from an attribute rather than a
mapping key — and a number copied into a *comment* rather than into a string
literal, are out of its reach. That is the same class of gap the pragma
mechanism exists to make visible. A build identifier is deliberately not a
release number: the KDK policy already draws that line between a
`ProductBuildVersion` and a `ProductVersion`, and the guard follows it.

## Broadwell Payload Audit (2026-09-27)

The exact `2.0.0-tahoe-restored.1` PatcherSupportPkg Git tree was queried
recursively. It contains 7,733 entries, including every source selected by
the currently enabled Tahoe Modern Wireless and Modern Audio patchsets. The
release DMG was downloaded and its SHA-256
`3659ae0ebadc1062252bbeeb7fe75dce292b5b9d599681c6dfa3dc4430bbc6a4`
matches GitHub's published release-asset digest. The read-only image check
passed for enabled Tahoe patches. With Broadwell added temporarily to the
check's in-memory registry, the image fails for exactly two missing sources:

- `12.5-25/System/Library/Extensions/AppleIntelBDWGraphicsMTLDriver.bundle`
- `RenderBox-25/System/Library/PrivateFrameworks/RenderBox.framework/Versions/A/Resources/default.metallib`

Enabling Broadwell detection with this payload would fail root-patch
preflight. Do not substitute a Sequoia variant without a Tahoe compatibility
test on the target machine.

The application build now validates all sources emitted by enabled Tahoe
hardware patchsets against the downloaded `Universal-Binaries.dmg`. This
keeps the current wireless/audio package buildable while ensuring that a
future Broadwell registry change fails the build until the matching resources
are supplied. Both cached and newly downloaded images must match the pinned
release SHA-256 before they are mounted read-only for the source check. The
mount skips hdiutil's image verification only after the SHA-256 check succeeds.
Local free space later increased enough to validate the exact tagged DMG.
The focused payload-contract suite passes (17 tests). CI must still exercise
the new image validation on the exact repair commit.

A second local integration run through `_download_resources()` reached
`hdiutil attach` but did not finish within about three minutes, so it was
interrupted. `hdiutil info` confirmed the image was not left mounted. The
validator has a ten-minute attach timeout; investigate repeat-mount latency
if CI hits that limit.

## Experimental Fork Follow-up (2026-09-28)

The T2 fork is a useful source of experimental Tahoe patching work, but its
published successful graphics examples are 2017 Kaby Lake MacBook Pros. Its
README still lists 2015 Haswell/Broadwell graphics as work in progress, with
no validated `MacBookAir7,2`/HD 6000 report:
<https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2#-work-in-progress--experimental-testing>.

Its PatcherSupportPkg tag `2.0.3` contains a `12.5-25` Broadwell MTL driver
bundle, unlike this project's pinned `2.0.0-tahoe-restored.1` tag. The Git
tree object SHA for the entire `AppleIntelBDWGraphicsMTLDriver.bundle` is
`604bd8c9bdf0d16c46d96d84a2d29ae264af98aa` for both `12.5-24` and
`12.5-25` (and `12.5-26`). Its main driver executable and `_real` executable
also have identical blob SHAs across those directories. Therefore the
`12.5-25` directory is a copy of the Sequoia-era bundle, not evidence of a
distinct Tahoe rewrite. This alone does not establish whether the copied
bundle could work with other Tahoe patches.

The T2 fork's `LegacyMetal31001` implementation removes the `RenderBox-25`
override because no such source exists and applies metallib downgrades from
MetallibSupportPkg on Tahoe instead:
<https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/blob/main/opencore_legacy_patcher/sys_patch/patchsets/shared_patches/metal_31001.py>.
It uses a Tahoe-capable metallib provider, while Dortania's published
MetallibSupportPkg is Sequoia-oriented. The T2 package's tagged tree still
has no `RenderBox-25`. A future port must assess the metallib package and
its build-specific download/checking path, not merely rename a RenderBox
file.

Do not enable Broadwell for the primary MacBook target on this evidence
alone. Next evidence needed: a reproducible `MacBookAir7,2`/HD 6000 Tahoe
test with Metal acceleration, display output, brightness, sleep/wake, and
rollback; then port the smallest proven source and payload changes, update
the pinned package digest, and run the root-patch contract and detection
tests. No experimental fork has been installed or run on this machine.

## Dortania MetallibSupportPkg Check (2026-09-28)

Dortania's MetallibSupportPkg README explicitly targets Metal 3802 graphics
on macOS Sequoia, and its current manifest at
<https://dortania.github.io/MetallibSupportPkg/manifest.json> has 88 entries,
all for macOS 15/Darwin 24; none is for macOS 26/Darwin 25. This project's
`opencore_legacy_patcher/support/metallib_handler.py` still uses that
manifest URL. It cannot supply the Tahoe metallib downgrades used by the T2
fork's experimental Metal 31001 patches. The Tahoe-capable metallib API used
by that fork must be assessed separately, along with its build matching and
package integrity checks. MetallibSupportPkg supplies shader libraries, not
the Broadwell MTL driver bundle.

## Self-building Tahoe Metallibs (2026-09-28)

Building a Tahoe metallib package is technically possible using the
Tahoe-capable `pyquick/MetallibSupportPkg` fork, whose README documents
fetching an exact macOS 26 IPSW, extracting its system volume, patching the
`.metallib` files, and packaging them:
<https://github.com/pyquick/MetallibSupportPkg>. Its releases include Darwin
25 builds, which demonstrates a package pipeline; this does not establish
HD 6000 acceleration on the target MacBook.

The current development host is macOS 15.7.9 with Xcode 26.3. `xcrun metal`
exists, but `xcrun metal-objdump` and `xcrun metallib` fail because the
optional Metal Toolchain is not installed. Apple documents installing that
component separately:
<https://developer.apple.com/documentation/xcode/downloading-and-installing-additional-xcode-components>.
There is no Tahoe IPSW or prebuilt Tahoe metallib package in the workspace;
the development volume had about 6.8 GiB free and the external volume about
8.7 GiB. A full IPSW extraction and package build need substantially more
scratch space. No package was built or installed during this audit.

For a reproducible build, use a machine/drive with room for the IPSW and
extracted system volume, install the Metal Toolchain, select the exact Tahoe
build running on the target Mac, and follow the fork's fetch, extract, patch,
and package commands. Record the source revision, IPSW build, package hash,
and the build log. Inspect the package contents and wire its source into the
patcher's build-specific metallib resolver only after those checks. The
Broadwell MTL bundle and root-patch behavior remain separate requirements.

## Runtime Image Check and Storage (2026-09-28)

The runtime root-patch mount now hashes a present `Universal-Binaries.dmg`
before mounting it and refuses an image that differs from the pinned
PatcherSupportPkg release digest. The build-time validator already checks the
same digest and Tahoe source paths. Focused runtime/image tests pass. The full
suite ran 268 tests: 263 passed and the same five Broadwell detection tests
failed because the graphics family remains deliberately disabled.

Regenerable Xcode/CMake/Swift build output was removed from other local
projects. Free space rose from about 6.3 GiB to roughly 17 GiB after APFS
reclaimed the files. No Tahoe IPSW or graphics package was downloaded.
`system_profiler` reports Intel HD Graphics 6000 on this host, while its
current model identifier is `MacBookAir9,1`; account for the spoofed model
when validating the physical Broadwell MacBook Air.

## Tahoe Graphics Follow-up (2026-09-28)

The same host currently runs macOS 15.7.9 (24G830). Its Intel HD Graphics
6000 reports PCI device ID `0x1626`, platform ID `0x16260006`, 1536 MB dynamic
VRAM, and Metal support on Sequoia. This is a useful working baseline, not
evidence of Tahoe acceleration. The optional Xcode `metal-objdump` and
`metallib` tools remain unavailable.

The T2 fork's current shared Metal 31001 Tahoe patch removes the nonexistent
`RenderBox-25` override and routes Tahoe to MetallibSupportPkg's downgraded
shader libraries. Its source comment identifies WindowServer deadlock fixes
for AMD GCN/Polaris and Intel Skylake, but does not claim a Broadwell test:
<https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/blob/main/opencore_legacy_patcher/sys_patch/patchsets/shared_patches/metal_31001.py>.
The fork uses `https://albert-mueller.github.io/MetallibSupportPkg/manifest.json`
with other fallback manifests. Its maintainer states that this API supports
Tahoe 26.6 and later, not earlier builds:
<https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/issues/161>.
That is a provider claim, not a verified HD 6000 result. The fork's README
still lists only Kaby Lake Macs as validated Tahoe graphics examples:
<https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2>.

The untracked Broadwell detection test once encoded the old `RenderBox-25`
override and asserted that Broadwell needs no metallib package. Both were
superseded on 2026-09-29: `RenderBox-25` is no longer emitted on Tahoe and the
test pins dormancy. Keep Broadwell disabled until an exact Tahoe build, a
matching metallib package, a published Broadwell MTL driver source, a revision
of the publication scope, and device rollback/test results are all available.
No external fork was installed or used to modify this host.

### Prebuilt 26.6.2 metallib package inspection

The `pyquick/MetallibSupportPkg` release tagged `26.6.2-25G83` publishes a
116,574,196-byte package with SHA-256
`3578553873558f97c7aba27722fb16ec63ab838a8b4d823c07e129c6df9c5867`
and a `sys_patch_dict.py` with SHA-256
`49d48dc731955312487d4863393e73383aa9420217c5dac9f547b6148edfbb0b`:
<https://github.com/pyquick/MetallibSupportPkg/releases/tag/26.6.2-25G83>.
Both downloaded files matched GitHub's release-asset digests. The package is
unsigned and was expanded only for read-only inspection; it was not installed.
Its payload places 180 `.metallib` files under
`/Library/Application Support/Pyquick/MetallibSupportPkg/26.6.2-25G83`.
The patch dictionary names 182 files. The remaining two use source version
`14.6.1` (CoreImage and MPSCore) and are present in the pinned
`2.0.0-tahoe-restored.1` PatcherSupportPkg Git tree, under
`Universal-Binaries/14.6.1`. This verifies source availability across the
two packages for build `25G83`; it does not establish binary compatibility,
that the current patcher resolves both roots, or Broadwell acceleration.
The temporary expanded package and download were removed after inspection.

## RenderBox-25 Latent Bug (2026-09-29)

`LegacyMetal31001.get_patches()` in
`opencore_legacy_patcher/sys_patch/patchsets/shared_patches/metal_31001.py`
emitted `f"RenderBox-{self._xnu_major}"` unconditionally. On Tahoe that names
`RenderBox-25`, a source no pinned PatcherSupportPkg release publishes, so
root-patch preflight would fail for every family consuming this patch set:
`intel_broadwell`, `intel_skylake`, `amd_legacy_gcn`, `amd_polaris`, and
`amd_vega`. The failure was latent rather than reported only because those
families are currently disabled on Tahoe.

The patch set now returns no patches once `xnu_major` reaches
`os_data.tahoe.value`, with a comment recording why. Ventura, Sonoma and
Sequoia output is unchanged. `tests/test_tahoe_metal_31001_sources.py` covers
the Tahoe behaviour.

The untracked Broadwell detection test had encoded the disproved model: it
expected a `"Metal 31001 Common"` patch group containing a `RenderBox-25`
source. Those assertions were corrected to assert the group's absence, so the
five remaining Broadwell failures are now only about the deliberately disabled
graphics family rather than about this bug.

## Tahoe Metallib Resolver and Digest Pinning (2026-09-29)

The static manifest used for Sequoia serves no Tahoe build, so the previous
lookup could never satisfy a Darwin 25 host. Tahoe now resolves through the
GitHub releases API of `pyquick/MetallibSupportPkg`
(`TAHOE_METALLIB_RELEASES_API`) and falls back to the existing manifest path
when the API is unreachable.

Only pinned bytes are accepted. `TAHOE_METALLIB_PINNED_SHA256` holds the
SHA-256 of all 55 published Darwin 25 packages, generated from the releases
API. A build absent from the table is refused with a warning, and a build whose
upstream digest disagrees with its pinned value is refused as well.
`verify_metallib()` re-hashes the downloaded file immediately before the
root-privileged install runs and returns `False` without elevating when the
digest does not match.

Third-party manifests that do list Darwin 25 builds were rejected as a source:
they resolve to `github.com/hackdoc/metal`, not pyquick, and publish
byte-different packages under identical version tags. The tag `26.6.2-25G83`
illustrates it, with hackdoc's 116,574,280-byte package and
`73c98a51...ff55` against pyquick's 116,574,196 bytes and `35785538...5867`.
Following those manifests would install a different publisher's bytes under a
pyquick-looking name.

The installed-package check now scans the Pyquick, Dortania and Hackdoc vendor
folders, so a package that is already installed is recognised instead of being
downloaded again. Selection prefers an exact build match, then the closest
release in the same major version that is not newer than the host.

These are shader libraries for Metal 3802 GPUs. They are not the Metal 31001
Broadwell driver and do not by themselves enable Broadwell graphics.

### Pinned table maintenance

`ci_tooling/generate_tahoe_metallib_pins.py` regenerates the table from the
releases API. `--stdout` prints it, the default rewrites only the pinned block,
and `--check` exits non-zero when the checked-in table differs from what the
generator would produce. It is deliberately not wired into CI: a new upstream
build should not fail an unrelated build or test run. Run it whenever upstream
publishes a Darwin 25 package, so the refusal path applies only to genuinely
unvetted builds rather than to every build of a new OS release.

Fresh coverage lives in `tests/test_tahoe_metallib_resolver.py` (13 tests) and
`tests/test_tahoe_metallib_pin_generator.py` (13 tests): digest refusal,
closest-match selection, API failure fallback, install refusing to elevate on a
bad digest, all three vendor install paths, and the generator's pagination,
ordering and rewrite boundaries. One test asserts the checked-in table is
already in the generator's canonical form, and another pins the `25G83` digest
against the value recorded above.

Not covered: no Darwin 25 metallib package has been downloaded or installed for
the target build, and no on-device result exists. Resolution and integrity
checking are tested; acceleration is not.
