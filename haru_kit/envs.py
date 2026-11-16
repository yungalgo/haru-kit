"""One uv environment for each way, with exact upstream versions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import paths
from .detect import Facts
from .procs import Failure

PYTHON = "3.12"
REQUIREMENTS = {
    "mac": ["haru-speech-mlx==0.1.0", "haru-speech==0.1.1"],
    "nvidia": ["haru-speech==0.1.1"],
    "modal": ["modal==1.6.3"],
}
STAMP = "haru-kit-env.json"


def uv_install_line(os_name: str) -> str:
    if os_name == "windows":
        return 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    return "curl -LsSf https://astral.sh/uv/install.sh | sh"


def find_uv(os_name: str) -> str:
    uv = shutil.which("uv")
    if not uv:
        raise Failure("uv is not installed.", "haru-kit uses uv to install the engine for each way.",
                      f"Install it, then run haru-kit again:\n  {uv_install_line(os_name)}")
    return uv


def torch_backend(facts: Facts) -> str | None:
    """PyTorch wheel set for the NVIDIA env: the newest CUDA build that the driver supports."""
    gpu = facts.gpu
    if gpu is None or gpu.cuda is None:
        return None
    return "cu128" if gpu.cuda >= (12, 8) else "cu126"


def spec(way: str, facts: Facts) -> dict:
    """What the env must contain; a change rebuilds it."""
    # Only what is installed in the env: a new haru-kit version alone does not reinstall the engine.
    data: dict = {"python": PYTHON, "requirements": REQUIREMENTS[way]}
    if way == "nvidia":
        data["torch_backend"] = torch_backend(facts)
    return data


def installed(way: str, facts: Facts) -> bool:
    stamp = paths.env_dir(way) / STAMP
    try:
        current = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return current == spec(way, facts) and paths.env_python(way, facts.os).is_file()


def commands(way: str, facts: Facts, uv: str) -> list[list[str]]:
    """The uv commands that make the env (lists, never shell strings: the folder has spaces)."""
    target = str(paths.env_dir(way))
    python = str(paths.env_python(way, facts.os))
    install = [uv, "pip", "install", "--python", python, *REQUIREMENTS[way]]
    backend = spec(way, facts).get("torch_backend")
    if backend:
        install += ["--torch-backend", backend]
    return [[uv, "venv", "--python", PYTHON, "--clear", "--quiet", target], install]


def size_bytes(path: Path) -> int:
    """Disk use of a folder. Symlinked files count once (the Hugging Face cache links to shared blobs)."""
    total = 0
    if not path.exists():
        return 0
    seen: set[tuple[int, int]] = set()
    for item in path.rglob("*"):
        try:
            if not item.is_file():
                continue
            stat = item.stat()
        except OSError:
            continue
        if stat.st_ino == 0:
            total += stat.st_size
            continue
        key = (stat.st_dev, stat.st_ino)
        if key not in seen:
            seen.add(key)
            total += stat.st_size
    return total


def ensure(way: str, facts: Facts, run) -> None:
    """Make the env if it is missing or old. ``run(args)`` runs one command and raises on failure."""
    if installed(way, facts):
        return
    uv = find_uv(facts.os)
    stamp = paths.env_dir(way) / STAMP
    stamp.unlink(missing_ok=True)
    for args in commands(way, facts, uv):
        run(args)
    stamp.write_text(json.dumps(spec(way, facts), indent=2), encoding="utf-8")

