"""Folders that haru-kit uses on each operating system."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

MODEL_REPO = "higurashi-ai/Haru-4B"


def system() -> str:
    """"mac", "windows", "linux" or "other"."""
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def home_dir(os_name: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    """The haru-kit folder: environments, settings, logs."""
    os_name = os_name or system()
    env = os.environ if env is None else env
    if os_name == "mac":
        return Path.home() / "Library" / "Application Support" / "haru-kit"
    if os_name == "windows":
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "haru-kit"
    base = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "haru-kit"


def hf_home(env: Mapping[str, str] | None = None) -> Path:
    """The Hugging Face folder (same rule as huggingface_hub)."""
    env = os.environ if env is None else env
    if env.get("HF_HOME"):
        return Path(env["HF_HOME"]).expanduser()
    if env.get("XDG_CACHE_HOME"):
        return Path(env["XDG_CACHE_HOME"]).expanduser() / "huggingface"
    return Path.home() / ".cache" / "huggingface"


def hub_cache(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("HF_HUB_CACHE"):
        return Path(env["HF_HUB_CACHE"]).expanduser()
    return hf_home(env) / "hub"


def model_dir(env: Mapping[str, str] | None = None) -> Path:
    return hub_cache(env) / ("models--" + MODEL_REPO.replace("/", "--"))


def env_dir(way: str) -> Path:
    return home_dir() / "envs" / way


def env_python(way: str, os_name: str | None = None) -> Path:
    if (os_name or system()) == "windows":
        return env_dir(way) / "Scripts" / "python.exe"
    return env_dir(way) / "bin" / "python"


def settings_file() -> Path:
    return home_dir() / "settings.json"


def log_file(way: str) -> Path:
    return home_dir() / "logs" / f"{way}.log"


def app_path() -> Path:
    return Path.home() / "Applications" / "Haru.app"


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def runtime_script(name: str) -> Path:
    return package_dir() / "runtime" / name


def app_library_dir() -> Path:
    """Where the Mac app keeps the user's library: characters/, voices/, images/."""
    return home_dir() / "library"


def nearest_existing(path: Path) -> Path:
    """The path itself or its nearest parent that exists (for free space)."""
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def short(path: Path) -> str:
    """The path with ~ for the home folder."""
    path = Path(path)
    home = Path.home()
    if path.is_relative_to(home):
        rel = path.relative_to(home)
        return "~" if rel == Path(".") else str(Path("~") / rel)
    return str(path)
