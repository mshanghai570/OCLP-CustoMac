# Tahoe on-device validation checklist

This checklist is for the supported Modern Wireless + Modern Audio scope. It
does **not** enable dormant Broadwell graphics patching. The primary target is
MacBookAir7,2 with Intel HD 6000. Do not proceed if the candidate EFI fails its
offline validation or the target cannot be recovered with known-good media.

## Before installing anything

- [ ] Record the exact Mac model, board ID, CPU/GPU, installed macOS build,
      installer build, and patcher source revision.
- [ ] Keep a verified, bootable rollback USB/EFI and a second recovery route.
      Confirm that the rollback EFI boots before replacing the installed EFI.
- [ ] Back up important data and record current SIP, Secure Boot, FileVault,
      root-patch, Wi-Fi/Bluetooth, audio, display, brightness, and sleep state.
- [ ] Build the EFI for `MacBookAir7,2` and inspect the generated config and
      build log. Confirm Modern Wireless and Modern Audio are the only registered
      root patch families; do not infer graphics support from the HD 6000 being
      detected or from root-patch resource checks passing.
- [ ] Save the exact generated EFI, config plist, and build log with hashes.
- [ ] Confirm the Tahoe installer is the intended build and that the recovery
      route can reinstall or roll back without relying on the test EFI.

## First boot and core checks

Boot from the test EFI while retaining the rollback EFI. Stop and roll back on a
kernel panic, boot loop, inaccessible recovery, or unexpected loss of input.

- [ ] Boot to the intended Tahoe build; record the build number and boot log.
- [ ] Confirm keyboard and trackpad input, including click and gestures.
- [ ] Confirm internal panel output, native resolution, and stable display after
      sleep/wake. Record that graphics acceleration is **not claimed** by this
      patch scope; note any graphical artifacts separately.
- [ ] Check brightness keys and the on-screen brightness indicator at low,
      middle, and high settings; do not treat brightness control as proof of
      graphics acceleration.
- [ ] Check Wi-Fi: scan, join a known network, transfer data, reconnect after
      sleep/wake, and note interface/driver state.
- [ ] Check Bluetooth: discover and pair a known device, verify input/audio if
      applicable, disconnect/reconnect, and check after sleep/wake.
- [ ] Check audio output and input (if present), volume controls, and playback
      after sleep/wake.
- [ ] Check sleep/wake at least three times on AC and once on battery. Record
      wake time, Wi-Fi/Bluetooth/audio recovery, and any panic or hang.

## Persistence and rollback

- [ ] Reboot once more and repeat the core Wi-Fi, Bluetooth, audio, display,
      brightness, keyboard/trackpad, and sleep/wake checks.
- [ ] Verify root-patch state and patcher logs. Confirm no unrelated patch family
      was selected and no failed/partial patch is reported.
- [ ] Test the rollback EFI before removing the test media. If a failure occurred,
      roll back first and capture logs afterward; do not repeatedly boot a failing
      configuration.
- [ ] Record pass/fail, exact reproduction steps, logs, photos/video for visual
      issues, and whether rollback was needed. Keep serial numbers and other
      personally identifying data out of shared reports.

## Scope and exit criteria

Passing this checklist supports only the exact hardware, Tahoe build, patcher
revision, and Modern Wireless/Modern Audio behaviors tested. It does not validate
Broadwell/Metal acceleration, other Mac models, other Tahoe point releases, or
installation media. Broadwell graphics remains dormant until its unpublished
`12.5-25` driver source gap is resolved, the publication scope is deliberately
revised, and graphics acceleration is independently validated with rollback.

A release candidate is not device-validated until the completed checklist and
captured evidence have been reviewed alongside a green CI run for the exact
source revision and package inspection results.
