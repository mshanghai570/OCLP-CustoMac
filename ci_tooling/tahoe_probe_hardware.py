"""
tahoe_probe_hardware.py: Representative hardware for probing the dormant Tahoe families

Four root-patch families cannot be enumerated on a machine that is not that
hardware: `AMDLegacyGCN`, `AMDPolaris` and `AMDVega` build their patch
dictionaries from the detected GPUs, and `LegacyAudio` from the machine
identifier. Until they could be probed, whatever PatcherSupportPkg paths they
ask for went unchecked, which is how a source naming a directory that exists in
no release could reach a dormant patch class unnoticed.

The profiles below supply only what those families read, chosen to reach the
widest set of sources each one can emit. They are fixtures for source
enumeration, not hardware claims: a profile establishes which payload paths a
family can request, not that any real Mac accelerates correctly.

`present()` is deliberately not consulted. It reports what this machine has, and
a dormant family is dormant precisely because this machine does not have it; the
question being answered is what the family would request on the hardware it
targets.
"""

import types

from collections.abc import Iterable
from dataclasses import dataclass

from opencore_legacy_patcher.detections import device_probe
from opencore_legacy_patcher.datasets import pci_data
from opencore_legacy_patcher.sys_patch.patchsets.hardware.base import BaseHardware

from ci_tooling.build_modules.payload_contract import PayloadContract


GPU_CLASS_CODE: int = 0x030000


def _amd(device_id: int) -> device_probe.AMD:
    return device_probe.AMD(
        vendor_id=device_probe.AMD.VENDOR_ID,
        device_id=device_id,
        class_code=GPU_CLASS_CODE,
    )


def _intel(device_id: int) -> device_probe.Intel:
    return device_probe.Intel(
        vendor_id=device_probe.Intel.VENDOR_ID,
        device_id=device_id,
        class_code=GPU_CLASS_CODE,
    )


def _nvidia(device_id: int) -> device_probe.NVIDIA:
    return device_probe.NVIDIA(
        vendor_id=device_probe.NVIDIA.VENDOR_ID,
        device_id=device_id,
        class_code=GPU_CLASS_CODE,
    )


@dataclass(frozen=True)
class TahoeProbeProfile:
    """One representative machine used to enumerate a dormant family's sources."""

    name: str
    description: str
    real_model: str
    devices: tuple[device_probe.GPU, ...]
    cpu_leafs: tuple[str, ...] = ()
    rosetta_active: bool = False

    def computer(self) -> types.SimpleNamespace:
        """A ``Constants.computer`` stand-in carrying only what the families read."""
        return types.SimpleNamespace(
            real_model=self.real_model,
            gpus=list(self.devices),
            cpu=types.SimpleNamespace(leafs=set(self.cpu_leafs)),
            rosetta_active=self.rosetta_active,
        )


_GCN_DEVICES: tuple[device_probe.GPU, ...] = (
    _amd(pci_data.amd_ids.gcn_7000_ids[0]),
    _amd(pci_data.amd_ids.gcn_8000_ids[0]),
    _amd(pci_data.amd_ids.gcn_9000_ids[0]),
)

_POLARIS_DEVICE: device_probe.GPU = _amd(pci_data.amd_ids.polaris_ids[0])
_VEGA_DEVICE: device_probe.GPU = _amd(pci_data.amd_ids.vega_ids[0])
_NAVI_DEVICE: device_probe.GPU = _amd(pci_data.amd_ids.navi_ids[0])
_IVY_DEVICE: device_probe.GPU = _intel(pci_data.intel_ids.ivy_ids[0])
_KEPLER_DEVICE: device_probe.GPU = _nvidia(pci_data.nvidia_ids.kepler_ids[0])

_AMD_DEVICES: tuple[device_probe.GPU, ...] = (
    *_GCN_DEVICES,
    _POLARIS_DEVICE,
    _VEGA_DEVICE,
    _NAVI_DEVICE,
)

TAHOE_PROBE_PROFILES: tuple[TahoeProbeProfile, ...] = (
    TahoeProbeProfile(
        name="amd_gcn_polaris_vega",
        description=(
            "Pre-AVX2 Mac Pro with GCN, Polaris, Vega and Navi cards and no Metal 3802 "
            "GPU, so the GCN bronze bundle resolves to the Sequoia source."
        ),
        real_model="MacPro6,1",
        devices=_AMD_DEVICES,
    ),
    TahoeProbeProfile(
        name="amd_gcn_with_metal_3802",
        description=(
            "The same AMD cards alongside Ivy Bridge and Kepler GPUs, which switches "
            "the GCN bronze bundle back to the stock Monterey source."
        ),
        real_model="MacPro6,1",
        devices=(*_AMD_DEVICES, _IVY_DEVICE, _KEPLER_DEVICE),
    ),
    TahoeProbeProfile(
        name="legacy_realtek_audio",
        description="Early Intel iMac, which takes the Realtek audio branch.",
        real_model="iMac8,1",
        devices=(),
    ),
    TahoeProbeProfile(
        name="legacy_missing_gop",
        description="Other pre-2012 Mac, which takes the missing-GOP audio branch.",
        real_model="MacPro3,1",
        devices=(),
    ),
)


def probe_tahoe_hardware(
    variants: Iterable[type[BaseHardware]] | None = None,
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Return `({family: sources}, {name: failure})` for every Tahoe hardware family.

    Each family's sources are the union over every probe profile, so a source a
    family emits only under one hardware description is still checked. Families
    that fail under every profile are reported rather than silently skipped.
    """

    if variants is None:
        from opencore_legacy_patcher.sys_patch.patchsets.detect import HardwarePatchsetDetection

        variants = HardwarePatchsetDetection.all_hardware_variants()

    variants = list(variants)

    sources: dict[str, set[str]] = {}
    failures: dict[str, str] = {}
    for profile in TAHOE_PROBE_PROFILES:
        contract = PayloadContract()
        families, unprobeable = contract.probe_tahoe_patches(
            variants, computer=profile.computer()
        )
        for family, patches in families.items():
            sources.setdefault(family, set()).update(
                relative_path
                for _, relative_path in contract.iter_root_patch_sources(patches)
            )
        failures.update(unprobeable)

    # A family that probed under any profile is not unprobeable, whichever
    # profile happened to report it.
    for family in sources:
        failures.pop(family, None)

    return sources, failures
