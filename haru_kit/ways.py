"""Which ways can run on this computer, and what each way needs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .detect import Facts

WAYS = ("mac", "nvidia", "modal")
LABELS = {"mac": "On this Mac", "nvidia": "On this NVIDIA GPU", "modal": "On Modal (cloud GPU)"}
SHORT = {"mac": "Mac", "nvidia": "NVIDIA", "modal": "Modal"}
NOTES = {
    "mac": "free · runs on this Mac",
    "nvidia": "free · runs on this PC",
    "modal": "you need a Modal account · about $1.27 an hour while it runs, $30 free each month",
}
DRIVER_URL = "https://www.nvidia.com/drivers"

MIN_MAC_MEMORY_GB = 16
MIN_MACOS = 13
MIN_GPU_GB = 12
MIN_CUDA = (12, 0)

# Sizes in GB. Engine sizes measured on 2026-11-14 (Mac: M3 Pro; NVIDIA: Linux, CUDA 12.8 wheels).
MODEL_GB = 10.5
ENV_GB = {"mac": 1.1, "nvidia": 7.2, "modal": 0.1}
MARGIN_GB = 2.0


@dataclass
class Way:
    key: str
    possible: bool
    reason: str = ""  # why it is not possible
    recommended: bool = False

    @property
    def label(self) -> str:
        return LABELS[self.key]


def _mac(f: Facts) -> Way:
    if f.os != "mac":
        return Way("mac", False, "needs a Mac")
    if not f.apple_silicon:
        return Way("mac", False, "needs Apple silicon")
    if f.macos_major and f.macos_major < MIN_MACOS:
        return Way("mac", False, f"needs macOS {MIN_MACOS} or later")
    if f.memory_gb < MIN_MAC_MEMORY_GB - 0.5:
        return Way("mac", False, f"needs {MIN_MAC_MEMORY_GB} GB memory; this Mac has {f.memory_gb:.0f} GB")
    return Way("mac", True)


def _nvidia(f: Facts) -> Way:
    if f.os not in ("windows", "linux"):
        return Way("nvidia", False, "needs an NVIDIA GPU")
    gpu = f.gpu
    if gpu is None:
        return Way("nvidia", False, "no NVIDIA GPU found")
    if gpu.memory_gb < MIN_GPU_GB - 0.5:
        return Way("nvidia", False, f"needs {MIN_GPU_GB} GB GPU memory; this GPU has {gpu.memory_gb:.0f} GB")
    if gpu.cuda is None or gpu.cuda < MIN_CUDA:
        return Way("nvidia", False, f"update the NVIDIA driver (CUDA 12 or later): {DRIVER_URL}")
    return Way("nvidia", True)


def evaluate(facts: Facts) -> list[Way]:
    """The three ways, in screen order, with one recommended."""
    ways = [_mac(facts), _nvidia(facts), Way("modal", True)]
    local = next((w for w in ways if w.key != "modal" and w.possible), None)
    (local or ways[2]).recommended = True
    return ways


def recommended(ways: list[Way]) -> Way:
    return next(w for w in ways if w.recommended)


# --------------------------------------------------------------------- plan

@dataclass
class Item:
    text: str
    size_gb: float  # disk space it takes; 0 when nothing is stored
    folder: Path | None
    done: bool = False
    note: str = ""  # shown instead of a size ("about 2 min")


def plan(way: str, front: str | None, status: dict[str, bool]) -> list[Item]:
    """The steps of the plan screen. ``status``: "env", "model", "app" already done."""
    home = paths.home_dir()
    if way == "modal":
        return [
            Item("Install the Modal tool", ENV_GB["modal"], home, status.get("env", False)),
            Item("Start a cloud GPU and load Haru", 0, None, note="about 5 min; the model stays in your Modal account"),
        ]
    engine = "Install the Mac engine" if way == "mac" else "Install the NVIDIA engine"
    items = [
        Item(engine, ENV_GB[way], home, status.get("env", False)),
        Item("Download the Haru model", MODEL_GB, paths.hub_cache(), status.get("model", False)),
    ]
    if way == "mac" and front == "app":
        # The app is about 1 MB: no space check for it alone (else the screen shows only the 2 GB margin).
        items.append(Item("Build and install the Mac app", 0, paths.app_path().parent, status.get("app", False),
                          note="about 6 min"))
    return items


def _disk(path: Path) -> int:
    return os.stat(paths.nearest_existing(path)).st_dev


@dataclass
class Space:
    folder: Path
    need_gb: float
    free_gb: float

    @property
    def ok(self) -> bool:
        return self.free_gb >= self.need_gb


def space(items: list[Item], free_gb=None) -> list[Space]:
    """Space needed on each disk (items not done, plus a margin), with the free space there.

    ``free_gb``: function folder -> free GB (tests pass a fake)."""
    from .detect import free_gb as real_free

    free_gb = free_gb or real_free
    disks: dict[int, Space] = {}
    for item in items:
        if item.done or item.folder is None or item.size_gb <= 0:
            continue
        key = _disk(item.folder)
        if key not in disks:
            disks[key] = Space(item.folder, MARGIN_GB, free_gb(item.folder))
        disks[key].need_gb += item.size_gb
    return list(disks.values())
