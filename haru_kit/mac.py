"""Mac way: the web app in the browser, or the Mac app (built on this Mac)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path

from . import paths, steps, ui
from .detect import Facts
from .procs import CommandError, Failure, run_logged
from .settings import Settings

BUILD_SECONDS = 360  # measured: the app build takes about 6 minutes on an M3 Pro
APP_ID = "io.github.yungalgo.haru-kit"  # the Mac app's bundle id (build_app.sh)
_SKIP_DIRS = {"build", "Tests", "__pycache__"}


def browser(facts: Facts, settings: Settings, verbose: bool) -> None:
    steps.install_env("mac", facts, verbose)
    steps.download_model("mac", verbose)
    args = ["--engine", "mlx", "--quantize", settings.quantize, "--port", str(settings.port),
            "--network-profile", "local"]
    runtime, url = steps.start_server("mac", args, verbose)
    opened = webbrowser.open(url) if facts.display else False
    ui.ready_browser(url, opened)
    steps.wait_until_stopped(runtime)


# ---------------------------------------------------------------- Mac app

def command_line_tools() -> bool:
    try:
        return (subprocess.run(["xcode-select", "-p"], capture_output=True).returncode == 0
                and subprocess.run(["xcrun", "--find", "swiftc"], capture_output=True).returncode == 0)
    except OSError:
        return False


def require_command_line_tools() -> None:
    if command_line_tools():
        return
    try:
        subprocess.run(["xcode-select", "--install"], capture_output=True)
    except OSError:
        pass
    raise Failure(
        "Apple's Command Line Tools are not installed.",
        "The Mac app is built on this Mac, and the build needs them.\n"
        "A window from Apple is now open: click Install (about 5 minutes).",
        "When it is done, run: haru-kit start --app\nOr use the browser now: haru-kit start --browser",
    )


def migrate_library() -> None:
    """Once: the Mac app's library had ``builtin/`` (a copy of a package library) and ``user/`` (the user's items).
    Now the app reads ``<haru-kit folder>/library/{characters,voices,images}``: move ``user/*`` up and delete
    ``builtin/``. An item that is already in the new place stays."""
    root = paths.app_library_dir()
    user = root / "user"
    if user.is_dir():
        for folder in user.iterdir():
            target = root / folder.name
            if folder.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                for item in folder.iterdir():
                    if not (target / item.name).exists():
                        shutil.move(str(item), str(target / item.name))
            elif not target.exists():
                shutil.move(str(folder), str(target))
        shutil.rmtree(user, ignore_errors=True)
    shutil.rmtree(root / "builtin", ignore_errors=True)


def app_source_hash() -> str:
    """SHA-256 over the Mac app source (paths and bytes), so any change to the source builds the app again."""
    source = paths.package_dir() / "apps" / "mac"
    digest = hashlib.sha256()
    for file in sorted(p for p in source.rglob("*") if p.is_file()):
        relative = file.relative_to(source)
        if any(part.startswith(".") or part in _SKIP_DIRS for part in relative.parts):
            continue
        digest.update(relative.as_posix().encode() + b"\0" + file.read_bytes() + b"\0")
    return digest.hexdigest()


def app_current(settings: Settings) -> bool:
    path = paths.app_path() / "Contents" / "MacOS" / "Haru"
    return path.is_file() and settings.app_source == app_source_hash()


def build_app(verbose: bool) -> None:
    """Build the app from our copy (apps/mac) with the env's Python, and install it in ~/Applications."""
    source = paths.package_dir() / "apps" / "mac"
    work = paths.home_dir() / "build" / "mac"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(source, work, ignore=shutil.ignore_patterns(".*", "build", "Tests", "__pycache__"))
    log = paths.log_file("mac")
    env = {"PYTHON": str(paths.env_python("mac")), "SKIP_CLI": "1"}
    started = time.monotonic()
    with ui.TimedStep("Build and install the Mac app", BUILD_SECONDS,
                      "the compiler shows no progress; this is normal") as step:
        try:
            run_logged(["/bin/bash", str(work / "build_app.sh")], log, step.update, verbose, env=env)
        except CommandError as error:
            raise Failure("The Mac app build failed.", "\n".join(error.tail[-8:]),
                          f"Full log: {log}\nUse the browser now: haru-kit start --browser") from None
        target = paths.app_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(work / "build" / "Haru.app", target, symlinks=True)
    shutil.rmtree(work.parent, ignore_errors=True)
    ui.done("Build and install the Mac app", f"{paths.short(target)} · {time.monotonic() - started:.0f} s")


def app(facts: Facts, settings: Settings, verbose: bool) -> None:
    require_command_line_tools()
    steps.install_env("mac", facts, verbose)
    migrate_library()
    steps.download_model("mac", verbose)
    if app_current(settings):
        ui.done("Build and install the Mac app", "already done")
    else:
        build_app(verbose)
        settings.app_source = app_source_hash()
        settings.save()
    set_registry()
    open_app()
    ui.ready_app()


def open_app(path: Path | None = None) -> None:
    subprocess.run(["open", str(path or paths.app_path())], check=False)


def set_registry() -> None:
    """The Mac app reads the shared character list from UserDefaults "registryURL" (missing: its default).
    HARU_KIT_REGISTRY sets it (empty: no list); without it, the app's default is used."""
    import os

    url = os.environ.get("HARU_KIT_REGISTRY")
    command = ["defaults", "write", APP_ID, "registryURL", "-string", url] if url is not None else \
        ["defaults", "delete", APP_ID, "registryURL"]
    subprocess.run(command, capture_output=True, check=False)
