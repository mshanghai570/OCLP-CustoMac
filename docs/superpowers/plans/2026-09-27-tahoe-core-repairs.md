# Tahoe Core Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore constants initialization and deterministic AppleHDA selection for Tahoe.

**Architecture:** Restore the eager `Constants` class. Make ModernAudio describe the target system volume without querying the host OS.

**Tech Stack:** Python, `unittest`, GitHub Actions on macOS.

**Spec:** `docs/TAHOE-INTEL-REPAIR-HANDOFF.md`

## Global Constraints

- Target macOS Tahoe on `MacBookAir7,2`-class Intel Broadwell hardware.
- Preserve the uncommitted ModernAudio import correction.
- Do not alter `.agent-orchestration/`, `.tmp_config/`, `data/`, or the Broadwell registry in this plan.

---

### Task 1: Restore the `Constants` contract

**Files:**

- Modify: `opencore_legacy_patcher/constants.py:13-1105`
- Create: `tests/test_constants_contract.py`

**Interfaces:**

- Produces: `Constants()` with public settings available at construction.

- [ ] **Step 1: Write the failing test**

```python
def test_constructor_exposes_core_runtime_settings(self) -> None:
    current = Constants()
    self.assertEqual(current.patcher_version, "3.0.3")
    self.assertEqual(current.patcher_name, "OCLP-CustoMac")
    self.assertEqual(current.opencore_version, "1.0.7")
    self.assertEqual(current.payload_path.name, "payloads")
```

- [ ] **Step 2: Verify it fails**

Run `.venv-build/bin/python -m unittest tests.test_constants_contract -v`. Expect `AttributeError` for `patcher_version` with `LazyConstants` active.

- [ ] **Step 3: Restore eager initialization**

Replace `LazyConstants` with `Constants`; retain the original one-pass initializer. Remove `_initialized`, `_cached_values`, `_ensure_initialized`, `_initialize_constants`, and `Constants = LazyConstants`.

- [ ] **Step 4: Verify it passes**

Run `.venv-build/bin/python -m unittest tests.test_constants_contract -v`. Expect PASS.

- [ ] **Step 5: Commit**

Run `git add opencore_legacy_patcher/constants.py tests/test_constants_contract.py`, then `git commit -m "fix: restore constants initialization"`.

### Task 2: Make ModernAudio target-deterministic

**Files:**

- Modify: `opencore_legacy_patcher/sys_patch/patchsets/hardware/misc/modern_audio.py:4-104`
- Modify: `tests/test_phase3b_kdk_selection.py:121-139`

**Interfaces:**

- Produces: `AppleHDA.kext: "26.0 Beta 1"` for non-native Tahoe and `{}` for native OS versions.

- [ ] **Step 1: Write the failing test**

Mock `modern_audio.subprocess.run` to return `"com.apple.driver.AppleHDA"`; call `ModernAudio(25, 0, "25A123", constants).patches()` and assert `extensions["AppleHDA.kext"] == "26.0 Beta 1"`.

- [ ] **Step 2: Verify it fails**

Run the single test with `unittest -v`. Expect missing `AppleHDA.kext` because the current host probe returns an empty mapping.

- [ ] **Step 3: Restore deterministic payload selection**

Keep `from .....constants import Constants`. Remove the cache, `kextstat` helpers, and conditional mapping; return the fixed AppleHDA mapping from `_modern_audio_patches()`.

- [ ] **Step 4: Verify it passes**

Run `.venv-build/bin/python -m unittest tests.test_phase3b_kdk_selection -v`. Expect PASS.

- [ ] **Step 5: Commit**

Run `git add opencore_legacy_patcher/sys_patch/patchsets/hardware/misc/modern_audio.py tests/test_phase3b_kdk_selection.py`, then `git commit -m "fix: make Tahoe audio patch deterministic"`.

### Task 3: Validate the repaired core

**Files:**

- Verify: `.github/workflows/build-app-wxpython.yml:94-143`
- Modify: `docs/TAHOE-INTEL-REPAIR-HANDOFF.md`

**Interfaces:**

- Produces: a green validation record for a separate Broadwell graphics plan.

- [ ] **Step 1: Run `.venv-build/bin/python -m unittest discover -s tests`.** Expect PASS.
- [ ] **Step 2: Run `.venv-build/bin/python -m compileall -q opencore_legacy_patcher ci_tooling tests`.** Expect exit code 0.
- [ ] **Step 3: Run `git diff --check`.** Expect no output.
- [ ] **Step 4: Trigger and watch CI for the repair SHA.** Expect success for that exact commit.
- [ ] **Step 5: Update the handoff with the workflow URL, SHA, conclusion, and package validation result.**
