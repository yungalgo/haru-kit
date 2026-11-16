"""Facts about this computer. Only the standard library; every probe has a parser that tests can feed."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

GB = 1_000_000_000


@dataclass
class Gpu:
    name: str
    memory_gb: float
    driver: str
    cuda: tuple[int, int] | None  # highest CUDA version that the driver supports


@dataclass
class Facts:
    os: str  # "mac", "windows", "linux", "other"
    arch: str  # platform.machine(), lower case
    chip: str = ""
    memory_gb: float = 0.0
    os_version: str = ""  # "15.4" on macOS, release text elsewhere
    gpus: list[Gpu] = field(default_factory=list)
    nvidia_smi: bool = False
    ssh: bool = False
    display: bool = True
    modal_login: bool = False
    uv: str | None = None
    free_gb: dict[str, float] = field(default_factory=dict)  # folder -> free GB on its disk

    @property
    def apple_silicon(self) -> bool:
        return self.os == "mac" and self.arch in ("arm64", "aarch64")

    @property
    def macos_major(self) -> int:
        try:
            return int(self.os_version.split(".")[0])
        except ValueError:
            return 0

    @property
    def gpu(self) -> Gpu | None:
        """The NVIDIA GPU with the most memory."""
        return max(self.gpus, key=lambda g: g.memory_gb) if self.gpus else None

    def describe(self) -> str:
        """One line: "Mac, Apple M3 Pro, 36 GB memory, macOS 15.4"."""
        mem = f"{self.memory_gb:.0f} GB memory"
        if self.os == "mac":
            return f"Mac, {self.chip or self.arch}, {mem}, macOS {self.os_version}"
        name = {"windows": "Windows", "linux": "Linux"}.get(self.os, platform.system() or "Unknown")
        parts = [name, self.chip or self.arch, mem]
        if self.gpu:
            parts.append(f"{self.gpu.name} ({self.gpu.memory_gb:.0f} GB)")
        return ", ".join(p for p in parts if p)


# ------------------------------------------------------------------ parsers

def parse_meminfo(text: str) -> float:
    """GB of memory (binary, as sold: "16 GB") from /proc/meminfo."""
    match = re.search(r"^MemTotal:\s+(\d+)\s*kB", text, re.MULTILINE)
    return int(match.group(1)) / 2**20 if match else 0.0


def parse_memsize(text: str) -> float:
    """GB of memory (binary) from `sysctl -n hw.memsize`."""
    try:
        return int(text.strip()) / 2**30
    except ValueError:
        return 0.0


def parse_nvidia_smi(csv: str) -> list[Gpu]:
    """Lines of `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits`."""
    gpus = []
    for line in csv.strip().splitlines():
        parts = [p.strip() for p in line.rsplit(",", 2)]
        if len(parts) < 3:
            continue
        try:
            mib = float(parts[1])
        except ValueError:
            continue
        gpus.append(Gpu(name=parts[0], memory_gb=mib / 1024, driver=parts[2], cuda=None))
    return gpus


def parse_cuda_version(header: str) -> tuple[int, int] | None:
    """The driver's CUDA version from the plain `nvidia-smi` output.

    Older drivers print "CUDA Version: 12.8"; newer ones "CUDA UMD Version: 13.3"."""
    match = re.search(r"CUDA (?:UMD )?Version:\s*(\d+)\.(\d+)", header)
    return (int(match.group(1)), int(match.group(2))) if match else None


def cuda_from_driver(driver: str) -> tuple[int, int] | None:
    """Lowest CUDA version that a driver supports, from NVIDIA's table (for when the header has none)."""
    try:
        major = int(driver.split(".")[0])
    except ValueError:
        return None
    for minimum, cuda in ((570, (12, 8)), (560, (12, 6)), (525, (12, 0))):
        if major >= minimum:
            return cuda
    return (11, 0)


def modal_logged_in(env: Mapping[str, str] | None = None, home: Path | None = None) -> bool:
    env = os.environ if env is None else env
    if env.get("MODAL_TOKEN_ID") and env.get("MODAL_TOKEN_SECRET"):
        return True
    config = Path(env.get("MODAL_CONFIG_PATH") or (home or Path.home()) / ".modal.toml")
    try:
        return "token_id" in config.read_text(encoding="utf-8")
    except OSError:
        return False


def in_ssh(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return bool(env.get("SSH_CONNECTION") or env.get("SSH_CLIENT") or env.get("SSH_TTY"))


def has_display(os_name: str, env: Mapping[str, str] | None = None) -> bool:
    """Can we open a browser here? Over SSH on macOS and Windows: no."""
    env = os.environ if env is None else env
    if os_name == "linux":
        return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    return not in_ssh(env)


# ------------------------------------------------------------------- probes

def _run(args: list[str], timeout: float = 10) -> str | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _windows_memory_gb() -> float:
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return 0.0
    return status.ullTotalPhys / 2**30


def free_gb(path: Path) -> float:
    return shutil.disk_usage(paths.nearest_existing(path)).free / GB


def nvidia() -> tuple[bool, list[Gpu]]:
    smi = shutil.which("nvidia-smi")
    if not smi and sys.platform.startswith("win"):
        smi_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation" / "NVSMI"
        candidate = smi_dir / "nvidia-smi.exe"
        smi = str(candidate) if candidate.is_file() else None
    if not smi:
        return False, []
    csv = _run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    gpus = parse_nvidia_smi(csv or "")
    cuda = parse_cuda_version(_run([smi]) or "")
    for gpu in gpus:
        gpu.cuda = cuda or cuda_from_driver(gpu.driver)
    return True, gpus


def collect() -> Facts:
    os_name = paths.system()
    facts = Facts(os=os_name, arch=platform.machine().lower())
    if os_name == "mac":
        facts.chip = (_run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "").strip()
        facts.memory_gb = parse_memsize(_run(["sysctl", "-n", "hw.memsize"]) or "")
        facts.os_version = platform.mac_ver()[0]
    elif os_name == "windows":
        facts.chip = platform.processor()
        facts.memory_gb = _windows_memory_gb()
        facts.os_version = platform.release()
    elif os_name == "linux":
        try:
            facts.memory_gb = parse_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8"))
        except OSError:
            pass
        facts.os_version = platform.release()
    if os_name in ("windows", "linux"):
        facts.nvidia_smi, facts.gpus = nvidia()
    facts.ssh = in_ssh()
    facts.display = has_display(os_name)
    facts.modal_login = modal_logged_in()
    facts.uv = shutil.which("uv")
    for folder in (paths.home_dir(), paths.hub_cache()):
        facts.free_gb[str(folder)] = free_gb(folder)
    return facts
