"""The Haru model in the Hugging Face cache: present or not, size."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import paths
from .envs import size_bytes

FILES = ("config.json", "model.safetensors", "tokenizer.json")


def snapshot(env=None) -> Path | None:
    """A snapshot folder with all three files, or None."""
    snapshots = paths.model_dir(env) / "snapshots"
    if not snapshots.is_dir():
        return None
    for folder in sorted(snapshots.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if all((folder / name).exists() for name in FILES):
            return folder
    return None


def downloaded(env=None) -> bool:
    return snapshot(env) is not None


def size_gb(env=None) -> float:
    return size_bytes(paths.model_dir(env)) / 1e9


def remove(env=None) -> None:
    """Delete the model folder and any unshared blobs it links to inside the Hugging Face cache."""
    cache = paths.hub_cache(env).resolve()
    blobs_dir = cache / "blobs"
    folder = paths.model_dir(env)
    if not folder.exists():
        return
    targets: set[Path] = set()
    for item in folder.rglob("*"):
        if item.is_symlink():
            try:
                target = item.resolve()
            except OSError:
                continue
            if target.is_file() and target.is_relative_to(blobs_dir):
                targets.add(target)
    shutil.rmtree(folder, ignore_errors=True)
    if targets:
        still_used: set[Path] = set()
        for other in cache.glob("models--*"):
            for link in other.rglob("*"):
                if link.is_symlink():
                    try:
                        still_used.add(link.resolve())
                    except OSError:
                        pass
        for target in targets - still_used:
            target.unlink(missing_ok=True)
